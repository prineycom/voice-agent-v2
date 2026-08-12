"""Dependency-free Slice 6 realtime session, endpointing, and event gates."""

from __future__ import annotations

from array import array
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import math
import sys
import time
from typing import Callable, Protocol

from .contracts import StageFailure, valid_correlation_id
from .tracer import CancellationToken, TraceResult

CONTROL_EVENT_VERSION = "voice-agent.realtime-control.v1"
CLIENT_CONTROL_VERSION = "voice-agent.client-control.v1"
CONTROL_TOPIC = "voice-agent.control.v1"
CLIENT_CONTROL_TOPIC = "voice-agent.client-control.v1"
SESSION_TURN_ID = "session"
MAX_CONTROL_BYTES = 65_536
MAX_EVENT_SEQUENCE = 1_000_000_000
PUBLIC_EVENT_TYPES = frozenset({
    "session.ready",
    "session.reconnected",
    "session.degraded",
    "turn.listening",
    "turn.transcribing",
    "stt.final",
    "turn.thinking",
    "llm.final",
    "turn.speaking",
    "turn.playout-ready",
    "turn.completed",
    "turn.interrupted",
    "turn.failed",
})
TERMINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.interrupted", "turn.failed"})
TURN_PREDECESSOR = {
    "turn.transcribing": "turn.listening",
    "stt.final": "turn.transcribing",
    "turn.thinking": "stt.final",
    "llm.final": "turn.thinking",
    "turn.speaking": "llm.final",
    "turn.playout-ready": "turn.speaking",
    "turn.completed": "turn.playout-ready",
}
BARGE_IN_DRAIN_BOUND_MS = 250
CANCELLATION_CLEANUP_BOUND_MS = 1_000
CLIENT_PLAYOUT_ACK_TIMEOUT_MS = 3_000
CLIENT_MEDIA_READY_TIMEOUT_MS = 3_000
CLIENT_MEDIA_READY_ACK_MARGIN_MS = 250
MAX_UTTERANCE_BYTES = 30 * 16_000 * 2


class EventSink(Protocol):
    async def send(self, event: dict[str, object]) -> None: ...


@dataclass(frozen=True)
class MediaBoundary:
    completed_publication_id: str
    next_publication_id: str


class AudioSink(Protocol):
    async def play(
        self, turn_id: str, pcm: bytes, cancelled: Callable[[], bool]
    ) -> MediaBoundary | None: ...

    async def clear(self, turn_id: str) -> str: ...


class TurnRunner(Protocol):
    def run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        input_pcm: bytes,
        cancellation: CancellationToken,
        event_observer: Callable[[dict[str, object]], None],
    ) -> TraceResult: ...

    def cancel(self) -> None: ...


@dataclass
class TurnContext:
    turn_id: str
    cancellation: CancellationToken
    task: asyncio.Task[None] | None = None
    terminal: bool = False
    rollback_complete: bool = False
    rollback_error: str | None = None
    cancellation_cleanup: asyncio.Task[str | None] | None = None
    worker: asyncio.Task[TraceResult] | None = None
    playout_ack: asyncio.Future[None] | None = None
    playout_boundary: MediaBoundary | None = None
    playout_media_generation: int | None = None


def _bounded_json_value(value: object, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or isinstance(value, (bool, str)):
        return not isinstance(value, str) or len(value) <= 8192
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(value)
    if isinstance(value, list):
        return len(value) <= 128 and all(_bounded_json_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 64 and all(
            isinstance(key, str)
            and len(key) <= 128
            and _bounded_json_value(item, depth + 1)
            for key, item in value.items()
        )
    return False


class ControlEventGate:
    """Strict receiver gate mirrored by the browser reducer."""

    def __init__(self, session_id: str, stream_epoch: int = 1) -> None:
        if not valid_correlation_id(session_id) or stream_epoch < 1:
            raise ValueError("invalid control gate identity")
        self.session_id = session_id
        self.stream_epoch = stream_epoch
        self.last_sequence = 0
        self.current_turn_id: str | None = None
        self.current_turn_terminal = True
        self.last_turn_event: str | None = None
        self.drop_count = 0

    def accept(self, event: object) -> bool:
        if not isinstance(event, dict) or set(event) != {
            "schema_version", "session_id", "turn_id", "stream_epoch",
            "sequence", "type", "terminal", "payload",
        }:
            return self._drop()
        event_type = event.get("type")
        turn_id = event.get("turn_id")
        sequence = event.get("sequence")
        epoch = event.get("stream_epoch")
        terminal = event.get("terminal")
        if (
            event.get("schema_version") != CONTROL_EVENT_VERSION
            or event.get("session_id") != self.session_id
            or not isinstance(turn_id, str)
            or not valid_correlation_id(turn_id)
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= self.last_sequence
            or sequence > MAX_EVENT_SEQUENCE
            or not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or not isinstance(event_type, str)
            or event_type not in PUBLIC_EVENT_TYPES
            or not isinstance(terminal, bool)
            or terminal != (event_type in TERMINAL_EVENT_TYPES)
            or not isinstance(event.get("payload"), dict)
            or not _bounded_json_value(event["payload"])
        ):
            return self._drop()
        if event_type in {"session.reconnected", "session.degraded"} and epoch == self.stream_epoch + 1:
            if turn_id != SESSION_TURN_ID:
                return self._drop()
            self.stream_epoch = epoch
            self.current_turn_id = None
            self.current_turn_terminal = True
            self.last_turn_event = None
        elif epoch != self.stream_epoch:
            return self._drop()
        elif event_type.startswith("session."):
            if turn_id != SESSION_TURN_ID:
                return self._drop()
        elif event_type == "turn.listening":
            if self.current_turn_id == turn_id or not self.current_turn_terminal:
                return self._drop()
            self.current_turn_id = turn_id
            self.current_turn_terminal = False
            self.last_turn_event = event_type
        elif turn_id != self.current_turn_id or self.current_turn_terminal:
            return self._drop()
        elif event_type in TURN_PREDECESSOR and TURN_PREDECESSOR[event_type] != self.last_turn_event:
            return self._drop()
        if terminal:
            self.current_turn_terminal = True
        elif not event_type.startswith("session."):
            self.last_turn_event = event_type
        self.last_sequence = sequence
        return True

    def _drop(self) -> bool:
        self.drop_count += 1
        return False


class RealtimeSession:
    """Own one room-scoped session and prevent stale inference/media delivery."""

    def __init__(
        self,
        *,
        session_id: str,
        runner: TurnRunner,
        event_sink: EventSink,
        audio_sink: AudioSink,
        failure_handler: Callable[[str, str], None] | None = None,
    ) -> None:
        if not valid_correlation_id(session_id):
            raise ValueError("invalid realtime session ID")
        self.session_id = session_id
        self.runner = runner
        self.event_sink = event_sink
        self.audio_sink = audio_sink
        self.failure_handler = failure_handler
        self.stream_epoch = 1
        self._event_sequence = 0
        self._turn_sequence = 0
        self._client_sequence = 0
        self._active: TurnContext | None = None
        self._closed = False
        self._failure_reported = False
        self._disconnect_complete = False
        self._lock = asyncio.Lock()
        self._runner_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        self._disconnect_lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[str | None]] = set()
        self._cleanup_error: str | None = None
        self._media_generation = 0
        self._media_ready_generation = 0
        self._media_generation_turn_id: str | None = None
        self._media_ready = asyncio.Event()
        self._media_ready.set()
        self._media_ready_timeout_task: asyncio.Task[None] | None = None
        self.drop_counts = {"stale_event": 0, "client_control": 0}

    @property
    def active_turn_id(self) -> str | None:
        return self._active.turn_id if self._active and not self._active.terminal else None

    async def ready(self) -> None:
        await self._emit(
            SESSION_TURN_ID,
            "session.ready",
            {"state": "ready", "barge_in_drain_bound_ms": BARGE_IN_DRAIN_BOUND_MS},
        )

    async def start_utterance(self) -> str:
        async with self._reconnect_lock:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("session is closed")
                if self._active is not None and not self._active.terminal:
                    _cleanup, drain_error, _publication_id = await self._interrupt_locked(
                        "barge_in"
                    )
                    if drain_error is not None:
                        await self._degrade_locked("publication", drain_error)
                        raise RuntimeError("audio publication could not be cancelled safely")
                    return await self._admit_listening_locked()
        while True:
            expected_epoch = self.stream_epoch
            expected_generation = self._media_generation
            media_error = await self._await_media_ready()
            async with self._reconnect_lock:
                async with self._lock:
                    if self._closed:
                        raise RuntimeError("session is closed")
                    if (
                        expected_epoch != self.stream_epoch
                        or expected_generation != self._media_generation
                        or self._media_ready_generation < self._media_generation
                    ):
                        continue
                    if media_error is not None:
                        await self._degrade_locked("publication", media_error)
                        raise RuntimeError(media_error)
                    return await self._admit_listening_locked()

    async def _admit_listening_locked(self) -> str:
        self._turn_sequence += 1
        turn_id = f"turn-{self._turn_sequence:08d}"
        context = TurnContext(
            turn_id=turn_id,
            cancellation=CancellationToken(),
        )
        self._active = context
        await self._emit(turn_id, "turn.listening", {"state": "listening"})
        return turn_id

    async def finish_utterance(self, pcm: bytes) -> str:
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_UTTERANCE_BYTES:
            raise ValueError("utterance PCM is outside the 16 kHz mono s16le bound")
        async with self._lock:
            context = self._active
            if context is None or context.terminal or context.task is not None:
                raise RuntimeError("there is no listening turn")
            context.task = asyncio.create_task(
                self._run_turn(context, bytes(pcm)), name=f"realtime-{context.turn_id}"
            )
            return context.turn_id

    async def submit_utterance(self, pcm: bytes) -> str:
        await self.start_utterance()
        return await self.finish_utterance(pcm)

    async def discard_utterance(self) -> None:
        async with self._lock:
            cleanup, drain_error, _publication_id = await self._interrupt_locked("utterance_too_short")
            if drain_error is not None:
                await self._degrade_locked("publication", drain_error)
            elif cleanup is not None:
                self._watch_cleanup(cleanup)

    async def interrupt(self, reason: str = "barge_in") -> None:
        async with self._lock:
            cleanup, drain_error, _publication_id = await self._interrupt_locked(reason)
            if drain_error is not None:
                await self._degrade_locked("publication", drain_error)
            elif cleanup is not None:
                self._watch_cleanup(cleanup)

    async def fail(self, stage: str, code: str) -> None:
        async with self._lock:
            cleanup, drain_error, _publication_id = await self._interrupt_locked(code)
            if cleanup is not None:
                self._watch_cleanup(cleanup)
            await self._degrade_locked(stage, drain_error or code)

    async def _clear_audio(self, turn_id: str) -> tuple[str | None, str | None]:
        try:
            publication_id = await asyncio.wait_for(
                self.audio_sink.clear(turn_id),
                timeout=BARGE_IN_DRAIN_BOUND_MS / 1000,
            )
            if not publication_id or len(publication_id) > 128:
                return "audio_boundary_invalid", None
        except TimeoutError:
            return "audio_drain_timeout", None
        except Exception:
            return "audio_drain_failed", None
        return None, publication_id

    async def _rollback_context(self, context: TurnContext) -> str | None:
        async with self._runner_lock:
            if context.rollback_complete:
                return context.rollback_error
            discard = getattr(self.runner, "discard_turn", None)
            try:
                if discard is not None:
                    await asyncio.to_thread(discard, self.session_id, context.turn_id)
            except Exception:
                context.rollback_error = "context_rollback_failed"
            context.rollback_complete = True
            return context.rollback_error

    def _report_failure(self, stage: str, code: str) -> None:
        if self._failure_reported:
            return
        self._failure_reported = True
        if self.failure_handler is not None:
            self.failure_handler(stage, code)

    async def _degrade_locked(self, stage: str, code: str) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._emit(
                SESSION_TURN_ID,
                "session.degraded",
                {"state": "degraded", "stage": stage, "code": code},
            )
        finally:
            self._report_failure(stage, code)

    def _track_cleanup(self, cleanup: asyncio.Task[str | None]) -> None:
        self._cleanup_tasks.add(cleanup)

        def complete(task: asyncio.Task[str | None]) -> None:
            self._cleanup_tasks.discard(task)
            try:
                error = task.result()
            except BaseException:
                error = "cancellation_cleanup_failed"
            if error is not None and self._cleanup_error is None:
                self._cleanup_error = error

        cleanup.add_done_callback(complete)

    async def _await_cleanup_barrier(
        self,
        timeout_ms: int | None,
        stop: Callable[[], bool] | None = None,
    ) -> str | None:
        deadline = (
            None
            if timeout_ms is None
            else asyncio.get_running_loop().time() + timeout_ms / 1000
        )
        while self._cleanup_tasks:
            if stop is not None and stop():
                return None
            pending = tuple(self._cleanup_tasks)
            try:
                if deadline is None:
                    results = await asyncio.gather(
                        *(asyncio.shield(task) for task in pending)
                    )
                else:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return "cancellation_cleanup_timeout"
                    results = await asyncio.wait_for(
                        asyncio.gather(*(asyncio.shield(task) for task in pending)),
                        timeout=remaining,
                    )
            except TimeoutError:
                return "cancellation_cleanup_timeout"
            except BaseException:
                return "cancellation_cleanup_failed"
            for task, error in zip(pending, results, strict=True):
                self._cleanup_tasks.discard(task)
                if error is not None and self._cleanup_error is None:
                    self._cleanup_error = error
        return self._cleanup_error

    async def wait_for_cleanup(self) -> None:
        error = await self._await_cleanup_barrier(timeout_ms=None)
        if error is not None:
            raise RuntimeError(error)
        context = self._active
        task = context.task if context is not None else None
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(task)
        error = await self._await_cleanup_barrier(timeout_ms=None)
        if error is not None:
            raise RuntimeError(error)

    def _watch_cleanup(self, cleanup: asyncio.Task[str | None]) -> None:
        async def finish() -> None:
            error = await cleanup
            if error is not None:
                async with self._lock:
                    await self._degrade_locked("controller", error)

        asyncio.create_task(finish(), name=f"watch-{cleanup.get_name()}")

    def _ensure_context_cleanup(
        self, context: TurnContext, *, wait_for_turn: bool = True
    ) -> asyncio.Task[str | None]:
        cleanup = context.cancellation_cleanup
        if cleanup is not None:
            return cleanup

        async def cancel_runner() -> str | None:
            cancel_error: Exception | None = None
            try:
                await asyncio.to_thread(context.cancellation.cancel)
                await asyncio.to_thread(self.runner.cancel)
            except Exception as error:
                cancel_error = error
            task = context.task
            worker = context.worker
            awaited = task if wait_for_turn else worker
            if awaited is not None and awaited is not asyncio.current_task():
                try:
                    await awaited
                except Exception as error:
                    cancel_error = cancel_error or error
                finally:
                    if not wait_for_turn and context.worker is worker:
                        context.worker = None
            if not context.rollback_complete:
                context.rollback_error = await self._rollback_context(context)
            if cancel_error is not None:
                return "cancellation_cleanup_failed"
            return context.rollback_error

        cleanup = asyncio.create_task(cancel_runner(), name=f"cancel-{context.turn_id}")
        context.cancellation_cleanup = cleanup
        self._track_cleanup(cleanup)
        return cleanup

    async def _interrupt_locked(
        self, reason: str, *, notify_client: bool = True
    ) -> tuple[asyncio.Task[str | None] | None, str | None, str | None]:
        context = self._active
        if context is None or context.terminal:
            return None, None, None
        context.terminal = True
        if context.playout_ack is not None and not context.playout_ack.done():
            context.playout_ack.set_result(None)

        started = time.monotonic()
        try:
            drain_error, publication_id = await self._clear_audio(context.turn_id)
        finally:
            drain_ms = (time.monotonic() - started) * 1000
            cleanup = self._ensure_context_cleanup(context)
        if notify_client:
            payload: dict[str, object] = {
                "outcome": "interrupted",
                "reason": reason,
                "drain_ms": round(drain_ms, 3),
                "drain_bound_ms": BARGE_IN_DRAIN_BOUND_MS,
            }
            if publication_id is not None:
                payload["media_publication_id"] = publication_id
            await self._emit(
                context.turn_id,
                "turn.interrupted",
                payload,
                terminal=True,
            )
        return cleanup, drain_error, publication_id

    async def _run_turn(self, context: TurnContext, pcm: bytes) -> None:
        try:
            await self._run_turn_body(context, pcm)
        except Exception:
            await self._abort_failed_transport(context)

    async def _abort_failed_transport(self, context: TurnContext) -> None:
        context.terminal = True
        self._closed = True
        self._report_failure("transport", "control_publish_failed")
        await self._clear_audio(context.turn_id)
        existing_cleanup = context.cancellation_cleanup
        cleanup = self._ensure_context_cleanup(context, wait_for_turn=False)
        if existing_cleanup is None:
            context.rollback_error = await cleanup

    async def _run_turn_body(self, context: TurnContext, pcm: bytes) -> None:
        if context.terminal or self._closed:
            return
        cleanup_error = await self._await_cleanup_barrier(
            timeout_ms=CANCELLATION_CLEANUP_BOUND_MS,
            stop=lambda: context.terminal or self._closed,
        )
        if cleanup_error is not None:
            async with self._lock:
                if not context.terminal:
                    context.terminal = True
                    await self._emit(
                        context.turn_id,
                        "turn.failed",
                        {"outcome": "failed", "stage": "controller", "code": cleanup_error},
                        terminal=True,
                    )
                await self._degrade_locked("controller", cleanup_error)
            return
        if context.terminal or self._closed:
            return
        media_error = await self._await_media_ready(context)
        if media_error is not None:
            async with self._lock:
                if not context.terminal:
                    context.terminal = True
                    await self._emit(
                        context.turn_id,
                        "turn.failed",
                        {"outcome": "failed", "stage": "publication", "code": media_error},
                        terminal=True,
                    )
                await self._degrade_locked("publication", media_error)
            return
        if context.terminal or self._closed:
            return

        loop = asyncio.get_running_loop()
        observed: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        def observe(event: dict[str, object]) -> None:
            loop.call_soon_threadsafe(observed.put_nowait, event)

        result: TraceResult | None = None
        runner_failed = False
        async with self._runner_lock:
            if context.terminal or self._closed:
                return
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self.runner.run_turn,
                    session_id=self.session_id,
                    turn_id=context.turn_id,
                    input_pcm=pcm,
                    cancellation=context.cancellation,
                    event_observer=observe,
                ),
                name=f"inference-{context.turn_id}",
            )
            context.worker = worker
            try:
                while not worker.done() or not observed.empty():
                    try:
                        event = await asyncio.wait_for(observed.get(), timeout=0.01)
                    except TimeoutError:
                        continue
                    await self._relay_internal(context, event)
                try:
                    result = await worker
                except Exception:
                    runner_failed = True
            finally:
                if worker.done() and context.worker is worker:
                    context.worker = None

        if runner_failed or result is None:
            if not context.terminal:
                context.terminal = True
                await self._emit(
                    context.turn_id,
                    "turn.failed",
                    {"outcome": "failed", "stage": "controller", "code": "inference_runner_failure"},
                    terminal=True,
                )
            context.rollback_error = await self._rollback_context(context)
            if context.rollback_error is not None:
                async with self._lock:
                    await self._degrade_locked("controller", context.rollback_error)
            return

        while not observed.empty():
            await self._relay_internal(context, observed.get_nowait())
        terminal = result.terminal_event
        output_pcm = result.output_pcm
        output_bytes = len(output_pcm)
        del result
        del worker
        pcm = b""
        if context.terminal:
            context.rollback_error = await self._rollback_context(context)
            if context.rollback_error is not None:
                async with self._lock:
                    await self._degrade_locked("controller", context.rollback_error)
            return
        if self._closed:
            return
        if terminal["type"] != "turn.completed":
            await self._relay_internal(context, terminal)
            if context.terminal:
                context.rollback_error = await self._rollback_context(context)
                if context.rollback_error is not None:
                    async with self._lock:
                        await self._degrade_locked("controller", context.rollback_error)
            return
        if not output_pcm:
            await self._fail_publication(context, "empty_audio_output", clear_audio=False)
            return
        try:
            boundary = await self.audio_sink.play(
                context.turn_id,
                output_pcm,
                lambda: context.terminal or context.cancellation.cancelled,
            )
        except Exception:
            await self._fail_publication(context, "audio_playout_exception", clear_audio=True)
            return
        finally:
            output_pcm = b""
        if boundary is None:
            await self._fail_publication(context, "audio_playout_failed", clear_audio=True)
            return
        if (
            not boundary.completed_publication_id
            or not boundary.next_publication_id
            or boundary.completed_publication_id == boundary.next_publication_id
            or len(boundary.completed_publication_id) > 128
            or len(boundary.next_publication_id) > 128
        ):
            await self._fail_publication(context, "audio_boundary_invalid", clear_audio=True)
            return
        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            context.playout_ack = asyncio.get_running_loop().create_future()
            context.playout_boundary = boundary
            context.playout_media_generation = self._invalidate_media(context.turn_id)
            await self._emit(
                context.turn_id,
                "turn.playout-ready",
                {
                    "state": "awaiting_client_playout_boundary",
                    "ack_timeout_ms": CLIENT_PLAYOUT_ACK_TIMEOUT_MS,
                    "ack_deadline_ms": (
                        CLIENT_PLAYOUT_ACK_TIMEOUT_MS
                        - CLIENT_MEDIA_READY_ACK_MARGIN_MS
                    ),
                    "media_generation": context.playout_media_generation,
                    "completed_publication_id": boundary.completed_publication_id,
                    "media_publication_id": boundary.next_publication_id,
                },
            )
        try:
            await asyncio.wait_for(
                asyncio.shield(context.playout_ack),
                timeout=CLIENT_PLAYOUT_ACK_TIMEOUT_MS / 1000,
            )
        except TimeoutError:
            await self._fail_publication(
                context, "client_playout_ack_timeout", clear_audio=True
            )
            async with self._lock:
                await self._degrade_locked("transport", "client_playout_ack_timeout")
            return

        # Completion and admission of the next turn share the session lock. This
        # prevents an old completion from appearing after the next listening event.
        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            delivered = getattr(self.runner, "turn_delivered", None)
            if delivered is not None:
                try:
                    async with self._runner_lock:
                        await asyncio.to_thread(
                            delivered, self.session_id, context.turn_id
                        )
                except Exception:
                    context.rollback_error = await self._rollback_context(context)
                    context.terminal = True
                    await self._emit(
                        context.turn_id,
                        "turn.failed",
                        {"outcome": "failed", "stage": "controller", "code": "context_commit_failed"},
                        terminal=True,
                    )
                    await self._degrade_locked(
                        "controller", context.rollback_error or "context_commit_failed"
                    )
                    return
            context.terminal = True
            await self._emit(
                context.turn_id,
                "turn.completed",
                {
                    "outcome": "completed",
                    "output_bytes": output_bytes,
                    "audio_format": {
                        "encoding": "pcm_s16le", "sample_rate_hz": 16_000,
                        "channels": 1, "sample_width_bytes": 2,
                    },
                },
                terminal=True,
            )

    async def _fail_publication(
        self, context: TurnContext, code: str, *, clear_audio: bool
    ) -> None:
        async with self._lock:
            await self._fail_publication_locked(context, code, clear_audio=clear_audio)

    async def _fail_publication_locked(
        self, context: TurnContext, code: str, *, clear_audio: bool
    ) -> None:
        if context.terminal or self._closed or self._active is not context:
            return
        drain_error: str | None = None
        publication_id: str | None = None
        if clear_audio:
            drain_error, publication_id = await self._clear_audio(context.turn_id)
        context.rollback_error = await self._rollback_context(context)
        context.terminal = True
        payload: dict[str, object] = {
            "outcome": "failed", "stage": "publication", "code": code
        }
        if publication_id is not None:
            payload["media_publication_id"] = publication_id
        await self._emit(
            context.turn_id,
            "turn.failed",
            payload,
            terminal=True,
        )
        if drain_error is not None:
            await self._degrade_locked("publication", drain_error)
        elif context.rollback_error is not None:
            await self._degrade_locked("controller", context.rollback_error)

    async def _await_media_ready(
        self, context: TurnContext | None = None
    ) -> str | None:
        deadline = (
            asyncio.get_running_loop().time()
            + CLIENT_MEDIA_READY_TIMEOUT_MS / 1000
        )
        while self._media_ready_generation < self._media_generation:
            if self._closed or (context is not None and context.terminal):
                return None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return "client_media_ready_timeout"
            try:
                await asyncio.wait_for(self._media_ready.wait(), timeout=remaining)
            except TimeoutError:
                return "client_media_ready_timeout"
        return None

    def _invalidate_media(self, turn_id: str) -> int:
        self._media_generation += 1
        if self._media_generation > MAX_EVENT_SEQUENCE:
            raise RuntimeError("session media generation exhausted")
        self._media_generation_turn_id = turn_id
        self._media_ready.clear()
        timeout = self._media_ready_timeout_task
        if timeout is not None and not timeout.done():
            timeout.cancel()
        generation = self._media_generation
        epoch = self.stream_epoch

        async def expire() -> None:
            try:
                await asyncio.sleep(CLIENT_MEDIA_READY_TIMEOUT_MS / 1000)
                async with self._lock:
                    if (
                        not self._closed
                        and self.stream_epoch == epoch
                        and self._media_ready_generation < generation
                    ):
                        await self._degrade_locked(
                            "publication", "client_media_ready_timeout"
                        )
            except asyncio.CancelledError:
                raise

        self._media_ready_timeout_task = asyncio.create_task(
            expire(), name=f"media-ready-{self.session_id}-{generation}"
        )
        return generation

    def _confirm_media_ready(self, generation: int) -> None:
        self._media_ready_generation = generation
        self._media_ready.set()
        timeout = self._media_ready_timeout_task
        if timeout is not None and not timeout.done():
            timeout.cancel()
        self._media_ready_timeout_task = None

    async def _relay_internal(self, context: TurnContext, event: dict[str, object]) -> None:
        if context.terminal or self._active is not context:
            self.drop_counts["stale_event"] += 1
            return
        if event.get("session_id") != self.session_id or event.get("turn_id") != context.turn_id:
            self.drop_counts["stale_event"] += 1
            return
        event_type = event.get("type")
        if event_type in {"turn.listening", "tts.audio", "turn.completed"}:
            return
        if event_type not in PUBLIC_EVENT_TYPES or not isinstance(event.get("payload"), dict):
            self.drop_counts["stale_event"] += 1
            return
        terminal = event_type in TERMINAL_EVENT_TYPES
        if terminal:
            context.terminal = True
        await self._emit(context.turn_id, str(event_type), dict(event["payload"]), terminal=terminal)

    async def handle_client_control(self, payload: bytes) -> bool:
        if not payload or len(payload) > MAX_CONTROL_BYTES:
            self.drop_counts["client_control"] += 1
            return False
        try:
            event = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError):
            self.drop_counts["client_control"] += 1
            return False
        if not isinstance(event, dict):
            self.drop_counts["client_control"] += 1
            return False
        event_type = event.get("type")
        if event_type == "client.reconnected":
            expected_keys = {
                "schema_version", "session_id", "stream_epoch", "sequence", "type"
            }
        elif event_type == "client.media-ready":
            expected_keys = {
                "schema_version", "session_id", "turn_id", "stream_epoch",
                "sequence", "media_generation", "type",
            }
        elif event_type == "client.playout-completed":
            expected_keys = {
                "schema_version", "session_id", "turn_id", "stream_epoch",
                "sequence", "media_generation", "completed_publication_id",
                "media_publication_id", "type",
            }
        else:
            expected_keys = set()
        sequence = event.get("sequence")
        stream_epoch = event.get("stream_epoch")
        if (
            set(event) != expected_keys
            or event.get("schema_version") != CLIENT_CONTROL_VERSION
            or event.get("session_id") != self.session_id
            or event_type not in {
                "client.reconnected", "client.playout-completed", "client.media-ready"
            }
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
            or sequence > MAX_EVENT_SEQUENCE
            or not isinstance(stream_epoch, int)
            or isinstance(stream_epoch, bool)
            or stream_epoch < 1
            or stream_epoch > MAX_EVENT_SEQUENCE
        ):
            self.drop_counts["client_control"] += 1
            return False
        if event_type in {"client.playout-completed", "client.media-ready"}:
            async with self._lock:
                context = self._active
                media_generation = event.get("media_generation")
                media_ready = (
                    event_type == "client.media-ready"
                    and isinstance(media_generation, int)
                    and not isinstance(media_generation, bool)
                    and media_generation == self._media_generation
                    and media_generation > self._media_ready_generation
                    and event.get("turn_id") == self._media_generation_turn_id
                )
                boundary = context.playout_boundary if context is not None else None
                playout_complete = (
                    event_type == "client.playout-completed"
                    and context is not None
                    and not context.terminal
                    and event.get("turn_id") == context.turn_id
                    and context.playout_ack is not None
                    and not context.playout_ack.done()
                    and boundary is not None
                    and event.get("media_generation")
                    == context.playout_media_generation
                    == self._media_generation
                    and event.get("completed_publication_id")
                    == boundary.completed_publication_id
                    and event.get("media_publication_id")
                    == boundary.next_publication_id
                )
                if (
                    sequence <= self._client_sequence
                    or self._closed
                    or stream_epoch != self.stream_epoch
                    or not (media_ready or playout_complete)
                ):
                    self.drop_counts["client_control"] += 1
                    return False
                self._client_sequence = sequence
                if media_ready:
                    assert isinstance(media_generation, int)
                    self._confirm_media_ready(media_generation)
                    if event.get("turn_id") == SESSION_TURN_ID:
                        await self._emit(
                            SESSION_TURN_ID,
                            "session.ready",
                            {
                                "state": "ready",
                                "media_generation": media_generation,
                            },
                        )
                else:
                    assert context is not None and context.playout_ack is not None
                    assert context.playout_media_generation is not None
                    self._confirm_media_ready(context.playout_media_generation)
                    context.playout_ack.set_result(None)
                return True
        async with self._reconnect_lock:
            async with self._lock:
                if sequence <= self._client_sequence or stream_epoch != self.stream_epoch:
                    self.drop_counts["client_control"] += 1
                    return False
                self._client_sequence = sequence
                if self._closed:
                    self.stream_epoch += 1
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.degraded",
                        {"state": "degraded", "stage": "controller", "code": "session_closed"},
                    )
                    self._report_failure("controller", "session_closed")
                    return True
                interrupted_turn_id = self.active_turn_id
                _cleanup, reset_error, publication_id = await self._interrupt_locked(
                    "client_reconnected", notify_client=False
                )
                if publication_id is None and reset_error is None:
                    reset_error, publication_id = await self._clear_audio(SESSION_TURN_ID)
            try:
                async with asyncio.timeout(CANCELLATION_CLEANUP_BOUND_MS / 1000):
                    cleanup_error = await self._await_cleanup_barrier(
                        timeout_ms=CANCELLATION_CLEANUP_BOUND_MS
                    )
                    if cleanup_error is not None:
                        reset_error = cleanup_error
                    async with self._runner_lock:
                        reset = getattr(self.runner, "reset_session", None)
                        if reset is not None:
                            await asyncio.to_thread(reset, self.session_id)
            except TimeoutError:
                reset_error = "reconnect_cleanup_timeout"
            except Exception:
                reset_error = "reconnect_context_reset_failed"
            async with self._lock:
                self.stream_epoch += 1
                if reset_error is None:
                    media_generation = self._invalidate_media(SESSION_TURN_ID)
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.reconnected",
                        {
                            "state": "awaiting_media",
                            "stale_media_discarded": True,
                            "conversation_context_reset": True,
                            "media_generation": media_generation,
                            "media_ready_timeout_ms": CLIENT_MEDIA_READY_TIMEOUT_MS,
                            "media_ready_ack_deadline_ms": (
                                CLIENT_MEDIA_READY_TIMEOUT_MS
                                - CLIENT_MEDIA_READY_ACK_MARGIN_MS
                            ),
                            "media_publication_id": publication_id,
                            "interrupted_turn_id": interrupted_turn_id,
                        },
                    )
                else:
                    await self._degrade_locked("controller", reset_error)
            return True

    async def disconnect(self, *, notify_client: bool = True) -> None:
        async with self._disconnect_lock:
            if self._disconnect_complete:
                return
            async with self._lock:
                context = self._active
                drain_error: str | None = None
                if context is not None and not context.terminal:
                    _cleanup, drain_error, _publication_id = await self._interrupt_locked(
                        "client_disconnected", notify_client=notify_client
                    )
                self._closed = True
            if drain_error is not None:
                raise RuntimeError(drain_error)
            cleanup_error = await self._await_cleanup_barrier(
                timeout_ms=CANCELLATION_CLEANUP_BOUND_MS
            )
            if cleanup_error is not None:
                raise RuntimeError(cleanup_error)
            if context is not None and context.task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(context.task), timeout=1.25)
                except TimeoutError as error:
                    raise RuntimeError("turn_cleanup_timeout") from error
            timeout = self._media_ready_timeout_task
            if timeout is not None and timeout is not asyncio.current_task():
                timeout.cancel()
                try:
                    await timeout
                except asyncio.CancelledError:
                    pass
                self._media_ready_timeout_task = None
            self._disconnect_complete = True

    async def _emit(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        terminal: bool = False,
    ) -> None:
        if event_type not in PUBLIC_EVENT_TYPES or terminal != (event_type in TERMINAL_EVENT_TYPES):
            raise ValueError("invalid public control event")
        self._event_sequence += 1
        if self._event_sequence > MAX_EVENT_SEQUENCE:
            raise RuntimeError("session event sequence exhausted")
        if event_type in {"turn.interrupted", "turn.failed"}:
            payload = {**payload, "media_generation": self._invalidate_media(turn_id)}
        try:
            await self.event_sink.send({
                "schema_version": CONTROL_EVENT_VERSION,
                "session_id": self.session_id,
                "turn_id": turn_id,
                "stream_epoch": self.stream_epoch,
                "sequence": self._event_sequence,
                "type": event_type,
                "terminal": terminal,
                "payload": payload,
            })
        except Exception:
            self._closed = True
            self._report_failure("transport", "control_publish_failed")
            raise


class EnergyEndpoint:
    """Bounded server-side endpoint detector for 16 kHz mono s16le browser audio."""

    def __init__(
        self,
        *,
        threshold_rms: int = 700,
        frame_ms: int = 20,
        start_ms: int = 100,
        end_silence_ms: int = 600,
        min_speech_ms: int = 180,
        pre_roll_ms: int = 200,
        max_utterance_ms: int = 15_000,
    ) -> None:
        values = (threshold_rms, frame_ms, start_ms, end_silence_ms, min_speech_ms, pre_roll_ms, max_utterance_ms)
        if any(value <= 0 for value in values) or any(
            duration % frame_ms for duration in (start_ms, end_silence_ms, min_speech_ms, pre_roll_ms, max_utterance_ms)
        ):
            raise ValueError("endpoint durations must be positive frame multiples")
        self.threshold_rms = threshold_rms
        self.frame_ms = frame_ms
        self.frame_bytes = 16_000 * 2 * frame_ms // 1000
        self.start_frames = start_ms // frame_ms
        self.end_frames = end_silence_ms // frame_ms
        self.min_speech_frames = min_speech_ms // frame_ms
        self.max_frames = max_utterance_ms // frame_ms
        self._pre_roll: deque[bytes] = deque(maxlen=pre_roll_ms // frame_ms)
        self._candidate_frames = 0
        self._speaking = False
        self._utterance: list[bytes] = []
        self._speech_frames = 0
        self._silence_frames = 0

    def feed(self, pcm: bytes) -> list[tuple[str, bytes | None]]:
        if len(pcm) != self.frame_bytes:
            raise ValueError("endpoint frame must be 20 ms of 16 kHz mono s16le PCM")
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        mean_square = sum(sample * sample for sample in samples) // max(len(samples), 1)
        rms = int(mean_square ** 0.5)
        voiced = rms >= self.threshold_rms
        signals: list[tuple[str, bytes | None]] = []

        if not self._speaking:
            self._pre_roll.append(pcm)
            if voiced:
                self._candidate_frames += 1
            else:
                self._candidate_frames = 0
            if self._candidate_frames >= self.start_frames:
                self._speaking = True
                self._utterance = list(self._pre_roll)
                self._speech_frames = self._candidate_frames
                self._silence_frames = 0
                signals.append(("speech_started", None))
            return signals

        self._utterance.append(pcm)
        if voiced:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
        if self._silence_frames >= self.end_frames or len(self._utterance) >= self.max_frames:
            if self._speech_frames >= self.min_speech_frames:
                signals.append(("utterance", b"".join(self._utterance)))
            else:
                signals.append(("speech_discarded", None))
            self.reset()
        return signals

    def flush(self) -> list[tuple[str, bytes | None]]:
        if not self._speaking:
            self.reset()
            return []
        signal = (
            ("utterance", b"".join(self._utterance))
            if self._speech_frames >= self.min_speech_frames
            else ("speech_discarded", None)
        )
        self.reset()
        return [signal]

    def reset(self) -> None:
        self._pre_roll.clear()
        self._candidate_frames = 0
        self._speaking = False
        self._utterance = []
        self._speech_frames = 0
        self._silence_frames = 0

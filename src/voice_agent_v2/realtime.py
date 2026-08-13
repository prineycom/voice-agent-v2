"""Dependency-free Slice 6 realtime session, endpointing, and event gates."""

from __future__ import annotations

from array import array
import asyncio
from collections import deque
from dataclasses import dataclass
import inspect
import json
import math
import sys
import threading
import time
from typing import Callable, Protocol

from .audio import OUTPUT_MEDIA_MAX_BYTES
from .contracts import StageFailure, valid_correlation_id
from .tracer import CancellationToken, TraceResult

CONTROL_EVENT_VERSION = "voice-agent.realtime-control.v1"
CLIENT_CONTROL_VERSION = "voice-agent.client-control.v1"
CONTROL_TOPIC = "voice-agent.control.v1"
CLIENT_CONTROL_TOPIC = "voice-agent.client-control.v1"
SESSION_TURN_ID = "session"
MAX_CONTROL_BYTES = 65_536
MAX_EVENT_SEQUENCE = 1_000_000_000
MAX_DIAGNOSTIC_FAILURES = 20_000
PCM_PUMP_MAX_BLOCKS = 2
PCM_PRODUCER_WAIT_SECONDS = 0.02
PUBLIC_EVENT_TYPES = frozenset({
    "session.ready",
    "session.reconnected",
    "session.degraded",
    "turn.listening",
    "stt.final",
    "turn.thinking",
    "llm.visible",
    "turn.speaking",
    "turn.completed",
    "turn.interrupted",
    "turn.failed",
})
TERMINAL_EVENT_TYPES = frozenset({"turn.completed", "turn.interrupted", "turn.failed"})
TURN_PREDECESSOR = {
    "stt.final": "turn.listening",
    "turn.thinking": "stt.final",
    "llm.visible": {"turn.thinking", "llm.visible", "turn.speaking"},
    "turn.speaking": {"turn.thinking", "llm.visible"},
    "turn.completed": {"turn.thinking", "llm.visible", "turn.speaking"},
}
BARGE_IN_DRAIN_BOUND_MS = 250
CANCELLATION_CLEANUP_BOUND_MS = 1_000
CONTROL_PUBLISH_BOUND_MS = 1_000
MAX_UTTERANCE_BYTES = 30 * 16_000 * 2
TERMINAL_CONTROLLER_FAILURE_CODES = frozenset({"local_lfm_handoff_cleanup_failed"})


class EventSink(Protocol):
    async def send(self, event: dict[str, object]) -> None: ...


class AudioSink(Protocol):
    async def write(
        self, turn_id: str, pcm: bytes, cancelled: Callable[[], bool]
    ) -> bool: ...

    async def clear(self, turn_id: str) -> str | None: ...


class TurnRunner(Protocol):
    def run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        input_pcm: bytes,
        cancellation: CancellationToken,
        event_observer: Callable[[dict[str, object]], None],
        trace_observer: Callable[[str, str, dict[str, object]], None] | None = None,
    ) -> TraceResult: ...

    def cancel(self) -> None: ...


@dataclass
class TurnContext:
    turn_id: str
    cancellation: CancellationToken
    endpoint_monotonic: float
    task: asyncio.Task[None] | None = None
    terminal: bool = False
    rollback_complete: bool = False
    rollback_error: str | None = None
    cancellation_cleanup: asyncio.Task[str | None] | None = None
    worker: asyncio.Task[TraceResult] | None = None
    audio_chunk_sequence: int = 0
    audio_bytes_streamed: int = 0
    pcm_queue_high_water: int = 0
    first_visible_ms: float | None = None
    first_pcm_ms: float | None = None


@dataclass(frozen=True)
class PcmPumpItem:
    request: TurnContext
    chunk_index: int
    pcm: bytes


def _bounded_json_value(value: object, depth: int = 0) -> bool:
    if depth > 5:
        return False
    if value is None or isinstance(value, (bool, str)):
        return not isinstance(value, str) or len(value) <= 8192
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(value)
    if isinstance(value, list):
        return len(value) <= 128 and all(
            _bounded_json_value(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 64 and all(
            isinstance(key, str)
            and len(key) <= 128
            and _bounded_json_value(item, depth + 1)
            for key, item in value.items()
        )
    return False


class ControlEventGate:
    """Strict same-session, same-turn, monotonically sequenced receiver gate."""

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
        elif event_type in TURN_PREDECESSOR:
            predecessor = TURN_PREDECESSOR[event_type]
            if (
                self.last_turn_event not in predecessor
                if isinstance(predecessor, set)
                else predecessor != self.last_turn_event
            ):
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
        trace_observer: Callable[[str, str, dict[str, object]], None] | None = None,
    ) -> None:
        if not valid_correlation_id(session_id):
            raise ValueError("invalid realtime session ID")
        self.session_id = session_id
        self.runner = runner
        self.event_sink = event_sink
        self.audio_sink = audio_sink
        self.failure_handler = failure_handler
        self.trace_observer = trace_observer
        self.stream_epoch = 1
        self._event_sequence = 0
        self._turn_sequence = 0
        self._client_sequence = 0
        self._last_reconnect_request: tuple[int, int] | None = None
        self._last_reconnect_payload: dict[str, object] | None = None
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
        self.drop_counts = {"stale_event": 0, "client_control": 0}
        self.diagnostic_failure_counts = {"validation": 0, "write": 0, "observer": 0}

    def _trace(self, stage: str, event: str, **fields: object) -> None:
        if self.trace_observer is None:
            return
        try:
            self.trace_observer(stage, event, fields)
        except Exception as error:
            if isinstance(error, OSError):
                kind = "write"
            elif isinstance(error, (TypeError, ValueError)):
                kind = "validation"
            else:
                kind = "observer"
            self.diagnostic_failure_counts[kind] = min(
                self.diagnostic_failure_counts[kind] + 1,
                MAX_DIAGNOSTIC_FAILURES,
            )

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
                    cleanup, drain_error, _publication_id = await self._interrupt_locked("barge_in")
                    if drain_error is not None:
                        await self._degrade_locked("publication", drain_error)
                        raise RuntimeError("audio publication could not be cancelled safely")
                    if cleanup is not None:
                        self._watch_cleanup(cleanup)
                elif self._active is not None:
                    drain_error, _publication_id = await self._clear_audio(SESSION_TURN_ID)
                    if drain_error is not None:
                        await self._degrade_locked("publication", drain_error)
                        raise RuntimeError("audio publication could not be reset safely")
                return await self._admit_listening_locked()

    async def _admit_listening_locked(self) -> str:
        self._turn_sequence += 1
        turn_id = f"turn-{self._turn_sequence:08d}"
        context = TurnContext(
            turn_id=turn_id,
            cancellation=CancellationToken(),
            endpoint_monotonic=time.monotonic(),
        )
        self._active = context
        await self._emit(turn_id, "turn.listening", {"state": "listening"})
        return turn_id

    async def finish_utterance(
        self, pcm: bytes, *, endpoint_monotonic: float | None = None
    ) -> str:
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_UTTERANCE_BYTES:
            raise ValueError("utterance PCM is outside the 16 kHz mono s16le bound")
        async with self._lock:
            context = self._active
            if context is None or context.terminal or context.task is not None:
                raise RuntimeError("there is no listening turn")
            endpoint = time.monotonic() if endpoint_monotonic is None else endpoint_monotonic
            if not math.isfinite(endpoint) or endpoint > time.monotonic() + 0.01:
                raise ValueError("invalid acoustic endpoint timestamp")
            context.endpoint_monotonic = endpoint
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
                self.audio_sink.clear(turn_id), BARGE_IN_DRAIN_BOUND_MS / 1000
            )
            if publication_id is not None and (
                not isinstance(publication_id, str)
                or not publication_id
                or len(publication_id) > 128
            ):
                return "audio_boundary_invalid", None
        except TimeoutError:
            return "audio_drain_timeout", None
        except Exception:
            return "audio_drain_failed", None
        return None, publication_id

    async def _abandon_audio(self, turn_id: str) -> tuple[str | None, str | None]:
        abandon = getattr(self.audio_sink, "abandon", None)
        if abandon is None:
            return None, None
        try:
            publication_id = await asyncio.wait_for(
                abandon(turn_id), BARGE_IN_DRAIN_BOUND_MS / 1000
            )
            if publication_id is not None and (
                not isinstance(publication_id, str)
                or not publication_id
                or len(publication_id) > 128
            ):
                return "audio_boundary_invalid", None
        except TimeoutError:
            return "audio_drain_timeout", None
        except Exception:
            return "audio_drain_failed", None
        return None, publication_id

    def _report_failure(self, stage: str, code: str) -> None:
        if self._failure_reported:
            return
        self._failure_reported = True
        if self.failure_handler is not None:
            self.failure_handler(stage, code)

    async def _publish_degraded_locked(self, stage: str, code: str) -> None:
        try:
            await self._emit(
                SESSION_TURN_ID,
                "session.degraded",
                {"state": "degraded", "stage": stage, "code": code},
            )
        finally:
            self._report_failure(stage, code)

    async def _degrade_locked(self, stage: str, code: str) -> None:
        if self._closed:
            return
        self._closed = True
        await self._publish_degraded_locked(stage, code)

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
        deadline = None if timeout_ms is None else asyncio.get_running_loop().time() + timeout_ms / 1000
        while self._cleanup_tasks:
            if stop is not None and stop():
                return None
            pending = tuple(self._cleanup_tasks)
            try:
                if deadline is None:
                    results = await asyncio.gather(*(asyncio.shield(task) for task in pending))
                else:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return "cancellation_cleanup_timeout"
                    results = await asyncio.wait_for(
                        asyncio.gather(*(asyncio.shield(task) for task in pending)),
                        remaining,
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
        error = await self._await_cleanup_barrier(None)
        if error is not None:
            raise RuntimeError(error)
        context = self._active
        task = context.task if context is not None else None
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(task)
        error = await self._await_cleanup_barrier(None)
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
        if context.cancellation_cleanup is not None:
            return context.cancellation_cleanup

        async def cancel_runner() -> str | None:
            cancel_error: Exception | None = None
            try:
                await asyncio.to_thread(context.cancellation.cancel)
                await asyncio.to_thread(self.runner.cancel)
            except Exception as error:
                cancel_error = error
            awaited = context.task if wait_for_turn else context.worker
            if awaited is not None and awaited is not asyncio.current_task():
                try:
                    await awaited
                except Exception as error:
                    cancel_error = cancel_error or error
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
                payload["server_media_publication_id"] = publication_id
            self._add_metrics(context, payload)
            await self._emit(context.turn_id, "turn.interrupted", payload, terminal=True)
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
        cleanup = self._ensure_context_cleanup(context, wait_for_turn=False)
        if context.cancellation_cleanup is cleanup:
            context.rollback_error = await cleanup

    async def _run_turn_body(self, context: TurnContext, pcm: bytes) -> None:
        if context.terminal or self._closed:
            return
        cleanup_error = await self._await_cleanup_barrier(
            CANCELLATION_CLEANUP_BOUND_MS,
            stop=lambda: context.terminal or self._closed,
        )
        if cleanup_error is not None:
            await self._terminate_failed_turn(
                context,
                "turn.failed",
                {"outcome": "failed", "stage": "controller", "code": cleanup_error},
            )
            async with self._lock:
                await self._degrade_locked("controller", cleanup_error)
            return
        if context.terminal or self._closed:
            return

        loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        pcm_queue: asyncio.Queue[PcmPumpItem | None] = asyncio.Queue(
            maxsize=PCM_PUMP_MAX_BLOCKS
        )
        pcm_capacity = asyncio.Semaphore(PCM_PUMP_MAX_BLOCKS)
        outstanding_pcm = 0
        pump_errors: list[BaseException] = []

        def observe(event: dict[str, object]) -> None:
            delivery = asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)
            while True:
                try:
                    delivery.result(timeout=PCM_PRODUCER_WAIT_SECONDS)
                    return
                except TimeoutError:
                    if context.cancellation.cancelled or context.terminal or self._closed:
                        delivery.cancel()
                        return

        async def enqueue_pcm(chunk_index: int, chunk: bytes) -> None:
            nonlocal outstanding_pcm
            await pcm_capacity.acquire()
            try:
                await pcm_queue.put(PcmPumpItem(context, chunk_index, chunk))
            except BaseException:
                pcm_capacity.release()
                raise
            outstanding_pcm += 1
            context.pcm_queue_high_water = max(
                context.pcm_queue_high_water, outstanding_pcm
            )

        def observe_audio(chunk_index: int, chunk: bytes) -> None:
            barrier = asyncio.run_coroutine_threadsafe(event_queue.join(), loop)
            while True:
                try:
                    barrier.result(timeout=PCM_PRODUCER_WAIT_SECONDS)
                    break
                except TimeoutError:
                    if context.cancellation.cancelled or context.terminal or self._closed:
                        barrier.cancel()
                        raise StageFailure("tts", "selected_tts_cancelled")
            delivery = asyncio.run_coroutine_threadsafe(
                enqueue_pcm(chunk_index, bytes(chunk)), loop
            )
            while True:
                try:
                    delivery.result(timeout=PCM_PRODUCER_WAIT_SECONDS)
                    return
                except TimeoutError:
                    if context.cancellation.cancelled or context.terminal or self._closed:
                        delivery.cancel()
                        raise StageFailure("tts", "selected_tts_cancelled")

        async def consume_events() -> None:
            while True:
                event = await event_queue.get()
                try:
                    if event is None:
                        return
                    await self._relay_internal(context, event)
                finally:
                    event_queue.task_done()

        async def consume_pcm() -> None:
            nonlocal outstanding_pcm
            while True:
                item = await pcm_queue.get()
                try:
                    if item is None:
                        return
                    await self._relay_queued_pcm(item)
                except BaseException as error:
                    pump_errors.append(error)
                    context.cancellation.cancel()
                    return
                finally:
                    if item is not None:
                        outstanding_pcm -= 1
                        pcm_capacity.release()
                    pcm_queue.task_done()

        run_parameters = inspect.signature(type(self.runner).run_turn).parameters
        streaming_audio = (
            hasattr(self.audio_sink, "write")
            and "audio_observer" in run_parameters
            and "trace_observer" in run_parameters
        )
        runner_arguments: dict[str, object] = {
            "session_id": self.session_id,
            "turn_id": context.turn_id,
            "input_pcm": pcm,
            "cancellation": context.cancellation,
            "event_observer": observe,
        }
        if "trace_observer" in run_parameters:
            runner_arguments["trace_observer"] = lambda stage, event, fields: self._trace(
                stage, event, turn_id=context.turn_id, **fields
            )
        if streaming_audio:
            runner_arguments["audio_observer"] = observe_audio
            runner_arguments["retain_output"] = False

        event_consumer = asyncio.create_task(consume_events(), name=f"events-{context.turn_id}")
        pcm_consumer = asyncio.create_task(consume_pcm(), name=f"pcm-{context.turn_id}")
        result: TraceResult | None = None
        runner_failure: Exception | None = None
        async with self._runner_lock:
            if context.terminal or self._closed:
                event_consumer.cancel()
                pcm_consumer.cancel()
                return
            worker = asyncio.create_task(
                asyncio.to_thread(self.runner.run_turn, **runner_arguments),
                name=f"inference-{context.turn_id}",
            )
            context.worker = worker
            try:
                try:
                    result = await worker
                except Exception as error:
                    runner_failure = error
                await event_queue.put(None)
                await pcm_queue.put(None)
                await event_consumer
                await pcm_consumer
            finally:
                if context.worker is worker:
                    context.worker = None
                for consumer in (event_consumer, pcm_consumer):
                    if not consumer.done():
                        consumer.cancel()

        if pump_errors:
            error = pump_errors[0]
            stage = error.stage if isinstance(error, StageFailure) else "publication"
            code = error.code if isinstance(error, StageFailure) else "audio_stream_failed"
            await self._terminate_failed_turn(
                context,
                "turn.failed",
                {"outcome": "failed", "stage": stage, "code": code},
            )
            return
        if runner_failure is not None or result is None:
            failure_stage = "controller"
            failure_code = "inference_runner_failure"
            failure_class = "NoneType"
            if runner_failure is not None:
                failure_class = type(runner_failure).__name__[:128]
                if isinstance(runner_failure, StageFailure):
                    failure_stage = runner_failure.stage
                    failure_code = runner_failure.code
            self._trace(
                failure_stage,
                "runner_failed",
                turn_id=context.turn_id,
                failure_class=failure_class,
                failure_code=failure_code,
            )
            await self._terminate_failed_turn(
                context,
                "turn.failed",
                {"outcome": "failed", "stage": failure_stage, "code": failure_code},
            )
            return

        terminal = result.terminal_event
        terminal_payload = terminal.get("payload")
        if terminal["type"] != "turn.completed":
            payload = terminal_payload if isinstance(terminal_payload, dict) else {
                "outcome": "failed",
                "stage": "controller",
                "code": "inference_runner_protocol_error",
            }
            await self._terminate_failed_turn(context, str(terminal["type"]), payload)
            return
        if context.terminal or self._closed:
            return

        output_pcm = result.output_pcm
        if not streaming_audio:
            if not output_pcm:
                await self._terminate_failed_turn(
                    context,
                    "turn.failed",
                    {"outcome": "failed", "stage": "publication", "code": "empty_audio_output"},
                )
                return
            await self._relay_audio_chunk(context, 0, output_pcm)
        output_bytes = (
            terminal_payload.get("output_bytes", context.audio_bytes_streamed)
            if isinstance(terminal_payload, dict)
            else context.audio_bytes_streamed
        )
        if (
            not isinstance(output_bytes, int)
            or isinstance(output_bytes, bool)
            or output_bytes < 1
            or output_bytes > OUTPUT_MEDIA_MAX_BYTES
            or context.audio_bytes_streamed != output_bytes
        ):
            await self._terminate_failed_turn(
                context,
                "turn.failed",
                {"outcome": "failed", "stage": "publication", "code": "audio_stream_invalid"},
            )
            return
        finish = getattr(self.audio_sink, "finish", None)
        if finish is not None:
            try:
                accepted = await finish(
                    context.turn_id,
                    lambda: context.terminal or context.cancellation.cancelled,
                )
            except Exception:
                accepted = False
            if not accepted:
                await self._terminate_failed_turn(
                    context,
                    "turn.failed",
                    {"outcome": "failed", "stage": "publication", "code": "audio_stream_failed"},
                )
                return

        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            delivered = getattr(self.runner, "turn_delivered", None)
            if delivered is not None:
                try:
                    async with self._runner_lock:
                        await asyncio.to_thread(delivered, self.session_id, context.turn_id)
                except Exception:
                    context.terminal = True
                    payload = {
                        "outcome": "failed",
                        "stage": "controller",
                        "code": "context_commit_failed",
                    }
                    self._add_metrics(context, payload)
                    await self._emit(context.turn_id, "turn.failed", payload, terminal=True)
                    await self._degrade_locked("controller", "context_commit_failed")
                    return
            context.terminal = True
            payload: dict[str, object] = {
                "outcome": "completed",
                "output_bytes": output_bytes,
                "server_pcm_queue_max_blocks": context.pcm_queue_high_water,
                "audio_format": {
                    "encoding": "pcm_s16le",
                    "sample_rate_hz": 16_000,
                    "channels": 1,
                    "sample_width_bytes": 2,
                },
            }
            self._add_metrics(context, payload)
            await self._emit(context.turn_id, "turn.completed", payload, terminal=True)

    def _add_metrics(self, context: TurnContext, payload: dict[str, object]) -> None:
        if context.first_visible_ms is not None:
            payload["endpoint_to_first_visible_ms"] = context.first_visible_ms
        if context.first_pcm_ms is not None:
            payload["endpoint_to_first_accepted_pcm_ms"] = context.first_pcm_ms

    async def _terminate_failed_turn(
        self,
        context: TurnContext,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            if payload.get("stage") == "publication":
                drain_error, publication_id = await self._clear_audio(context.turn_id)
            else:
                drain_error, publication_id = await self._abandon_audio(context.turn_id)
            context.rollback_error = await self._rollback_context(context)
            context.terminal = True
            public_payload = dict(payload)
            if publication_id is not None:
                public_payload["server_media_publication_id"] = publication_id
            self._add_metrics(context, public_payload)
            await self._emit(context.turn_id, event_type, public_payload, terminal=True)
            terminal_code = payload.get("code")
            terminal_controller_failure = (
                payload.get("stage") == "llm_provider"
                and isinstance(terminal_code, str)
                and terminal_code in TERMINAL_CONTROLLER_FAILURE_CODES
            )
            if terminal_controller_failure:
                await self._degrade_locked("llm_provider", terminal_code)
            elif drain_error is not None:
                await self._degrade_locked("publication", drain_error)
            elif context.rollback_error is not None:
                await self._degrade_locked("controller", context.rollback_error)

    async def _relay_queued_pcm(self, item: PcmPumpItem) -> None:
        context = item.request
        if (
            self._active is not context
            or context.terminal
            or context.cancellation.cancelled
            or self._closed
        ):
            self.drop_counts["stale_event"] += 1
            return
        await self._relay_audio_chunk(context, item.chunk_index, item.pcm)

    async def _relay_audio_chunk(
        self, context: TurnContext, chunk_index: int, chunk: bytes
    ) -> None:
        if (
            context.terminal
            or self._closed
            or self._active is not context
            or context.cancellation.cancelled
        ):
            self.drop_counts["stale_event"] += 1
            return
        if (
            chunk_index != context.audio_chunk_sequence
            or not chunk
            or len(chunk) % 2
            or context.audio_bytes_streamed + len(chunk) > OUTPUT_MEDIA_MAX_BYTES
        ):
            raise StageFailure("publication", "audio_stream_invalid")
        submitted_before = getattr(self.audio_sink, "submitted_bytes", None)
        try:
            accepted = await self.audio_sink.write(
                context.turn_id,
                chunk,
                lambda: context.terminal or context.cancellation.cancelled,
            )
        except Exception as error:
            self._trace(
                "publication",
                "media_write_failed",
                turn_id=context.turn_id,
                failure_class=type(error).__name__,
                failure_code="audio_stream_failed",
            )
            accepted = False
        if not accepted:
            if (
                context.terminal
                or context.cancellation.cancelled
                or self._closed
                or self._active is not context
            ):
                self.drop_counts["stale_event"] += 1
                return
            raise StageFailure("publication", "audio_stream_failed")
        if (
            context.terminal
            or context.cancellation.cancelled
            or self._closed
            or self._active is not context
        ):
            self.drop_counts["stale_event"] += 1
            return
        context.audio_chunk_sequence += 1
        context.audio_bytes_streamed += len(chunk)
        self._trace(
            "publication",
            "media_chunk",
            turn_id=context.turn_id,
            chunk_index=chunk_index,
            byte_count=len(chunk),
            total_bytes=context.audio_bytes_streamed,
        )
        submitted_after = getattr(self.audio_sink, "submitted_bytes", None)
        frame_submitted = (
            submitted_before is None
            or submitted_after is None
            or submitted_after > submitted_before
        )
        if context.first_pcm_ms is None and frame_submitted:
            context.first_pcm_ms = round(
                max(0.0, (time.monotonic() - context.endpoint_monotonic) * 1000), 3
            )
            await self._emit(
                context.turn_id,
                "turn.speaking",
                {
                    "state": "speaking",
                    "server_streamed_output": True,
                    "endpoint_to_first_accepted_pcm_ms": context.first_pcm_ms,
                },
            )

    async def _relay_internal(self, context: TurnContext, event: dict[str, object]) -> None:
        if context.terminal or self._active is not context:
            self.drop_counts["stale_event"] += 1
            return
        if event.get("session_id") != self.session_id or event.get("turn_id") != context.turn_id:
            self.drop_counts["stale_event"] += 1
            return
        event_type = event.get("type")
        if event_type in {
            "turn.listening", "turn.transcribing", "llm.final", "turn.speaking",
            "tts.audio", "turn.completed", "turn.interrupted", "turn.failed",
        }:
            return
        if event_type not in PUBLIC_EVENT_TYPES or not isinstance(event.get("payload"), dict):
            self.drop_counts["stale_event"] += 1
            return
        public_payload = dict(event["payload"])
        if event_type == "llm.visible" and context.first_visible_ms is None:
            context.first_visible_ms = round(
                max(0.0, (time.monotonic() - context.endpoint_monotonic) * 1000), 3
            )
            public_payload["endpoint_to_first_visible_ms"] = context.first_visible_ms
        await self._emit(context.turn_id, str(event_type), public_payload)

    async def handle_client_control(self, payload: bytes) -> bool:
        if not payload or len(payload) > MAX_CONTROL_BYTES:
            self.drop_counts["client_control"] += 1
            return False
        try:
            event = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError):
            self.drop_counts["client_control"] += 1
            return False
        expected_keys = {
            "schema_version", "session_id", "stream_epoch", "sequence", "type"
        }
        if not isinstance(event, dict) or set(event) != expected_keys:
            self.drop_counts["client_control"] += 1
            return False
        sequence = event.get("sequence")
        stream_epoch = event.get("stream_epoch")
        if (
            event.get("schema_version") != CLIENT_CONTROL_VERSION
            or event.get("session_id") != self.session_id
            or event.get("type") != "client.reconnected"
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
        request_key = (sequence, stream_epoch)
        async with self._reconnect_lock:
            async with self._lock:
                if (
                    request_key == self._last_reconnect_request
                    and self._last_reconnect_payload is not None
                    and stream_epoch + 1 == self.stream_epoch
                    and not self._closed
                ):
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.reconnected",
                        dict(self._last_reconnect_payload),
                    )
                    await self._emit(SESSION_TURN_ID, "session.ready", {"state": "ready"})
                    return True
                if sequence <= self._client_sequence or stream_epoch != self.stream_epoch:
                    self.drop_counts["client_control"] += 1
                    return False
                self._client_sequence = sequence
                if self._closed:
                    return False
                context = self._active
                interrupted_turn_id = self.active_turn_id
                if context is not None and not context.terminal:
                    cleanup, reset_error, _publication_id = await self._interrupt_locked(
                        "client_reconnected", notify_client=False
                    )
                else:
                    cleanup = None
                    reset_error, _publication_id = await self._clear_audio(
                        context.turn_id if context is not None else SESSION_TURN_ID
                    )
            if cleanup is not None:
                reset_error = reset_error or await self._await_cleanup_barrier(
                    CANCELLATION_CLEANUP_BOUND_MS
                )
            try:
                async with self._runner_lock:
                    reset = getattr(self.runner, "reset_session", None)
                    if reset is not None:
                        await asyncio.to_thread(reset, self.session_id)
            except Exception:
                reset_error = "reconnect_context_reset_failed"
            async with self._lock:
                self.stream_epoch += 1
                if reset_error is None:
                    reconnect_payload: dict[str, object] = {
                        "state": "ready",
                        "stale_server_pcm_discarded": True,
                        "interrupted_turn_id": interrupted_turn_id,
                    }
                    self._last_reconnect_request = request_key
                    self._last_reconnect_payload = reconnect_payload
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.reconnected",
                        dict(reconnect_payload),
                    )
                    await self._emit(SESSION_TURN_ID, "session.ready", {"state": "ready"})
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
            cleanup_error = await self._await_cleanup_barrier(CANCELLATION_CLEANUP_BOUND_MS)
            if cleanup_error is not None:
                raise RuntimeError(cleanup_error)
            if context is not None and context.task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(context.task), 1.25)
                except TimeoutError as error:
                    raise RuntimeError("turn_cleanup_timeout") from error
            self._disconnect_complete = True

    def _reserve_public_event(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        terminal: bool = False,
    ) -> dict[str, object]:
        if event_type not in PUBLIC_EVENT_TYPES or terminal != (event_type in TERMINAL_EVENT_TYPES):
            raise ValueError("invalid public control event")
        self._event_sequence += 1
        if self._event_sequence > MAX_EVENT_SEQUENCE:
            raise RuntimeError("session event sequence exhausted")
        return {
            "schema_version": CONTROL_EVENT_VERSION,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "stream_epoch": self.stream_epoch,
            "sequence": self._event_sequence,
            "type": event_type,
            "terminal": terminal,
            "payload": payload,
        }

    async def _emit(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        terminal: bool = False,
    ) -> None:
        event = self._reserve_public_event(turn_id, event_type, payload, terminal=terminal)
        try:
            await self.event_sink.send(event)
            self._trace(
                "control", "published", event_type=event_type, sequence=self._event_sequence
            )
        except Exception as error:
            self._trace(
                "control",
                "publish_failed",
                event_type=event_type,
                failure_class=type(error).__name__,
                failure_code="control_publish_failed",
            )
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
        values = (
            threshold_rms, frame_ms, start_ms, end_silence_ms,
            min_speech_ms, pre_roll_ms, max_utterance_ms,
        )
        if any(value <= 0 for value in values) or any(
            duration % frame_ms
            for duration in (
                start_ms, end_silence_ms, min_speech_ms, pre_roll_ms, max_utterance_ms,
            )
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
        rms = int(mean_square**0.5)
        voiced = rms >= self.threshold_rms
        signals: list[tuple[str, bytes | None]] = []
        if not self._speaking:
            self._pre_roll.append(pcm)
            self._candidate_frames = self._candidate_frames + 1 if voiced else 0
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

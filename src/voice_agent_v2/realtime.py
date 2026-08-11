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
    "turn.completed": "turn.speaking",
}
BARGE_IN_DRAIN_BOUND_MS = 250
CANCELLATION_CLEANUP_BOUND_MS = 1_000
MAX_UTTERANCE_BYTES = 30 * 16_000 * 2


class EventSink(Protocol):
    async def send(self, event: dict[str, object]) -> None: ...


class AudioSink(Protocol):
    async def play(
        self, turn_id: str, pcm: bytes, cancelled: Callable[[], bool]
    ) -> bool: ...

    async def clear(self, turn_id: str) -> None: ...


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
    prior_cleanup: asyncio.Task[None] | None = None
    task: asyncio.Task[None] | None = None
    terminal: bool = False


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
    ) -> None:
        if not valid_correlation_id(session_id):
            raise ValueError("invalid realtime session ID")
        self.session_id = session_id
        self.runner = runner
        self.event_sink = event_sink
        self.audio_sink = audio_sink
        self.stream_epoch = 1
        self._event_sequence = 0
        self._turn_sequence = 0
        self._client_sequence = 0
        self._active: TurnContext | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._runner_lock = asyncio.Lock()
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
        async with self._lock:
            if self._closed:
                raise RuntimeError("session is closed")
            prior_cleanup = await self._interrupt_locked("barge_in")
            self._turn_sequence += 1
            turn_id = f"turn-{self._turn_sequence:08d}"
            context = TurnContext(
                turn_id=turn_id,
                cancellation=CancellationToken(),
                prior_cleanup=prior_cleanup,
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
            await self._interrupt_locked("utterance_too_short")

    async def interrupt(self, reason: str = "barge_in") -> None:
        async with self._lock:
            await self._interrupt_locked(reason)

    async def _interrupt_locked(self, reason: str) -> asyncio.Task[None] | None:
        context = self._active
        if context is None or context.terminal:
            return None
        started = time.monotonic()
        context.terminal = True
        context.cancellation.cancel()
        try:
            await asyncio.wait_for(
                self.audio_sink.clear(context.turn_id),
                timeout=BARGE_IN_DRAIN_BOUND_MS / 1000,
            )
        except TimeoutError:
            pass

        async def cancel_runner() -> None:
            try:
                discard = getattr(self.runner, "discard_turn", None)
                if discard is not None:
                    await asyncio.to_thread(discard, self.session_id, context.turn_id)
                await asyncio.to_thread(self.runner.cancel)
            except Exception:
                return

        cleanup = asyncio.create_task(cancel_runner(), name=f"cancel-{context.turn_id}")
        drain_ms = min((time.monotonic() - started) * 1000, float(BARGE_IN_DRAIN_BOUND_MS))
        await self._emit(
            context.turn_id,
            "turn.interrupted",
            {
                "outcome": "interrupted",
                "reason": reason,
                "drain_ms": round(drain_ms, 3),
                "drain_bound_ms": BARGE_IN_DRAIN_BOUND_MS,
            },
            terminal=True,
        )
        return cleanup

    async def _run_turn(self, context: TurnContext, pcm: bytes) -> None:
        if context.prior_cleanup is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(context.prior_cleanup),
                    timeout=CANCELLATION_CLEANUP_BOUND_MS / 1000,
                )
            except TimeoutError:
                if not context.terminal:
                    context.terminal = True
                    await self._emit(
                        context.turn_id,
                        "turn.failed",
                        {"outcome": "failed", "stage": "controller", "code": "cancellation_cleanup_timeout"},
                        terminal=True,
                    )
                return
        if context.terminal or self._closed:
            return

        loop = asyncio.get_running_loop()
        observed: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        def observe(event: dict[str, object]) -> None:
            loop.call_soon_threadsafe(observed.put_nowait, event)

        async with self._runner_lock:
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
            while not worker.done() or not observed.empty():
                try:
                    event = await asyncio.wait_for(observed.get(), timeout=0.01)
                except TimeoutError:
                    continue
                await self._relay_internal(context, event)
            try:
                result = await worker
            except Exception:
                if not context.terminal:
                    context.terminal = True
                    await self._emit(
                        context.turn_id,
                        "turn.failed",
                        {"outcome": "failed", "stage": "controller", "code": "inference_runner_failure"},
                        terminal=True,
                    )
                return

        while not observed.empty():
            await self._relay_internal(context, observed.get_nowait())
        if context.terminal or self._closed:
            return
        terminal = result.terminal_event
        if terminal["type"] != "turn.completed":
            await self._relay_internal(context, terminal)
            return
        if not result.output_pcm:
            context.terminal = True
            await self._emit(
                context.turn_id,
                "turn.failed",
                {"outcome": "failed", "stage": "publication", "code": "empty_audio_output"},
                terminal=True,
            )
            return
        played = await self.audio_sink.play(
            context.turn_id, result.output_pcm, lambda: context.terminal or context.cancellation.cancelled
        )
        # Completion and admission of the next turn share the session lock. This
        # prevents an old completion from appearing after the next listening event.
        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            if played:
                delivered = getattr(self.runner, "turn_delivered", None)
                if delivered is not None:
                    try:
                        await asyncio.to_thread(delivered, self.session_id, context.turn_id)
                    except Exception:
                        context.terminal = True
                        await self._emit(
                            context.turn_id,
                            "turn.failed",
                            {"outcome": "failed", "stage": "controller", "code": "context_commit_failed"},
                            terminal=True,
                        )
                        return
                context.terminal = True
                await self._emit(
                    context.turn_id,
                    "turn.completed",
                    {
                        "outcome": "completed",
                        "output_bytes": len(result.output_pcm),
                        "audio_format": {
                            "encoding": "pcm_s16le", "sample_rate_hz": 16_000,
                            "channels": 1, "sample_width_bytes": 2,
                        },
                    },
                    terminal=True,
                )
            else:
                discard = getattr(self.runner, "discard_turn", None)
                if discard is not None:
                    try:
                        await asyncio.to_thread(discard, self.session_id, context.turn_id)
                    except Exception:
                        pass
                context.terminal = True
                await self._emit(
                    context.turn_id,
                    "turn.failed",
                    {"outcome": "failed", "stage": "publication", "code": "audio_playout_failed"},
                    terminal=True,
                )

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
            if event_type == "turn.failed":
                discard = getattr(self.runner, "discard_turn", None)
                if discard is not None:
                    try:
                        await asyncio.to_thread(discard, self.session_id, context.turn_id)
                    except Exception:
                        pass
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
        if not isinstance(event, dict) or set(event) != {"schema_version", "session_id", "sequence", "type"}:
            self.drop_counts["client_control"] += 1
            return False
        sequence = event.get("sequence")
        if (
            event.get("schema_version") != CLIENT_CONTROL_VERSION
            or event.get("session_id") != self.session_id
            or event.get("type") != "client.reconnected"
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= self._client_sequence
            or sequence > MAX_EVENT_SEQUENCE
        ):
            self.drop_counts["client_control"] += 1
            return False
        self._client_sequence = sequence
        async with self._lock:
            cleanup = await self._interrupt_locked("client_reconnected")
            reset_error: str | None = None
            try:
                async with asyncio.timeout(CANCELLATION_CLEANUP_BOUND_MS / 1000):
                    if cleanup is not None:
                        await asyncio.shield(cleanup)
                    async with self._runner_lock:
                        reset = getattr(self.runner, "reset_session", None)
                        if reset is not None:
                            await asyncio.to_thread(reset, self.session_id)
            except TimeoutError:
                reset_error = "reconnect_cleanup_timeout"
            except Exception:
                reset_error = "reconnect_context_reset_failed"
            self.stream_epoch += 1
            if reset_error is None:
                await self._emit(
                    SESSION_TURN_ID,
                    "session.reconnected",
                    {
                        "state": "ready",
                        "stale_media_discarded": True,
                        "conversation_context_reset": True,
                    },
                )
            else:
                self._closed = True
                await self._emit(
                    SESSION_TURN_ID,
                    "session.degraded",
                    {"state": "degraded", "stage": "controller", "code": reset_error},
                )
        return True

    async def disconnect(self) -> None:
        async with self._lock:
            if self._closed:
                return
            cleanup = await self._interrupt_locked("client_disconnected")
            self._closed = True
        if cleanup is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(cleanup),
                    timeout=CANCELLATION_CLEANUP_BOUND_MS / 1000,
                )
            except TimeoutError:
                pass
        context = self._active
        if context is not None and context.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(context.task), timeout=1.25)
            except TimeoutError:
                pass

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

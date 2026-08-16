"""Dependency-free Slice 6 realtime session, endpointing, and event gates."""

from __future__ import annotations

from array import array
import asyncio
from collections import deque
from dataclasses import dataclass, field
import inspect
import json
import math
import sys
import threading
import time
from typing import Awaitable, Callable, Protocol

from .contracts import StageFailure, valid_correlation_id
from .observability import (
    ComponentHealth,
    HealthReport,
    ResourceSampler,
    failure_payload,
)
from .v2_audio import (
    INPUT_AUDIO_FORMAT,
    INPUT_MEDIA_MAX_BYTES,
    OUTPUT_DELIVERY_BLOCK_BYTES,
    TTS_OUTPUT_AUDIO_FORMAT,
    TTS_V2_OUTPUT_MEDIA_MAX_BYTES,
)
from .v2_contracts import TTS_V2_VERSION
from .tracer import CancellationToken, TraceResult

CONTROL_EVENT_VERSION = "voice-agent.realtime-control.v2"
CLIENT_CONTROL_VERSION = "voice-agent.client-control.v1"
CONTROL_TOPIC = "voice-agent.control.v2"
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
    "turn.media-ready",
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
    "turn.media-ready": "turn.listening",
    "stt.final": "turn.media-ready",
    "turn.thinking": "stt.final",
    "llm.visible": {"turn.thinking", "llm.visible", "turn.speaking"},
    "turn.speaking": {"turn.thinking", "llm.visible"},
    "turn.completed": {"turn.thinking", "llm.visible", "turn.speaking"},
}
BARGE_IN_DRAIN_BOUND_MS = 250
CANCELLATION_CLEANUP_BOUND_MS = 1_000
CONTROL_PUBLISH_BOUND_MS = 1_000
MAX_UTTERANCE_BYTES = INPUT_MEDIA_MAX_BYTES
TERMINAL_CONTROLLER_FAILURE_CODES = frozenset({"local_lfm_handoff_cleanup_failed"})
CONTROL_OBSERVATION_PAYLOAD_FIELDS = (
    "user_state",
    "dependency_class",
    "failure_matrix_id",
    "outcome",
    "admit_turn",
    "voice_continues",
    "text_salvageable",
    "retry_count",
    "retry_limit",
    "operator_only",
    "provider_switched",
    "external_transfer_changed",
    "stt_or_tts_moved_to_cloud",
    "auth_changed",
    "wake_changed",
    "avatar_selection_changed",
    "provider_mode",
    "provider_identity",
    "external_transfer",
    "provider_request_id_available",
    "provider_input_unit_count",
    "provider_output_unit_count",
    "provider_total_unit_count",
    "endpoint_to_stt_final_ms",
    "provider_time_to_first_token_ms",
    "provider_completion_ms",
    "tts_time_to_first_audio_ms",
    "cancellation_latency_ms",
    "endpoint_to_first_visible_ms",
    "endpoint_to_first_accepted_pcm_ms",
    "total_turn_ms",
    "slowest_stage",
    "output_bytes",
    "output_padded_bytes",
    "output_frames_submitted",
    "server_pcm_queue_max_blocks",
    "server_segment_queue_max_segments",
    "turn_admission_count",
    "turn_completion_count",
    "turn_failure_count",
    "turn_interruption_count",
    "cancellation_count",
    "stale_drop_count",
    "client_control_drop_count",
    "cpu_utilization_percent",
    "host_ram_used_mib",
    "process_rss_mib",
    "gpu_vram_used_mib",
    "gpu_utilization_percent",
)


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
        trace_observer: Callable[[str, str, dict[str, object]], bool | None] | None = None,
    ) -> TraceResult: ...

    def cancel(self) -> None: ...


@dataclass
class TurnContext:
    turn_id: str
    cancellation: CancellationToken
    endpoint_monotonic: float
    stream_epoch: int = 1
    turn_generation: int = 1
    request_id: str = "request-test"
    media_generation: int = 1
    task: asyncio.Task[None] | None = None
    terminal: bool = False
    announced: bool = True
    rollback_complete: bool = False
    rollback_error: str | None = None
    cancellation_cleanup: asyncio.Task[str | None] | None = None
    worker: asyncio.Task[TraceResult] | None = None
    audio_chunk_sequence: int = 0
    audio_bytes_streamed: int = 0
    pcm_queue_high_water: int = 0
    segment_queue_high_water: int = 0
    first_visible_ms: float | None = None
    first_pcm_ms: float | None = None
    media_publication_id: str | None = None
    internal_event_sequence: int = 0
    noncooperative_worker_drains: int = 0
    cooperative_cleanup_complete: threading.Event = field(default_factory=threading.Event)
    llm_context_committed: bool = False
    stt_final_ms: float | None = None
    llm_started_monotonic: float | None = None
    provider_first_token_ms: float | None = None
    provider_completion_ms: float | None = None
    provider_input_unit_count: int | None = None
    provider_output_unit_count: int | None = None
    provider_total_unit_count: int | None = None
    tts_started_monotonic: float | None = None
    tts_first_audio_ms: float | None = None
    resource_endpoint: dict[str, object] = field(default_factory=dict)
    resource_terminal: dict[str, object] = field(default_factory=dict)
    resource_endpoint_started: bool = False
    resource_terminal_started: bool = False
    transport_failed: bool = False
    admission_counted: bool = False
    public_event_published: bool = False


@dataclass(frozen=True)
class PcmPumpItem:
    request: TurnContext
    block_index: int
    pcm: bytes
    final_partial: bool = False
    block_size: int = OUTPUT_DELIVERY_BLOCK_BYTES


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
        self.current_turn_generation = 0
        self.current_request_id = SESSION_TURN_ID
        self.current_media_generation = 0
        self.drop_count = 0

    def accept(self, event: object) -> bool:
        if not isinstance(event, dict) or set(event) != {
            "schema_version", "session_id", "turn_id", "stream_epoch",
            "turn_generation", "request_id", "media_generation",
            "sequence", "type", "terminal", "payload",
        }:
            return self._drop()
        event_type = event.get("type")
        turn_id = event.get("turn_id")
        sequence = event.get("sequence")
        epoch = event.get("stream_epoch")
        terminal = event.get("terminal")
        turn_generation = event.get("turn_generation")
        request_id = event.get("request_id")
        media_generation = event.get("media_generation")
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
            or not isinstance(turn_generation, int)
            or isinstance(turn_generation, bool)
            or not 0 <= turn_generation <= MAX_EVENT_SEQUENCE
            or not isinstance(request_id, str)
            or not valid_correlation_id(request_id)
            or not isinstance(media_generation, int)
            or isinstance(media_generation, bool)
            or not 0 <= media_generation <= MAX_EVENT_SEQUENCE
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
            self.current_turn_generation = 0
            self.current_request_id = SESSION_TURN_ID
            self.current_media_generation = 0
        elif epoch != self.stream_epoch:
            return self._drop()
        elif event_type.startswith("session."):
            if (
                turn_id != SESSION_TURN_ID
                or turn_generation != 0
                or request_id != SESSION_TURN_ID
                or media_generation != 0
            ):
                return self._drop()
        elif event_type == "turn.listening" or self._initial_publication_failure(event):
            if (
                self.current_turn_id == turn_id
                or not self.current_turn_terminal
                or turn_generation < 1
                or request_id == SESSION_TURN_ID
                or media_generation < 1
            ):
                return self._drop()
            self.current_turn_id = turn_id
            self.current_turn_generation = turn_generation
            self.current_request_id = request_id
            self.current_media_generation = media_generation
            self.current_turn_terminal = terminal
            self.last_turn_event = event_type
        elif (
            turn_id != self.current_turn_id
            or turn_generation != self.current_turn_generation
            or request_id != self.current_request_id
            or media_generation != self.current_media_generation
            or self.current_turn_terminal
        ):
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

    @staticmethod
    def _initial_publication_failure(event: dict[str, object]) -> bool:
        payload = event.get("payload")
        return (
            event.get("type") == "turn.failed"
            and event.get("terminal") is True
            and isinstance(payload, dict)
            and payload.get("outcome") == "failed"
            and payload.get("stage") == "publication"
            and isinstance(payload.get("code"), str)
            and 1 <= len(payload["code"]) <= 64
            and all(
                character in "abcdefghijklmnopqrstuvwxyz0123456789_"
                for character in payload["code"]
            )
            and payload.get("dependency_class") == "hard"
            and payload.get("failure_matrix_id") == "livekit_unavailable"
            and payload.get("admit_turn") is False
            and payload.get("user_state") == "retrying"
        )

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
        trace_observer: Callable[[str, str, dict[str, object]], bool | None] | None = None,
        reconnect_reset_handler: Callable[[], Awaitable[None]] | None = None,
        resource_sampler: ResourceSampler | None = None,
        diagnostic_capture: object | None = None,
    ) -> None:
        if not valid_correlation_id(session_id):
            raise ValueError("invalid realtime session ID")
        self.session_id = session_id
        self.runner = runner
        self.event_sink = event_sink
        self.audio_sink = audio_sink
        self.failure_handler = failure_handler
        self.trace_observer = trace_observer
        self.reconnect_reset_handler = reconnect_reset_handler
        self.resource_sampler = resource_sampler
        self.diagnostic_capture = diagnostic_capture
        self.stream_epoch = 1
        self._event_sequence = 0
        self._turn_sequence = 0
        self._media_generation = 0
        self._client_sequence = 0
        self._last_reconnect_request: tuple[int, int] | None = None
        self._last_reconnect_payload: dict[str, object] | None = None
        self._active: TurnContext | None = None
        self._closed = False
        self._failure_reported = False
        self._disconnect_complete = False
        self._lock = asyncio.Lock()
        self._runner_lock = asyncio.Lock()
        shared_segment_capacity = getattr(runner, "complete_segment_capacity", None)
        self._segment_capacity = (
            shared_segment_capacity
            if shared_segment_capacity is not None
            else asyncio.BoundedSemaphore(2)
        )
        self._reconnect_lock = asyncio.Lock()
        self._disconnect_lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[str | None]] = set()
        self._detached_cleanup_tasks: set[asyncio.Task[str | None]] = set()
        self._cleanup_error: str | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._diagnostic_tasks: set[asyncio.Task[None]] = set()
        self.drop_counts = {"stale_event": 0, "client_control": 0}
        self.turn_counts = {
            "admitted": 0,
            "completed": 0,
            "failed": 0,
            "interrupted": 0,
            "cancellations": 0,
        }
        self.diagnostic_failure_counts = {"validation": 0, "write": 0, "observer": 0}

    def _trace(self, stage: str, event: str, **fields: object) -> None:
        if self.trace_observer is None:
            return
        try:
            accepted = self.trace_observer(stage, event, fields)
            if accepted is False:
                self.diagnostic_failure_counts["observer"] = min(
                    self.diagnostic_failure_counts["observer"] + 1,
                    MAX_DIAGNOSTIC_FAILURES,
                )
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

    @property
    def closed(self) -> bool:
        return self._closed

    def _count_admission(self, context: TurnContext) -> None:
        if context.admission_counted:
            return
        context.admission_counted = True
        self.turn_counts["admitted"] += 1

    def _rollback_unpublished_admission(self, context: TurnContext) -> None:
        if (
            context.admission_counted
            and not context.public_event_published
            and not context.transport_failed
        ):
            context.admission_counted = False
            self.turn_counts["admitted"] -= 1

    def _health_report(
        self, *, failed_stage: str | None = None, failure_code: str | None = None
    ) -> dict[str, object]:
        components = [
            ComponentHealth(
                "livekit", "alive", "ready", True,
                "livekit-room-session", CONTROL_EVENT_VERSION,
            ),
            ComponentHealth(
                "controller", "alive", "ready", True,
                "voice-agent-v2-controller", CONTROL_EVENT_VERSION,
            ),
        ]
        readiness_components = getattr(self.runner, "readiness_components", None)
        if readiness_components is None:
            components.extend((
                ComponentHealth("stt", "alive", "ready", True, "configured-stt", "voice-agent.stt.v1"),
                ComponentHealth("selected_llm", "alive", "ready", True, "configured-local-llm", "voice-agent.llm-provider.v1"),
                ComponentHealth("tts", "alive", "ready", True, "configured-tts", TTS_V2_VERSION),
            ))
        else:
            reported = tuple(readiness_components())
            if not all(isinstance(component, ComponentHealth) for component in reported):
                raise RuntimeError("runner readiness components are invalid")
            components.extend(reported)
        if failed_stage is not None and failure_code is not None:
            affected = {
                "transport": "livekit",
                "livekit": "livekit",
                "publication": "livekit",
                "controller": "controller",
                "input": "controller",
                "microphone_capture": "controller",
                "stt": "stt",
                "llm_provider": "selected_llm",
                "local_inference": "selected_llm",
                "tts": "tts",
            }.get(failed_stage, "controller")
            force_dead = any(
                marker in failure_code
                for marker in ("unavailable", "crash", "process", "publish_failed")
            ) or failure_code in {"tts_backend_not_ready", "silero_pool_not_ready"}
            components = [
                ComponentHealth(
                    component.component,
                    "dead" if component.component == affected and force_dead else component.liveness,
                    "unready" if component.component == affected else component.readiness,
                    component.compatible,
                    component.identity,
                    component.contract_version,
                    failure_code if component.component == affected else component.reason_code,
                    component.retry_count,
                    component.retry_limit,
                )
                for component in components
            ]
        return HealthReport(tuple(components)).as_dict()

    def _readiness_failure(self) -> tuple[str, str]:
        failure = getattr(self.runner, "admission_failure", None)
        if failure is not None:
            reported = failure()
            if (
                isinstance(reported, tuple)
                and len(reported) == 2
                and all(isinstance(value, str) for value in reported)
            ):
                return reported
        return "tts", "tts_backend_not_ready"

    async def _sample_resources(self, context: TurnContext, phase: str) -> None:
        if self.resource_sampler is None:
            return
        try:
            sample = await asyncio.to_thread(self.resource_sampler.sample)
            fields = sample.as_fields(phase=phase)
            if phase == "endpoint":
                context.resource_endpoint = fields
            else:
                context.resource_terminal = fields
            self._trace(
                "resource", "sample", turn_id=context.turn_id,
                stream_epoch=context.stream_epoch, **fields,
            )
        except Exception as error:
            self._trace(
                "resource", "sample_failed", turn_id=context.turn_id,
                stream_epoch=context.stream_epoch,
                failure_class=type(error).__name__, failure_code="resource_sample_failed",
            )

    def _schedule_resource_sample(self, context: TurnContext, phase: str) -> None:
        if self.resource_sampler is None:
            return
        started_name = (
            "resource_endpoint_started" if phase == "endpoint"
            else "resource_terminal_started"
        )
        if getattr(context, started_name):
            return
        setattr(context, started_name, True)
        task = asyncio.create_task(
            self._sample_resources(context, phase),
            name=f"resource-{phase}-{context.turn_id}",
        )
        self._diagnostic_tasks.add(task)
        task.add_done_callback(self._diagnostic_tasks.discard)

    def _capture_content(self, kind: str, payload: bytes | str, turn_id: str) -> None:
        capture = self.diagnostic_capture
        if capture is None:
            return
        try:
            capture.capture(kind, payload)
            self._trace("capture", "written", turn_id=turn_id, capture_kind=kind)
        except Exception as error:
            self._trace(
                "capture", "write_failed", turn_id=turn_id,
                capture_kind=kind, failure_class=type(error).__name__,
                failure_code="capture_write_failed",
            )

    async def ready(self) -> bool:
        async with self._lock:
            if self._closed:
                return False
            admission = getattr(self.runner, "ready_for_admission", None)
            if admission is not None and not admission():
                stage, code = self._readiness_failure()
                await self._degrade_locked(stage, code)
                return False
            await self._emit(
                SESSION_TURN_ID,
                "session.ready",
                {
                    "state": "ready",
                    "user_state": "available",
                    "barge_in_drain_bound_ms": BARGE_IN_DRAIN_BOUND_MS,
                    "health": self._health_report(),
                },
            )
            return True

    async def start_utterance(self, *, announce: bool = True) -> str:
        async with self._reconnect_lock:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("session is closed")
                admission = getattr(self.runner, "ready_for_admission", None)
                if admission is not None and not admission():
                    stage, code = self._readiness_failure()
                    await self._degrade_locked(stage, code)
                    raise RuntimeError(f"{stage} is not ready for admission")
                if self._active is not None and not self._active.terminal:
                    cleanup, drain_error, _publication_id = await self._interrupt_locked("barge_in")
                    if drain_error is not None:
                        await self._degrade_locked("publication", drain_error)
                        raise RuntimeError("audio publication could not be cancelled safely")
                    if cleanup is not None:
                        try:
                            cleanup_error = await asyncio.wait_for(
                                asyncio.shield(cleanup),
                                CANCELLATION_CLEANUP_BOUND_MS / 1000,
                            )
                        except TimeoutError:
                            cleanup_error = "cancellation_cleanup_timeout"
                        if cleanup_error is not None:
                            await self._degrade_locked("controller", cleanup_error)
                            raise RuntimeError("inference cancellation did not drain safely")
                elif self._active is not None:
                    drain_error, _publication_id = await self._clear_audio(SESSION_TURN_ID)
                    if drain_error is not None:
                        await self._degrade_locked("publication", drain_error)
                        raise RuntimeError("audio publication could not be reset safely")
                return await self._admit_listening_locked(announce=announce)

    async def _admit_listening_locked(self, *, announce: bool = True) -> str:
        self._turn_sequence += 1
        self._media_generation += 1
        turn_id = f"turn-{self._turn_sequence:08d}"
        context = TurnContext(
            turn_id=turn_id,
            stream_epoch=self.stream_epoch,
            turn_generation=self._turn_sequence,
            request_id=f"request-{self._turn_sequence:08d}",
            media_generation=self._media_generation,
            cancellation=CancellationToken(),
            endpoint_monotonic=time.monotonic(),
            announced=announce,
        )
        self._active = context
        if announce:
            try:
                await self._announce_context_cancellation_safe_locked(context)
            except BaseException:
                context.terminal = True
                self._rollback_unpublished_admission(context)
                if self._active is context:
                    self._active = None
                if context.media_publication_id is not None:
                    await self._abandon_audio(context.turn_id)
                raise
        return turn_id

    async def _announce_context_cancellation_safe_locked(
        self, context: TurnContext
    ) -> None:
        announcement = asyncio.create_task(
            self._announce_context_locked(context),
            name=f"announce-{context.turn_id}",
        )
        cancellation_requested = False
        while True:
            try:
                await asyncio.wait((announcement,))
                await announcement
                break
            except asyncio.CancelledError:
                if announcement.done():
                    try:
                        await announcement
                    except asyncio.CancelledError:
                        if not context.public_event_published:
                            raise
                        cancellation_requested = True
                        break
                if not context.public_event_published:
                    announcement.cancel()
                    try:
                        await announcement
                    except asyncio.CancelledError:
                        pass
                    if not context.public_event_published:
                        raise
                cancellation_requested = True
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
        if cancellation_requested:
            if self._active is context and not context.terminal:
                cleanup, drain_error, _publication_id = await self._interrupt_locked(
                    "announcement_cancelled"
                )
                if drain_error is not None:
                    await self._degrade_locked("publication", drain_error)
                elif cleanup is not None:
                    self._watch_cleanup(cleanup)
            raise asyncio.CancelledError

    async def _announce_context_locked(self, context: TurnContext) -> None:
        prepare = getattr(self.audio_sink, "prepare", None)
        if prepare is None:
            publication_id = "legacy-test-publication"
        else:
            try:
                publication_id = await asyncio.wait_for(
                    prepare(context.turn_id, context.media_generation),
                    CONTROL_PUBLISH_BOUND_MS / 1000,
                )
            except asyncio.CancelledError:
                await self._abandon_audio(context.turn_id)
                raise
            except Exception:
                self._count_admission(context)
                await self._terminate_failed_turn_locked(
                    context,
                    "turn.failed",
                    {
                        "outcome": "failed",
                        "stage": "publication",
                        "code": "audio_publication_unavailable",
                    },
                    abandon_audio=True,
                )
                raise
        if not isinstance(publication_id, str) or not publication_id or len(publication_id) > 128:
            self._count_admission(context)
            await self._terminate_failed_turn_locked(
                context,
                "turn.failed",
                {
                    "outcome": "failed",
                    "stage": "publication",
                    "code": "audio_publication_identity_invalid",
                },
                abandon_audio=True,
            )
            raise RuntimeError("audio publication has no bounded identity")
        context.media_publication_id = publication_id
        self._count_admission(context)
        await self._emit(context.turn_id, "turn.listening", {"state": "listening"})
        tts_profile = getattr(self.runner, "tts_profile", None)
        metadata = tts_profile.public_metadata() if tts_profile is not None else {
            "profile": "deterministic-test",
            "backend": "deterministic",
            "speaker": "test",
            "output_sample_rate_hz": 48_000,
            "native_sample_rate_hz": 48_000,
            "license": "test-only",
            "private_noncommercial_only": True,
        }
        await self._emit(
            context.turn_id,
            "turn.media-ready",
            {
                "state": "media-ready",
                "server_media_publication_id": publication_id,
                "audio": TTS_OUTPUT_AUDIO_FORMAT.as_dict(),
                "tts": metadata,
            },
        )

    async def abandon_unannounced_utterance(self, turn_id: str) -> bool:
        async with self._lock:
            context = self._active
            if (
                context is None
                or context.turn_id != turn_id
                or context.terminal
                or context.task is not None
                or context.announced
            ):
                return False
            context.terminal = True
            self._active = None
            return True

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
            self._trace(
                "turn", "endpoint", turn_id=context.turn_id,
                input_bytes=len(pcm),
                duration_ms=round(len(pcm) / 2 / INPUT_AUDIO_FORMAT.sample_rate_hz * 1000, 3),
            )
            self._schedule_resource_sample(context, "endpoint")
            self._capture_content("raw-audio", pcm, context.turn_id)
            if not context.announced:
                try:
                    await self._announce_context_cancellation_safe_locked(context)
                except BaseException:
                    context.terminal = True
                    self._rollback_unpublished_admission(context)
                    if self._active is context:
                        self._active = None
                    if context.media_publication_id is not None:
                        await self._abandon_audio(context.turn_id)
                    raise
                context.announced = True
            context.task = asyncio.create_task(
                self._run_turn(context, bytes(pcm)), name=f"realtime-{context.turn_id}"
            )
            self._turn_tasks.add(context.task)
            context.task.add_done_callback(self._turn_tasks.discard)
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
            try:
                cleanup, drain_error, _publication_id = await self._interrupt_locked(code)
            except asyncio.CancelledError:
                await self._degrade_locked(stage, code)
                raise
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
                {
                    "state": "degraded",
                    "stage": stage,
                    "code": code,
                    **failure_payload(stage, code),
                    "health": self._health_report(
                        failed_stage=stage, failure_code=code
                    ),
                },
            )
        finally:
            self._report_failure(stage, code)

    async def _degrade_locked(self, stage: str, code: str) -> None:
        if self._closed:
            return
        self._closed = True
        await self._publish_degraded_locked(stage, code)

    async def _rollback_context(
        self, context: TurnContext, *, restore_context: bool = True
    ) -> str | None:
        async with self._runner_lock:
            if context.rollback_complete:
                return context.rollback_error
            discard = getattr(
                self.runner,
                "discard_turn" if restore_context else "discard_obsolete_turn",
                None,
            )
            if discard is None and not restore_context:
                discard = getattr(self.runner, "discard_turn", None)
            try:
                if discard is not None:
                    await asyncio.to_thread(discard, self.session_id, context.turn_id)
            except Exception:
                context.rollback_error = "context_rollback_failed"
            context.rollback_complete = True
            return context.rollback_error

    def _track_cleanup(
        self, cleanup: asyncio.Task[str | None], *, admission_blocking: bool = True
    ) -> None:
        tasks = self._cleanup_tasks if admission_blocking else self._detached_cleanup_tasks
        tasks.add(cleanup)

        def complete(task: asyncio.Task[str | None]) -> None:
            tasks.discard(task)
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
        if self._detached_cleanup_tasks:
            results = await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._detached_cleanup_tasks)),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) or result is not None for result in results):
                raise RuntimeError("cancellation_cleanup_failed")
        while True:
            pending_turns = tuple(
                task
                for task in self._turn_tasks
                if task is not asyncio.current_task() and not task.done()
            )
            if not pending_turns:
                break
            await asyncio.gather(*(asyncio.shield(task) for task in pending_turns))
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
        self,
        context: TurnContext,
        *,
        wait_for_turn: bool = True,
        admission_blocking: bool = True,
    ) -> asyncio.Task[str | None]:
        if context.cancellation_cleanup is not None:
            return context.cancellation_cleanup

        async def cancel_runner() -> str | None:
            cancel_error: Exception | None = None
            try:
                await asyncio.to_thread(context.cancellation.cancel)
                cancel_turn = getattr(self.runner, "cancel_turn", None)
                if cancel_turn is not None:
                    await asyncio.to_thread(
                        cancel_turn,
                        self.session_id,
                        context.stream_epoch,
                        context.turn_id,
                        context.turn_generation,
                    )
                else:
                    await asyncio.to_thread(self.runner.cancel)
            except Exception as error:
                cancel_error = error
            detach_noncooperative_tts = False
            awaited = context.task if wait_for_turn else None
            if awaited is not None and awaited is not asyncio.current_task():
                deadline = (
                    asyncio.get_running_loop().time()
                    + CANCELLATION_CLEANUP_BOUND_MS / 1000
                )
                while (
                    not awaited.done()
                    and not context.cooperative_cleanup_complete.is_set()
                ):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return "cancellation_cleanup_timeout"
                    await asyncio.wait((awaited,), timeout=min(remaining, 0.01))
                detach_noncooperative_tts = (
                    not awaited.done()
                    and context.cooperative_cleanup_complete.is_set()
                    and context.noncooperative_worker_drains > 0
                )
                if not detach_noncooperative_tts:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if not awaited.done() and remaining > 0:
                        await asyncio.wait((awaited,), timeout=remaining)
                    if not awaited.done():
                        return "cancellation_cleanup_timeout"
                    try:
                        await awaited
                    except Exception as error:
                        cancel_error = cancel_error or error
            if not context.rollback_complete:
                context.rollback_error = await self._rollback_context(
                    context,
                    restore_context=(
                        wait_for_turn
                        and (
                            not detach_noncooperative_tts
                            or context.llm_context_committed
                        )
                    ),
                )
            if cancel_error is not None:
                return "cancellation_cleanup_failed"
            return context.rollback_error

        cleanup = asyncio.create_task(cancel_runner(), name=f"cancel-{context.turn_id}")
        context.cancellation_cleanup = cleanup
        self._track_cleanup(cleanup, admission_blocking=admission_blocking)
        return cleanup

    async def _interrupt_locked(
        self, reason: str, *, notify_client: bool = True
    ) -> tuple[asyncio.Task[str | None] | None, str | None, str | None]:
        context = self._active
        if context is None or context.terminal:
            return None, None, None
        context.terminal = True
        self.turn_counts["interrupted"] += 1
        self.turn_counts["cancellations"] += 1
        # Invalidate request-scoped provider/TTS work before a replacement can be
        # admitted. Cancellation callbacks are non-blocking; a Silero model call
        # may continue only as a silent worker drain.
        context.cancellation.cancel()
        started = time.monotonic()
        cancellation_requested = False
        try:
            try:
                drain_error, publication_id = await self._clear_audio(context.turn_id)
            except asyncio.CancelledError:
                cancellation_requested = True
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
                drain_error, publication_id = "audio_drain_cancelled", None
        finally:
            drain_ms = (time.monotonic() - started) * 1000
            cleanup = self._ensure_context_cleanup(context)
        if notify_client:
            payload: dict[str, object] = {
                "outcome": "interrupted",
                "reason": reason,
                "user_state": "interrupted",
                "retry_count": 0,
                "retry_limit": 0,
                "drain_ms": round(drain_ms, 3),
                "cancellation_latency_ms": round(drain_ms, 3),
                "drain_bound_ms": BARGE_IN_DRAIN_BOUND_MS,
            }
            if publication_id is not None:
                payload["server_media_publication_id"] = publication_id
            self._add_metrics(context, payload)
            try:
                await self._emit(context.turn_id, "turn.interrupted", payload, terminal=True)
            except asyncio.CancelledError:
                cancellation_requested = True
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
        if cancellation_requested:
            if drain_error is not None:
                await self._degrade_locked("publication", drain_error)
            elif cleanup is not None:
                self._watch_cleanup(cleanup)
            raise asyncio.CancelledError
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
        register_turn = getattr(self.runner, "register_turn", None)
        if register_turn is not None:
            try:
                register_turn(
                    self.session_id,
                    context.stream_epoch,
                    context.turn_id,
                    context.turn_generation,
                )
            except Exception:
                await self._terminate_failed_turn(
                    context,
                    "turn.failed",
                    {
                        "outcome": "failed",
                        "stage": "controller",
                        "code": "turn_registration_failed",
                    },
                )
                return
        if context.terminal or self._closed:
            return

        loop = asyncio.get_running_loop()
        event_queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        segment_queue: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue(maxsize=2)
        pcm_queue: asyncio.Queue[PcmPumpItem | None] = asyncio.Queue(
            maxsize=PCM_PUMP_MAX_BLOCKS
        )
        pcm_capacity = asyncio.Semaphore(PCM_PUMP_MAX_BLOCKS)
        reserved_segments: set[int] = set()
        noncooperative_reserved_segments: set[int] = set()
        segment_release_events: dict[int, asyncio.Event] = {}
        outstanding_pcm = 0
        pump_errors: list[BaseException] = []
        delivery_block_bytes = (
            640
            if getattr(getattr(self.runner, "tts", None), "version", None)
            == "voice-agent.tts.v1"
            else OUTPUT_DELIVERY_BLOCK_BYTES
        )
        expected_audio_chunk = 0
        expected_synthesis_segment = 0

        def fresh() -> bool:
            return (
                self._active is context
                and not context.terminal
                and not context.cancellation.cancelled
                and not self._closed
                and self.stream_epoch == context.stream_epoch
            )

        def observe(event: dict[str, object]) -> None:
            if not fresh():
                self.drop_counts["stale_event"] += 1
                return
            delivery = asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)
            while True:
                try:
                    delivery.result(timeout=PCM_PRODUCER_WAIT_SECONDS)
                    return
                except TimeoutError:
                    if context.cancellation.cancelled or context.terminal or self._closed:
                        delivery.cancel()
                        return

        async def enqueue_pcm(
            block_index: int, chunk: bytes, *, final_partial: bool = False
        ) -> None:
            nonlocal outstanding_pcm
            if not fresh():
                self.drop_counts["stale_event"] += 1
                return
            await pcm_capacity.acquire()
            if not fresh():
                pcm_capacity.release()
                self.drop_counts["stale_event"] += 1
                return
            try:
                await pcm_queue.put(PcmPumpItem(
                    context, block_index, chunk, final_partial, delivery_block_bytes
                ))
            except BaseException:
                pcm_capacity.release()
                raise
            outstanding_pcm += 1
            context.pcm_queue_high_water = max(
                context.pcm_queue_high_water, outstanding_pcm
            )

        async def reserve_segment(segment_index: int) -> None:
            if not fresh():
                raise StageFailure("tts", "selected_tts_cancelled")
            if segment_index in reserved_segments:
                raise StageFailure("tts", "selected_tts_output_out_of_bounds")
            await self._segment_capacity.acquire()
            if not fresh():
                self._segment_capacity.release()
                raise StageFailure("tts", "selected_tts_cancelled")
            if segment_index in reserved_segments:
                self._segment_capacity.release()
                raise StageFailure("tts", "selected_tts_output_out_of_bounds")
            reserved_segments.add(segment_index)
            segment_release_events[segment_index] = asyncio.Event()
            context.segment_queue_high_water = max(
                context.segment_queue_high_water, len(reserved_segments)
            )

        async def release_segment(segment_index: int) -> None:
            if segment_index not in reserved_segments:
                return
            reserved_segments.remove(segment_index)
            noncooperative_reserved_segments.discard(segment_index)
            self._segment_capacity.release()
            released = segment_release_events.get(segment_index)
            if released is not None:
                released.set()

        async def enqueue_segment(
            chunk_index: int, chunk: bytes, *, reserved: bool = False
        ) -> None:
            if not reserved:
                await reserve_segment(chunk_index)
            if not fresh():
                await release_segment(chunk_index)
                raise StageFailure("tts", "selected_tts_cancelled")
            try:
                await segment_queue.put((chunk_index, chunk))
            except BaseException:
                await release_segment(chunk_index)
                raise

        def wait_for_delivery(delivery) -> None:
            while True:
                try:
                    delivery.result(timeout=PCM_PRODUCER_WAIT_SECONDS)
                    return
                except TimeoutError:
                    if context.cancellation.cancelled or context.terminal or self._closed:
                        delivery.cancel()
                        raise StageFailure("tts", "selected_tts_cancelled")

        def wait_for_event_barrier() -> None:
            barrier = asyncio.run_coroutine_threadsafe(event_queue.join(), loop)
            wait_for_delivery(barrier)

        def observe_audio(chunk_index: int, chunk: bytes) -> None:
            nonlocal expected_audio_chunk
            if (
                chunk_index != expected_audio_chunk
                or not chunk
                or len(chunk) % TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes
            ):
                raise StageFailure("tts", "selected_tts_output_out_of_bounds")
            expected_audio_chunk += 1
            if not fresh():
                raise StageFailure("tts", "selected_tts_cancelled")
            wait_for_event_barrier()
            if not fresh():
                raise StageFailure("tts", "selected_tts_cancelled")
            delivery = asyncio.run_coroutine_threadsafe(
                enqueue_segment(chunk_index, bytes(chunk)), loop
            )
            wait_for_delivery(delivery)

        def reserve_synthesis_segment(segment_index: int) -> None:
            wait_for_delivery(asyncio.run_coroutine_threadsafe(
                reserve_segment(segment_index), loop
            ))

        def observe_synthesis_segment(
            segment_index: int, chunk: bytes | None
        ) -> None:
            nonlocal expected_synthesis_segment
            if segment_index != expected_synthesis_segment:
                wait_for_delivery(asyncio.run_coroutine_threadsafe(
                    release_segment(segment_index), loop
                ))
                raise StageFailure("tts", "selected_tts_output_out_of_bounds")
            expected_synthesis_segment += 1
            if chunk is None:
                wait_for_delivery(asyncio.run_coroutine_threadsafe(
                    release_segment(segment_index), loop
                ))
                return
            if not chunk or len(chunk) % TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes:
                wait_for_delivery(asyncio.run_coroutine_threadsafe(
                    release_segment(segment_index), loop
                ))
                raise StageFailure("tts", "selected_tts_output_out_of_bounds")
            try:
                wait_for_event_barrier()
            except BaseException:
                wait_for_delivery(asyncio.run_coroutine_threadsafe(
                    release_segment(segment_index), loop
                ))
                raise
            wait_for_delivery(asyncio.run_coroutine_threadsafe(
                enqueue_segment(segment_index, bytes(chunk), reserved=True), loop
            ))

        async def consume_events() -> None:
            while True:
                event = await event_queue.get()
                try:
                    if event is None:
                        return
                    await self._relay_internal(context, event)
                finally:
                    event_queue.task_done()

        async def consume_segments() -> None:
            assembly = bytearray()
            block_sequence = 0
            while True:
                item = await segment_queue.get()
                try:
                    if item is None:
                        if assembly:
                            await enqueue_pcm(
                                block_sequence, bytes(assembly), final_partial=True
                            )
                        await pcm_queue.put(None)
                        return
                    _segment_index, segment = item
                    if not fresh():
                        self.drop_counts["stale_event"] += 1
                        assembly.clear()
                        continue
                    assembly.extend(segment)
                    while len(assembly) >= delivery_block_bytes:
                        block = bytes(assembly[:delivery_block_bytes])
                        del assembly[:delivery_block_bytes]
                        await enqueue_pcm(block_sequence, block)
                        block_sequence += 1
                finally:
                    if item is not None:
                        await release_segment(item[0])
                    segment_queue.task_done()

        async def consume_pcm() -> None:
            nonlocal outstanding_pcm
            while True:
                item = await pcm_queue.get()
                try:
                    if item is None:
                        return
                    await self._relay_queued_pcm(item)
                except BaseException as error:
                    if not pump_errors:
                        pump_errors.append(error)
                    context.cancellation.cancel()
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
        for name, value in (
            ("stream_epoch", context.stream_epoch),
            ("turn_generation", context.turn_generation),
            ("request_id", context.request_id),
        ):
            if name in run_parameters:
                runner_arguments[name] = value
        if "trace_observer" in run_parameters:
            def observe_trace(stage: str, event: str, fields: dict[str, object]) -> None:
                if stage == "tts" and event == "noncooperative_worker_started":
                    context.noncooperative_worker_drains += 1
                    segment_index = fields.get("segment_index")
                    if isinstance(segment_index, int) and not isinstance(segment_index, bool):
                        noncooperative_reserved_segments.add(segment_index)
                elif stage == "tts" and event == "noncooperative_worker_finished":
                    context.noncooperative_worker_drains = max(
                        0, context.noncooperative_worker_drains - 1
                    )
                elif stage == "llm_provider" and event == "cooperative_cleanup_complete":
                    context.llm_context_committed = bool(fields.get("context_committed"))
                    context.cooperative_cleanup_complete.set()
                elif stage == "llm_provider" and event == "completed":
                    if context.llm_started_monotonic is not None:
                        context.provider_completion_ms = round(
                            max(
                                0.0,
                                (time.monotonic() - context.llm_started_monotonic) * 1000,
                            ),
                            3,
                        )
                elif stage == "llm_provider" and event == "summary":
                    first = fields.get("provider_time_to_first_token_ms")
                    completion = fields.get("provider_completion_ms")
                    if type(first) in {int, float} and math.isfinite(first) and first >= 0:
                        context.provider_first_token_ms = round(float(first), 3)
                    if type(completion) in {int, float} and math.isfinite(completion) and completion >= 0:
                        context.provider_completion_ms = round(float(completion), 3)
                    for name in (
                        "provider_input_unit_count",
                        "provider_output_unit_count",
                        "provider_total_unit_count",
                    ):
                        value = fields.get(name.removeprefix("provider_"))
                        if type(value) is int and value >= 0:
                            setattr(context, name, value)
                elif stage == "tts" and event == "segment_started":
                    if context.tts_started_monotonic is None:
                        context.tts_started_monotonic = time.monotonic()
                self._trace(stage, event, turn_id=context.turn_id, **fields)

            runner_arguments["trace_observer"] = observe_trace
        if streaming_audio:
            runner_arguments["audio_observer"] = observe_audio
            if (
                getattr(getattr(self.runner, "tts", None), "version", None)
                == TTS_V2_VERSION
                and "segment_started_observer" in run_parameters
                and "segment_audio_observer" in run_parameters
            ):
                runner_arguments["segment_started_observer"] = reserve_synthesis_segment
                runner_arguments["segment_audio_observer"] = observe_synthesis_segment
            runner_arguments["retain_output"] = False

        event_consumer = asyncio.create_task(consume_events(), name=f"events-{context.turn_id}")
        segment_consumer = asyncio.create_task(
            consume_segments(), name=f"segments-{context.turn_id}"
        )
        pcm_consumer = asyncio.create_task(consume_pcm(), name=f"pcm-{context.turn_id}")
        result: TraceResult | None = None
        runner_failure: Exception | None = None
        if context.terminal or self._closed:
            event_consumer.cancel()
            segment_consumer.cancel()
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
            await segment_queue.put(None)
            await event_consumer
            await segment_consumer
            await pcm_consumer
        finally:
            if context.worker is worker:
                context.worker = None
            consumers = (event_consumer, segment_consumer, pcm_consumer)
            for consumer in consumers:
                if not consumer.done():
                    consumer.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)
            retained_segments = reserved_segments & noncooperative_reserved_segments
            for segment_index in tuple(reserved_segments - retained_segments):
                await release_segment(segment_index)
            if retained_segments:
                retained_events = tuple(
                    segment_release_events[segment_index]
                    for segment_index in retained_segments
                )

                async def await_retained_releases() -> str | None:
                    await asyncio.gather(*(released.wait() for released in retained_events))
                    return None

                detached_release = asyncio.create_task(
                    await_retained_releases(),
                    name=f"segment-release-{context.turn_id}",
                )
                self._track_cleanup(detached_release, admission_blocking=False)

        if context.terminal or self._closed:
            return
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

        captured_transcript = False
        latest_response: str | None = None
        for internal_event in result.events:
            internal_payload = internal_event.get("payload")
            if not isinstance(internal_payload, dict):
                continue
            if internal_event.get("type") == "stt.final" and not captured_transcript:
                private_value = internal_payload.get("transcript")
                if isinstance(private_value, str) and private_value:
                    self._capture_content("transcript", private_value, context.turn_id)
                    captured_transcript = True
            if internal_event.get("type") in {"llm.final", "llm.visible"}:
                private_value = internal_payload.get("response")
                if isinstance(private_value, str) and private_value:
                    latest_response = private_value
        if latest_response is not None:
            self._capture_content("response", latest_response, context.turn_id)
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
            or output_bytes > TTS_V2_OUTPUT_MEDIA_MAX_BYTES
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
            submitted_before_finish = getattr(self.audio_sink, "submitted_bytes", None)
            try:
                finish_parameters = inspect.signature(finish).parameters
                finish_arguments: dict[str, object] = {}
                if "media_generation" in finish_parameters:
                    finish_arguments["media_generation"] = context.media_generation
                accepted = await finish(
                    context.turn_id,
                    lambda: (
                        context.terminal
                        or context.cancellation.cancelled
                        or self._active is not context
                        or self.stream_epoch != context.stream_epoch
                    ),
                    **finish_arguments,
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
            submitted_after_finish = getattr(self.audio_sink, "submitted_bytes", None)
            if (
                context.first_pcm_ms is None
                and submitted_before_finish is not None
                and submitted_after_finish is not None
                and submitted_after_finish > submitted_before_finish
            ):
                context.first_pcm_ms = round(
                    max(0.0, (time.monotonic() - context.endpoint_monotonic) * 1000), 3
                )
                if context.tts_started_monotonic is not None:
                    context.tts_first_audio_ms = round(
                        max(0.0, (time.monotonic() - context.tts_started_monotonic) * 1000),
                        3,
                    )
                await self._emit(
                    context.turn_id,
                    "turn.speaking",
                    {
                        "state": "speaking",
                        "server_streamed_output": True,
                        "endpoint_to_first_accepted_pcm_ms": context.first_pcm_ms,
                        "tts_time_to_first_audio_ms": context.tts_first_audio_ms,
                    },
                )

        async with self._lock:
            if context.terminal or self._closed or self._active is not context:
                return
            delivered = getattr(self.runner, "turn_delivered", None)
            if delivered is not None:
                try:
                    async with self._runner_lock:
                        await asyncio.to_thread(delivered, self.session_id, context.turn_id)
                except Exception:
                    await self._terminate_failed_turn_locked(
                        context,
                        "turn.failed",
                        {
                            "outcome": "failed",
                            "stage": "controller",
                            "code": "context_commit_failed",
                        },
                    )
                    return
            context.terminal = True
            self.turn_counts["completed"] += 1
            payload: dict[str, object] = {
                "outcome": "completed",
                "output_bytes": output_bytes,
                "server_pcm_queue_max_blocks": context.pcm_queue_high_water,
                "server_segment_queue_max_segments": context.segment_queue_high_water,
                "audio_format": TTS_OUTPUT_AUDIO_FORMAT.as_dict(),
                "output_padded_bytes": int(getattr(self.audio_sink, "padded_bytes", 0)),
                "output_frames_submitted": int(getattr(self.audio_sink, "submitted_frames", 0)),
            }
            self._add_metrics(context, payload)
            await self._emit(context.turn_id, "turn.completed", payload, terminal=True)

    def _add_metrics(self, context: TurnContext, payload: dict[str, object]) -> None:
        self._schedule_resource_sample(context, "terminal")
        if context.first_visible_ms is not None:
            payload["endpoint_to_first_visible_ms"] = context.first_visible_ms
        if context.first_pcm_ms is not None:
            payload["endpoint_to_first_accepted_pcm_ms"] = context.first_pcm_ms
        if context.stt_final_ms is not None:
            payload["endpoint_to_stt_final_ms"] = context.stt_final_ms
        if context.provider_first_token_ms is not None:
            payload["provider_time_to_first_token_ms"] = context.provider_first_token_ms
        if context.provider_completion_ms is not None:
            payload["provider_completion_ms"] = context.provider_completion_ms
        if context.tts_first_audio_ms is not None:
            payload["tts_time_to_first_audio_ms"] = context.tts_first_audio_ms
        payload["total_turn_ms"] = round(
            max(0.0, (time.monotonic() - context.endpoint_monotonic) * 1000), 3
        )
        stages = {
            "stt": context.stt_final_ms,
            "selected_llm": context.provider_completion_ms,
            "tts": context.tts_first_audio_ms,
        }
        measured = {name: value for name, value in stages.items() if value is not None}
        if measured:
            payload["slowest_stage"] = max(measured, key=measured.get)
        llm = getattr(self.runner, "llm", None)
        payload["provider_mode"] = getattr(llm, "provider_mode", "local")
        payload["provider_identity"] = getattr(llm, "provider_identity", "configured-local-llm")
        payload["external_transfer"] = False
        payload["provider_request_id_available"] = False
        payload["provider_input_unit_count"] = context.provider_input_unit_count
        payload["provider_output_unit_count"] = context.provider_output_unit_count
        payload["provider_total_unit_count"] = context.provider_total_unit_count
        payload["turn_admission_count"] = self.turn_counts["admitted"]
        payload["turn_completion_count"] = self.turn_counts["completed"]
        payload["turn_failure_count"] = self.turn_counts["failed"]
        payload["turn_interruption_count"] = self.turn_counts["interrupted"]
        payload["cancellation_count"] = self.turn_counts["cancellations"]
        payload["stale_drop_count"] = self.drop_counts["stale_event"]
        payload["client_control_drop_count"] = self.drop_counts["client_control"]
        latest_resource = context.resource_terminal or context.resource_endpoint
        for name, value in latest_resource.items():
            if name != "resource_phase":
                payload[name] = value

    async def _terminate_failed_turn(
        self,
        context: TurnContext,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        async with self._lock:
            await self._terminate_failed_turn_locked(context, event_type, payload)

    async def _terminate_failed_turn_locked(
        self,
        context: TurnContext,
        event_type: str,
        payload: dict[str, object],
        *,
        abandon_audio: bool = False,
    ) -> None:
        if context.terminal or self._closed or self._active is not context:
            return
        if abandon_audio or payload.get("stage") != "publication":
            drain_error, publication_id = await self._abandon_audio(context.turn_id)
        else:
            drain_error, publication_id = await self._clear_audio(context.turn_id)
        context.rollback_error = await self._rollback_context(context)
        context.terminal = True
        self.turn_counts["failed"] += 1
        public_payload = dict(payload)
        stage = str(public_payload.get("stage", "controller"))
        code = str(public_payload.get("code", "unknown_failure"))
        public_payload.update(failure_payload(stage, code))
        if publication_id is not None:
            public_payload["server_media_publication_id"] = publication_id
        self._add_metrics(context, public_payload)
        cancellation_after_publish = False
        try:
            await self._emit(context.turn_id, event_type, public_payload, terminal=True)
        except asyncio.CancelledError:
            if context.public_event_published and not context.transport_failed:
                cancellation_after_publish = True
            else:
                raise
        terminal_code = payload.get("code")
        terminal_controller_failure = (
            payload.get("stage") == "llm_provider"
            and isinstance(terminal_code, str)
            and terminal_code in TERMINAL_CONTROLLER_FAILURE_CODES
        )
        admission = getattr(self.runner, "ready_for_admission", None)
        if terminal_controller_failure:
            await self._degrade_locked("llm_provider", terminal_code)
        elif (
            payload.get("stage") == "controller"
            and terminal_code == "context_commit_failed"
        ):
            await self._degrade_locked("controller", terminal_code)
        elif stage == "publication" and public_payload.get("admit_turn") is False:
            await self._degrade_locked(stage, code)
        elif admission is not None and not admission():
            readiness_stage, readiness_code = self._readiness_failure()
            await self._degrade_locked(readiness_stage, readiness_code)
        elif drain_error is not None:
            await self._degrade_locked("publication", drain_error)
        elif context.rollback_error is not None:
            await self._degrade_locked("controller", context.rollback_error)
        if cancellation_after_publish:
            raise asyncio.CancelledError

    async def _relay_queued_pcm(self, item: PcmPumpItem) -> None:
        context = item.request
        if (
            self._active is not context
            or context.terminal
            or context.cancellation.cancelled
            or self._closed
            or self.stream_epoch != context.stream_epoch
        ):
            self.drop_counts["stale_event"] += 1
            return
        if (
            len(item.pcm) > item.block_size
            or (not item.final_partial and len(item.pcm) != item.block_size)
        ):
            raise StageFailure("publication", "audio_stream_invalid")
        await self._relay_audio_chunk(context, item.block_index, item.pcm)

    async def _relay_audio_chunk(
        self, context: TurnContext, chunk_index: int, chunk: bytes
    ) -> None:
        if (
            context.terminal
            or self._closed
            or self._active is not context
            or context.cancellation.cancelled
            or self.stream_epoch != context.stream_epoch
        ):
            self.drop_counts["stale_event"] += 1
            return
        if (
            chunk_index != context.audio_chunk_sequence
            or not chunk
            or len(chunk) % 2
            or context.audio_bytes_streamed + len(chunk) > TTS_V2_OUTPUT_MEDIA_MAX_BYTES
        ):
            raise StageFailure("publication", "audio_stream_invalid")
        submitted_before = getattr(self.audio_sink, "submitted_bytes", None)
        try:
            write_parameters = inspect.signature(self.audio_sink.write).parameters
            write_arguments: dict[str, object] = {}
            if "media_generation" in write_parameters:
                write_arguments["media_generation"] = context.media_generation
            accepted = await self.audio_sink.write(
                context.turn_id,
                chunk,
                lambda: (
                    context.terminal
                    or context.cancellation.cancelled
                    or self._active is not context
                    or self.stream_epoch != context.stream_epoch
                ),
                **write_arguments,
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
                or self.stream_epoch != context.stream_epoch
            ):
                self.drop_counts["stale_event"] += 1
                return
            raise StageFailure("publication", "audio_stream_failed")
        if (
            context.terminal
            or context.cancellation.cancelled
            or self._closed
            or self._active is not context
            or self.stream_epoch != context.stream_epoch
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
            if context.tts_started_monotonic is not None:
                context.tts_first_audio_ms = round(
                    max(0.0, (time.monotonic() - context.tts_started_monotonic) * 1000),
                    3,
                )
            await self._emit(
                context.turn_id,
                "turn.speaking",
                {
                    "state": "speaking",
                    "server_streamed_output": True,
                    "endpoint_to_first_accepted_pcm_ms": context.first_pcm_ms,
                    "tts_time_to_first_audio_ms": context.tts_first_audio_ms,
                },
            )

    async def _relay_internal(self, context: TurnContext, event: dict[str, object]) -> None:
        if (
            context.terminal
            or self._active is not context
            or self.stream_epoch != context.stream_epoch
        ):
            self.drop_counts["stale_event"] += 1
            return
        if event.get("session_id") != self.session_id or event.get("turn_id") != context.turn_id:
            self.drop_counts["stale_event"] += 1
            return
        schema_version = event.get("schema_version")
        if schema_version == "voice-agent.event-envelope.v2":
            sequence = event.get("sequence")
            if (
                event.get("stream_epoch") != context.stream_epoch
                or event.get("turn_generation") != context.turn_generation
                or event.get("request_id") != context.request_id
                or sequence != context.internal_event_sequence + 1
            ):
                self.drop_counts["stale_event"] += 1
                return
            context.internal_event_sequence = int(sequence)
        elif schema_version != "voice-agent.event-envelope.v1":
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
        observed = time.monotonic()
        if event_type == "stt.final" and context.stt_final_ms is None:
            context.stt_final_ms = round(
                max(0.0, (observed - context.endpoint_monotonic) * 1000), 3
            )
            public_payload["endpoint_to_stt_final_ms"] = context.stt_final_ms
        if event_type == "turn.thinking" and context.llm_started_monotonic is None:
            context.llm_started_monotonic = observed
        if event_type == "llm.visible" and context.first_visible_ms is None:
            context.first_visible_ms = round(
                max(0.0, (observed - context.endpoint_monotonic) * 1000), 3
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
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.ready",
                        {
                            "state": "ready",
                            "user_state": "available",
                            "health": self._health_report(),
                        },
                    )
                    return True
                if sequence <= self._client_sequence or stream_epoch != self.stream_epoch:
                    self.drop_counts["client_control"] += 1
                    return False
                self._client_sequence = sequence
                if self._closed:
                    return False
            reset_error: str | None = None
            if self.reconnect_reset_handler is not None:
                try:
                    await self.reconnect_reset_handler()
                except Exception:
                    reset_error = "reconnect_input_reset_failed"
            async with self._lock:
                if self._closed:
                    return False
                context = self._active
                interrupted_turn_id = self.active_turn_id
                if context is not None and not context.terminal:
                    cleanup, media_reset_error, _publication_id = await self._interrupt_locked(
                        "client_reconnected", notify_client=False
                    )
                else:
                    cleanup = None
                    media_reset_error, _publication_id = await self._clear_audio(
                        context.turn_id if context is not None else SESSION_TURN_ID
                    )
                reset_error = reset_error or media_reset_error
            if cleanup is not None:
                try:
                    reset_error = reset_error or await asyncio.wait_for(
                        asyncio.shield(cleanup), CANCELLATION_CLEANUP_BOUND_MS / 1000
                    )
                except TimeoutError:
                    reset_error = "cancellation_cleanup_timeout"
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
                    await self._emit(
                        SESSION_TURN_ID,
                        "session.ready",
                        {
                            "state": "ready",
                            "user_state": "available",
                            "health": self._health_report(),
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
            cleanup_error = await self._await_cleanup_barrier(CANCELLATION_CLEANUP_BOUND_MS)
            if cleanup_error is not None:
                raise RuntimeError(cleanup_error)
            pending_turns = tuple(task for task in self._turn_tasks if not task.done())
            if pending_turns:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(asyncio.shield(task) for task in pending_turns)),
                        2.0,
                    )
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
        if turn_id == SESSION_TURN_ID:
            turn_generation = media_generation = 0
            request_id = SESSION_TURN_ID
        else:
            context = self._active
            if context is None or context.turn_id != turn_id:
                raise RuntimeError("public event has no current correlation context")
            turn_generation = context.turn_generation
            request_id = context.request_id
            media_generation = context.media_generation
        return {
            "schema_version": CONTROL_EVENT_VERSION,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "stream_epoch": self.stream_epoch,
            "turn_generation": turn_generation,
            "request_id": request_id,
            "media_generation": media_generation,
            "sequence": self._event_sequence,
            "type": event_type,
            "terminal": terminal,
            "payload": payload,
        }

    @staticmethod
    def _control_observation_fields(
        event: dict[str, object],
        event_type: str,
        payload: dict[str, object],
        terminal: bool,
    ) -> dict[str, object]:
        observation_fields: dict[str, object] = {
            "event_type": event_type,
            "sequence": int(event["sequence"]),
            "terminal": terminal,
            "stream_epoch": int(event["stream_epoch"]),
            "request_id": str(event["request_id"]),
            "media_generation": int(event["media_generation"]),
        }
        for name in CONTROL_OBSERVATION_PAYLOAD_FIELDS:
            value = payload.get(name)
            if value is None or isinstance(value, (bool, int, float, str)):
                observation_fields[name] = value
        if isinstance(payload.get("stage"), str):
            observation_fields["failure_stage"] = payload["stage"]
        if isinstance(payload.get("code"), str):
            observation_fields["failure_code"] = payload["code"]
        return observation_fields

    def _record_control_publish_failure(
        self,
        event: dict[str, object],
        turn_id: str,
        event_type: str,
        error: BaseException,
    ) -> None:
        failure = {
            "outcome": "failed",
            "stage": "transport",
            "code": "control_publish_failed",
            **failure_payload("transport", "control_publish_failed"),
        }
        failed_event_type = event_type
        failed_terminal = False
        context = self._active
        if (
            turn_id != SESSION_TURN_ID
            and context is not None
            and context.turn_id == turn_id
        ):
            if not context.transport_failed:
                if event_type == "turn.completed" and self.turn_counts["completed"] > 0:
                    self.turn_counts["completed"] -= 1
                if event_type == "turn.interrupted" and self.turn_counts["interrupted"] > 0:
                    self.turn_counts["interrupted"] -= 1
                if event_type != "turn.failed":
                    self.turn_counts["failed"] += 1
                context.transport_failed = True
            context.cancellation.cancel()
            self._ensure_context_cleanup(context, wait_for_turn=False)
            context.terminal = True
            self._add_metrics(context, failure)
            failed_event_type = "turn.failed"
            failed_terminal = True
        observation_fields = self._control_observation_fields(
            event, failed_event_type, failure, failed_terminal
        )
        observation_fields["failed_event_type"] = event_type
        observation_fields["failure_class"] = type(error).__name__
        self._trace(
            "control", "publish_failed", turn_id=turn_id, **observation_fields
        )
        self._closed = True
        self._report_failure("transport", "control_publish_failed")

    async def _emit(
        self,
        turn_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        terminal: bool = False,
    ) -> None:
        event = self._reserve_public_event(turn_id, event_type, payload, terminal=terminal)
        send_task = asyncio.create_task(
            self.event_sink.send(event),
            name=f"control-send-{event_type}-{turn_id}",
        )
        cancellation_after_success = False

        async def cancel_unfinished_send() -> None:
            if send_task.done():
                return
            send_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(send_task), CONTROL_PUBLISH_BOUND_MS / 1000
                )
            except BaseException:
                pass

        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(send_task), CONTROL_PUBLISH_BOUND_MS / 1000
                )
            except asyncio.CancelledError:
                if send_task.done() and not send_task.cancelled():
                    send_error = send_task.exception()
                    if send_error is not None:
                        raise send_error
                    cancellation_after_success = True
                else:
                    await cancel_unfinished_send()
                    raise
            except TimeoutError:
                await cancel_unfinished_send()
                raise
            context = self._active
            if (
                turn_id != SESSION_TURN_ID
                and context is not None
                and context.turn_id == turn_id
            ):
                context.public_event_published = True
            observation_fields = self._control_observation_fields(
                event, event_type, payload, terminal
            )
            self._trace(
                "control", "published", turn_id=turn_id, **observation_fields
            )
            if cancellation_after_success:
                raise asyncio.CancelledError
        except asyncio.CancelledError as error:
            if not cancellation_after_success:
                context = self._active
                correlated_turn = (
                    turn_id != SESSION_TURN_ID
                    and context is not None
                    and context.turn_id == turn_id
                )
                cancelled_terminal = (
                    terminal
                    and correlated_turn
                    and (context.public_event_published or context.admission_counted)
                )
                cancelled_post_announcement = (
                    not terminal
                    and event_type != "turn.media-ready"
                    and correlated_turn
                    and context.public_event_published
                )
                if cancelled_terminal or cancelled_post_announcement:
                    self._record_control_publish_failure(
                        event, turn_id, event_type, error
                    )
            raise
        except Exception as error:
            self._record_control_publish_failure(event, turn_id, event_type, error)
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
        self.frame_bytes = (
            INPUT_AUDIO_FORMAT.sample_rate_hz
            * INPUT_AUDIO_FORMAT.sample_width_bytes
            * frame_ms
            // 1000
        )
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

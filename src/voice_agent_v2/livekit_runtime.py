"""Low-level official LiveKit RTC/API adapters for the Slice 6 session controller."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from livekit import api, rtc

from .v2_audio import (
    INPUT_AUDIO_FORMAT,
    OUTPUT_DELIVERY_BLOCK_BYTES,
    OUTPUT_FRAME_BYTES,
    TTS_OUTPUT_AUDIO_FORMAT,
)
from .diagnostics import (
    DiagnosticContentCapture,
    PrivacySafeTrace,
    TraceIdentity,
)
from .local_lfm import MODEL_ALIAS, LocalLFMProvider
from .local_stt import WhisperSTT
from .real_turn import RealTurnController
from .silero_tts import SileroKseniyaTTS, SileroVoiceProfile
from .local_vad import SileroOnnxModel, SileroSpeechEndpoint
from .observability import ComponentHealth, HealthReport, ResourceSampler
from .realtime import (
    CLIENT_CONTROL_TOPIC,
    CONTROL_TOPIC,
    MAX_CONTROL_BYTES,
    AudioSink,
    EventSink,
    RealtimeSession,
)
from .slice6_config import Slice6Settings, supervised_process_alive
from .tracer import CancellationToken, TraceResult

AUDIO_FRAME_MS = 20
AUDIO_FRAME_BYTES = OUTPUT_FRAME_BYTES
AUDIO_QUEUE_MS = 100
BROWSER_CONTROL_QUEUE_SIZE = 32
MAX_SESSION_OBSERVATIONS = 128
TRACE_ROOT = Path.home() / ".cache/voice-agent-v2/slice-6/diagnostics"
OPERATIONAL_PROBE_TIMEOUT_SECONDS = 0.1
OPERATIONAL_PROBE_BODY_LIMIT_BYTES = 4_096
LOCAL_LFM_HOST = "127.0.0.1"
LOCAL_LFM_PORT = 18_080


def livekit_endpoint_ready(url: str, timeout: float = OPERATIONAL_PROBE_TIMEOUT_SECONDS) -> bool:
    endpoint = urlsplit(url)
    if endpoint.scheme not in {"ws", "wss"} or endpoint.hostname is None:
        return False
    try:
        port = endpoint.port or (443 if endpoint.scheme == "wss" else 80)
    except ValueError:
        return False
    connection_type = (
        http.client.HTTPSConnection
        if endpoint.scheme == "wss"
        else http.client.HTTPConnection
    )
    connection = connection_type(endpoint.hostname, port, timeout=timeout)
    try:
        connection.request("GET", "/", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(OPERATIONAL_PROBE_BODY_LIMIT_BYTES + 1)
        return response.status == 200 and body == b"OK"
    except (OSError, TimeoutError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def local_lfm_endpoint_health(
    host: str = LOCAL_LFM_HOST,
    port: int = LOCAL_LFM_PORT,
    timeout: float = OPERATIONAL_PROBE_TIMEOUT_SECONDS,
) -> str:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/health", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(OPERATIONAL_PROBE_BODY_LIMIT_BYTES + 1)
        if response.status != 200 or len(body) > OPERATIONAL_PROBE_BODY_LIMIT_BYTES:
            return "unready"
        document = json.loads(body)
        return (
            "ready"
            if isinstance(document, dict) and document.get("status") == "ok"
            else "unready"
        )
    except (OSError, TimeoutError, http.client.HTTPException):
        return "unavailable"
    except (UnicodeError, json.JSONDecodeError):
        return "unready"
    finally:
        connection.close()


class SessionCapacityError(RuntimeError):
    pass


class LiveTurnRunner:
    """Reuse the cumulative real controller and warm every resident adapter."""

    def __init__(self, settings: Slice6Settings) -> None:
        self.stt = WhisperSTT()
        del settings
        self.llm = LocalLFMProvider()
        self.tts_profile = SileroVoiceProfile()
        self.tts = SileroKseniyaTTS()
        self.controller = RealTurnController(self.stt, self.llm, self.tts)
        self.complete_segment_capacity = asyncio.BoundedSemaphore(2)
        self._snapshots: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
        self._turn_correlations: dict[tuple[str, str], tuple[int, int]] = {}
        self._turn_correlations_lock = threading.Lock()
        self._startup_cancellation = CancellationToken()
        self._start_lock = threading.Lock()
        self._started = False
        self.warmup_metadata: dict[str, object] | None = None

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            lfm_ready = self.llm.readiness(self._startup_cancellation)
            lfm_warmup = self.llm.warmup(self._startup_cancellation)
            self.stt.start(self._startup_cancellation)
            stt_warmup = self.stt.warmup(self._startup_cancellation)
            self.tts.start(self._startup_cancellation)
            tts_warmup = self.tts.warmup(self._startup_cancellation)
            self.warmup_metadata = {
                "lfm_ready": lfm_ready,
                "lfm": lfm_warmup,
                "stt": stt_warmup,
                "tts": tts_warmup,
            }
            self._started = True

    def public_llm_profile(self) -> dict[str, str]:
        readiness = (
            self.warmup_metadata.get("lfm_ready")
            if self._started and isinstance(self.warmup_metadata, dict)
            else None
        )
        if (
            not isinstance(readiness, dict)
            or readiness.get("ready") is not True
            or readiness.get("provider_mode") != self.llm.provider_mode
            or readiness.get("provider_identity") != self.llm.provider_identity
            or readiness.get("selected_alias") != MODEL_ALIAS
        ):
            raise RuntimeError("verified local LLM identity is unavailable")
        return {
            "provider_mode": self.llm.provider_mode,
            "model_identity": self.llm.provider_identity,
        }

    def cancel_startup(self) -> None:
        self._startup_cancellation.cancel()

    def run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        input_pcm: bytes,
        cancellation: CancellationToken,
        event_observer,
        trace_observer=None,
        audio_observer=None,
        segment_started_observer: Callable[[int], None] | None = None,
        segment_audio_observer: Callable[[int, bytes | None], None] | None = None,
        retain_output: bool = True,
        stream_epoch: int = 1,
        turn_generation: int = 1,
        request_id: str | None = None,
    ) -> TraceResult:
        self._snapshots[(session_id, turn_id)] = self.llm.snapshot_session(session_id)
        self.register_turn(session_id, stream_epoch, turn_id, turn_generation)
        effective_request_id = request_id or f"request-{turn_id.removeprefix('turn-')}"
        try:
            return self.controller.run_turn(
                session_id=session_id,
                turn_id=turn_id,
                input_pcm=input_pcm,
                cancellation=cancellation,
                event_observer=event_observer,
                trace_observer=trace_observer,
                audio_observer=audio_observer,
                segment_started_observer=segment_started_observer,
                segment_audio_observer=segment_audio_observer,
                retain_output=retain_output,
                stream_epoch=stream_epoch,
                turn_generation=turn_generation,
                request_id=effective_request_id,
            )
        finally:
            take_turn_observations = getattr(self.tts, "take_turn_observations", None)
            turn_observations = (
                take_turn_observations(
                    session_id,
                    stream_epoch,
                    turn_id,
                    turn_generation,
                    effective_request_id,
                )
                if take_turn_observations is not None
                else ()
            )
            if trace_observer is not None:
                for observation in turn_observations:
                    safe_observation = {
                        ("output_bytes" if key == "audio_bytes" else key): value
                        for key, value in observation.items()
                    }
                    trace_observer("tts", "segment_observation", safe_observation)
                counters = getattr(getattr(self.tts, "pool", None), "counters", None)
                if isinstance(counters, dict):
                    trace_observer(
                        "tts",
                        "pool_counters",
                        {
                            "ready_workers": self.tts.pool.ready_count,
                            "request_count": int(counters.get("requests", 0)),
                            "failure_count": int(counters.get("failures", 0)),
                            "capacity_timeout_count": int(counters.get("capacity_timeouts", 0)),
                            "stale_after_worker_count": int(counters.get("stale_after_worker", 0)),
                            "stale_before_dispatch_count": int(counters.get("stale_before_dispatch", 0)),
                        },
                    )
            if trace_observer is not None:
                stt_observation = next(
                    (
                        item for item in reversed(self.stt.observations)
                        if item.get("session_id") == session_id
                        and item.get("turn_id") == turn_id
                    ),
                    None,
                )
                if isinstance(stt_observation, dict):
                    trace_observer(
                        "stt",
                        "summary",
                        {
                            "input_bytes": int(stt_observation.get("input_bytes", 0)),
                            "duration_ms": round(float(stt_observation.get("audio_duration_ms", 0.0)), 3),
                            "latency_ms": round(float(stt_observation.get("latency_ms", 0.0)), 3),
                            "temporary_input_retained": bool(
                                stt_observation.get("temporary_audio_retained", True)
                            ),
                            "request_count": 1,
                            "failure_count": 0,
                            "model_identity": self.stt.identity,
                        },
                    )
                llm_observation = next(
                    (
                        item for item in reversed(self.llm.observations)
                        if item.get("session_id") == session_id
                        and item.get("turn_id") == turn_id
                    ),
                    None,
                )
                if isinstance(llm_observation, dict):
                    usage = llm_observation.get("usage")
                    usage = usage if isinstance(usage, dict) else {}
                    trace_observer(
                        "llm_provider",
                        "summary",
                        {
                            "provider_mode": self.llm.provider_mode,
                            "provider_identity": self.llm.provider_identity,
                            "external_transfer": False,
                            "provider_request_id_available": False,
                            "success": bool(llm_observation.get("success")),
                            "request_count": 1,
                            "failure_count": 0 if llm_observation.get("success") else 1,
                            "failure_code": llm_observation.get("error_class"),
                            "provider_time_to_first_token_ms": llm_observation.get(
                                "provider_first_token_ms"
                            ),
                            "provider_completion_ms": llm_observation.get("completion_ms"),
                            "input_unit_count": usage.get("prompt_tokens"),
                            "output_unit_count": usage.get("completion_tokens"),
                            "total_unit_count": usage.get("total_tokens"),
                        },
                    )
            for adapter in (self.stt, self.llm, self.tts):
                trim_observations = getattr(adapter, "trim_observations", None)
                if trim_observations is not None:
                    trim_observations(MAX_SESSION_OBSERVATIONS)
                    continue
                observations = adapter.observations
                if len(observations) > MAX_SESSION_OBSERVATIONS:
                    del observations[:-MAX_SESSION_OBSERVATIONS]

    def _correlation_lock(self) -> threading.Lock:
        lock = getattr(self, "_turn_correlations_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._turn_correlations_lock = lock
        return lock

    def register_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        key = (session_id, turn_id)
        correlation = (stream_epoch, turn_generation)
        with self._correlation_lock():
            correlations = getattr(self, "_turn_correlations", None)
            if correlations is None:
                correlations = {}
                self._turn_correlations = correlations
            existing = correlations.get(key)
            if existing is not None and existing != correlation:
                raise RuntimeError("turn correlation changed during its lifecycle")
            correlations[key] = correlation

    def _release_tts_turn(self, session_id: str, turn_id: str) -> None:
        with self._correlation_lock():
            correlations = getattr(self, "_turn_correlations", {})
            correlation = correlations.pop((session_id, turn_id), None)
        release = getattr(self.tts, "release_turn", None)
        if correlation is not None and release is not None:
            release(session_id, correlation[0], turn_id, correlation[1])

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        snapshot = self._snapshots.pop((session_id, turn_id), None)
        if snapshot is not None:
            self.llm.restore_session(session_id, snapshot)
        self._release_tts_turn(session_id, turn_id)

    def discard_obsolete_turn(self, session_id: str, turn_id: str) -> None:
        """Drop only the snapshot for a cancelled pre-commit TTS handoff."""
        self._snapshots.pop((session_id, turn_id), None)
        self._release_tts_turn(session_id, turn_id)

    def turn_delivered(self, session_id: str, turn_id: str) -> None:
        self._snapshots.pop((session_id, turn_id), None)
        self._release_tts_turn(session_id, turn_id)

    def readiness_components(self) -> tuple[ComponentHealth, ...]:
        warmup_metadata = getattr(self, "warmup_metadata", None)
        warmup = warmup_metadata if isinstance(warmup_metadata, dict) else {}
        stt_alive = getattr(self.stt, "process_id", None) is not None
        stt_warmed = isinstance(warmup.get("stt"), dict) and warmup["stt"].get("discarded") is True
        lfm_ready = warmup.get("lfm_ready")
        runtime_health = getattr(self.llm, "runtime_health", None)
        runtime_health = runtime_health if isinstance(runtime_health, dict) else {
            "live": getattr(self.llm, "runtime_live", True),
            "ready": True,
            "compatible": True,
            "reason_code": None,
        }
        lfm_alive = (
            isinstance(lfm_ready, dict)
            and lfm_ready.get("ready") is True
            and runtime_health.get("live") is True
        )
        lfm_runtime_ready = runtime_health.get("ready") is True
        if isinstance(self.llm, LocalLFMProvider):
            lfm_compatible = (
                isinstance(lfm_ready, dict)
                and runtime_health.get("compatible") is True
                and lfm_ready.get("provider_mode") == self.llm.provider_mode
                and lfm_ready.get("provider_identity") == self.llm.provider_identity
                and lfm_ready.get("selected_alias") == MODEL_ALIAS
                and lfm_ready.get("external_transfer") is False
                and lfm_ready.get("automatic_fallback") is False
            )
        else:
            lfm_compatible = (
                getattr(self.llm, "version", None) == "voice-agent.llm-provider.v1"
                and getattr(self.llm, "provider_mode", None) == "local"
                and isinstance(warmup.get("lfm"), dict)
                and warmup["lfm"].get("discarded") is True
            )
        if isinstance(self.tts, SileroKseniyaTTS):
            tts_health = self.tts.health_snapshot()
            tts_alive = tts_health.live_worker_count > 0
            tts_ready = tts_health.ready_for_admission
            tts_compatible = (
                self.tts.version == "voice-agent.tts.v2"
                and isinstance(self.tts.identity, str)
                and bool(self.tts.identity)
                and self.tts.speaker == self.tts_profile.speaker
            )
        else:
            process_ids = getattr(self.tts, "process_ids", None)
            if process_ids is None:
                process_id = getattr(self.tts, "process_id", None)
                process_ids = () if process_id is None else (process_id,)
            tts_alive = len(process_ids) > 0
            tts_ready_check = getattr(self.tts, "ready_for_admission", None)
            tts_ready = tts_alive and (
                bool(tts_ready_check()) if tts_ready_check is not None else True
            )
            tts_compatible = (
                getattr(self.tts, "version", None) in {
                    "voice-agent.tts.v1", "voice-agent.tts.v2"
                }
                and isinstance(warmup.get("tts"), dict)
                and warmup["tts"].get("discarded") is True
            )
        return (
            ComponentHealth(
                "stt",
                "alive" if stt_alive else "dead",
                "ready" if stt_alive and stt_warmed else "unready",
                stt_warmed,
                getattr(self.stt, "identity", type(self.stt).__name__),
                self.stt.version,
                None if stt_alive and stt_warmed
                else "stt_not_warmed" if stt_alive else "selected_stt_unavailable",
            ),
            ComponentHealth(
                "selected_llm",
                "alive" if lfm_alive else "dead",
                "ready" if lfm_alive and lfm_runtime_ready and lfm_compatible else "unready",
                bool(lfm_compatible),
                self.llm.provider_identity,
                self.llm.version,
                None if lfm_alive and lfm_runtime_ready and lfm_compatible
                else str(
                    runtime_health.get("reason_code")
                    or (
                        "local_lfm_unavailable" if not lfm_alive
                        else "local_lfm_incompatible" if not lfm_compatible
                        else "local_lfm_unready"
                    )
                ),
            ),
            ComponentHealth(
                "tts",
                "alive" if tts_alive else "dead",
                "ready" if tts_ready and tts_compatible else "unready",
                tts_compatible,
                getattr(self.tts, "identity", type(self.tts).__name__),
                self.tts.version,
                None if tts_ready and tts_compatible else "silero_pool_not_ready",
            ),
        )

    def ready_for_admission(self) -> bool:
        if not all(hasattr(self, name) for name in ("stt", "llm", "tts")):
            ready = getattr(getattr(self, "tts", None), "ready_for_admission", None)
            return bool(getattr(self, "_started", False)) and (
                ready is None or bool(ready())
            )
        return self._started and all(
            component.liveness == "alive"
            and component.readiness == "ready"
            and component.compatible
            for component in self.readiness_components()
        )

    def admission_failure(self) -> tuple[str, str]:
        if not self._started:
            return "controller", "inference_stack_not_started"
        for component in self.readiness_components():
            if (
                component.liveness != "alive"
                or component.readiness != "ready"
                or not component.compatible
            ):
                return {
                    "stt": ("stt", component.reason_code or "selected_stt_unavailable"),
                    "selected_llm": (
                        "llm_provider",
                        component.reason_code or "local_lfm_unavailable",
                    ),
                    "tts": ("tts", component.reason_code or "silero_pool_not_ready"),
                }[component.component]
        return "controller", "inference_stack_not_ready"

    def cancel_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        """Invalidate one obsolete turn without touching a newer replacement."""
        errors: list[Exception] = []
        self.register_turn(session_id, stream_epoch, turn_id, turn_generation)
        invalidate_tts = getattr(self.tts, "invalidate_turn", None)
        if invalidate_tts is not None:
            try:
                invalidate_tts(session_id, stream_epoch, turn_id, turn_generation)
            except Exception as error:
                errors.append(error)
        # The old CancellationToken synchronously closes its own LocalLFM
        # generation before replacement admission. Calling the provider's global
        # cancel surface here could race and invalidate that replacement.
        if errors:
            raise ExceptionGroup("one or more inference requests failed to cancel", errors)

    def cancel(self) -> None:
        """Backend shutdown/startup cancellation, never ordinary turn replacement."""
        self.llm.cancel_request()

    def reset_session(self, session_id: str) -> None:
        self.llm.reset_session(session_id)
        with self._correlation_lock():
            correlation_keys = tuple(getattr(self, "_turn_correlations", {}))
        for key in correlation_keys:
            if key[0] == session_id:
                self._release_tts_turn(*key)
        for key in tuple(self._snapshots):
            if key[0] == session_id:
                self._snapshots.pop(key, None)

    def close(self, session_id: str) -> None:
        """Release session context without stopping backend-owned resident models."""
        errors: list[Exception] = []
        for cleanup in (self.cancel, lambda: self.llm.reset_session(session_id)):
            try:
                cleanup()
            except Exception as error:
                errors.append(error)
        for key in tuple(self._snapshots):
            if key[0] == session_id:
                self._snapshots.pop(key, None)
        with self._correlation_lock():
            correlation_keys = tuple(getattr(self, "_turn_correlations", {}))
        for key in correlation_keys:
            if key[0] == session_id:
                self._release_tts_turn(*key)
        if errors:
            raise ExceptionGroup("one or more inference session resources failed to close", errors)

    def shutdown(self) -> None:
        """Stop resident child processes only with backend shutdown."""
        errors: list[Exception] = []
        llm_close = getattr(self.llm, "close", lambda: None)
        for cleanup in (self.cancel, llm_close, self.stt.close, self.tts.close):
            try:
                cleanup()
            except Exception as error:
                errors.append(error)
        self._snapshots.clear()
        with self._correlation_lock():
            getattr(self, "_turn_correlations", {}).clear()
        self._started = False
        if errors:
            raise ExceptionGroup("one or more resident inference resources failed to close", errors)


class LiveKitEventSink(EventSink):
    def __init__(self, room: rtc.Room, browser_identity: str) -> None:
        self.room = room
        self.browser_identity = browser_identity

    async def send(self, event: dict[str, object]) -> None:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_CONTROL_BYTES:
            raise RuntimeError("realtime control event exceeds the transport bound")
        await self.room.local_participant.publish_data(
            payload,
            reliable=True,
            destination_identities=[self.browser_identity],
            topic=CONTROL_TOPIC,
        )


class LiveKitAudioSink(AudioSink):
    """One persistent session publication with request-correlated 20 ms writes."""

    def __init__(
        self,
        room: rtc.Room,
        source: rtc.AudioSource,
        source_changed: Callable[[rtc.AudioSource], None],
    ) -> None:
        self.room = room
        self.source = source
        self.source_changed = source_changed
        self.publication = None
        self._active_turn: str | None = None
        self._active_media_generation: int | None = None
        self._pending_pcm = bytearray()
        self._submitted_bytes = 0
        self._padded_bytes = 0
        self._submitted_frames = 0
        self._lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self._transport_disconnected = False
        self._close_task: asyncio.Task[None] | None = None

    @staticmethod
    def _publication_id(publication) -> str:
        publication_id = getattr(publication, "sid", None)
        if not isinstance(publication_id, str) or not publication_id:
            raise RuntimeError("LiveKit audio publication has no stable identity")
        return publication_id

    async def _publish(self):
        track = rtc.LocalAudioTrack.create_audio_track("agent-response", self.source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        options.dtx = False
        options.red = True
        return await self.room.local_participant.publish_track(track, options)

    async def start(self) -> None:
        async with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("LiveKit audio sink is closing")
            if self.publication is not None:
                return
            self.publication = await self._publish()
            self._publication_id(self.publication)

    def current_publication_id(self) -> str:
        if self.publication is None:
            raise RuntimeError("LiveKit audio publication is not started")
        return self._publication_id(self.publication)

    @property
    def submitted_bytes(self) -> int:
        return self._submitted_bytes

    @property
    def padded_bytes(self) -> int:
        return self._padded_bytes

    @property
    def submitted_frames(self) -> int:
        return self._submitted_frames

    async def prepare(self, turn_id: str, media_generation: int) -> str:
        if media_generation < 1:
            raise RuntimeError("invalid media generation")
        async with self._lock:
            if self._closing or self._closed or self.publication is None:
                raise RuntimeError("LiveKit audio publication is unavailable")
            if self._active_turn not in {None, turn_id}:
                raise RuntimeError("another LiveKit audio request is active")
            self._active_turn = turn_id
            self._active_media_generation = media_generation
            self._pending_pcm.clear()
            self._submitted_bytes = 0
            self._padded_bytes = 0
            self._submitted_frames = 0
            return self._publication_id(self.publication)

    async def write(
        self, turn_id: str, pcm: bytes, cancelled, media_generation: int | None = None
    ) -> bool:
        if not pcm or len(pcm) % 2 or len(pcm) > OUTPUT_DELIVERY_BLOCK_BYTES:
            return False
        async with self._lock:
            if self._closing or self._closed or self.publication is None:
                raise RuntimeError("LiveKit audio publication is unavailable")
            if cancelled():
                return False
            if self._active_turn is None:
                # Compatibility for historical sink-level tests; active v2 always
                # calls prepare and supplies its generation before any PCM.
                self._active_turn = turn_id
                self._active_media_generation = media_generation or 1
                self._submitted_bytes = self._padded_bytes = self._submitted_frames = 0
            if (
                self._active_turn != turn_id
                or (media_generation is not None and self._active_media_generation != media_generation)
            ):
                raise RuntimeError("another LiveKit audio request is active")
            self._pending_pcm.extend(pcm)
            while len(self._pending_pcm) >= AUDIO_FRAME_BYTES:
                if (
                    cancelled()
                    or self._active_turn != turn_id
                    or (media_generation is not None and self._active_media_generation != media_generation)
                ):
                    return False
                chunk = bytes(self._pending_pcm[:AUDIO_FRAME_BYTES])
                del self._pending_pcm[:AUDIO_FRAME_BYTES]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz,
                    num_channels=TTS_OUTPUT_AUDIO_FORMAT.channels,
                    samples_per_channel=AUDIO_FRAME_BYTES // 2,
                )
                await self.source.capture_frame(frame)
                if cancelled():
                    return False
                self._submitted_bytes += len(chunk)
                self._submitted_frames += 1
            return not cancelled() and self._active_turn == turn_id

    async def finish(
        self, turn_id: str, cancelled, media_generation: int | None = None
    ) -> bool:
        async with self._lock:
            if (
                cancelled()
                or self._active_turn != turn_id
                or self.publication is None
                or (media_generation is not None and self._active_media_generation != media_generation)
            ):
                return False
            if self._pending_pcm:
                original_bytes = len(self._pending_pcm)
                padding_bytes = AUDIO_FRAME_BYTES - original_bytes
                frame_bytes = bytes(self._pending_pcm) + bytes(padding_bytes)
                if cancelled():
                    return False
                frame = rtc.AudioFrame(
                    data=frame_bytes,
                    sample_rate=TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz,
                    num_channels=TTS_OUTPUT_AUDIO_FORMAT.channels,
                    samples_per_channel=AUDIO_FRAME_BYTES // 2,
                )
                await self.source.capture_frame(frame)
                if cancelled():
                    return False
                self._submitted_bytes += original_bytes
                self._padded_bytes += padding_bytes
                self._submitted_frames += 1
                self._pending_pcm.clear()
            self._active_turn = None
            self._active_media_generation = None
            return True

    async def abandon(self, turn_id: str) -> str | None:
        """Drop only unsubmitted bytes; preserve the accepted server PCM prefix."""
        async with self._lock:
            if self.publication is None:
                return None
            publication_id = self._publication_id(self.publication)
            if self._active_turn not in {None, turn_id}:
                return publication_id
            self._active_turn = None
            self._active_media_generation = None
            self._pending_pcm.clear()
            return publication_id

    async def clear(self, turn_id: str) -> str | None:
        async with self._lock:
            if self.publication is None:
                return None
            publication_id = self._publication_id(self.publication)
            if self._active_turn not in {None, turn_id}:
                return publication_id
            self._active_turn = None
            self._active_media_generation = None
            self._pending_pcm.clear()
            self._submitted_bytes = 0
            self._padded_bytes = 0
            self._submitted_frames = 0
            self.source.clear_queue()
            return publication_id

    async def transport_disconnected(self) -> None:
        async with self._lock:
            self._transport_disconnected = True
            self._active_turn = None
            self._active_media_generation = None
            self._pending_pcm.clear()
            self._submitted_bytes = 0
            self._padded_bytes = 0
            self._submitted_frames = 0
            if self.publication is not None:
                self.source.clear_queue()

    async def _run_close(self) -> None:
        async with self._lock:
            publication = self.publication
            publication_id = (
                self._publication_id(publication) if publication is not None else None
            )
            transport_disconnected = self._transport_disconnected
            self._active_turn = None
            self._active_media_generation = None
            self._pending_pcm.clear()
            self._submitted_bytes = 0
            self._padded_bytes = 0
            self._submitted_frames = 0
            if publication is not None:
                self.source.clear_queue()
        if publication_id is not None and not transport_disconnected:
            await self.room.local_participant.unpublish_track(publication_id)
        await self.source.aclose()
        async with self._lock:
            if self.publication is publication:
                self.publication = None
            self._closed = True

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closing = True
            task = self._close_task
            if task is None:
                task = asyncio.create_task(self._run_close(), name="livekit-audio-sink-close")
                self._close_task = task
        await asyncio.shield(task)

    async def wait_for_cleanup(self) -> None:
        task = self._close_task
        if task is not None:
            await asyncio.shield(task)


class LiveKitRoomController:
    """Own one isolated room, one browser, one inference runner, and one audio track."""

    def __init__(
        self,
        *,
        settings: Slice6Settings,
        session_id: str,
        room_name: str,
        browser_identity: str,
        on_closed: Callable[[str], Awaitable[None]],
        runner: LiveTurnRunner | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = session_id
        self.room_name = room_name
        self.browser_identity = browser_identity
        self.on_closed = on_closed
        self.agent_identity = f"agent-{session_id}"
        self._owns_runner = runner is None
        self.runner = runner or LiveTurnRunner(settings)
        self.room = rtc.Room()
        self.audio_source = rtc.AudioSource(
            TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz,
            TTS_OUTPUT_AUDIO_FORMAT.channels,
            queue_size_ms=AUDIO_QUEUE_MS,
        )
        self.trace = PrivacySafeTrace(
            TRACE_ROOT / f"{session_id}.jsonl", TraceIdentity(session_id)
        )
        self.diagnostic_capture = (
            DiagnosticContentCapture(
                settings.diagnostic_capture_root,
                session_id,
                opt_in=True,
                ttl_seconds=settings.diagnostic_capture_ttl_seconds,
            )
            if settings.diagnostic_capture_root is not None
            else None
        )
        self.audio_sink = LiveKitAudioSink(
            self.room,
            self.audio_source,
            lambda source: setattr(self, "audio_source", source),
        )
        self.session = RealtimeSession(
            session_id=session_id,
            runner=self.runner,
            event_sink=LiveKitEventSink(self.room, browser_identity),
            audio_sink=self.audio_sink,
            failure_handler=self._session_failed,
            trace_observer=lambda stage, event, fields: self.trace.observe(
                stage,
                event,
                fields,
                stream_epoch=self.session.stream_epoch if hasattr(self, "session") else 1,
            ),
            reconnect_reset_handler=self._invalidate_microphone_for_reconnect,
            resource_sampler=ResourceSampler(),
            diagnostic_capture=self.diagnostic_capture,
        )
        self._audio_task: asyncio.Task[None] | None = None
        self._microphone_resume_task: asyncio.Task[None] | None = None
        self._microphone_track = None
        self._microphone_publication_id: str | None = None
        self._microphone_generation = 0
        self._vad_model: SileroOnnxModel | None = None
        self._microphone_muted = False
        self._capture_invalidated = False
        self._browser_join_task: asyncio.Task[None] | None = None
        self._runner_start_task: asyncio.Task[None] | None = None
        self._control_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=BROWSER_CONTROL_QUEUE_SIZE
        )
        self._control_task: asyncio.Task[None] | None = None
        self._close_retry_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._cleanup_complete = False
        self._close_notified = False
        self._transport_failed = False
        self._room_disconnected = False
        self._browser_ready = False

    async def start(self) -> None:
        try:
            if self._vad_model is None:
                self._vad_model = await asyncio.to_thread(SileroOnnxModel)
            if not getattr(self.runner, "_started", False):
                self._runner_start_task = asyncio.create_task(
                    asyncio.to_thread(self.runner.start), name=f"startup-{self.session_id}"
                )
                await asyncio.shield(self._runner_start_task)
            if self._closed:
                raise RuntimeError("room closed during runner startup")
            self._register_handlers()
            token = self._agent_token()
            await self.room.connect(self.settings.livekit_internal_url, token)
            await self.audio_sink.start()
        except Exception:
            await self.close(notify=False)
            raise

    def _session_failed(self, stage: str, _code: str) -> None:
        if stage == "transport":
            self._transport_failed = True
        asyncio.create_task(self.close(), name=f"failed-session-{self.session_id}")

    def arm_browser_join_timeout(self) -> None:
        if self._closed or self._browser_ready or self._browser_join_task is not None:
            return
        self._browser_join_task = asyncio.create_task(
            self._expire_unclaimed_room(), name=f"browser-join-{self.session_id}"
        )

    async def _expire_unclaimed_room(self) -> None:
        try:
            await asyncio.sleep(
                min(
                    self.settings.browser_join_timeout_seconds,
                    self.settings.room_token_ttl_seconds,
                )
            )
            if not self._browser_ready:
                await self.close()
        except asyncio.CancelledError:
            raise

    def browser_token(self) -> str:
        grants = api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_publish_sources=["microphone"],
            room_create=False,
            room_admin=False,
            room_list=False,
            room_record=False,
        )
        return (
            api.AccessToken(self.settings.livekit_api_key, self.settings.livekit_api_secret)
            .with_identity(self.browser_identity)
            .with_name("Voice browser")
            .with_grants(grants)
            .with_ttl(timedelta(seconds=self.settings.room_token_ttl_seconds))
            .to_jwt()
        )

    def _agent_token(self) -> str:
        grants = api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            room_create=False,
            room_admin=False,
            hidden=False,
        )
        return (
            api.AccessToken(self.settings.livekit_api_key, self.settings.livekit_api_secret)
            .with_identity(self.agent_identity)
            .with_name("Voice Agent v2")
            .with_grants(grants)
            .with_ttl(timedelta(minutes=10))
            .to_jwt()
        )

    def _register_handlers(self) -> None:
        if self._control_task is None:
            self._control_task = asyncio.create_task(
                self._consume_controls(), name=f"browser-control-{self.session_id}"
            )

        @self.room.on("participant_connected")
        def participant_connected(participant) -> None:
            if participant.identity != self.browser_identity:
                return

        @self.room.on("track_subscribed")
        def track_subscribed(track, publication, participant) -> None:
            if (
                participant.identity != self.browser_identity
                or track.kind != rtc.TrackKind.KIND_AUDIO
                or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
            ):
                return
            if (
                self._microphone_publication_id is not None
                and publication.sid != self._microphone_publication_id
            ):
                return
            if not self._browser_ready:
                self._browser_ready = True
                if self._browser_join_task is not None:
                    self._browser_join_task.cancel()
                asyncio.create_task(self.session.ready())
            self._microphone_track = track
            self._microphone_publication_id = publication.sid
            self._microphone_muted = publication.muted
            if self._microphone_muted:
                self._retire_microphone_consumer()
            else:
                self._queue_microphone_resume()

        @self.room.on("track_muted")
        def track_muted(participant, publication) -> None:
            if not self._is_browser_microphone_publication(participant, publication):
                return
            self._microphone_muted = True
            self._retire_microphone_consumer()

        @self.room.on("track_unmuted")
        def track_unmuted(participant, publication) -> None:
            if not self._is_browser_microphone_publication(participant, publication):
                return
            self._microphone_muted = False
            if publication.track is not None:
                self._microphone_track = publication.track
            self._queue_microphone_resume()

        @self.room.on("data_received")
        def data_received(packet) -> None:
            if (
                packet.participant is None
                or packet.participant.identity != self.browser_identity
                or packet.topic != CLIENT_CONTROL_TOPIC
            ):
                return
            payload = bytes(packet.data)
            if not payload or len(payload) > MAX_CONTROL_BYTES:
                self.session.drop_counts["client_control"] += 1
                return
            try:
                self._control_queue.put_nowait(payload)
            except asyncio.QueueFull:
                self.session.drop_counts["client_control"] += 1

        @self.room.on("participant_disconnected")
        def participant_disconnected(participant) -> None:
            if participant.identity == self.browser_identity:
                asyncio.create_task(self.close())

    async def _consume_controls(self) -> None:
        while True:
            payload = await self._control_queue.get()
            try:
                accepted = await self.session.handle_client_control(payload)
                self._resume_microphone_after_reconnect()
                trace = getattr(self, "trace", None)
                if trace is not None:
                    trace.emit(
                        "control", "client_control",
                        {"accepted": accepted, "byte_count": len(payload)},
                        stream_epoch=getattr(self.session, "stream_epoch", 1),
                    )
            except Exception as error:
                trace = getattr(self, "trace", None)
                if trace is not None:
                    trace.emit(
                        "control", "client_control_failed",
                        {
                            "failure_class": type(error).__name__,
                            "failure_code": "client_control_handler_failed",
                        },
                        stream_epoch=getattr(self.session, "stream_epoch", 1),
                    )
                await self.session.fail("transport", "client_control_handler_failed")
            finally:
                self._control_queue.task_done()

    def _is_browser_microphone_publication(self, participant, publication) -> bool:
        if (
            participant.identity != self.browser_identity
            or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
            or (
                self._microphone_publication_id is not None
                and publication.sid != self._microphone_publication_id
            )
        ):
            return False
        self._microphone_publication_id = publication.sid
        if publication.track is not None:
            self._microphone_track = publication.track
        return True

    def _retire_microphone_consumer(self) -> asyncio.Task[None] | None:
        self._microphone_generation += 1
        task = self._audio_task
        if task is not None and not task.done():
            task.cancel()
        return task

    def _start_microphone_consumer(self, track) -> None:
        if (
            self._closed
            or self._capture_invalidated
            or self._microphone_muted
            or (
                self._audio_task is not None
                and not self._audio_task.done()
            )
        ):
            return
        self._microphone_generation += 1
        generation = self._microphone_generation
        self._audio_task = asyncio.create_task(
            self._consume_microphone(track, generation),
            name=f"microphone-{self.session_id}-{generation}",
        )

    def _queue_microphone_resume(self) -> None:
        if (
            self._closed
            or self._capture_invalidated
            or self._microphone_muted
            or self._microphone_track is None
        ):
            return
        if self._microphone_resume_task is not None and not self._microphone_resume_task.done():
            return

        async def resume() -> None:
            previous = self._audio_task
            if previous is not None and previous is not asyncio.current_task():
                try:
                    await previous
                except asyncio.CancelledError:
                    pass
            if (
                not self._closed
                and not self._capture_invalidated
                and not self._microphone_muted
                and self._microphone_track is not None
            ):
                self._start_microphone_consumer(self._microphone_track)

        task = asyncio.create_task(
            resume(), name=f"microphone-resume-{self.session_id}"
        )
        self._microphone_resume_task = task

        def clear_resume(completed: asyncio.Task[None]) -> None:
            if self._microphone_resume_task is completed:
                self._microphone_resume_task = None
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(clear_resume)

    async def _invalidate_microphone_for_reconnect(self) -> None:
        self._capture_invalidated = True
        resume = self._microphone_resume_task
        if resume is not None and resume is not asyncio.current_task() and not resume.done():
            resume.cancel()
        task = self._retire_microphone_consumer()
        for pending in (resume, task):
            if pending is None or pending is asyncio.current_task() or pending.done():
                continue
            try:
                await pending
            except asyncio.CancelledError:
                pass

    def _resume_microphone_after_reconnect(self) -> None:
        if not self._capture_invalidated:
            return
        self._capture_invalidated = False
        self._queue_microphone_resume()

    async def _consume_microphone(self, track, generation: int) -> None:
        trace = getattr(self, "trace", None)
        terminal_silence_ms = 0
        audio_clock_samples = 0
        audio_clock_origin: float | None = None

        def observe_vad(fields: dict[str, object]) -> None:
            nonlocal terminal_silence_ms
            silence = fields.get("silence_duration_ms")
            if isinstance(silence, int) and not isinstance(silence, bool) and silence >= 0:
                terminal_silence_ms = silence
            if trace is not None:
                trace.emit(
                    "vad", str(fields["decision"]),
                    {key: value for key, value in fields.items() if key != "decision"},
                    stream_epoch=getattr(self.session, "stream_epoch", 1),
                )

        endpoint: SileroSpeechEndpoint | None = None
        stream = None
        failure_code = "microphone_stream_ended"
        listening_turn_id: str | None = None
        try:
            model = self._vad_model
            if model is None:
                raise RuntimeError("resident microphone VAD is unavailable")
            model.reset()
            endpoint = SileroSpeechEndpoint(model, telemetry=observe_vad)
            stream = rtc.AudioStream.from_track(
                track=track,
                capacity=20,
                sample_rate=INPUT_AUDIO_FORMAT.sample_rate_hz,
                num_channels=INPUT_AUDIO_FORMAT.channels,
                frame_size_ms=AUDIO_FRAME_MS,
            )
            async for event in stream:
                if (
                    generation != self._microphone_generation
                    or self._capture_invalidated
                    or self._microphone_muted
                    or self._closed
                ):
                    return
                frame_pcm = bytes(event.frame.data)
                frame_samples = len(frame_pcm) // 2
                if audio_clock_origin is None:
                    audio_clock_origin = (
                        time.monotonic() - frame_samples / INPUT_AUDIO_FORMAT.sample_rate_hz
                    )
                audio_clock_samples += frame_samples
                for decision in endpoint.feed(frame_pcm):
                    if (
                        generation != self._microphone_generation
                        or self._capture_invalidated
                        or self._microphone_muted
                        or self._closed
                    ):
                        return
                    signal, payload = decision.kind, decision.payload
                    if signal == "speech_started":
                        listening_turn_id = await self.session.start_utterance(announce=False)
                    elif signal == "speech_discarded":
                        if listening_turn_id is not None:
                            await self.session.abandon_unannounced_utterance(listening_turn_id)
                            listening_turn_id = None
                    elif signal == "utterance" and payload is not None:
                        now = time.monotonic()
                        endpoint_monotonic = min(
                            now,
                            now
                            if audio_clock_origin is None
                            else audio_clock_origin
                            + max(
                                0.0,
                                audio_clock_samples / INPUT_AUDIO_FORMAT.sample_rate_hz
                                - terminal_silence_ms / 1000,
                            ),
                        )
                        await self.session.finish_utterance(
                            payload, endpoint_monotonic=endpoint_monotonic
                        )
                        listening_turn_id = None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if getattr(self.session, "closed", False):
                return
            failure_code = "microphone_stream_failed"
            if trace is not None:
                trace.emit(
                    "input", "microphone_failed",
                    {
                        "failure_class": type(error).__name__,
                        "failure_code": failure_code,
                    },
                    stream_epoch=getattr(self.session, "stream_epoch", 1),
                )
        finally:
            current_generation = (
                generation == self._microphone_generation
                and not self._capture_invalidated
                and not self._microphone_muted
                and not self._closed
                and not getattr(self.session, "closed", False)
            )
            try:
                if not current_generation:
                    if listening_turn_id is not None:
                        await self.session.abandon_unannounced_utterance(listening_turn_id)
                        listening_turn_id = None
                    if endpoint is not None:
                        endpoint.reset()
                elif endpoint is not None:
                    for decision in endpoint.flush():
                        signal, payload = decision.kind, decision.payload
                        if signal == "utterance" and payload is not None:
                            try:
                                await self.session.finish_utterance(payload)
                            except (RuntimeError, ValueError):
                                pass
                        elif signal == "speech_discarded" and listening_turn_id is not None:
                            await self.session.abandon_unannounced_utterance(listening_turn_id)
                            listening_turn_id = None
            finally:
                if stream is not None:
                    try:
                        await stream.aclose()
                    except Exception as error:
                        failure_code = "microphone_stream_failed"
                        if trace is not None:
                            trace.emit(
                                "input", "microphone_failed",
                                {
                                    "failure_class": type(error).__name__,
                                    "failure_code": failure_code,
                                },
                                stream_epoch=getattr(self.session, "stream_epoch", 1),
                            )
                if (
                    current_generation
                    and self._audio_task is asyncio.current_task()
                ):
                    await self.session.fail("input", failure_code)

    async def _disconnect_room(self) -> None:
        if getattr(self, "_room_disconnected", False):
            return
        await self.room.disconnect()
        self._room_disconnected = True

    def _schedule_close_retry(self, notify: bool) -> None:
        retry = getattr(self, "_close_retry_task", None)
        if retry is not None and not retry.done():
            return

        async def retry_after_cleanup() -> None:
            try:
                while not self._cleanup_complete:
                    await self.session.wait_for_cleanup()
                    await self.audio_sink.wait_for_cleanup()
                    await self.close(notify=notify)
                    if not self._cleanup_complete:
                        await asyncio.sleep(0.05)
            except Exception:
                pass

        self._close_retry_task = asyncio.create_task(
            retry_after_cleanup(), name=f"cleanup-retry-{self.session_id}"
        )

    async def close(self, *, notify: bool = True) -> None:
        async with self._close_lock:
            self._closed = True
            if not self._cleanup_complete:
                errors: list[Exception] = []
                startup = self._runner_start_task
                if (
                    startup is not None
                    and startup is not asyncio.current_task()
                    and not startup.done()
                ):
                    try:
                        cancel_startup = getattr(self.runner, "cancel_startup", None)
                        if cancel_startup is None:
                            cancel_startup = self.runner.cancel
                        await asyncio.to_thread(cancel_startup)
                    except Exception as error:
                        errors.append(error)
                if startup is not None and startup is not asyncio.current_task():
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                for task in (
                    self._browser_join_task,
                    self._microphone_resume_task,
                    self._audio_task,
                    self._control_task,
                ):
                    if task is None or task is asyncio.current_task():
                        continue
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as error:
                        errors.append(error)
                if errors:
                    raise ExceptionGroup("room resource cleanup failed", errors)
                if self._transport_failed:
                    try:
                        await self._disconnect_room()
                    except Exception as error:
                        raise ExceptionGroup(
                            "room resource cleanup failed", [error]
                        ) from error
                    await self.audio_sink.transport_disconnected()
                    try:
                        await self.session.disconnect(notify_client=False)
                    except RuntimeError as error:
                        if str(error) in {
                            "cancellation_cleanup_timeout",
                            "turn_cleanup_timeout",
                            "audio_drain_timeout",
                        }:
                            self._schedule_close_retry(notify)
                            return
                        raise
                    await self.audio_sink.close()
                else:
                    try:
                        await self.session.disconnect()
                    except RuntimeError as error:
                        if str(error) in {
                            "cancellation_cleanup_timeout",
                            "turn_cleanup_timeout",
                            "audio_drain_timeout",
                        }:
                            self._schedule_close_retry(notify)
                            return
                        raise
                    await self.audio_sink.close()
                    await self._disconnect_room()
                await asyncio.to_thread(self.runner.close, self.session_id)
                if getattr(self, "_owns_runner", False):
                    shutdown = getattr(self.runner, "shutdown", None)
                    if shutdown is not None:
                        await asyncio.to_thread(shutdown)
                self._cleanup_complete = True
            if notify and not self._close_notified:
                await self.on_closed(self.session_id)
                self._close_notified = True


class SessionRegistry:
    def __init__(self, settings: Slice6Settings) -> None:
        self.settings = settings
        self.runner = LiveTurnRunner(settings)
        runtime_id = f"runtime-{os.getpid()}-{time.monotonic_ns():x}"
        self.trace = PrivacySafeTrace(
            TRACE_ROOT / f"{runtime_id}.jsonl", TraceIdentity(runtime_id)
        )
        self._controllers: dict[str, LiveKitRoomController] = {}
        self._lock = asyncio.Lock()
        self._accepting = False

    async def start(self) -> None:
        await asyncio.to_thread(self.runner.start)
        self._accepting = True
        for component in self.runner.readiness_components():
            self.trace.emit(
                "model",
                "loaded",
                {
                    "component": component.component,
                    "model_identity": component.identity,
                    "contract_version": component.contract_version,
                    "compatible": component.compatible,
                    "ready": component.readiness == "ready",
                },
            )

    @property
    def active_count(self) -> int:
        return len(self._controllers)

    @property
    def accepting(self) -> bool:
        return self._accepting

    def operational_health(self) -> dict[str, object]:
        accepting = self._accepting
        development = (
            self.settings.build_id == "development"
            and self.settings.release_id == "development"
        )

        def parent_process_alive(identity: str | None) -> bool:
            return development if identity is None else supervised_process_alive(identity)

        livekit_alive = parent_process_alive(
            self.settings.supervised_livekit_process
        )
        lfm_alive = parent_process_alive(self.settings.supervised_lfm_process)
        livekit_ready = livekit_alive and livekit_endpoint_ready(
            self.settings.livekit_internal_url
        )
        lfm_endpoint_health = (
            local_lfm_endpoint_health() if lfm_alive else "unavailable"
        )
        runtime_components: list[ComponentHealth] = []
        for component in self.runner.readiness_components():
            if component.component == "selected_llm" and (
                not lfm_alive or lfm_endpoint_health != "ready"
            ):
                component = ComponentHealth(
                    component.component,
                    "alive" if lfm_alive else "dead",
                    "unready",
                    component.compatible,
                    component.identity,
                    component.contract_version,
                    (
                        "local_lfm_health_failed"
                        if lfm_alive and lfm_endpoint_health == "unready"
                        else "local_lfm_unavailable"
                    ),
                    component.retry_count,
                    component.retry_limit,
                )
            runtime_components.append(component)
        components = (
            ComponentHealth(
                "livekit",
                "alive" if livekit_alive else "dead",
                "ready" if accepting and livekit_ready else "unready",
                True,
                "livekit-server-v1.13.5",
                "voice-agent.realtime-control.v2",
                None if accepting and livekit_ready
                else "service_draining" if not accepting
                else "livekit_unavailable",
            ),
            ComponentHealth(
                "controller", "alive", "ready" if accepting else "unready", True,
                "voice-agent-v2-controller", "voice-agent.realtime-control.v2",
                None if accepting else "service_draining",
            ),
            *runtime_components,
        )
        return HealthReport(tuple(components)).as_dict()

    async def create(self) -> dict[str, object]:
        async with self._lock:
            if not self._accepting:
                raise RuntimeError("the voice stack is draining")
            if len(self._controllers) >= self.settings.max_sessions:
                raise SessionCapacityError("the single measured Slice 6 session is in use")
            if self.operational_health()["overall_readiness"] != "ready":
                raise RuntimeError("the voice stack is unavailable")
            session_id = f"session-{secrets.token_hex(12)}"
            room_name = f"voice-{session_id}"
            browser_identity = f"browser-{session_id}"
            controller = LiveKitRoomController(
                settings=self.settings,
                session_id=session_id,
                room_name=room_name,
                browser_identity=browser_identity,
                on_closed=self.remove,
                runner=self.runner,
            )
            self._controllers[session_id] = controller
        try:
            await controller.start()
            llm_profile = self.runner.public_llm_profile()
            tts_profile = self.runner.tts_profile.public_metadata()
            if self.operational_health()["overall_readiness"] != "ready":
                raise RuntimeError("the voice stack became unavailable during admission")
            capability = {
                "session_id": session_id,
                "stream_epoch": 1,
                "livekit_url": self.settings.livekit_public_url,
                "token": controller.browser_token(),
                "expires_in_seconds": self.settings.room_token_ttl_seconds,
                "admission_timeout_ms": min(
                    self.settings.browser_join_timeout_seconds,
                    self.settings.room_token_ttl_seconds,
                ) * 1_000,
                "control_version": "voice-agent.realtime-control.v2",
                "llm_profile": llm_profile,
                "tts_profile": tts_profile,
            }
            controller.arm_browser_join_timeout()
            if self.operational_health()["overall_readiness"] != "ready":
                raise RuntimeError("the voice stack became unavailable before capability issue")
        except BaseException:
            try:
                await controller.close(notify=False)
            finally:
                async with self._lock:
                    self._controllers.pop(session_id, None)
            raise
        return capability

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._controllers.pop(session_id, None)

    async def close(self) -> None:
        async with self._lock:
            self._accepting = False
            controllers = list(self._controllers.values())
        results = await asyncio.gather(
            *(controller.close() for controller in controllers),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        try:
            components = self.runner.readiness_components()
        except Exception:
            components = ()
        try:
            await asyncio.to_thread(self.runner.shutdown)
        except Exception as error:
            errors.append(error)
        finally:
            for component in components:
                self.trace.emit(
                    "model",
                    "unloaded",
                    {
                        "component": component.component,
                        "model_identity": component.identity,
                        "contract_version": component.contract_version,
                    },
                )
        if errors:
            raise ExceptionGroup("session registry cleanup failed", errors)

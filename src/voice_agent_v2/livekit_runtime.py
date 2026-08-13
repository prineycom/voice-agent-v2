"""Low-level official LiveKit RTC/API adapters for the Slice 6 session controller."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Awaitable, Callable

from livekit import api, rtc

from .audio import OUTPUT_MEDIA_MAX_BYTES
from .diagnostics import PrivacySafeTrace, TraceIdentity
from .local_lfm import LocalLFMProvider
from .local_stt import WhisperSTT
from .local_tts import Qwen3TTS
from .real_turn import RealTurnController
from .local_vad import SileroOnnxModel, SileroSpeechEndpoint
from .realtime import (
    CLIENT_CONTROL_TOPIC,
    CONTROL_TOPIC,
    MAX_CONTROL_BYTES,
    AudioSink,
    EventSink,
    RealtimeSession,
)
from .slice6_config import Slice6Settings
from .tracer import CancellationToken, TraceResult

AUDIO_FRAME_MS = 20
AUDIO_FRAME_BYTES = 16_000 * 2 * AUDIO_FRAME_MS // 1000
AUDIO_QUEUE_MS = 100
BROWSER_CONTROL_QUEUE_SIZE = 32
MAX_SESSION_OBSERVATIONS = 128
TRACE_ROOT = Path.home() / ".cache/voice-agent-v2/slice-6/diagnostics"


class SessionCapacityError(RuntimeError):
    pass


class LiveTurnRunner:
    """Reuse the cumulative real controller and warm every resident adapter."""

    def __init__(self, settings: Slice6Settings) -> None:
        self.stt = WhisperSTT()
        del settings
        self.llm = LocalLFMProvider()
        self.tts = Qwen3TTS()
        self.controller = RealTurnController(self.stt, self.llm, self.tts)
        self._snapshots: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
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
        retain_output: bool = True,
    ) -> TraceResult:
        self._snapshots[(session_id, turn_id)] = self.llm.snapshot_session(session_id)
        try:
            return self.controller.run_turn(
                session_id=session_id,
                turn_id=turn_id,
                input_pcm=input_pcm,
                cancellation=cancellation,
                event_observer=event_observer,
                trace_observer=trace_observer,
                audio_observer=audio_observer,
                retain_output=retain_output,
            )
        finally:
            for adapter in (self.stt, self.llm, self.tts):
                observations = adapter.observations
                if len(observations) > MAX_SESSION_OBSERVATIONS:
                    del observations[:-MAX_SESSION_OBSERVATIONS]

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        snapshot = self._snapshots.pop((session_id, turn_id), None)
        if snapshot is not None:
            self.llm.restore_session(session_id, snapshot)

    def turn_delivered(self, session_id: str, turn_id: str) -> None:
        self._snapshots.pop((session_id, turn_id), None)

    def cancel(self) -> None:
        """Invalidate only active generation/synthesis requests; keep resident models alive."""
        errors: list[Exception] = []
        for adapter in (self.llm, self.tts):
            try:
                cancel_request = getattr(adapter, "cancel_request", None)
                if cancel_request is not None:
                    cancel_request()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("one or more inference requests failed to cancel", errors)

    def reset_session(self, session_id: str) -> None:
        self.llm.reset_session(session_id)
        self._snapshots.clear()

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
        self._pending_pcm = bytearray()
        self._submitted_bytes = 0
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

    async def write(self, turn_id: str, pcm: bytes, cancelled) -> bool:
        if not pcm or len(pcm) % 2 or len(pcm) > OUTPUT_MEDIA_MAX_BYTES:
            return False
        async with self._lock:
            if self._closing or self._closed or self.publication is None:
                raise RuntimeError("LiveKit audio publication is unavailable")
            if cancelled():
                return False
            if self._active_turn not in {None, turn_id}:
                raise RuntimeError("another LiveKit audio request is active")
            if self._active_turn is None:
                self._submitted_bytes = 0
            self._active_turn = turn_id
            self._pending_pcm.extend(pcm)
            while len(self._pending_pcm) >= AUDIO_FRAME_BYTES:
                if cancelled() or self._active_turn != turn_id:
                    return False
                chunk = bytes(self._pending_pcm[:AUDIO_FRAME_BYTES])
                del self._pending_pcm[:AUDIO_FRAME_BYTES]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16_000,
                    num_channels=1,
                    samples_per_channel=AUDIO_FRAME_BYTES // 2,
                )
                await self.source.capture_frame(frame)
                self._submitted_bytes += len(chunk)
            return not cancelled() and self._active_turn == turn_id

    async def finish(self, turn_id: str, cancelled) -> bool:
        async with self._lock:
            if (
                cancelled()
                or self._active_turn != turn_id
                or self._pending_pcm
                or self.publication is None
            ):
                return False
            self._active_turn = None
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
            self._pending_pcm.clear()
            self._submitted_bytes = 0
            self.source.clear_queue()
            return publication_id

    async def transport_disconnected(self) -> None:
        async with self._lock:
            self._transport_disconnected = True
            self._active_turn = None
            self._pending_pcm.clear()
            self._submitted_bytes = 0
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
            self._pending_pcm.clear()
            self._submitted_bytes = 0
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
        self.audio_source = rtc.AudioSource(16_000, 1, queue_size_ms=AUDIO_QUEUE_MS)
        self.trace = PrivacySafeTrace(
            TRACE_ROOT / f"{session_id}.jsonl", TraceIdentity(session_id)
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
            trace_observer=lambda stage, event, fields: self.trace.emit(
                stage, event, fields,
                turn_id=str(fields.get("turn_id", "session")),
                stream_epoch=self.session.stream_epoch if hasattr(self, "session") else 1,
            ),
            reconnect_reset_handler=self._invalidate_microphone_for_reconnect,
        )
        self._audio_task: asyncio.Task[None] | None = None
        self._microphone_resume_task: asyncio.Task[None] | None = None
        self._microphone_track = None
        self._microphone_publication_id: str | None = None
        self._microphone_generation = 0
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

        endpoint = SileroSpeechEndpoint(SileroOnnxModel(), telemetry=observe_vad)
        stream = rtc.AudioStream.from_track(
            track=track,
            capacity=20,
            sample_rate=16_000,
            num_channels=1,
            frame_size_ms=AUDIO_FRAME_MS,
        )
        failure_code = "microphone_stream_ended"
        listening_turn_id: str | None = None
        try:
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
                    audio_clock_origin = time.monotonic() - frame_samples / 16_000
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
                            + max(0.0, audio_clock_samples / 16_000 - terminal_silence_ms / 1000),
                        )
                        await self.session.finish_utterance(
                            payload, endpoint_monotonic=endpoint_monotonic
                        )
                        listening_turn_id = None
        except asyncio.CancelledError:
            raise
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
        finally:
            current_generation = (
                generation == self._microphone_generation
                and not self._capture_invalidated
                and not self._microphone_muted
                and not self._closed
            )
            try:
                if not current_generation and listening_turn_id is not None:
                    await self.session.abandon_unannounced_utterance(listening_turn_id)
                    listening_turn_id = None
                if current_generation:
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
                try:
                    await stream.aclose()
                except Exception:
                    failure_code = "microphone_stream_failed"
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
                await self.session.wait_for_cleanup()
                await self.audio_sink.wait_for_cleanup()
                await self.close(notify=notify)
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
        self._controllers: dict[str, LiveKitRoomController] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await asyncio.to_thread(self.runner.start)

    @property
    def active_count(self) -> int:
        return len(self._controllers)

    async def create(self) -> dict[str, object]:
        async with self._lock:
            if len(self._controllers) >= self.settings.max_sessions:
                raise SessionCapacityError("the single measured Slice 6 session is in use")
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
            controller.arm_browser_join_timeout()
        except BaseException:
            await controller.close(notify=False)
            async with self._lock:
                self._controllers.pop(session_id, None)
            raise
        return {
            "session_id": session_id,
            "stream_epoch": 1,
            "livekit_url": self.settings.livekit_public_url,
            "token": controller.browser_token(),
            "expires_in_seconds": self.settings.room_token_ttl_seconds,
            "admission_timeout_ms": min(
                self.settings.browser_join_timeout_seconds,
                self.settings.room_token_ttl_seconds,
            ) * 1_000,
            "control_version": "voice-agent.realtime-control.v1",
        }

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._controllers.pop(session_id, None)

    async def close(self) -> None:
        async with self._lock:
            controllers = list(self._controllers.values())
        results = await asyncio.gather(
            *(controller.close() for controller in controllers),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        try:
            await asyncio.to_thread(self.runner.shutdown)
        except Exception as error:
            errors.append(error)
        if errors:
            raise ExceptionGroup("session registry cleanup failed", errors)

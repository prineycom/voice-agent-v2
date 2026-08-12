"""Low-level official LiveKit RTC/API adapters for the Slice 6 session controller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import secrets
from typing import Awaitable, Callable

from livekit import api, rtc

from .audio import OUTPUT_MEDIA_MAX_BYTES
from .local_lfm import LocalLFMProvider
from .local_stt import WhisperSTT
from .local_tts import Qwen3TTS
from .real_turn import RealTurnController
from .realtime import (
    CLIENT_CONTROL_TOPIC,
    CONTROL_TOPIC,
    MAX_CONTROL_BYTES,
    AudioSink,
    EnergyEndpoint,
    MediaBoundary,
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


class SessionCapacityError(RuntimeError):
    pass


@dataclass
class _PublicationRetirement:
    publication_id: str
    source: rtc.AudioSource
    task: asyncio.Task[None] | None = None
    error: BaseException | None = None
    source_close_started: bool = False
    local_only: bool = False
    complete: bool = False


class LiveTurnRunner:
    """Reuse the cumulative real controller and preserve rollback on undelivered turns."""

    def __init__(self, settings: Slice6Settings) -> None:
        self.stt = WhisperSTT()
        del settings
        self.llm = LocalLFMProvider()
        self.tts = Qwen3TTS()
        self.controller = RealTurnController(self.stt, self.llm, self.tts)
        self._snapshots: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
        self._startup_cancellation = CancellationToken()

    def start(self) -> None:
        self.llm.readiness(self._startup_cancellation)
        self.stt.start(self._startup_cancellation)
        self.tts.start(self._startup_cancellation)

    def cancel_startup(self) -> None:
        self._startup_cancellation.cancel()
        self.cancel()

    def run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        input_pcm: bytes,
        cancellation: CancellationToken,
        event_observer,
    ) -> TraceResult:
        self._snapshots[(session_id, turn_id)] = self.llm.snapshot_session(session_id)
        try:
            return self.controller.run_turn(
                session_id=session_id,
                turn_id=turn_id,
                input_pcm=input_pcm,
                cancellation=cancellation,
                event_observer=event_observer,
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
        errors: list[Exception] = []
        for adapter in (self.llm, self.tts, self.stt):
            try:
                adapter.cancel()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("one or more inference adapters failed to cancel", errors)

    def reset_session(self, session_id: str) -> None:
        self.llm.reset_session(session_id)
        self._snapshots.clear()

    def close(self, session_id: str) -> None:
        errors: list[Exception] = []
        for cleanup in (
            self.cancel,
            lambda: self.llm.reset_session(session_id),
            self.stt.close,
            self.tts.close,
        ):
            try:
                cleanup()
            except Exception as error:
                errors.append(error)
        self._snapshots.clear()
        if errors:
            raise ExceptionGroup("one or more inference resources failed to close", errors)


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
    requires_media_start_ack = True

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
        self._prepared_turn: str | None = None
        self._active_turn: str | None = None
        self._sealed_boundary: tuple[str, MediaBoundary] | None = None
        self._rotation_lock = asyncio.Lock()
        self._rotation_failed = False
        self._retirement: _PublicationRetirement | None = None
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

    async def _publish(self, source: rtc.AudioSource):
        track = rtc.LocalAudioTrack.create_audio_track("agent-response", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        options.dtx = False
        options.red = True
        return await self.room.local_participant.publish_track(track, options)

    async def start(self) -> None:
        return None

    @staticmethod
    def _consume_retirement(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _run_retirement(self, state: _PublicationRetirement) -> None:
        try:
            async with self._rotation_lock:
                local_only = state.local_only or self._transport_disconnected
            if not local_only:
                await self.room.local_participant.unpublish_track(state.publication_id)
            async with self._rotation_lock:
                if self._retirement is not state:
                    raise RuntimeError("LiveKit publication retirement state changed")
                state.source_close_started = True
            await state.source.aclose()
            source = rtc.AudioSource(16_000, 1, queue_size_ms=AUDIO_QUEUE_MS)
            async with self._rotation_lock:
                if self._retirement is not state:
                    await source.aclose()
                    raise RuntimeError("LiveKit publication retirement state changed")
                self.source = source
                self.source_changed(source)
                self.publication = None
                self._prepared_turn = None
                self._active_turn = None
                self._sealed_boundary = None
                state.complete = True
                self._retirement = None
        except asyncio.CancelledError:
            async with self._rotation_lock:
                superseded = (
                    self._retirement is state
                    and state.local_only
                    and not state.source_close_started
                )
            if superseded:
                raise
            async with self._rotation_lock:
                if self._retirement is state:
                    state.error = asyncio.CancelledError()
                    state.complete = True
                    self._rotation_failed = True
            raise
        except BaseException as error:
            async with self._rotation_lock:
                if self._retirement is state:
                    state.error = error
                    state.complete = True
                    self._rotation_failed = True
            raise

    def _reserve_retirement_locked(
        self, publication_id: str, source: rtc.AudioSource
    ) -> _PublicationRetirement:
        state = self._retirement
        if state is not None:
            if state.publication_id != publication_id or state.source is not source:
                raise RuntimeError("another LiveKit publication retirement is active")
            return state
        state = _PublicationRetirement(publication_id, source)
        state.task = asyncio.create_task(
            self._run_retirement(state),
            name=f"livekit-publication-retirement-{publication_id}",
        )
        state.task.add_done_callback(self._consume_retirement)
        self._retirement = state
        return state

    async def _wait_for_retirement(self, state: _PublicationRetirement) -> None:
        task = state.task
        if task is None:
            raise RuntimeError("LiveKit publication retirement was not started")
        await asyncio.wait({task})
        task.result()

    async def transport_disconnected(self) -> None:
        async with self._rotation_lock:
            self._transport_disconnected = True
            if self.publication is None:
                return
            state = self._reserve_retirement_locked(
                self._publication_id(self.publication), self.source
            )
            state.local_only = True
            task = state.task
            if (
                task is not None
                and not task.done()
                and not state.source_close_started
            ):
                task.cancel()
        if task is not None:
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass
        async with self._rotation_lock:
            if self._retirement is not state or state.complete and state.error is None:
                return
            current_task = state.task
            if current_task is not None and current_task is not task:
                task = current_task
            elif state.source_close_started:
                task = current_task
            else:
                state.error = None
                state.complete = False
                self._rotation_failed = False
                task = asyncio.create_task(
                    self._run_retirement(state),
                    name=f"livekit-local-retirement-{state.publication_id}",
                )
                task.add_done_callback(self._consume_retirement)
                state.task = task
        if task is not None:
            await asyncio.shield(task)

    async def prepare(self, turn_id: str) -> str:
        while True:
            retirement: _PublicationRetirement | None = None
            async with self._rotation_lock:
                if self._rotation_failed:
                    raise RuntimeError("LiveKit audio publication rotation previously failed")
                if self._closing or self._closed:
                    raise RuntimeError("LiveKit audio sink is closing")
                if self._retirement is not None:
                    retirement = self._retirement
                elif self._sealed_boundary is not None:
                    raise RuntimeError("previous LiveKit audio boundary is not finalized")
                elif self.publication is not None:
                    if self._prepared_turn == turn_id:
                        return self._publication_id(self.publication)
                    retirement = self._reserve_retirement_locked(
                        self._publication_id(self.publication), self.source
                    )
                else:
                    source = self.source
                    try:
                        publication = await self._publish(source)
                        publication_id = self._publication_id(publication)
                    except Exception:
                        await source.aclose()
                        replacement = rtc.AudioSource(
                            16_000, 1, queue_size_ms=AUDIO_QUEUE_MS
                        )
                        self.source = replacement
                        self.source_changed(replacement)
                        raise
                    self.publication = publication
                    self._prepared_turn = turn_id
                    return publication_id
            assert retirement is not None
            await self._wait_for_retirement(retirement)

    def current_publication_id(self) -> str:
        if self.publication is None:
            raise RuntimeError("LiveKit audio publication is not prepared")
        return self._publication_id(self.publication)

    async def play(
        self, turn_id: str, pcm: bytes, cancelled
    ) -> MediaBoundary | None:
        if not pcm or len(pcm) % 2 or len(pcm) > OUTPUT_MEDIA_MAX_BYTES:
            return None
        source = self.source
        publication = self.publication
        if publication is None or self._prepared_turn != turn_id:
            raise RuntimeError("turn audio publication is not prepared")
        self._active_turn = turn_id
        try:
            for offset in range(0, len(pcm), AUDIO_FRAME_BYTES):
                if cancelled() or self._active_turn != turn_id:
                    return None
                chunk = pcm[offset : offset + AUDIO_FRAME_BYTES]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16_000,
                    num_channels=1,
                    samples_per_channel=len(chunk) // 2,
                )
                await source.capture_frame(frame)
            await source.wait_for_playout()
            if cancelled() or self._active_turn != turn_id:
                return None
            boundary = MediaBoundary(
                self._publication_id(publication),
                len(pcm) // 2,
                16_000,
            )
            async with self._rotation_lock:
                if (
                    cancelled()
                    or self.publication is not publication
                    or self._prepared_turn != turn_id
                ):
                    return None
                self._sealed_boundary = (turn_id, boundary)
            return boundary
        finally:
            if self._active_turn == turn_id:
                self._active_turn = None

    async def complete(self, turn_id: str, boundary: MediaBoundary) -> None:
        async with self._rotation_lock:
            if (
                self._sealed_boundary != (turn_id, boundary)
                or self.publication is None
                or self._prepared_turn != turn_id
            ):
                raise RuntimeError("LiveKit media boundary is not pending")
            retirement = self._reserve_retirement_locked(
                boundary.completed_publication_id, self.source
            )
        await self._wait_for_retirement(retirement)

    async def clear(self, turn_id: str) -> str | None:
        async with self._rotation_lock:
            if self.publication is None:
                return None
            publication_id = self._publication_id(self.publication)
            if self._active_turn not in {None, turn_id}:
                return publication_id
            self._active_turn = None
            retirement = self._reserve_retirement_locked(publication_id, self.source)
            if not retirement.source_close_started and not retirement.complete:
                self.source.clear_queue()
        await self._wait_for_retirement(retirement)
        return publication_id

    async def _run_close(self) -> None:
        async with self._rotation_lock:
            retirement = self._retirement
            if retirement is None and self.publication is not None:
                retirement = self._reserve_retirement_locked(
                    self._publication_id(self.publication), self.source
                )
        if retirement is not None:
            await self._wait_for_retirement(retirement)
        async with self._rotation_lock:
            if self._rotation_failed:
                raise RuntimeError("LiveKit audio publication rotation previously failed")
            source = self.source
        await source.aclose()
        async with self._rotation_lock:
            if self.source is not source or self.publication is not None:
                raise RuntimeError("LiveKit audio sink changed while closing")
            self._closed = True

    async def close(self) -> None:
        async with self._rotation_lock:
            if self._closed:
                return
            self._closing = True
            task = self._close_task
            if task is None:
                task = asyncio.create_task(
                    self._run_close(), name="livekit-audio-sink-close"
                )
                task.add_done_callback(self._consume_retirement)
                self._close_task = task
        await asyncio.shield(task)

    async def wait_for_cleanup(self) -> None:
        task = self._close_task
        if task is not None:
            await asyncio.shield(task)
            return
        retirement = self._retirement
        if retirement is not None:
            await self._wait_for_retirement(retirement)


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
    ) -> None:
        self.settings = settings
        self.session_id = session_id
        self.room_name = room_name
        self.browser_identity = browser_identity
        self.on_closed = on_closed
        self.agent_identity = f"agent-{session_id}"
        self.runner = LiveTurnRunner(settings)
        self.room = rtc.Room()
        self.audio_source = rtc.AudioSource(16_000, 1, queue_size_ms=AUDIO_QUEUE_MS)
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
        )
        self._audio_task: asyncio.Task[None] | None = None
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
            if not self._browser_ready:
                self._browser_ready = True
                if self._browser_join_task is not None:
                    self._browser_join_task.cancel()
                asyncio.create_task(self.session.ready())
            if self._audio_task is not None and not self._audio_task.done():
                self._audio_task.cancel()
            self._audio_task = asyncio.create_task(
                self._consume_microphone(track), name=f"microphone-{self.session_id}"
            )

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
                await self.session.handle_client_control(payload)
            except Exception:
                pass
            finally:
                self._control_queue.task_done()

    async def _consume_microphone(self, track) -> None:
        endpoint = EnergyEndpoint()
        stream = rtc.AudioStream.from_track(
            track=track,
            capacity=20,
            sample_rate=16_000,
            num_channels=1,
            frame_size_ms=AUDIO_FRAME_MS,
        )
        failure_code = "microphone_stream_ended"
        try:
            async for event in stream:
                for signal, payload in endpoint.feed(bytes(event.frame.data)):
                    if signal == "speech_started":
                        await self.session.start_utterance()
                    elif signal == "speech_discarded":
                        await self.session.discard_utterance()
                    elif signal == "utterance" and payload is not None:
                        await self.session.finish_utterance(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_code = "microphone_stream_failed"
        finally:
            try:
                for signal, payload in endpoint.flush():
                    if signal == "utterance" and payload is not None:
                        try:
                            await self.session.finish_utterance(payload)
                        except (RuntimeError, ValueError):
                            pass
                    elif signal == "speech_discarded":
                        await self.session.discard_utterance()
            finally:
                try:
                    await stream.aclose()
                except Exception:
                    failure_code = "microphone_stream_failed"
                if (
                    not self._closed
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
                self._cleanup_complete = True
            if notify and not self._close_notified:
                await self.on_closed(self.session_id)
                self._close_notified = True


class SessionRegistry:
    def __init__(self, settings: Slice6Settings) -> None:
        self.settings = settings
        self._controllers: dict[str, LiveKitRoomController] = {}
        self._lock = asyncio.Lock()

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
        if errors:
            raise ExceptionGroup("session registry cleanup failed", errors)

"""Low-level official LiveKit RTC/API adapters for the Slice 6 session controller."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import secrets
from typing import Awaitable, Callable

from livekit import api, rtc

from .cloud_llm import LiteLLMProvider
from .local_stt import WhisperSTT
from .local_tts import Qwen3TTS
from .real_turn import RealTurnController
from .realtime import (
    CLIENT_CONTROL_TOPIC,
    CONTROL_TOPIC,
    MAX_CONTROL_BYTES,
    AudioSink,
    EnergyEndpoint,
    EventSink,
    RealtimeSession,
)
from .slice6_config import Slice6Settings
from .tracer import CancellationToken, TraceResult

AUDIO_FRAME_MS = 20
AUDIO_FRAME_BYTES = 16_000 * 2 * AUDIO_FRAME_MS // 1000
AUDIO_QUEUE_MS = 100


class SessionCapacityError(RuntimeError):
    pass


class LiveTurnRunner:
    """Reuse the cumulative real controller and preserve rollback on undelivered turns."""

    def __init__(self, settings: Slice6Settings) -> None:
        self.stt = WhisperSTT()
        self.llm = LiteLLMProvider(
            base_url=settings.litellm_base_url,
            token_path=settings.litellm_token_file,
        )
        self.tts = Qwen3TTS()
        self.controller = RealTurnController(self.stt, self.llm, self.tts)
        self._snapshots: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}

    def start(self) -> None:
        self.llm.readiness()
        self.stt.start()
        self.tts.start()

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
        return self.controller.run_turn(
            session_id=session_id,
            turn_id=turn_id,
            input_pcm=input_pcm,
            cancellation=cancellation,
            event_observer=event_observer,
        )

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        snapshot = self._snapshots.pop((session_id, turn_id), None)
        if snapshot is not None:
            self.llm.restore_session(session_id, snapshot)

    def turn_delivered(self, session_id: str, turn_id: str) -> None:
        self._snapshots.pop((session_id, turn_id), None)

    def cancel(self) -> None:
        for adapter in (self.llm, self.tts, self.stt):
            try:
                adapter.cancel()
            except Exception:
                pass

    def reset_session(self, session_id: str) -> None:
        self.llm.reset_session(session_id)
        self._snapshots.clear()

    def close(self, session_id: str) -> None:
        self.cancel()
        self.llm.reset_session(session_id)
        self.stt.close()
        self.tts.close()
        self._snapshots.clear()


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
    def __init__(self, source: rtc.AudioSource) -> None:
        self.source = source
        self._active_turn: str | None = None

    async def play(self, turn_id: str, pcm: bytes, cancelled) -> bool:
        if not pcm or len(pcm) % 2:
            return False
        self._active_turn = turn_id
        try:
            for offset in range(0, len(pcm), AUDIO_FRAME_BYTES):
                if cancelled() or self._active_turn != turn_id:
                    return False
                chunk = pcm[offset : offset + AUDIO_FRAME_BYTES]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16_000,
                    num_channels=1,
                    samples_per_channel=len(chunk) // 2,
                )
                await self.source.capture_frame(frame)
            await self.source.wait_for_playout()
            return not cancelled() and self._active_turn == turn_id
        finally:
            if self._active_turn == turn_id:
                self._active_turn = None

    async def clear(self, turn_id: str) -> None:
        if self._active_turn in {None, turn_id}:
            self._active_turn = None
            self.source.clear_queue()


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
        self.session = RealtimeSession(
            session_id=session_id,
            runner=self.runner,
            event_sink=LiveKitEventSink(self.room, browser_identity),
            audio_sink=LiveKitAudioSink(self.audio_source),
            failure_handler=self._session_failed,
        )
        self._audio_task: asyncio.Task[None] | None = None
        self._browser_join_task: asyncio.Task[None] | None = None
        self._runner_start_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._cleanup_complete = False
        self._close_notified = False
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
            track = rtc.LocalAudioTrack.create_audio_track("agent-response", self.audio_source)
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_MICROPHONE
            options.dtx = False
            options.red = True
            await self.room.local_participant.publish_track(track, options)
        except Exception:
            await self.close(notify=False)
            raise

    def _session_failed(self, _stage: str, _code: str) -> None:
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
            asyncio.create_task(self.session.handle_client_control(packet.data))

        @self.room.on("participant_disconnected")
        def participant_disconnected(participant) -> None:
            if participant.identity == self.browser_identity:
                asyncio.create_task(self.close())

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

    async def close(self, *, notify: bool = True) -> None:
        async with self._close_lock:
            self._closed = True
            if not self._cleanup_complete:
                errors: list[Exception] = []
                startup = self._runner_start_task
                if startup is not None and startup is not asyncio.current_task():
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                for task in (self._browser_join_task, self._audio_task):
                    if task is None or task is asyncio.current_task():
                        continue
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as error:
                        errors.append(error)
                for cleanup in (
                    self.session.disconnect,
                    self.room.disconnect,
                    self.audio_source.aclose,
                ):
                    try:
                        await cleanup()
                    except Exception as error:
                        errors.append(error)
                try:
                    await asyncio.to_thread(self.runner.close, self.session_id)
                except Exception as error:
                    errors.append(error)
                if errors:
                    raise ExceptionGroup("room resource cleanup failed", errors)
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

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import types
import unittest


def load_runtime():
    if "voice_agent_v2.livekit_runtime" in sys.modules:
        return sys.modules["voice_agent_v2.livekit_runtime"]
    rtc = types.ModuleType("livekit.rtc")
    rtc.TrackKind = types.SimpleNamespace(KIND_AUDIO="audio")
    rtc.TrackSource = types.SimpleNamespace(SOURCE_MICROPHONE="microphone")
    api = types.ModuleType("livekit.api")
    livekit = types.ModuleType("livekit")
    livekit.api = api
    livekit.rtc = rtc
    sys.modules["livekit"] = livekit
    sys.modules["livekit.api"] = api
    sys.modules["livekit.rtc"] = rtc
    return importlib.import_module("voice_agent_v2.livekit_runtime")


runtime = load_runtime()


class FakeRoom:
    def __init__(self) -> None:
        self.handlers = {}

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback
        return register


class FakeSession:
    def __init__(self) -> None:
        self.ready_count = 0
        self.failures = []
        self.drop_counts = {"client_control": 0}
        self.controls: list[bytes] = []
        self.control_started = asyncio.Event()
        self.control_release = asyncio.Event()
        self.control_release.set()

    async def ready(self) -> None:
        self.ready_count += 1

    async def fail(self, stage: str, code: str) -> None:
        self.failures.append((stage, code))

    async def handle_client_control(self, payload: bytes) -> bool:
        self.controls.append(payload)
        self.control_started.set()
        await self.control_release.wait()
        return True


class FakeAudioStream:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.error is not None:
            error = self.error
            self.error = None
            raise error
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


class RecordingRoom:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls
        self.disconnected = False

    async def disconnect(self) -> None:
        self.calls.append("room.disconnect")
        self.disconnected = True


class RecordingSession:
    def __init__(self, room: RecordingRoom, calls: list[object]) -> None:
        self.room = room
        self.calls = calls

    async def disconnect(self, *, notify_client: bool = True) -> None:
        self.calls.append(("session.disconnect", notify_client))
        if notify_client and self.room.disconnected:
            raise RuntimeError("control transport already disconnected")


class DelayedCleanupSession(RecordingSession):
    def __init__(self, room: RecordingRoom, calls: list[object]) -> None:
        super().__init__(room, calls)
        self.cleanup_release = asyncio.Event()
        self.disconnect_count = 0

    async def disconnect(self, *, notify_client: bool = True) -> None:
        self.calls.append(("session.disconnect", notify_client))
        self.disconnect_count += 1
        if self.disconnect_count == 1:
            raise RuntimeError("cancellation_cleanup_timeout")

    async def wait_for_cleanup(self) -> None:
        await self.cleanup_release.wait()


class RecordingAudioSource:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls

    def clear_queue(self) -> None:
        self.calls.append("audio.clear")

    async def aclose(self) -> None:
        self.calls.append("audio.close")


class RecordingRunner:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls

    def close(self, session_id: str) -> None:
        self.calls.append(("runner.close", session_id))


class StartupCancellationRunner(RecordingRunner):
    def __init__(self, calls: list[object]) -> None:
        super().__init__(calls)
        self.entered = asyncio.Event()
        self.cancelled = threading.Event()

    def start(self) -> None:
        loop = self.loop
        loop.call_soon_threadsafe(self.entered.set)
        while not self.cancelled.wait(0.01):
            pass
        self.calls.append("runner.start.cancelled")

    def cancel_startup(self) -> None:
        self.calls.append("runner.cancel_startup")
        self.cancelled.set()


class LiveKitRoomLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_playout_rotates_to_a_correlated_fresh_publication(self) -> None:
        calls: list[object] = []

        class Source:
            sequence = 0

            def __init__(self, *_args, **_kwargs) -> None:
                Source.sequence += 1
                self.name = f"source-{Source.sequence}"

            async def capture_frame(self, _frame) -> None:
                calls.append(("capture", self.name))

            async def wait_for_playout(self) -> None:
                calls.append(("drained", self.name))

            def clear_queue(self) -> None:
                calls.append(("clear", self.name))

            async def aclose(self) -> None:
                calls.append(("close", self.name))

        class Participant:
            sequence = 0

            async def publish_track(self, _track, _options):
                Participant.sequence += 1
                publication = types.SimpleNamespace(
                    sid=f"publication-{Participant.sequence}"
                )
                calls.append(("publish", publication.sid))
                return publication

            async def unpublish_track(self, publication_id: str) -> None:
                calls.append(("unpublish", publication_id))

        runtime.rtc.AudioSource = Source
        runtime.rtc.AudioFrame = lambda **kwargs: kwargs
        runtime.rtc.LocalAudioTrack = types.SimpleNamespace(
            create_audio_track=lambda _name, source: source
        )
        runtime.rtc.TrackPublishOptions = lambda: types.SimpleNamespace()
        room = types.SimpleNamespace(local_participant=Participant())
        source = Source()
        observed_sources = []
        sink = runtime.LiveKitAudioSink(room, source, observed_sources.append)

        await sink.start()
        self.assertNotIn(("publish", "publication-1"), calls)
        publication_id = await sink.prepare("turn-test")
        boundary = await sink.play("turn-test", b"\0\0" * 320, lambda: False)

        self.assertEqual(publication_id, "publication-1")
        self.assertEqual(
            boundary,
            runtime.MediaBoundary("publication-1", 320, 16_000),
        )
        self.assertEqual(len(observed_sources), 1)
        self.assertIs(observed_sources[-1], sink.source)
        self.assertNotIn(("unpublish", "publication-1"), calls)

        await sink.complete("turn-test", boundary)

        self.assertEqual(calls.count(("unpublish", "publication-1")), 1)
        self.assertLess(
            calls.index(("drained", "source-2")),
            calls.index(("close", "source-2")),
        )
        self.assertLess(
            calls.index(("close", "source-2")),
            calls.index(("unpublish", "publication-1")),
        )
        await sink.prepare("turn-next")
        self.assertIn(("publish", "publication-2"), calls)
        await sink.clear("turn-next")
        self.assertIn(("unpublish", "publication-2"), calls)

    def test_live_runner_bounds_session_diagnostics_after_every_turn(self) -> None:
        class Adapter:
            def __init__(self) -> None:
                self.observations = [
                    {"sequence": sequence}
                    for sequence in range(runtime.MAX_SESSION_OBSERVATIONS + 7)
                ]

            def snapshot_session(self, _session_id: str):
                return ()

        class Controller:
            def run_turn(self, **_kwargs):
                return runtime.TraceResult((), b"", b"")

        runner = runtime.LiveTurnRunner.__new__(runtime.LiveTurnRunner)
        runner.stt = Adapter()
        runner.llm = Adapter()
        runner.tts = Adapter()
        runner.controller = Controller()
        runner._snapshots = {}

        runner.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0",
            cancellation=runtime.CancellationToken(),
            event_observer=lambda _event: None,
        )

        for adapter in (runner.stt, runner.llm, runner.tts):
            self.assertEqual(len(adapter.observations), runtime.MAX_SESSION_OBSERVATIONS)
            self.assertEqual(adapter.observations[0], {"sequence": 7})

    def test_live_runner_cancel_attempts_every_adapter_and_aggregates_failures(self) -> None:
        calls: list[str] = []

        class Adapter:
            def __init__(self, name: str, fail: bool = False) -> None:
                self.name = name
                self.fail = fail

            def cancel(self) -> None:
                calls.append(self.name)
                if self.fail:
                    raise RuntimeError(f"{self.name} cancellation failed")

        runner = runtime.LiveTurnRunner.__new__(runtime.LiveTurnRunner)
        runner.llm = Adapter("llm", fail=True)
        runner.tts = Adapter("tts")
        runner.stt = Adapter("stt", fail=True)

        with self.assertRaises(ExceptionGroup) as raised:
            runner.cancel()

        self.assertEqual(calls, ["llm", "tts", "stt"])
        self.assertEqual(len(raised.exception.exceptions), 2)

    def controller(self):
        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.room = FakeRoom()
        controller.browser_identity = "browser-session-test"
        controller.session_id = "session-test"
        controller.session = FakeSession()
        controller._browser_ready = False
        controller._browser_join_task = None
        controller._audio_task = None
        controller._control_queue = asyncio.Queue(
            maxsize=runtime.BROWSER_CONTROL_QUEUE_SIZE
        )
        controller._control_task = None
        controller._closed = False
        return controller

    async def test_room_is_admitted_only_after_microphone_subscription(self) -> None:
        controller = self.controller()
        controller._browser_join_task = asyncio.create_task(asyncio.sleep(10))
        microphone_released = asyncio.Event()

        async def consume(_track):
            await microphone_released.wait()

        controller._consume_microphone = consume
        controller._register_handlers()
        participant = types.SimpleNamespace(identity=controller.browser_identity)
        controller.room.handlers["participant_connected"](participant)
        await asyncio.sleep(0)
        self.assertFalse(controller._browser_ready)
        self.assertEqual(controller.session.ready_count, 0)
        self.assertFalse(controller._browser_join_task.cancelled())

        track = types.SimpleNamespace(kind="audio")
        publication = types.SimpleNamespace(source="microphone")
        controller.room.handlers["track_subscribed"](track, publication, participant)
        await asyncio.sleep(0)
        self.assertTrue(controller._browser_ready)
        self.assertEqual(controller.session.ready_count, 1)
        self.assertTrue(controller._browser_join_task.cancelled())
        microphone_released.set()
        await controller._audio_task

    async def test_browser_control_delivery_is_serialized_and_bounded(self) -> None:
        controller = self.controller()
        controller.session.control_release.clear()
        controller._register_handlers()
        participant = types.SimpleNamespace(identity=controller.browser_identity)

        def packet(payload: bytes):
            return types.SimpleNamespace(
                participant=participant,
                topic=runtime.CLIENT_CONTROL_TOPIC,
                data=payload,
            )

        controller.room.handlers["data_received"](packet(b"first"))
        await asyncio.wait_for(controller.session.control_started.wait(), 1)
        for index in range(runtime.BROWSER_CONTROL_QUEUE_SIZE + 10):
            controller.room.handlers["data_received"](
                packet(f"control-{index}".encode())
            )

        self.assertEqual(
            controller._control_queue.qsize(), runtime.BROWSER_CONTROL_QUEUE_SIZE
        )
        self.assertEqual(controller.session.drop_counts["client_control"], 10)
        controller.session.control_release.set()
        await asyncio.wait_for(controller._control_queue.join(), 1)
        self.assertEqual(
            len(controller.session.controls), runtime.BROWSER_CONTROL_QUEUE_SIZE + 1
        )
        controller._control_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await controller._control_task

    async def test_microphone_eof_and_failure_degrade_the_room(self) -> None:
        for stream, expected in (
            (FakeAudioStream(), "microphone_stream_ended"),
            (FakeAudioStream(RuntimeError("stream failed")), "microphone_stream_failed"),
        ):
            with self.subTest(code=expected):
                controller = self.controller()
                controller._audio_task = asyncio.current_task()
                runtime.rtc.AudioStream = types.SimpleNamespace(
                    from_track=lambda **_kwargs: stream
                )
                await controller._consume_microphone(object())
                self.assertTrue(stream.closed)
                self.assertEqual(controller.session.failures, [("input", expected)])

    async def test_close_cancels_and_joins_provider_startup_before_release(self) -> None:
        calls: list[object] = []
        controller = runtime.LiveKitRoomController.__new__(
            runtime.LiveKitRoomController
        )
        controller.session_id = "session-test"
        controller.room = RecordingRoom(calls)
        controller.session = RecordingSession(controller.room, calls)
        controller.audio_source = RecordingAudioSource(calls)
        runner = StartupCancellationRunner(calls)
        runner.loop = asyncio.get_running_loop()
        controller.runner = runner
        controller.on_closed = lambda _session_id: asyncio.sleep(0)
        controller._closed = False
        controller._cleanup_complete = False
        controller._close_notified = False
        controller._transport_failed = False
        controller._room_disconnected = False
        controller._close_lock = asyncio.Lock()
        controller._close_retry_task = None
        controller._browser_join_task = None
        controller._audio_task = None
        controller._control_task = None
        controller._runner_start_task = asyncio.create_task(
            asyncio.to_thread(runner.start)
        )
        await asyncio.wait_for(runner.entered.wait(), 1)

        await controller.close()

        self.assertLess(
            calls.index("runner.cancel_startup"),
            calls.index("runner.start.cancelled"),
        )
        self.assertLess(
            calls.index("runner.start.cancelled"),
            calls.index(("runner.close", "session-test")),
        )
        self.assertTrue(controller._cleanup_complete)
        self.assertTrue(controller._close_notified)

    async def test_cleanup_timeout_holds_capacity_until_late_confirmed_release(self) -> None:
        calls: list[object] = []
        controller = runtime.LiveKitRoomController.__new__(
            runtime.LiveKitRoomController
        )
        controller.session_id = "session-test"
        controller.room = RecordingRoom(calls)
        controller.session = DelayedCleanupSession(controller.room, calls)
        controller.audio_source = RecordingAudioSource(calls)
        controller.runner = RecordingRunner(calls)
        closed = asyncio.Event()

        async def on_closed(_session_id: str) -> None:
            closed.set()

        controller.on_closed = on_closed
        controller._closed = False
        controller._cleanup_complete = False
        controller._close_notified = False
        controller._transport_failed = False
        controller._close_lock = asyncio.Lock()
        controller._close_retry_task = None
        controller._runner_start_task = None
        controller._browser_join_task = None
        controller._audio_task = None
        controller._control_task = None

        await controller.close()

        self.assertFalse(controller._cleanup_complete)
        self.assertFalse(closed.is_set())
        self.assertNotIn(("runner.close", "session-test"), calls)
        controller.session.cleanup_release.set()
        await asyncio.wait_for(closed.wait(), 1)

        self.assertTrue(controller._cleanup_complete)
        self.assertEqual(calls.count(("runner.close", "session-test")), 1)

    async def test_close_preserves_normal_and_failed_transport_ordering(self) -> None:
        for transport_failed, expected_prefix in (
            (False, [("session.disconnect", True), "room.disconnect"]),
            (True, ["room.disconnect", ("session.disconnect", False)]),
        ):
            with self.subTest(transport_failed=transport_failed):
                calls: list[object] = []
                controller = runtime.LiveKitRoomController.__new__(
                    runtime.LiveKitRoomController
                )
                controller.session_id = "session-test"
                controller.room = RecordingRoom(calls)
                controller.session = RecordingSession(controller.room, calls)
                controller.audio_source = RecordingAudioSource(calls)
                controller.runner = RecordingRunner(calls)
                controller.on_closed = lambda _session_id: asyncio.sleep(0)
                controller._closed = False
                controller._cleanup_complete = False
                controller._close_notified = False
                controller._transport_failed = transport_failed
                controller._close_lock = asyncio.Lock()
                controller._runner_start_task = None
                controller._browser_join_task = None
                controller._audio_task = None
                controller._control_task = None

                await controller.close()

                resource_calls = [call for call in calls if call != "audio.clear"]
                self.assertEqual(resource_calls[:2], expected_prefix)
                self.assertTrue(controller._cleanup_complete)
                self.assertTrue(controller._close_notified)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import importlib
import sys
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


class LiveKitRoomLifecycleTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()

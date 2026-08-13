from __future__ import annotations

import asyncio
import types
import unittest

from tests.test_checkpoint_ab import (
    CheckpointBLiveKitTests,
    CheckpointBWarmupTests,
    load_runtime,
)


class PersistentLiveKitTrackTests(CheckpointBLiveKitTests):
    """Checkpoint B persistent publication behavior."""


class ResidentQwenWarmupTests(CheckpointBWarmupTests):
    """Checkpoint B discard-only resident Qwen warm-up behavior."""


class ReconnectMicrophoneGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_invalidation_discards_active_stale_vad_candidate(self) -> None:
        runtime = load_runtime()
        speech_started = asyncio.Event()
        flush_calls = 0

        class Endpoint:
            def feed(self, _pcm: bytes):
                return [types.SimpleNamespace(kind="speech_started", payload=None)]

            def flush(self):
                nonlocal flush_calls
                flush_calls += 1
                return [types.SimpleNamespace(kind="utterance", payload=b"stale speech")]

        class Stream:
            async def __aiter__(self):
                yield types.SimpleNamespace(frame=types.SimpleNamespace(data=b"\0\0" * 320))
                await asyncio.Event().wait()

            async def aclose(self) -> None:
                return None

        class Session:
            starts = 0
            finishes = 0
            failures: list[tuple[str, str]] = []
            stream_epoch = 1

            async def start_utterance(self) -> None:
                self.starts += 1
                speech_started.set()

            async def discard_utterance(self) -> None:
                return None

            async def finish_utterance(self, _payload: bytes, **_kwargs) -> None:
                self.finishes += 1

            async def fail(self, stage: str, code: str) -> None:
                self.failures.append((stage, code))

        runtime.SileroOnnxModel = lambda: object()
        runtime.SileroSpeechEndpoint = lambda *_args, **_kwargs: Endpoint()
        runtime.rtc.AudioStream = types.SimpleNamespace(
            from_track=lambda **_kwargs: Stream()
        )
        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.session_id = "session-test"
        controller.session = Session()
        controller.trace = None
        controller._closed = False
        controller._capture_invalidated = False
        controller._microphone_generation = 1
        controller._microphone_track = object()
        controller._audio_task = asyncio.create_task(
            controller._consume_microphone(controller._microphone_track, 1)
        )

        await asyncio.wait_for(speech_started.wait(), 0.5)
        await controller._invalidate_microphone_for_reconnect()

        self.assertEqual(controller.session.starts, 1)
        self.assertEqual(controller.session.finishes, 0)
        self.assertEqual(controller.session.failures, [])
        self.assertEqual(flush_calls, 0)
        self.assertTrue(controller._capture_invalidated)
        self.assertIsNone(controller._audio_task)

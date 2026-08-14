from __future__ import annotations

import asyncio
import threading
import types
import unittest

from tests.test_checkpoint_ab import (
    CheckpointBLiveKitTests,
    CheckpointBWarmupTests,
    load_runtime,
)
from tests.test_silero_tts import FakeSTT, FakeVisibleLLM, ProcessCoordinator
from voice_agent_v2.silero_tts import SileroKseniyaTTS, SileroWorkerPool
from voice_agent_v2.tracer import CancellationToken


class LiveTurnObservationTests(unittest.TestCase):
    def test_segment_reservations_reach_the_real_controller_before_synthesis(self) -> None:
        runtime = load_runtime()

        class STT(FakeSTT):
            def __init__(self) -> None:
                self.observations: list[dict[str, object]] = []

        class LLM(FakeVisibleLLM):
            def __init__(self) -> None:
                super().__init__((
                    "Первое достаточно длинное русское предложение уже полностью готово. ",
                    "Второе достаточно длинное русское предложение тоже полностью готово.",
                ))
                self.observations: list[dict[str, object]] = []

            def snapshot_session(self, _session_id: str):
                return ()

        class TTS:
            version = "voice-agent.tts.v2"
            output_format = runtime.TTS_OUTPUT_AUDIO_FORMAT
            capabilities = {"cooperative_cancel": False}

            def __init__(self) -> None:
                self.calls: list[int] = []
                self.observations: list[dict[str, object]] = []

            def stream_synthesize(self, **arguments):
                self.calls.append(arguments["segment_index"])
                yield b"\0\0" * 2_880

            def invalidate_turn(self, *_arguments) -> None:
                return None

        runner = runtime.LiveTurnRunner.__new__(runtime.LiveTurnRunner)
        runner.stt = STT()
        runner.llm = LLM()
        runner.tts = TTS()
        runner.controller = runtime.RealTurnController(runner.stt, runner.llm, runner.tts)
        runner._snapshots = {}
        runner._turn_correlations = {}
        starts: list[tuple[int, int]] = []
        completions: list[tuple[int, bytes | None]] = []

        result = runner.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            cancellation=CancellationToken(),
            event_observer=lambda _event: None,
            segment_started_observer=lambda index: starts.append(
                (index, len(runner.tts.calls))
            ),
            segment_audio_observer=lambda index, pcm: completions.append((index, pcm)),
            request_id="request-test",
        )

        self.assertEqual(result.terminal_event["type"], "turn.completed")
        self.assertEqual(starts, [(0, 0), (1, 1)])
        self.assertEqual(runner.tts.calls, [0, 1])
        self.assertEqual([index for index, _pcm in completions], [0, 1])
        self.assertTrue(all(pcm == b"\0\0" * 2_880 for _index, pcm in completions))

    def test_overlapping_turns_trace_only_their_own_tts_observations(self) -> None:
        runtime = load_runtime()
        coordinator = ProcessCoordinator()
        pool = SileroWorkerPool(
            process_factory=coordinator.factory,
            verify_runtime=lambda: {"verified": True},
        )
        pool.start()
        old_release = threading.Event()
        coordinator.gates["request-old"] = old_release

        class STT(FakeSTT):
            def __init__(self) -> None:
                self.observations: list[dict[str, object]] = []

        class LLM(FakeVisibleLLM):
            def __init__(self) -> None:
                super().__init__(("Готовый ответ.",))
                self.observations: list[dict[str, object]] = []

            def snapshot_session(self, _session_id: str):
                return ()

        runner = runtime.LiveTurnRunner.__new__(runtime.LiveTurnRunner)
        runner.stt = STT()
        runner.llm = LLM()
        runner.tts = SileroKseniyaTTS(pool)
        runner.controller = runtime.RealTurnController(runner.stt, runner.llm, runner.tts)
        runner._snapshots = {}
        runner._turn_correlations = {}
        traces: dict[str, list[dict[str, object]]] = {"old": [], "current": []}
        failures: list[BaseException] = []

        def run_old() -> None:
            try:
                runner.run_turn(
                    session_id="session-test",
                    turn_id="turn-old",
                    input_pcm=b"\0\0" * 320,
                    cancellation=CancellationToken(),
                    event_observer=lambda _event: None,
                    trace_observer=lambda stage, event, fields: (
                        traces["old"].append(fields)
                        if (stage, event) == ("tts", "segment_observation")
                        else None
                    ),
                    request_id="request-old",
                )
            except BaseException as error:
                failures.append(error)

        old_thread = threading.Thread(target=run_old)
        try:
            old_thread.start()
            coordinator.entered.get(timeout=1)
            result = runner.run_turn(
                session_id="session-test",
                turn_id="turn-current",
                input_pcm=b"\0\0" * 320,
                cancellation=CancellationToken(),
                event_observer=lambda _event: None,
                trace_observer=lambda stage, event, fields: (
                    traces["current"].append(fields)
                    if (stage, event) == ("tts", "segment_observation")
                    else None
                ),
                request_id="request-current",
                turn_generation=2,
            )
            self.assertEqual(result.terminal_event["type"], "turn.completed")
            old_release.set()
            old_thread.join(1)
            self.assertFalse(old_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(traces["old"]), 1)
            self.assertEqual(len(traces["current"]), 1)
            self.assertNotEqual(
                traces["old"][0]["worker_id"],
                traces["current"][0]["worker_id"],
            )
        finally:
            old_release.set()
            old_thread.join(1)
            runner.tts.close()


class PersistentLiveKitTrackTests(CheckpointBLiveKitTests):
    """Checkpoint B persistent publication behavior."""


class ResidentQwenWarmupTests(CheckpointBWarmupTests):
    """Checkpoint B discard-only resident Qwen warm-up behavior."""


class CloseRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_repeats_when_nested_close_remains_incomplete(self) -> None:
        runtime = load_runtime()

        class CleanupBoundary:
            async def wait_for_cleanup(self) -> None:
                return None

        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.session_id = "session-test"
        controller.session = CleanupBoundary()
        controller.audio_sink = CleanupBoundary()
        controller._close_retry_task = None
        controller._cleanup_complete = False
        attempts = 0

        async def close(*, notify: bool = True) -> None:
            nonlocal attempts
            self.assertTrue(notify)
            attempts += 1
            if attempts == 2:
                controller._cleanup_complete = True

        controller.close = close
        controller._schedule_close_retry(True)
        await asyncio.wait_for(controller._close_retry_task, 0.5)

        self.assertEqual(attempts, 2)
        self.assertTrue(controller._cleanup_complete)


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

            def reset(self) -> None:
                return None

        class Stream:
            async def __aiter__(self):
                yield types.SimpleNamespace(frame=types.SimpleNamespace(data=b"\0\0" * 320))
                await asyncio.Event().wait()

            async def aclose(self) -> None:
                return None

        class Session:
            starts = 0
            finishes = 0
            abandoned = 0
            failures: list[tuple[str, str]] = []
            stream_epoch = 1

            async def start_utterance(self, *, announce: bool = True) -> str:
                self.starts += 1
                self.assert_unannounced = not announce
                speech_started.set()
                return "turn-candidate"

            async def abandon_unannounced_utterance(self, turn_id: str) -> bool:
                if turn_id == "turn-candidate":
                    self.abandoned += 1
                    return True
                return False

            async def discard_utterance(self) -> None:
                raise AssertionError("invalidation must not publish an interrupted turn")

            async def finish_utterance(self, _payload: bytes, **_kwargs) -> None:
                self.finishes += 1

            async def fail(self, stage: str, code: str) -> None:
                self.failures.append((stage, code))

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
        controller._microphone_muted = False
        controller._microphone_resume_task = None
        controller._microphone_generation = 1
        controller._vad_model = types.SimpleNamespace(reset=lambda: None)
        controller._microphone_track = object()
        controller._audio_task = asyncio.create_task(
            controller._consume_microphone(controller._microphone_track, 1)
        )

        await asyncio.wait_for(speech_started.wait(), 0.5)
        await controller._invalidate_microphone_for_reconnect()

        self.assertEqual(controller.session.starts, 1)
        self.assertEqual(controller.session.finishes, 0)
        self.assertEqual(controller.session.abandoned, 1)
        self.assertTrue(controller.session.assert_unannounced)
        self.assertEqual(controller.session.failures, [])
        self.assertEqual(flush_calls, 0)
        self.assertTrue(controller._capture_invalidated)
        self.assertIs(controller._audio_task.done(), True)

    async def test_consumer_generations_reuse_one_resident_vad_model(self) -> None:
        runtime = load_runtime()
        starts = [asyncio.Event(), asyncio.Event()]
        endpoint_models: list[object] = []

        class Model:
            def __init__(self) -> None:
                self.reset_calls = 0

            def reset(self) -> None:
                self.reset_calls += 1

        class Endpoint:
            def __init__(self, model, **_kwargs) -> None:
                self.model = model
                endpoint_models.append(model)

            def feed(self, _pcm: bytes):
                return []

            def flush(self):
                return []

            def reset(self) -> None:
                self.model.reset()

        class Stream:
            def __init__(self, index: int) -> None:
                self.index = index

            async def __aiter__(self):
                starts[self.index].set()
                await asyncio.Event().wait()
                if False:
                    yield None

            async def aclose(self) -> None:
                return None

        class Session:
            stream_epoch = 1
            failures: list[tuple[str, str]] = []

            async def fail(self, stage: str, code: str) -> None:
                self.failures.append((stage, code))

        stream_count = 0

        def open_stream(**_kwargs):
            nonlocal stream_count
            stream = Stream(stream_count)
            stream_count += 1
            return stream

        runtime.SileroOnnxModel = lambda: (_ for _ in ()).throw(
            AssertionError("consumer generation cold-loaded Silero")
        )
        runtime.SileroSpeechEndpoint = Endpoint
        runtime.rtc.AudioStream = types.SimpleNamespace(from_track=open_stream)
        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.session_id = "session-test"
        controller.session = Session()
        controller.trace = None
        controller._closed = False
        controller._capture_invalidated = False
        controller._microphone_muted = False
        controller._microphone_generation = 0
        controller._microphone_track = object()
        controller._audio_task = None
        model = Model()
        controller._vad_model = model

        controller._start_microphone_consumer(controller._microphone_track)
        await asyncio.wait_for(starts[0].wait(), 0.5)
        controller._microphone_muted = True
        old = controller._retire_microphone_consumer()
        assert old is not None
        await asyncio.gather(old, return_exceptions=True)

        controller._microphone_muted = False
        controller._start_microphone_consumer(controller._microphone_track)
        await asyncio.wait_for(starts[1].wait(), 0.5)
        controller._microphone_muted = True
        fresh = controller._retire_microphone_consumer()
        assert fresh is not None
        await asyncio.gather(fresh, return_exceptions=True)

        self.assertEqual(endpoint_models, [model, model])
        self.assertEqual(stream_count, 2)
        self.assertGreaterEqual(model.reset_calls, 4)
        self.assertEqual(controller.session.failures, [])

    async def test_current_generation_setup_failure_fails_closed_with_trace(self) -> None:
        runtime = load_runtime()

        class Model:
            def reset(self) -> None:
                raise OSError("vad reset failed")

        class Session:
            stream_epoch = 4

            def __init__(self) -> None:
                self.failures: list[tuple[str, str]] = []

            async def fail(self, stage: str, code: str) -> None:
                self.failures.append((stage, code))

        class Trace:
            def __init__(self) -> None:
                self.events: list[tuple[str, str, dict[str, object], int]] = []

            def emit(self, stage, event, fields, *, stream_epoch) -> None:
                self.events.append((stage, event, fields, stream_epoch))

        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.session_id = "session-test"
        controller.session = Session()
        controller.trace = Trace()
        controller._closed = False
        controller._capture_invalidated = False
        controller._microphone_muted = False
        controller._microphone_generation = 1
        controller._vad_model = Model()
        task = asyncio.create_task(controller._consume_microphone(object(), 1))
        controller._audio_task = task

        await asyncio.wait_for(task, 0.5)

        self.assertEqual(
            controller.session.failures,
            [("input", "microphone_stream_failed")],
        )
        self.assertEqual(len(controller.trace.events), 1)
        stage, event, fields, stream_epoch = controller.trace.events[0]
        self.assertEqual((stage, event, stream_epoch), ("input", "microphone_failed", 4))
        self.assertEqual(fields["failure_class"], "OSError")
        self.assertEqual(fields["failure_code"], "microphone_stream_failed")

    async def test_livekit_mute_events_retire_then_resume_one_fresh_consumer(self) -> None:
        runtime = load_runtime()
        old_cancelled = asyncio.Event()
        fresh_started = asyncio.Event()
        fresh_consumers = 0

        class Room:
            def __init__(self) -> None:
                self.handlers: dict[str, list] = {}

            def on(self, event: str, callback=None):
                def register(handler):
                    self.handlers.setdefault(event, []).append(handler)
                    return handler

                return register(callback) if callback is not None else register

            def emit(self, event: str, *arguments) -> None:
                for handler in self.handlers.get(event, []):
                    handler(*arguments)

        async def old_consumer() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                old_cancelled.set()

        async def fresh_consumer(_track, _generation: int) -> None:
            nonlocal fresh_consumers
            fresh_consumers += 1
            fresh_started.set()
            await asyncio.Event().wait()

        controller = runtime.LiveKitRoomController.__new__(runtime.LiveKitRoomController)
        controller.room = Room()
        controller.session_id = "session-test"
        controller.browser_identity = "browser-session-test"
        controller._control_task = None
        controller._control_queue = asyncio.Queue()
        controller._closed = False
        controller._browser_ready = True
        controller._browser_join_task = None
        controller._capture_invalidated = False
        controller._microphone_muted = False
        controller._microphone_generation = 1
        controller._microphone_resume_task = None
        track = types.SimpleNamespace(kind=runtime.rtc.TrackKind.KIND_AUDIO)
        controller._microphone_track = track
        controller._microphone_publication_id = "microphone-publication"
        controller._audio_task = asyncio.create_task(old_consumer())
        controller._consume_microphone = fresh_consumer
        controller._register_handlers()
        await asyncio.sleep(0)
        participant = types.SimpleNamespace(identity=controller.browser_identity)
        publication = types.SimpleNamespace(
            sid="microphone-publication",
            source=runtime.rtc.TrackSource.SOURCE_MICROPHONE,
            muted=True,
            track=track,
        )

        controller.room.emit("track_muted", participant, publication)
        await asyncio.wait_for(old_cancelled.wait(), 0.5)

        self.assertTrue(controller._microphone_muted)
        self.assertEqual(controller._microphone_generation, 2)

        publication.muted = False
        controller.room.emit("track_unmuted", participant, publication)
        controller.room.emit("track_unmuted", participant, publication)
        await asyncio.wait_for(fresh_started.wait(), 0.5)
        await asyncio.sleep(0)

        self.assertFalse(controller._microphone_muted)
        self.assertEqual(fresh_consumers, 1)
        self.assertIs(controller._microphone_track, track)
        self.assertEqual(controller._microphone_publication_id, publication.sid)

        for task in (controller._audio_task, controller._microphone_resume_task, controller._control_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (
                controller._audio_task,
                controller._microphone_resume_task,
                controller._control_task,
            ) if task is not None),
            return_exceptions=True,
        )

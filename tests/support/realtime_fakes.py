from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import types
import unittest

from voice_agent_v2.contracts import (
    EventEnvelope,
    LLM_VERSION,
    STT_VERSION,
)
from voice_agent_v2.v2_audio import OUTPUT_DELIVERY_BLOCK_BYTES, TTS_OUTPUT_AUDIO_FORMAT
from voice_agent_v2.v2_contracts import TTS_V2_VERSION
from voice_agent_v2.real_turn import RealTurnController
import voice_agent_v2.realtime as realtime_module
from voice_agent_v2.realtime import RealtimeSession
from voice_agent_v2.tracer import TraceResult


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def send(self, event: dict[str, object]) -> None:
        self.events.append(event)


class StreamingRunner:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, trace_observer, retain_output
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Тест."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Ответ."})
        emit("turn.speaking", {"stage": "tts"})
        audio_observer(0, b"\0\0" * 320)
        emit("tts.audio", {"chunk_index": 0, "byte_count": 640})
        emit("llm.final", {"response": "Ответ."})
        emit("turn.completed", {"outcome": "completed", "output_bytes": 640}, True)
        return TraceResult(tuple(events), b"", b"")

    def cancel(self) -> None:
        self.cancelled.set()

    def discard_turn(self, _session_id: str, _turn_id: str) -> None:
        return None

    def turn_delivered(self, _session_id: str, _turn_id: str) -> None:
        return None


class ReconnectRunner(StreamingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.resets = 0
        self.cancellations = 0

    def cancel(self) -> None:
        self.cancellations += 1
        super().cancel()

    def reset_session(self, _session_id: str) -> None:
        self.resets += 1


class MemoryAudio:
    def __init__(
        self, *, write_delay: float = 0, operations: list[str] | None = None
    ) -> None:
        self.write_delay = write_delay
        self.operations = operations
        self.chunks: list[bytes] = []
        self.cleared: list[str] = []
        self.abandoned: list[str] = []

    async def write(self, _turn_id: str, pcm: bytes, cancelled) -> bool:
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        if cancelled():
            return False
        self.chunks.append(pcm)
        if self.operations is not None:
            self.operations.append("pcm-submitted")
        return True

    async def abandon(self, turn_id: str) -> str | None:
        self.abandoned.append(turn_id)
        return "persistent-publication"

    async def clear(self, turn_id: str) -> str | None:
        self.cleared.append(turn_id)
        return "persistent-publication"


class FailingPrepareAudio(MemoryAudio):
    async def prepare(self, _turn_id: str, _media_generation: int) -> str:
        raise RuntimeError("publication unavailable")


class InvalidPrepareAudio(MemoryAudio):
    async def prepare(self, _turn_id: str, _media_generation: int) -> str:
        return ""


class CapacitySTT:
    version = STT_VERSION

    def transcribe(self, **_arguments) -> str:
        return "Проверочный вопрос."


class CapacityLLM:
    version = LLM_VERSION
    provider_mode = "local"
    provider_identity = "deterministic-local"
    supports_visible_handoff = True
    visible_handoff_is_cumulative = True

    def respond_with_handoff(self, *, on_sentence, on_visible_sentence, **_arguments) -> str:
        pieces = (
            "Первое достаточно длинное предложение полностью готово для синтеза. ",
            "Второе достаточно длинное предложение полностью готово для синтеза. ",
            "Третье достаточно длинное предложение полностью готово для синтеза.",
        )
        cumulative = ""
        for piece in pieces:
            cumulative += piece
            on_visible_sentence(cumulative)
            on_sentence(piece)
        return cumulative


class CapacityTTS:
    version = TTS_V2_VERSION
    output_format = TTS_OUTPUT_AUDIO_FORMAT
    capabilities = {"cooperative_cancel": True}

    def __init__(self) -> None:
        self.started = [threading.Event() for _ in range(3)]

    def stream_synthesize(self, *, segment_index: int, **_arguments):
        self.started[segment_index].set()
        yield bytes([segment_index, 0]) * (
            OUTPUT_DELIVERY_BLOCK_BYTES * 4 // 2
        )


class CapacityAudio(MemoryAudio):
    def __init__(self) -> None:
        super().__init__()
        self.started = [threading.Event() for _ in range(12)]
        self.release = [asyncio.Event() for _ in range(12)]
        self.write_index = 0

    async def write(self, _turn_id: str, pcm: bytes, cancelled) -> bool:
        index = self.write_index
        self.write_index += 1
        self.started[index].set()
        await self.release[index].wait()
        if cancelled():
            return False
        self.chunks.append(pcm)
        return True


class BurstStreamingRunner(StreamingRunner):
    def __init__(self, operations: list[str]) -> None:
        super().__init__()
        self.operations = operations

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, trace_observer, retain_output
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Тест."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Потоковый ответ."})
        emit("turn.speaking", {"stage": "tts"})
        for index in range(5):
            audio_observer(index, bytes([index, 0]) * 2_880)
            emit("tts.audio", {"chunk_index": index, "byte_count": 5_760})
        self.operations.append("synthesis-final")
        emit("llm.final", {"response": "Потоковый ответ."})
        emit("turn.completed", {"outcome": "completed", "output_bytes": 28_800}, True)
        return TraceResult(tuple(events), b"", b"")


class BoundedPumpRunner(StreamingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.chunk_started = [threading.Event() for _ in range(3)]
        self.chunk_returned = [threading.Event() for _ in range(3)]
        self.producer_final = threading.Event()

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, cancellation, trace_observer, retain_output
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Тест."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Потоковый ответ."})
        emit("turn.speaking", {"stage": "tts"})
        for index in range(3):
            self.chunk_started[index].set()
            audio_observer(index, bytes([index, 0]) * 8_640)
            self.chunk_returned[index].set()
            emit("tts.audio", {"chunk_index": index, "byte_count": 17_280})
        self.producer_final.set()
        emit("llm.final", {"response": "Потоковый ответ."})
        emit("turn.completed", {"outcome": "completed", "output_bytes": 51_840}, True)
        return TraceResult(tuple(events), b"", b"")


class ControlledAudio(MemoryAudio):
    def __init__(self) -> None:
        super().__init__()
        self.started = [threading.Event() for _ in range(9)]
        self.release = [asyncio.Event() for _ in range(9)]
        self.write_index = 0

    async def write(self, _turn_id: str, pcm: bytes, cancelled) -> bool:
        index = self.write_index
        self.write_index += 1
        self.started[index].set()
        await self.release[index].wait()
        if cancelled():
            return False
        self.chunks.append(pcm)
        return True


class LateTTSFailureRunner(StreamingRunner):
    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, cancellation, trace_observer, retain_output
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Вопрос."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Доставленный префикс."})
        emit("turn.speaking", {"stage": "tts"})
        audio_observer(0, b"\0\0" * 320)
        emit("tts.audio", {"chunk_index": 0, "byte_count": 640})
        emit("turn.failed", {
            "outcome": "failed", "stage": "tts", "code": "selected_tts_unavailable",
        }, True)
        return TraceResult(tuple(events), b"", b"")


class VisibleTTSFailureRunner(StreamingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.visible_failed: list[tuple[str, str]] = []

    def retain_visible_failed_turn(self, session_id: str, turn_id: str) -> None:
        self.visible_failed.append((session_id, turn_id))

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, cancellation, audio_observer, trace_observer, retain_output
        events: list[dict[str, object]] = []
        for event_type, payload, terminal in (
            ("turn.transcribing", {"stage": "stt"}, False),
            ("stt.final", {"transcript": "Вопрос."}, False),
            ("turn.thinking", {"stage": "llm_provider"}, False),
            ("llm.visible", {"response": "Сохранённый ответ."}, False),
            ("turn.failed", {
                "outcome": "failed", "stage": "tts",
                "code": "selected_tts_unavailable",
            }, True),
        ):
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)
        return TraceResult(tuple(events), b"", b"")


class CheckpointARealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_publication_preparation_failures_fail_and_close_the_session(self) -> None:
        cases = (
            (FailingPrepareAudio(), "publication unavailable", "audio_publication_unavailable"),
            (InvalidPrepareAudio(), "no bounded identity", "audio_publication_identity_invalid"),
        )
        for index, (audio, message, code) in enumerate(cases, start=1):
            with self.subTest(code=code):
                events = MemoryEvents()
                session = RealtimeSession(
                    session_id=f"session-publication-{index}",
                    runner=StreamingRunner(),
                    event_sink=events,
                    audio_sink=audio,
                )

                with self.assertRaisesRegex(RuntimeError, message):
                    await session.submit_utterance(b"\0\0" * 320)

                self.assertEqual(
                    [event["type"] for event in events.events],
                    ["turn.failed", "session.degraded"],
                )
                failed = events.events[0]["payload"]
                self.assertEqual(failed["stage"], "publication")
                self.assertEqual(failed["code"], code)
                self.assertEqual(failed["failure_matrix_id"], "livekit_unavailable")
                self.assertEqual(failed["dependency_class"], "hard")
                self.assertFalse(failed["admit_turn"])
                self.assertEqual(failed["turn_admission_count"], 1)
                self.assertEqual(failed["turn_failure_count"], 1)
                self.assertTrue(session.closed)
                self.assertFalse(await session.ready())
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    await session.start_utterance()
                self.assertIsNone(session.active_turn_id)
                self.assertEqual(audio.abandoned, ["turn-00000001"])

    async def test_streamed_turn_completes_without_browser_media_controls(self) -> None:
        events = MemoryEvents()
        audio = MemoryAudio()
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

        event_types = [event["type"] for event in events.events]
        self.assertEqual(event_types[-1], "turn.completed")
        self.assertNotIn("turn.playout-ready", event_types)
        self.assertNotIn("turn.playout-retired", event_types)
        self.assertEqual(audio.chunks, [b"\0\0" * 320])
        completed = events.events[-1]["payload"]
        self.assertIn("endpoint_to_first_visible_ms", completed)
        self.assertIn("endpoint_to_first_accepted_pcm_ms", completed)

    async def test_bounded_pcm_pump_streams_before_synthesis_finishes(self) -> None:
        events = MemoryEvents()
        operations: list[str] = []
        audio = MemoryAudio(write_delay=0.01, operations=operations)
        session = RealtimeSession(
            session_id="session-test",
            runner=BurstStreamingRunner(operations),
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 1)

        event_types = [event["type"] for event in events.events]
        self.assertLess(event_types.index("turn.speaking"), event_types.index("turn.completed"))
        self.assertEqual(len(audio.chunks), 5)
        self.assertLess(operations.index("pcm-submitted"), operations.index("synthesis-final"))
        self.assertLessEqual(events.events[-1]["payload"]["server_pcm_queue_max_blocks"], 2)

    async def test_queue_blocks_third_block_and_completion_waits_for_last_write(self) -> None:
        events = MemoryEvents()
        audio = ControlledAudio()
        runner = BoundedPumpRunner()
        session = RealtimeSession(
            session_id="session-test",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        try:
            self.assertTrue(await asyncio.to_thread(audio.started[0].wait, 0.5))
            self.assertTrue(await asyncio.to_thread(runner.chunk_returned[1].wait, 0.5))
            self.assertTrue(await asyncio.to_thread(runner.chunk_started[2].wait, 0.5))
            await asyncio.sleep(0.05)
            self.assertFalse(runner.chunk_returned[2].is_set())

            audio.release[0].set()
            self.assertTrue(await asyncio.to_thread(runner.chunk_returned[2].wait, 0.5))
            self.assertTrue(await asyncio.to_thread(runner.producer_final.wait, 0.5))
            audio.release[1].set()
            self.assertTrue(await asyncio.to_thread(audio.started[2].wait, 0.5))
            self.assertNotIn("turn.completed", [event["type"] for event in events.events])
        finally:
            for release in audio.release:
                release.set()
            await asyncio.wait_for(session.wait_for_cleanup(), 1)

        self.assertEqual(len(audio.chunks), 9)
        self.assertEqual(events.events[-1]["type"], "turn.completed")
        self.assertEqual(events.events[-1]["payload"]["server_pcm_queue_max_blocks"], 2)
        self.assertLessEqual(
            events.events[-1]["payload"]["server_segment_queue_max_segments"], 2
        )

    async def test_segment_capacity_is_reserved_before_synthesis(self) -> None:
        events = MemoryEvents()
        audio = CapacityAudio()
        tts = CapacityTTS()
        session = RealtimeSession(
            session_id="session-test",
            runner=RealTurnController(CapacitySTT(), CapacityLLM(), tts),
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        try:
            self.assertTrue(await asyncio.to_thread(audio.started[0].wait, 0.5))
            self.assertTrue(await asyncio.to_thread(tts.started[1].wait, 0.5))
            await asyncio.sleep(0.05)
            self.assertFalse(tts.started[2].is_set())

            audio.release[0].set()
            self.assertTrue(await asyncio.to_thread(audio.started[1].wait, 0.5))
            audio.release[1].set()
            self.assertTrue(await asyncio.to_thread(tts.started[2].wait, 0.5))
        finally:
            for release in audio.release:
                release.set()
            await asyncio.wait_for(session.wait_for_cleanup(), 1)

        self.assertEqual(events.events[-1]["type"], "turn.completed")
        self.assertEqual(
            events.events[-1]["payload"]["server_segment_queue_max_segments"], 2
        )

    async def test_stale_request_tagged_pcm_is_dropped_before_sink_write(self) -> None:
        events = MemoryEvents()
        audio = MemoryAudio()
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=events,
            audio_sink=audio,
        )
        stale = realtime_module.TurnContext(
            turn_id="turn-stale",
            cancellation=realtime_module.CancellationToken(),
            endpoint_monotonic=0,
        )
        current = realtime_module.TurnContext(
            turn_id="turn-current",
            cancellation=realtime_module.CancellationToken(),
            endpoint_monotonic=0,
        )
        session._active = current

        await session._relay_queued_pcm(realtime_module.PcmPumpItem(stale, 0, b"\0\0" * 320))

        self.assertEqual(audio.chunks, [])
        self.assertEqual(current.audio_chunk_sequence, 0)
        self.assertEqual(session.drop_counts["stale_event"], 1)

    async def test_tts_failure_retains_visible_prefix_without_degrading_session(self) -> None:
        events = MemoryEvents()
        audio = MemoryAudio()
        runner = VisibleTTSFailureRunner()
        session = RealtimeSession(
            session_id="session-test",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

        self.assertEqual(
            [event["type"] for event in events.events],
            ["turn.listening", "turn.media-ready", "stt.final", "turn.thinking", "llm.visible", "turn.failed"],
        )
        self.assertEqual(events.events[-2]["payload"]["response"], "Сохранённый ответ.")
        self.assertEqual(events.events[-1]["payload"]["stage"], "tts")
        self.assertEqual(audio.cleared, [])
        self.assertEqual(runner.visible_failed, [("session-test", "turn-00000001")])
        self.assertFalse(session._failure_reported)

    async def test_late_tts_failure_preserves_accepted_pcm_prefix(self) -> None:
        events = MemoryEvents()
        audio = MemoryAudio()
        session = RealtimeSession(
            session_id="session-test",
            runner=LateTTSFailureRunner(),
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

        self.assertEqual(audio.chunks, [b"\0\0" * 320])
        self.assertEqual(audio.cleared, [])
        self.assertEqual(audio.abandoned, ["turn-00000001"])
        self.assertEqual(events.events[-1]["type"], "turn.failed")
        self.assertEqual(events.events[-1]["payload"]["stage"], "tts")

    async def test_reconnect_retry_replays_ack_without_repeating_reset(self) -> None:
        events = MemoryEvents()
        audio = MemoryAudio()
        runner = ReconnectRunner()
        input_resets = 0

        async def reset_input() -> None:
            nonlocal input_resets
            input_resets += 1

        session = RealtimeSession(
            session_id="session-test",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
            reconnect_reset_handler=reset_input,
        )
        previous = realtime_module.TurnContext(
            turn_id="turn-00000001",
            cancellation=realtime_module.CancellationToken(),
            endpoint_monotonic=0,
        )
        session._active = previous
        request = json.dumps({
            "schema_version": "voice-agent.client-control.v1",
            "session_id": "session-test",
            "stream_epoch": 1,
            "sequence": 1,
            "type": "client.reconnected",
        }).encode()

        self.assertTrue(await asyncio.wait_for(session.handle_client_control(request), 0.5))
        self.assertEqual(runner.resets, 1)
        self.assertEqual(runner.cancellations, 1)
        self.assertEqual(input_resets, 1)
        self.assertEqual(session.stream_epoch, 2)
        self.assertEqual(audio.cleared, ["turn-00000001"])
        self.assertEqual(
            [event["type"] for event in events.events],
            ["session.reconnected", "session.ready"],
        )

        events.events.clear()  # The first reliable data delivery was not observed by the browser.
        self.assertTrue(await asyncio.wait_for(session.handle_client_control(request), 0.5))

        self.assertEqual(runner.resets, 1)
        self.assertEqual(runner.cancellations, 1)
        self.assertEqual(input_resets, 1)
        self.assertEqual(session.stream_epoch, 2)
        self.assertEqual(audio.cleared, ["turn-00000001"])
        self.assertEqual(
            [event["type"] for event in events.events],
            ["session.reconnected", "session.ready"],
        )
        self.assertTrue(all(event["stream_epoch"] == 2 for event in events.events))

    async def test_removed_media_ack_is_rejected_without_affecting_session(self) -> None:
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
        )
        removed = json.dumps({
            "schema_version": "voice-agent.client-control.v1",
            "session_id": "session-test",
            "turn_id": "turn-00000001",
            "stream_epoch": 1,
            "sequence": 1,
            "media_generation": 1,
            "type": "client.media-ready",
        }).encode()

        self.assertFalse(await session.handle_client_control(removed))
        self.assertEqual(session.drop_counts["client_control"], 1)
        self.assertFalse(session._closed)


def load_runtime():
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
    sys.modules.pop("voice_agent_v2.livekit_runtime", None)
    return importlib.import_module("voice_agent_v2.livekit_runtime")


class CheckpointBLiveKitTests(unittest.IsolatedAsyncioTestCase):
    async def test_sink_rechecks_freshness_before_every_48khz_frame(self) -> None:
        runtime = load_runtime()
        captured: list[dict[str, object]] = []
        stale = False

        class Source:
            async def capture_frame(self, frame) -> None:
                nonlocal stale
                captured.append(frame)
                stale = True

            def clear_queue(self) -> None:
                return None

            async def aclose(self) -> None:
                return None

        class Participant:
            async def publish_track(self, _track, _options):
                return types.SimpleNamespace(sid="publication-session")

            async def unpublish_track(self, _publication_id: str) -> None:
                return None

        runtime.rtc.AudioFrame = lambda **kwargs: kwargs
        runtime.rtc.LocalAudioTrack = types.SimpleNamespace(
            create_audio_track=lambda _name, source: source
        )
        runtime.rtc.TrackPublishOptions = lambda: types.SimpleNamespace()
        sink = runtime.LiveKitAudioSink(
            types.SimpleNamespace(local_participant=Participant()),
            Source(),
            lambda _source: None,
        )
        await sink.start()
        await sink.prepare("turn-one", 1)

        accepted = await sink.write(
            "turn-one", b"\0\0" * 2_880, lambda: stale, media_generation=1
        )

        self.assertFalse(accepted)
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(captured[0]["data"]), 1_920)
        self.assertEqual(captured[0]["sample_rate"], 48_000)
        await sink.close()

    async def test_one_publication_serves_multiple_turns_until_close(self) -> None:
        runtime = load_runtime()
        calls: list[tuple[str, object]] = []

        class Source:
            async def capture_frame(self, frame) -> None:
                calls.append(("capture", frame))

            def clear_queue(self) -> None:
                calls.append(("clear", "queue"))

            async def aclose(self) -> None:
                calls.append(("close", "source"))

        class Participant:
            async def publish_track(self, _track, _options):
                calls.append(("publish", "track"))
                return types.SimpleNamespace(sid="publication-session")

            async def unpublish_track(self, publication_id: str) -> None:
                calls.append(("unpublish", publication_id))

        runtime.rtc.AudioFrame = lambda **kwargs: kwargs
        runtime.rtc.LocalAudioTrack = types.SimpleNamespace(
            create_audio_track=lambda _name, source: source
        )
        runtime.rtc.TrackPublishOptions = lambda: types.SimpleNamespace()
        source = Source()
        sink = runtime.LiveKitAudioSink(
            types.SimpleNamespace(local_participant=Participant()),
            source,
            lambda _source: None,
        )

        await sink.start()
        publication = await sink.prepare("turn-one", 1)
        self.assertEqual(publication, "publication-session")
        self.assertTrue(await sink.write(
            "turn-one", b"\0\0" * 2_880, lambda: False, media_generation=1
        ))
        self.assertTrue(await sink.finish("turn-one", lambda: False, media_generation=1))
        await sink.prepare("turn-two", 2)
        self.assertTrue(await sink.write(
            "turn-two", b"\0\0" * 1_000, lambda: False, media_generation=2
        ))
        self.assertTrue(await sink.finish("turn-two", lambda: False, media_generation=2))
        final_original = sink.submitted_bytes
        final_padding = sink.padded_bytes
        final_frames = sink.submitted_frames
        await sink.clear("turn-two")
        await sink.close()

        self.assertEqual([call for call in calls if call[0] == "publish"], [("publish", "track")])
        self.assertEqual(
            [call for call in calls if call[0] == "unpublish"],
            [("unpublish", "publication-session")],
        )
        captured = [call[1] for call in calls if call[0] == "capture"]
        self.assertEqual(len(captured), 5)
        self.assertTrue(all(frame["sample_rate"] == 48_000 for frame in captured))
        self.assertTrue(all(frame["num_channels"] == 1 for frame in captured))
        self.assertTrue(all(frame["samples_per_channel"] == 960 for frame in captured))
        self.assertTrue(all(len(frame["data"]) == 1_920 for frame in captured))
        self.assertEqual((final_original, final_padding, final_frames), (2_000, 1_840, 2))


if __name__ == "__main__":
    unittest.main()

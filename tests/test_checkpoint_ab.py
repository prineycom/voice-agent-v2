from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import types
import unittest

from voice_agent_v2.contracts import EventEnvelope, valid_correlation_id
from voice_agent_v2.local_tts import Qwen3TTS
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
            audio_observer(index, bytes([index, 0]) * 320)
            emit("tts.audio", {"chunk_index": index, "byte_count": 640})
        self.operations.append("synthesis-final")
        emit("llm.final", {"response": "Потоковый ответ."})
        emit("turn.completed", {"outcome": "completed", "output_bytes": 3_200}, True)
        return TraceResult(tuple(events), b"", b"")


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
        session = RealtimeSession(
            session_id="session-test",
            runner=VisibleTTSFailureRunner(),
            event_sink=events,
            audio_sink=audio,
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

        self.assertEqual(
            [event["type"] for event in events.events],
            ["turn.listening", "stt.final", "turn.thinking", "llm.visible", "turn.failed"],
        )
        self.assertEqual(events.events[-2]["payload"]["response"], "Сохранённый ответ.")
        self.assertEqual(events.events[-1]["payload"]["stage"], "tts")
        self.assertEqual(audio.cleared, [])
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


class CheckpointBWarmupTests(unittest.TestCase):
    def test_real_public_synthesis_warmup_is_discarded_and_keeps_process(self) -> None:
        class FakeResidentQwen(Qwen3TTS):
            def __init__(self) -> None:
                self.pid = 4242
                self.requests: list[tuple[str, str]] = []

            @property
            def process_id(self) -> int | None:
                return self.pid

            def start(self, cancellation=None) -> dict:
                del cancellation
                return {"speaker": "ryan"}

            def stream_synthesize(self, **arguments):
                session_id = arguments["session_id"]
                turn_id = arguments["turn_id"]
                if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
                    raise AssertionError("warm-up bypassed the public correlation contract")
                self.requests.append((session_id, turn_id))
                yield b"\0\0" * 320
                yield b"\1\0" * 320

        tts = FakeResidentQwen()
        metadata = tts.warmup()
        later = tuple(tts.stream_synthesize(
            session_id="session-test",
            turn_id="turn-test",
            text="Ответ.",
            audio_format=tts.output_format,
        ))

        self.assertEqual(metadata, {
            "process_id": 4242,
            "chunk_count": 2,
            "output_bytes": 1280,
            "discarded": True,
        })
        self.assertEqual(tts.process_id, 4242)
        self.assertEqual(tts.requests[0], ("warmup-session", "warmup-turn"))
        self.assertEqual(len(later), 2)


class CheckpointBLiveKitTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertTrue(await sink.write("turn-one", b"\0\0" * 320, lambda: False))
        self.assertTrue(await sink.finish("turn-one", lambda: False))
        self.assertTrue(await sink.write("turn-two", b"\0\0" * 320, lambda: False))
        await sink.clear("turn-two")
        await sink.close()

        self.assertEqual([call for call in calls if call[0] == "publish"], [("publish", "track")])
        self.assertEqual(
            [call for call in calls if call[0] == "unpublish"],
            [("unpublish", "publication-session")],
        )
        self.assertEqual(len([call for call in calls if call[0] == "capture"]), 2)


if __name__ == "__main__":
    unittest.main()

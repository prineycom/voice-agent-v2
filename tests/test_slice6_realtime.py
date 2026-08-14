from __future__ import annotations

import asyncio
import threading
import time
import unittest

from tests.test_checkpoint_ab import (
    CheckpointARealtimeTests,
    MemoryAudio,
    MemoryEvents,
    StreamingRunner,
)
from voice_agent_v2.contracts import EventEnvelopeV2
from voice_agent_v2.realtime import (
    CONTROL_EVENT_VERSION,
    ControlEventGate,
    EnergyEndpoint,
    RealtimeSession,
)
from voice_agent_v2.tracer import TraceResult


class UnannouncedEndpointCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_abandoned_vad_candidate_emits_no_user_turn(self) -> None:
        events = MemoryEvents()
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=events,
            audio_sink=MemoryAudio(),
        )

        turn_id = await session.start_utterance(announce=False)
        self.assertEqual(events.events, [])
        self.assertTrue(await session.abandon_unannounced_utterance(turn_id))
        self.assertEqual(events.events, [])
        self.assertIsNone(session.active_turn_id)

    async def test_cancelled_candidate_announcement_cannot_launch_a_turn(self) -> None:
        class BlockingEvents:
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def send(self, _event: dict[str, object]) -> None:
                self.started.set()
                await asyncio.Event().wait()

        events = BlockingEvents()
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=events,
            audio_sink=MemoryAudio(),
        )
        await session.start_utterance(announce=False)
        finish = asyncio.create_task(session.finish_utterance(b"\0\0" * 320))
        await asyncio.wait_for(events.started.wait(), 0.5)

        finish.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await finish

        self.assertIsNone(session.active_turn_id)
        self.assertIsNone(session._active)


class OverlapRunner:
    def __init__(self) -> None:
        self.tts = type("TTS", (), {"version": "voice-agent.tts.v2"})()
        self.old_started = threading.Event()
        self.old_release = threading.Event()
        self.replacement_started = threading.Event()
        self.cancelled_keys: list[tuple[str, int, str, int]] = []
        self.drain_threads: list[threading.Thread] = []

    def ready_for_admission(self) -> bool:
        return True

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, stream_epoch, turn_generation, request_id,
        trace_observer=None, retain_output=True,
    ) -> TraceResult:
        del input_pcm, retain_output
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            event = EventEnvelopeV2(
                session_id=session_id,
                turn_id=turn_id,
                stream_epoch=stream_epoch,
                turn_generation=turn_generation,
                request_id=request_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        if turn_generation == 1:
            if trace_observer is not None:
                trace_observer("tts", "noncooperative_worker_started", {})

            def drain_worker() -> None:
                self.old_started.set()
                self.old_release.wait(2)
                if trace_observer is not None:
                    trace_observer("tts", "noncooperative_worker_finished", {})

            drain = threading.Thread(target=drain_worker)
            self.drain_threads.append(drain)
            drain.start()
            while not cancellation.cancelled:
                time.sleep(0.01)
            emit("turn.interrupted", {"outcome": "interrupted"}, True)
            return TraceResult(tuple(events), b"", b"")

        self.replacement_started.set()
        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Тест."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Новый ответ."})
        audio_observer(0, b"\0\0" * 2_880)
        emit("tts.audio", {"chunk_index": 0, "byte_count": 5_760})
        emit("llm.final", {"response": "Новый ответ."})
        emit("turn.completed", {"outcome": "completed", "output_bytes": 5_760}, True)
        return TraceResult(tuple(events), b"", b"")

    def cancel_turn(self, session_id, stream_epoch, turn_id, turn_generation) -> None:
        self.cancelled_keys.append((session_id, stream_epoch, turn_id, turn_generation))

    def cancel(self) -> None:
        raise AssertionError("global cancellation must not be used for replacement")

    def discard_turn(self, _session_id, _turn_id) -> None:
        return None

    def turn_delivered(self, _session_id, _turn_id) -> None:
        return None


class ObsoleteCurrentOverlapTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_does_not_wait_for_obsolete_noncooperative_call(self) -> None:
        runner = OverlapRunner()
        events = MemoryEvents()
        audio = MemoryAudio()
        session = RealtimeSession(
            session_id="session-overlap",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )
        first = await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.old_started.wait, 0.5))

        second = await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.replacement_started.wait, 0.2))
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)
        self.assertEqual(events.events[-1]["type"], "turn.completed")
        self.assertEqual(events.events[-1]["turn_id"], second)
        self.assertEqual(
            runner.cancelled_keys,
            [("session-overlap", 1, first, 1)],
        )
        self.assertEqual(len(audio.chunks), 1)
        self.assertIn(
            "turn.interrupted",
            [event["type"] for event in events.events if event["turn_id"] == first],
        )

        runner.old_release.set()
        await asyncio.to_thread(runner.drain_threads[0].join, 0.5)
        self.assertFalse(runner.drain_threads[0].is_alive())
        await asyncio.wait_for(
            asyncio.gather(*(task for task in tuple(session._turn_tasks))), 0.5
        )
        old_terminals = [
            event for event in events.events
            if event["turn_id"] == first and event["terminal"]
        ]
        self.assertEqual(len(old_terminals), 1)
        self.assertEqual(old_terminals[0]["type"], "turn.interrupted")


class CooperativeCleanupRunner(OverlapRunner):
    def __init__(self) -> None:
        super().__init__()
        self.cooperative_cleanup_started = threading.Event()

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, stream_epoch, turn_generation, request_id,
        trace_observer=None, retain_output=True,
    ) -> TraceResult:
        if turn_generation != 1:
            return super().run_turn(
                session_id=session_id,
                turn_id=turn_id,
                input_pcm=input_pcm,
                cancellation=cancellation,
                event_observer=event_observer,
                audio_observer=audio_observer,
                stream_epoch=stream_epoch,
                turn_generation=turn_generation,
                request_id=request_id,
                trace_observer=trace_observer,
                retain_output=retain_output,
            )
        del input_pcm, audio_observer, retain_output
        if trace_observer is not None:
            trace_observer("tts", "segment_completed", {})
        self.cooperative_cleanup_started.set()
        self.old_release.wait(2)
        event = EventEnvelopeV2(
            session_id=session_id,
            turn_id=turn_id,
            stream_epoch=stream_epoch,
            turn_generation=turn_generation,
            request_id=request_id,
            sequence=1,
            event_type="turn.interrupted",
            payload={"outcome": "interrupted"},
            terminal=True,
        ).as_dict()
        event_observer(event)
        return TraceResult((event,), b"", b"")


class CooperativeCleanupBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_waits_for_post_tts_cooperative_cleanup(self) -> None:
        runner = CooperativeCleanupRunner()
        session = RealtimeSession(
            session_id="session-cleanup",
            runner=runner,
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
        )
        await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(
            await asyncio.to_thread(runner.cooperative_cleanup_started.wait, 0.5)
        )

        replacement = asyncio.create_task(session.submit_utterance(b"\0\0" * 320))
        await asyncio.sleep(0.05)
        self.assertFalse(replacement.done())
        self.assertFalse(runner.replacement_started.is_set())

        runner.old_release.set()
        await asyncio.wait_for(replacement, 0.5)
        self.assertTrue(await asyncio.to_thread(runner.replacement_started.wait, 0.5))
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)


class RealtimeCheckpointTests(CheckpointARealtimeTests):
    """Checkpoint A behavior is the realtime regression contract."""


class ControlEventGateTests(unittest.TestCase):
    def event(
        self,
        sequence: int,
        event_type: str,
        *,
        turn_id: str = "turn-00000001",
        terminal: bool = False,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "schema_version": CONTROL_EVENT_VERSION,
            "session_id": "session-test",
            "turn_id": "session" if event_type.startswith("session.") else turn_id,
            "stream_epoch": 1,
            "turn_generation": 0 if event_type.startswith("session.") else 1,
            "request_id": "session" if event_type.startswith("session.") else "request-00000001",
            "media_generation": 0 if event_type.startswith("session.") else 1,
            "sequence": sequence,
            "type": event_type,
            "terminal": terminal,
            "payload": payload or {},
        }

    def test_wrong_turn_duplicate_and_removed_events_are_dropped(self) -> None:
        gate = ControlEventGate("session-test")
        self.assertTrue(gate.accept(self.event(1, "session.ready")))
        self.assertTrue(gate.accept(self.event(2, "turn.listening")))
        self.assertTrue(gate.accept(self.event(3, "turn.media-ready")))
        self.assertFalse(gate.accept(self.event(4, "stt.final", turn_id="turn-other")))
        self.assertTrue(gate.accept(self.event(4, "stt.final")))
        self.assertFalse(gate.accept(self.event(4, "turn.thinking")))
        self.assertFalse(gate.accept(self.event(5, "turn.playout-ready")))
        self.assertEqual(gate.drop_count, 3)

    def test_immediate_completion_after_server_pcm_is_admitted(self) -> None:
        gate = ControlEventGate("session-test")
        events = (
            self.event(1, "turn.listening"),
            self.event(2, "turn.media-ready"),
            self.event(3, "stt.final"),
            self.event(4, "turn.thinking"),
            self.event(5, "llm.visible"),
            self.event(6, "turn.speaking"),
            self.event(7, "turn.completed", terminal=True),
        )
        self.assertTrue(all(gate.accept(event) for event in events))


class BoundedEnergyEndpointTests(unittest.TestCase):
    def test_endpoint_emits_one_bounded_utterance(self) -> None:
        endpoint = EnergyEndpoint(threshold_rms=500)
        speech = (1000).to_bytes(2, "little", signed=True) * 320
        silence = b"\0\0" * 320
        signals: list[tuple[str, bytes | None]] = []
        for frame in [speech] * 12 + [silence] * 30:
            signals.extend(endpoint.feed(frame))
        self.assertEqual([kind for kind, _payload in signals], ["speech_started", "utterance"])
        self.assertGreater(len(signals[-1][1] or b""), 0)


if __name__ == "__main__":
    unittest.main()

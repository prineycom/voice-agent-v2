from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from tests.test_checkpoint_ab import (
    CapacityAudio,
    CheckpointARealtimeTests,
    MemoryAudio,
    MemoryEvents,
    StreamingRunner,
)
from voice_agent_v2.diagnostics import PrivacySafeTrace, TraceIdentity
from voice_agent_v2.observability import ResourceSnapshot, failure_payload, reconstruct_timelines
from voice_agent_v2.v2_audio import OUTPUT_DELIVERY_BLOCK_BYTES
from voice_agent_v2.v2_contracts import EventEnvelopeV2
from voice_agent_v2.realtime import (
    CONTROL_EVENT_VERSION,
    ControlEventGate,
    EnergyEndpoint,
    RealtimeSession,
)
from voice_agent_v2.tracer import TraceResult


class UnannouncedEndpointCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_observer_refusal_is_counted(self) -> None:
        session = RealtimeSession(
            session_id="session-test",
            runner=StreamingRunner(),
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
            trace_observer=lambda _stage, _event, _fields: False,
        )
        session._trace("control", "published", turn_id="turn-test")
        self.assertEqual(session.diagnostic_failure_counts["observer"], 1)

    async def test_context_commit_failure_uses_terminal_failure_metadata_boundary(self) -> None:
        class CommitFailingRunner(StreamingRunner):
            def turn_delivered(self, _session_id: str, _turn_id: str) -> None:
                raise RuntimeError("synthetic commit failure")

        events = MemoryEvents()
        observations: list[tuple[str, str, dict[str, object]]] = []
        session = RealtimeSession(
            session_id="session-commit-failure",
            runner=CommitFailingRunner(),
            event_sink=events,
            audio_sink=MemoryAudio(),
            trace_observer=lambda stage, event, fields: observations.append(
                (stage, event, dict(fields))
            ),
        )

        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

        failed = next(event for event in events.events if event["type"] == "turn.failed")
        self.assertEqual(failed["payload"]["outcome"], "failed")
        self.assertEqual(failed["payload"]["dependency_class"], "hard")
        self.assertEqual(failed["payload"]["failure_matrix_id"], "controller_failure")
        self.assertEqual(failed["payload"]["user_state"], "unavailable")
        self.assertEqual(failed["payload"]["turn_failure_count"], 1)
        published = next(
            fields for stage, event, fields in observations
            if stage == "control"
            and event == "published"
            and fields.get("event_type") == "turn.failed"
        )
        self.assertEqual(published["failure_stage"], "controller")
        self.assertEqual(published["failure_code"], "context_commit_failed")
        self.assertEqual(published["dependency_class"], "hard")
        self.assertEqual(published["failure_matrix_id"], "controller_failure")
        self.assertEqual(published["user_state"], "unavailable")

    async def test_publish_failure_records_correlated_enriched_failed_terminal(self) -> None:
        class FailingTerminalEvents(MemoryEvents):
            async def send(self, event: dict[str, object]) -> None:
                if event["type"] == "turn.completed":
                    raise RuntimeError("synthetic control transport failure")
                await super().send(event)

        with tempfile.TemporaryDirectory() as directory:
            events = FailingTerminalEvents()
            trace = PrivacySafeTrace(
                Path(directory) / "trace.jsonl",
                TraceIdentity("session-publish-failure"),
            )
            session = RealtimeSession(
                session_id="session-publish-failure",
                runner=StreamingRunner(),
                event_sink=events,
                audio_sink=MemoryAudio(),
                trace_observer=trace.observe,
            )

            await session.submit_utterance(b"\0\0" * 320)
            await asyncio.wait_for(session.wait_for_cleanup(), 0.5)

            records = [
                json.loads(line)
                for line in trace.path.read_text(encoding="utf-8").splitlines()
            ]
            timeline = reconstruct_timelines(records)[0]
            self.assertEqual(timeline.terminal_outcome, "failed")
            self.assertEqual(timeline.failure_stage, "transport")
            self.assertEqual(timeline.failure_code, "control_publish_failed")
            self.assertEqual(timeline.failure_matrix_id, "livekit_unavailable")
            self.assertEqual(timeline.dependency_class, "hard")
            self.assertEqual(timeline.user_state, "retrying")
            failed = next(
                record for record in records if record["event"] == "publish_failed"
            )
            self.assertEqual(failed["turn_id"], "turn-00000001")
            self.assertEqual(failed["fields"]["event_type"], "turn.failed")
            self.assertTrue(failed["fields"]["terminal"])
            self.assertEqual(failed["fields"]["failed_event_type"], "turn.completed")
            for name, value in failure_payload(
                "transport", "control_publish_failed"
            ).items():
                self.assertEqual(failed["fields"][name], value)
            self.assertEqual(failed["fields"]["turn_completion_count"], 0)
            self.assertEqual(failed["fields"]["turn_failure_count"], 1)
            self.assertNotIn("turn.completed", [event["type"] for event in events.events])

    async def test_interruption_publish_failure_replaces_terminal_counter(self) -> None:
        class FailingInterruptEvents(MemoryEvents):
            async def send(self, event: dict[str, object]) -> None:
                if event["type"] == "turn.interrupted":
                    raise RuntimeError("synthetic interruption publish failure")
                await super().send(event)

        session = RealtimeSession(
            session_id="session-interrupt-publish-failure",
            runner=StreamingRunner(),
            event_sink=FailingInterruptEvents(),
            audio_sink=MemoryAudio(write_delay=0.2),
        )
        await session.submit_utterance(b"\0\0" * 320)

        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            await session.interrupt()

        self.assertEqual(session.turn_counts["interrupted"], 0)
        self.assertEqual(session.turn_counts["failed"], 1)

    async def test_publication_failure_blocks_later_turn_admission(self) -> None:
        events = MemoryEvents()
        session = RealtimeSession(
            session_id="session-publication-failure",
            runner=StreamingRunner(),
            event_sink=events,
            audio_sink=MemoryAudio(),
        )

        await session.start_utterance()
        context = session._active
        self.assertIsNotNone(context)
        assert context is not None
        await session._terminate_failed_turn(
            context,
            "turn.failed",
            {"outcome": "failed", "stage": "publication", "code": "audio_stream_failed"},
        )

        failed = next(event for event in events.events if event["type"] == "turn.failed")
        self.assertEqual(failed["payload"]["failure_matrix_id"], "livekit_unavailable")
        self.assertFalse(failed["payload"]["admit_turn"])
        self.assertIn("session.degraded", [event["type"] for event in events.events])
        self.assertFalse(await session.ready())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await session.start_utterance()

    async def test_resource_sampling_never_blocks_endpoint_admission(self) -> None:
        class SlowSampler:
            def sample(self) -> ResourceSnapshot:
                time.sleep(0.25)
                return ResourceSnapshot(None, 1.0, 1.0, None, None)

        session = RealtimeSession(
            session_id="session-resource",
            runner=StreamingRunner(),
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
            resource_sampler=SlowSampler(),
        )
        await session.start_utterance(announce=False)
        started = time.monotonic()
        await session.finish_utterance(b"\0\0" * 320)
        self.assertLess(time.monotonic() - started, 0.1)
        await asyncio.sleep(0.6)
        self.assertFalse(session._diagnostic_tasks)

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


class CrossGenerationCapacityRunner(OverlapRunner):
    def __init__(self) -> None:
        super().__init__()
        self.complete_segment_capacity = asyncio.BoundedSemaphore(2)
        self.old_session_id: str | None = None
        self.replacement_first_started = threading.Event()
        self.replacement_second_started = threading.Event()

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer,
        audio_observer, stream_epoch, turn_generation, request_id,
        trace_observer=None, retain_output=True, segment_started_observer=None,
        segment_audio_observer=None,
    ) -> TraceResult:
        del input_pcm, audio_observer, retain_output
        assert segment_started_observer is not None
        assert segment_audio_observer is not None
        if (
            session_id == self.old_session_id
            if self.old_session_id is not None
            else turn_generation == 1
        ):
            segment_started_observer(0)
            if trace_observer is not None:
                trace_observer(
                    "tts", "noncooperative_worker_started", {"segment_index": 0}
                )

            def drain_worker() -> None:
                self.old_started.set()
                self.old_release.wait(2)
                if trace_observer is not None:
                    trace_observer(
                        "tts", "noncooperative_worker_finished", {"segment_index": 0}
                    )
                segment_audio_observer(0, None)

            drain = threading.Thread(target=drain_worker)
            self.drain_threads.append(drain)
            drain.start()
            while not cancellation.cancelled:
                time.sleep(0.01)
            if trace_observer is not None:
                trace_observer(
                    "llm_provider", "cooperative_cleanup_complete",
                    {"context_committed": True},
                )
            return TraceResult((), b"", b"")

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

        self.replacement_started.set()
        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Тест."})
        emit("turn.thinking", {"stage": "llm_provider"})
        emit("llm.visible", {"response": "Новый ответ."})
        emit("turn.speaking", {"stage": "tts"})
        segment_started_observer(0)
        self.replacement_first_started.set()
        first = b"\0\0" * (OUTPUT_DELIVERY_BLOCK_BYTES * 4 // 2)
        segment_audio_observer(0, first)
        emit("tts.audio", {"chunk_index": 0, "byte_count": len(first)})
        segment_started_observer(1)
        self.replacement_second_started.set()
        second = b"\0\0" * (OUTPUT_DELIVERY_BLOCK_BYTES // 2)
        segment_audio_observer(1, second)
        emit("tts.audio", {"chunk_index": 1, "byte_count": len(second)})
        emit("llm.final", {"response": "Новый ответ."})
        emit(
            "turn.completed",
            {"outcome": "completed", "output_bytes": len(first) + len(second)},
            True,
        )
        return TraceResult(tuple(events), b"", b"")


class CrossGenerationCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_obsolete_and_replacement_share_two_segment_capacity(self) -> None:
        runner = CrossGenerationCapacityRunner()
        audio = CapacityAudio()
        events = MemoryEvents()
        session = RealtimeSession(
            session_id="session-segment-capacity",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )

        first_turn = await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.old_started.wait, 0.5))
        second_turn = await session.submit_utterance(b"\0\0" * 320)
        try:
            self.assertTrue(
                await asyncio.to_thread(runner.replacement_first_started.wait, 0.5)
            )
            self.assertTrue(await asyncio.to_thread(audio.started[0].wait, 0.5))
            await asyncio.sleep(0.05)
            self.assertFalse(runner.replacement_second_started.is_set())

            runner.old_release.set()
            self.assertTrue(
                await asyncio.to_thread(runner.replacement_second_started.wait, 0.5)
            )
        finally:
            runner.old_release.set()
            for release in audio.release:
                release.set()
            for drain in runner.drain_threads:
                await asyncio.to_thread(drain.join, 0.5)
            await asyncio.wait_for(session.wait_for_cleanup(), 1)

        self.assertTrue(all(not drain.is_alive() for drain in runner.drain_threads))
        self.assertEqual(events.events[-1]["type"], "turn.completed")
        self.assertEqual(events.events[-1]["turn_id"], second_turn)
        self.assertIn(
            "turn.interrupted",
            [event["type"] for event in events.events if event["turn_id"] == first_turn],
        )

    async def test_replacement_session_cannot_mint_new_segment_permits(self) -> None:
        runner = CrossGenerationCapacityRunner()
        runner.old_session_id = "session-old-capacity"
        old_session = RealtimeSession(
            session_id=runner.old_session_id,
            runner=runner,
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
        )
        replacement_audio = CapacityAudio()
        replacement = RealtimeSession(
            session_id="session-new-capacity",
            runner=runner,
            event_sink=MemoryEvents(),
            audio_sink=replacement_audio,
        )

        await old_session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.old_started.wait, 0.5))
        await old_session.disconnect()
        await replacement.submit_utterance(b"\0\0" * 320)
        try:
            self.assertTrue(
                await asyncio.to_thread(runner.replacement_first_started.wait, 0.5)
            )
            self.assertTrue(
                await asyncio.to_thread(replacement_audio.started[0].wait, 0.5)
            )
            await asyncio.sleep(0.05)
            self.assertFalse(runner.replacement_second_started.is_set())

            runner.old_release.set()
            self.assertTrue(
                await asyncio.to_thread(runner.replacement_second_started.wait, 0.5)
            )
        finally:
            runner.old_release.set()
            for release in replacement_audio.release:
                release.set()
            for drain in runner.drain_threads:
                await asyncio.to_thread(drain.join, 0.5)
            await asyncio.wait_for(old_session.wait_for_cleanup(), 1)
            await asyncio.wait_for(replacement.wait_for_cleanup(), 1)

        self.assertTrue(all(not drain.is_alive() for drain in runner.drain_threads))


class SessionReadinessLossTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_ready_worker_loss_degrades_and_closes_session(self) -> None:
        class UnreadyRunner:
            def ready_for_admission(self) -> bool:
                return False

        events = MemoryEvents()
        failures: list[tuple[str, str]] = []
        session = RealtimeSession(
            session_id="session-ready-loss",
            runner=UnreadyRunner(),
            event_sink=events,
            audio_sink=MemoryAudio(),
            failure_handler=lambda stage, code: failures.append((stage, code)),
        )

        self.assertFalse(await session.ready())
        self.assertEqual(failures, [("tts", "tts_backend_not_ready")])
        self.assertEqual(len(events.events), 1)
        degraded = events.events[0]
        self.assertEqual(degraded["schema_version"], CONTROL_EVENT_VERSION)
        self.assertEqual(degraded["session_id"], "session-ready-loss")
        self.assertEqual(degraded["type"], "session.degraded")
        self.assertEqual(degraded["payload"]["stage"], "tts")
        self.assertEqual(degraded["payload"]["code"], "tts_backend_not_ready")
        self.assertEqual(degraded["payload"]["user_state"], "degraded")
        self.assertEqual(degraded["payload"]["failure_matrix_id"], "tts_failure")
        self.assertEqual(degraded["payload"]["health"]["overall_readiness"], "unready")
        tts_health = next(
            component for component in degraded["payload"]["health"]["components"]
            if component["component"] == "tts"
        )
        self.assertEqual(tts_health["liveness"], "dead")
        self.assertEqual(tts_health["readiness"], "unready")
        self.assertTrue(tts_health["compatible"])
        with self.assertRaisesRegex(RuntimeError, "session is closed"):
            await session.start_utterance()


class FinalSegmentOverlapRunner(OverlapRunner):
    def __init__(self) -> None:
        super().__init__()
        self.restored_turns: list[tuple[str, str]] = []

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
            trace_observer(
                "llm_provider", "cooperative_cleanup_complete",
                {"context_committed": True},
            )
            trace_observer("tts", "noncooperative_worker_started", {})
        self.old_started.set()
        self.old_release.wait(2)
        if trace_observer is not None:
            trace_observer("tts", "noncooperative_worker_finished", {})
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

    def discard_turn(self, session_id, turn_id) -> None:
        self.restored_turns.append((session_id, turn_id))


class FinalSegmentOverlapTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_detaches_only_after_llm_cleanup_and_restores_context(self) -> None:
        runner = FinalSegmentOverlapRunner()
        session = RealtimeSession(
            session_id="session-final-segment",
            runner=runner,
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
        )
        first = await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.old_started.wait, 0.5))

        second = await asyncio.wait_for(
            session.submit_utterance(b"\0\0" * 320), 0.5
        )
        self.assertTrue(await asyncio.to_thread(runner.replacement_started.wait, 0.2))
        self.assertEqual(runner.restored_turns, [("session-final-segment", first)])
        self.assertNotEqual(first, second)

        runner.old_release.set()
        await asyncio.wait_for(
            asyncio.gather(*(task for task in tuple(session._turn_tasks))), 0.5
        )
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)


class DelayedCooperativeCleanupRunner(OverlapRunner):
    def __init__(self) -> None:
        super().__init__()
        self.cooperative_cleanup_started = threading.Event()
        self.cooperative_cleanup_release = threading.Event()

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
        del input_pcm, event_observer, audio_observer, retain_output
        if trace_observer is not None:
            trace_observer("tts", "noncooperative_worker_started", {})
        self.old_started.set()
        while not cancellation.cancelled:
            time.sleep(0.005)
        self.cooperative_cleanup_started.set()
        self.cooperative_cleanup_release.wait(2)
        if trace_observer is not None:
            trace_observer(
                "llm_provider", "cooperative_cleanup_complete",
                {"context_committed": False},
            )
        self.old_release.wait(2)
        if trace_observer is not None:
            trace_observer("tts", "noncooperative_worker_finished", {})
        return TraceResult((), b"", b"")


class DelayedCooperativeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_detaches_after_delayed_cooperative_cleanup_signal(self) -> None:
        runner = DelayedCooperativeCleanupRunner()
        session = RealtimeSession(
            session_id="session-delayed-cleanup",
            runner=runner,
            event_sink=MemoryEvents(),
            audio_sink=MemoryAudio(),
        )
        await session.submit_utterance(b"\0\0" * 320)
        self.assertTrue(await asyncio.to_thread(runner.old_started.wait, 0.5))

        replacement = asyncio.create_task(session.submit_utterance(b"\0\0" * 320))
        try:
            self.assertTrue(
                await asyncio.to_thread(runner.cooperative_cleanup_started.wait, 0.5)
            )
            await asyncio.sleep(0.03)
            self.assertFalse(replacement.done())
            self.assertFalse(runner.replacement_started.is_set())

            runner.cooperative_cleanup_release.set()
            await asyncio.wait_for(replacement, 0.5)
            self.assertTrue(await asyncio.to_thread(runner.replacement_started.wait, 0.5))
            self.assertFalse(runner.old_release.is_set())
        finally:
            runner.cooperative_cleanup_release.set()
            runner.old_release.set()
            await asyncio.gather(replacement, return_exceptions=True)
            await asyncio.wait_for(session.wait_for_cleanup(), 0.5)


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

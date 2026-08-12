from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
import unittest
from pathlib import Path

from voice_agent_v2.audio import generated_output_pcm
from voice_agent_v2.contracts import EventEnvelope
from voice_agent_v2.realtime import (
    BARGE_IN_DRAIN_BOUND_MS,
    CLIENT_CONTROL_VERSION,
    CONTROL_EVENT_VERSION,
    ControlEventGate,
    EnergyEndpoint,
    RealtimeSession,
)
from voice_agent_v2.slice6_config import (
    Slice6ConfigurationError,
    Slice6Settings,
    app_origin_allowed,
    livekit_server_config,
)
from voice_agent_v2.tracer import TraceResult

ROOT = Path(__file__).resolve().parents[1]
RUN_SLICE6_SPEC = importlib.util.spec_from_file_location(
    "voice_agent_run_slice6", ROOT / "scripts" / "run_slice6.py"
)
assert RUN_SLICE6_SPEC is not None and RUN_SLICE6_SPEC.loader is not None
run_slice6 = importlib.util.module_from_spec(RUN_SLICE6_SPEC)
RUN_SLICE6_SPEC.loader.exec_module(run_slice6)


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def send(self, event: dict[str, object]) -> None:
        self.events.append(event)


class FailingEventSink(MemoryEventSink):
    def __init__(self, fail_type: str) -> None:
        super().__init__()
        self.fail_type = fail_type

    async def send(self, event: dict[str, object]) -> None:
        if event["type"] == self.fail_type:
            raise RuntimeError("control transport failed")
        await super().send(event)


class MemoryAudioSink:
    def __init__(
        self,
        *,
        block: bool = False,
        fail_play: bool = False,
        fail_clear: bool = False,
        block_clear: bool = False,
    ) -> None:
        self.block = block
        self.fail_play = fail_play
        self.fail_clear = fail_clear
        self.block_clear = block_clear
        self.started = asyncio.Event()
        self.released = asyncio.Event()
        self.cleared: list[str] = []
        self.played: list[str] = []

    async def play(self, turn_id, pcm, cancelled) -> bool:
        self.played.append(turn_id)
        self.started.set()
        if self.fail_play:
            raise RuntimeError("playout failed")
        if self.block:
            await self.released.wait()
        return bool(pcm) and not cancelled()

    async def clear(self, turn_id) -> None:
        self.cleared.append(turn_id)
        if self.fail_clear:
            raise RuntimeError("clear failed")
        if self.block_clear:
            await self.released.wait()
        self.released.set()


class FakeRunner:
    def __init__(self) -> None:
        self.cancel_count = 0
        self.cancelled = threading.Event()
        self.reset_sessions: list[str] = []
        self.discarded_turns: list[tuple[str, str]] = []

    def run_turn(
        self, *, session_id, turn_id, input_pcm, cancellation, event_observer
    ) -> TraceResult:
        del input_pcm
        events = []
        plan = (
            ("turn.transcribing", {"stage": "stt"}, False),
            ("stt.final", {"transcript": "Тестовая реплика."}, False),
            ("turn.thinking", {"stage": "llm_provider"}, False),
            ("llm.final", {"response": "Тестовый ответ."}, False),
            ("turn.speaking", {"stage": "tts"}, False),
            ("turn.completed", {"outcome": "completed"}, True),
        )
        for index, (event_type, payload, terminal) in enumerate(plan, 1):
            if cancellation.cancelled:
                event_type, payload, terminal = (
                    "turn.interrupted", {"outcome": "interrupted"}, True
                )
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=index,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)
            if terminal:
                break
        output = b"" if events[-1]["type"] == "turn.interrupted" else generated_output_pcm()
        return TraceResult(tuple(events), b"", output)

    def cancel(self) -> None:
        self.cancel_count += 1
        self.cancelled.set()

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        self.discarded_turns.append((session_id, turn_id))

    def reset_session(self, session_id: str) -> None:
        self.reset_sessions.append(session_id)


class DeliveryBlockingRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.delivery_entered = threading.Event()
        self.delivery_release = threading.Event()

    def turn_delivered(self, _session_id: str, _turn_id: str) -> None:
        self.delivery_entered.set()
        self.delivery_release.wait(2)


class CancellationRaceRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.rollback_done = threading.Event()
        self.context: list[str] = []
        self.operations: list[str] = []

    def run_turn(self, *, session_id, turn_id, input_pcm, cancellation, event_observer):
        del input_pcm
        self.entered.set()
        self.release.wait(2)
        self.context.append("late assistant response")
        self.operations.append("provider-write")
        event = EventEnvelope(
            session_id=session_id,
            turn_id=turn_id,
            sequence=1,
            event_type="turn.interrupted",
            payload={"outcome": "interrupted"},
            terminal=True,
        ).as_dict()
        event_observer(event)
        return TraceResult((event,), b"", b"")

    def cancel(self) -> None:
        super().cancel()
        self.operations.append("cancel")
        self.release.set()

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        super().discard_turn(session_id, turn_id)
        self.context.clear()
        self.operations.append("rollback")
        self.rollback_done.set()


class TransportFailureRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.context = ["prior context"]
        self.rollback_done = threading.Event()
        self.operations: list[str] = []

    def run_turn(self, *, session_id, turn_id, input_pcm, cancellation, event_observer):
        del input_pcm
        event = EventEnvelope(
            session_id=session_id,
            turn_id=turn_id,
            sequence=1,
            event_type="turn.transcribing",
            payload={"stage": "stt"},
            terminal=False,
        ).as_dict()
        event_observer(event)
        self.release.wait(2)
        self.context.append("late assistant response")
        return TraceResult((event,), b"", b"")

    def cancel(self) -> None:
        self.operations.append("cancel")
        super().cancel()
        self.release.set()

    def discard_turn(self, session_id: str, turn_id: str) -> None:
        self.operations.append("rollback")
        super().discard_turn(session_id, turn_id)
        self.context[:] = ["prior context"]
        self.rollback_done.set()


class OrderedAudioSink(MemoryAudioSink):
    def __init__(self, operations: list[str]) -> None:
        super().__init__()
        self.operations = operations

    async def clear(self, turn_id) -> None:
        self.operations.append("media-clear")
        await super().clear(turn_id)


class BlockingRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run_turn(self, *, session_id, turn_id, input_pcm, cancellation, event_observer):
        del input_pcm
        self.entered.set()
        self.release.wait(2)
        event = EventEnvelope(
            session_id=session_id,
            turn_id=turn_id,
            sequence=1,
            event_type="turn.interrupted" if cancellation.cancelled else "turn.failed",
            payload={"outcome": "interrupted" if cancellation.cancelled else "failed"},
            terminal=True,
        ).as_dict()
        event_observer(event)
        return TraceResult((event,), b"", b"")

    def cancel(self) -> None:
        super().cancel()
        self.release.set()


async def wait_for_turn(session: RealtimeSession) -> None:
    context = session._active
    if context is None or context.task is None:
        return
    for _ in range(200):
        if context.playout_ack is not None and not context.playout_ack.done():
            payload = json.dumps({
                "schema_version": CLIENT_CONTROL_VERSION,
                "session_id": session.session_id,
                "turn_id": context.turn_id,
                "stream_epoch": session.stream_epoch,
                "sequence": session._client_sequence + 1,
                "type": "client.playout-completed",
            }).encode()
            if await session.handle_client_control(payload):
                break
        if context.task.done():
            break
        await asyncio.sleep(0.01)
    await asyncio.wait_for(context.task, 2)


class RealtimeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_has_correlated_state_and_completion_after_audio(self) -> None:
        events = MemoryEventSink()
        audio = MemoryAudioSink()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=FakeRunner(),
            event_sink=events,
            audio_sink=audio,
        )
        await session.ready()
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await wait_for_turn(session)

        types = [event["type"] for event in events.events]
        self.assertEqual(types, [
            "session.ready", "turn.listening", "turn.transcribing", "stt.final",
            "turn.thinking", "llm.final", "turn.speaking", "turn.playout-ready",
            "turn.completed",
        ])
        self.assertEqual(audio.played, [turn_id])
        self.assertEqual([event["sequence"] for event in events.events], list(range(1, 10)))
        self.assertTrue(all(event["schema_version"] == CONTROL_EVENT_VERSION for event in events.events))
        self.assertEqual(events.events[-1]["turn_id"], turn_id)
        self.assertTrue(events.events[-1]["terminal"])

    async def test_barge_in_clears_old_playout_and_new_turn_has_no_stale_leakage(self) -> None:
        events = MemoryEventSink()
        audio = MemoryAudioSink(block=True)
        runner = FakeRunner()
        session = RealtimeSession(
            session_id="session-test-0001", runner=runner, event_sink=events, audio_sink=audio
        )
        old_turn = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(audio.started.wait(), 1)
        new_turn = await session.start_utterance()
        audio.block = False
        await session.finish_utterance(b"\0\0" * 320)
        await wait_for_turn(session)

        old_terminal = [
            event for event in events.events
            if event["turn_id"] == old_turn and event["terminal"]
        ]
        self.assertEqual([event["type"] for event in old_terminal], ["turn.interrupted"])
        self.assertEqual(audio.cleared, [old_turn])
        self.assertLessEqual(old_terminal[0]["payload"]["drain_ms"], BARGE_IN_DRAIN_BOUND_MS)
        self.assertEqual(
            [event["type"] for event in events.events if event["turn_id"] == new_turn][-1],
            "turn.completed",
        )
        new_start = next(
            index for index, event in enumerate(events.events)
            if event["turn_id"] == new_turn and event["type"] == "turn.listening"
        )
        self.assertFalse(any(
            event["turn_id"] == old_turn and event["type"] != "turn.interrupted"
            for event in events.events[new_start:]
        ))
        self.assertGreaterEqual(runner.cancel_count, 1)

    async def test_cancellation_waits_for_provider_before_context_rollback(self) -> None:
        events = MemoryEventSink()
        runner = CancellationRaceRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.to_thread(runner.entered.wait, 1)
        await session.interrupt()
        await asyncio.to_thread(runner.rollback_done.wait, 1)

        self.assertEqual(runner.context, [])
        self.assertEqual(runner.operations, ["cancel", "provider-write", "rollback"])
        self.assertEqual(runner.discarded_turns, [("session-test-0001", turn_id)])

    async def test_clear_failure_cancels_runner_and_degrades_session(self) -> None:
        events = MemoryEventSink()
        runner = FakeRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(fail_clear=True),
        )
        turn_id = await session.start_utterance()
        await session.interrupt()
        await asyncio.to_thread(runner.cancelled.wait, 1)

        self.assertEqual(
            [event["type"] for event in events.events if event["turn_id"] == turn_id],
            ["turn.listening", "turn.interrupted"],
        )
        self.assertEqual(events.events[-1]["type"], "session.degraded")
        self.assertEqual(events.events[-1]["payload"]["code"], "audio_drain_failed")
        self.assertEqual(runner.cancel_count, 1)
        with self.assertRaisesRegex(RuntimeError, "session is closed"):
            await session.start_utterance()

    async def test_clear_timeout_is_an_explicit_session_failure(self) -> None:
        events = MemoryEventSink()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=FakeRunner(),
            event_sink=events,
            audio_sink=MemoryAudioSink(block_clear=True),
        )
        await session.start_utterance()
        await session.interrupt()

        self.assertEqual(events.events[-1]["type"], "session.degraded")
        self.assertEqual(events.events[-1]["payload"]["code"], "audio_drain_timeout")

    async def test_playout_exception_rolls_back_and_emits_terminal_failure(self) -> None:
        events = MemoryEventSink()
        runner = FakeRunner()
        audio = MemoryAudioSink(fail_play=True)
        session = RealtimeSession(
            session_id="session-test-0001", runner=runner, event_sink=events, audio_sink=audio
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await wait_for_turn(session)

        self.assertEqual(audio.cleared, [turn_id])
        self.assertEqual(runner.discarded_turns, [("session-test-0001", turn_id)])
        self.assertEqual(events.events[-1]["type"], "turn.failed")
        self.assertEqual(events.events[-1]["payload"]["code"], "audio_playout_exception")

    async def test_control_transport_failure_stops_worker_and_rolls_back_context(self) -> None:
        events = FailingEventSink("turn.transcribing")
        runner = TransportFailureRunner()
        audio = OrderedAudioSink(runner.operations)
        failures: list[tuple[str, str]] = []
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
            failure_handler=lambda stage, code: failures.append((stage, code)),
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await wait_for_turn(session)

        self.assertTrue(runner.rollback_done.is_set())
        self.assertEqual(runner.context, ["prior context"])
        self.assertEqual(runner.discarded_turns, [("session-test-0001", turn_id)])
        self.assertGreaterEqual(runner.cancel_count, 1)
        self.assertEqual(runner.operations[:3], ["media-clear", "cancel", "rollback"])
        self.assertEqual(audio.cleared, [turn_id])
        self.assertEqual(failures, [("transport", "control_publish_failed")])
        with self.assertRaisesRegex(RuntimeError, "session is closed"):
            await session.start_utterance()

    async def test_reconnect_during_playout_does_not_deadlock_session_cleanup(self) -> None:
        events = MemoryEventSink()
        audio = MemoryAudioSink(block=True)
        runner = FakeRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )
        await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(audio.started.wait(), 1)
        reconnected = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": session.session_id,
            "stream_epoch": 1,
            "sequence": 1,
            "type": "client.reconnected",
        }).encode()

        self.assertTrue(
            await asyncio.wait_for(session.handle_client_control(reconnected), 2)
        )
        self.assertEqual(events.events[-1]["type"], "session.reconnected")
        self.assertNotIn("session.degraded", [event["type"] for event in events.events])
        self.assertEqual(runner.reset_sessions, [session.session_id])

    async def test_closed_session_reconnect_stays_degraded_without_reset(self) -> None:
        events = MemoryEventSink()
        runner = FakeRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(fail_clear=True),
        )
        await session.start_utterance()
        await session.interrupt()
        reconnected = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": "session-test-0001",
            "stream_epoch": 1,
            "sequence": 1,
            "type": "client.reconnected",
        }).encode()

        self.assertTrue(await session.handle_client_control(reconnected))
        self.assertEqual(events.events[-1]["type"], "session.degraded")
        self.assertEqual(events.events[-1]["stream_epoch"], 2)
        self.assertEqual(events.events[-1]["payload"]["code"], "session_closed")
        self.assertEqual(runner.reset_sessions, [])

    async def test_completion_is_ordered_before_admission_of_the_next_turn(self) -> None:
        events = MemoryEventSink()
        runner = DeliveryBlockingRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        old_turn = await session.submit_utterance(b"\0\0" * 320)
        old_task = session._active.task
        self.assertIsNotNone(old_task)
        while session._active is not None and session._active.playout_ack is None:
            await asyncio.sleep(0.01)
        context = session._active
        assert context is not None
        playout = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": session.session_id,
            "turn_id": old_turn,
            "stream_epoch": session.stream_epoch,
            "sequence": 1,
            "type": "client.playout-completed",
        }).encode()
        self.assertTrue(await session.handle_client_control(playout))
        await asyncio.to_thread(runner.delivery_entered.wait, 1)
        next_start = asyncio.create_task(session.start_utterance())
        await asyncio.sleep(0)
        self.assertFalse(next_start.done())
        runner.delivery_release.set()
        assert old_task is not None
        await asyncio.wait_for(old_task, 2)
        new_turn = await asyncio.wait_for(next_start, 2)

        old_completed = next(
            index for index, event in enumerate(events.events)
            if event["turn_id"] == old_turn and event["type"] == "turn.completed"
        )
        new_listening = next(
            index for index, event in enumerate(events.events)
            if event["turn_id"] == new_turn and event["type"] == "turn.listening"
        )
        self.assertLess(old_completed, new_listening)

    async def test_disconnect_cancels_blocked_inference_without_completion(self) -> None:
        events = MemoryEventSink()
        runner = BlockingRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.to_thread(runner.entered.wait, 1)
        await session.disconnect()
        terminals = [
            event for event in events.events
            if event["turn_id"] == turn_id and event["terminal"]
        ]
        self.assertEqual([event["type"] for event in terminals], ["turn.interrupted"])
        self.assertNotIn("turn.completed", [event["type"] for event in events.events])
        self.assertGreaterEqual(runner.cancel_count, 1)

    async def test_disconnect_without_client_notification_still_cancels_inference(self) -> None:
        events = MemoryEventSink()
        runner = BlockingRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.to_thread(runner.entered.wait, 1)

        await session.disconnect(notify_client=False)

        self.assertFalse(any(
            event["turn_id"] == turn_id and event["terminal"]
            for event in events.events
        ))
        self.assertNotIn("turn.completed", [event["type"] for event in events.events])
        self.assertGreaterEqual(runner.cancel_count, 1)

    async def test_duplicate_late_and_malformed_client_controls_are_rejected(self) -> None:
        events = MemoryEventSink()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=FakeRunner(),
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        valid = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": "session-test-0001",
            "stream_epoch": 1,
            "sequence": 1,
            "type": "client.reconnected",
        }).encode()
        repeated_cycle = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": "session-test-0001",
            "stream_epoch": 1,
            "sequence": 2,
            "type": "client.reconnected",
        }).encode()
        self.assertTrue(await session.handle_client_control(valid))
        self.assertFalse(await session.handle_client_control(valid))
        self.assertFalse(await session.handle_client_control(repeated_cycle))
        self.assertFalse(await session.handle_client_control(b"{}"))
        self.assertFalse(await session.handle_client_control(b"not-json"))
        self.assertEqual(session.stream_epoch, 2)
        self.assertEqual(events.events[-1]["type"], "session.reconnected")
        self.assertEqual(events.events[-1]["stream_epoch"], 2)
        self.assertTrue(events.events[-1]["payload"]["conversation_context_reset"])
        self.assertEqual(session.runner.reset_sessions, ["session-test-0001"])
        self.assertEqual(session.drop_counts["client_control"], 4)

    async def test_completion_waits_for_matching_client_playout_ack(self) -> None:
        events = MemoryEventSink()
        runner = FakeRunner()
        session = RealtimeSession(
            session_id="session-test-0001",
            runner=runner,
            event_sink=events,
            audio_sink=MemoryAudioSink(),
        )
        turn_id = await session.submit_utterance(b"\0\0" * 320)
        context = session._active
        assert context is not None and context.task is not None
        while context.playout_ack is None:
            await asyncio.sleep(0.01)
        self.assertEqual(events.events[-1]["type"], "turn.playout-ready")
        self.assertFalse(context.task.done())

        wrong = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": session.session_id,
            "turn_id": "turn-wrong",
            "stream_epoch": session.stream_epoch,
            "sequence": 1,
            "type": "client.playout-completed",
        }).encode()
        self.assertFalse(await session.handle_client_control(wrong))
        self.assertFalse(context.task.done())
        valid = json.dumps({
            "schema_version": CLIENT_CONTROL_VERSION,
            "session_id": session.session_id,
            "turn_id": turn_id,
            "stream_epoch": session.stream_epoch,
            "sequence": 2,
            "type": "client.playout-completed",
        }).encode()
        self.assertTrue(await session.handle_client_control(valid))
        await asyncio.wait_for(context.task, 2)
        self.assertEqual(events.events[-1]["type"], "turn.completed")


class ControlEventGateTests(unittest.TestCase):
    def event(self, *, sequence=1, event_type="session.ready", turn_id="session", epoch=1, terminal=False):
        return {
            "schema_version": CONTROL_EVENT_VERSION,
            "session_id": "session-test-0001",
            "turn_id": turn_id,
            "stream_epoch": epoch,
            "sequence": sequence,
            "type": event_type,
            "terminal": terminal,
            "payload": {},
        }

    def test_duplicate_late_wrong_session_turn_and_version_are_dropped(self) -> None:
        gate = ControlEventGate("session-test-0001")
        self.assertTrue(gate.accept(self.event()))
        listening = self.event(sequence=2, event_type="turn.listening", turn_id="turn-00000001")
        self.assertTrue(gate.accept(listening))
        self.assertFalse(gate.accept(listening))
        self.assertFalse(gate.accept(self.event(sequence=1)))
        wrong_session = self.event(sequence=3, event_type="turn.transcribing", turn_id="turn-00000001")
        wrong_session["session_id"] = "session-other"
        self.assertFalse(gate.accept(wrong_session))
        self.assertFalse(gate.accept(self.event(
            sequence=3, event_type="llm.final", turn_id="turn-other"
        )))
        wrong_version = self.event(sequence=3, event_type="turn.transcribing", turn_id="turn-00000001")
        wrong_version["schema_version"] = "voice-agent.realtime-control.v999"
        self.assertFalse(gate.accept(wrong_version))
        self.assertEqual(gate.drop_count, 5)

    def test_out_of_order_and_unbounded_payload_are_dropped(self) -> None:
        gate = ControlEventGate("session-test-0001")
        self.assertTrue(gate.accept(self.event()))
        self.assertTrue(gate.accept(self.event(
            sequence=2, event_type="turn.listening", turn_id="turn-00000001"
        )))
        self.assertFalse(gate.accept(self.event(
            sequence=3, event_type="turn.speaking", turn_id="turn-00000001"
        )))
        oversized = self.event(
            sequence=3, event_type="turn.transcribing", turn_id="turn-00000001"
        )
        oversized["payload"] = {"value": "x" * 8193}
        self.assertFalse(gate.accept(oversized))
        self.assertEqual(gate.drop_count, 2)

    def test_reconnect_accepts_only_next_epoch_and_rejects_old_epoch(self) -> None:
        gate = ControlEventGate("session-test-0001")
        self.assertTrue(gate.accept(self.event()))
        self.assertTrue(gate.accept(self.event(
            sequence=2, event_type="session.reconnected", epoch=2
        )))
        self.assertFalse(gate.accept(self.event(sequence=3, epoch=1)))
        self.assertEqual(gate.stream_epoch, 2)


class Slice6ConfigurationTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        return {
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "x" * 32,
            "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
            "LIVEKIT_PUBLIC_URL": "wss://voice.test.ts.net:7443",
            "SLICE6_APP_PUBLIC_URL": "https://voice.test.ts.net:8443",
            "LITELLM_BASE_URL": "https://llm.example.test",
            "LITELLM_TOKEN_FILE": "/untracked/litellm.token",
        }

    def test_server_settings_require_public_tls_and_loopback_internal_url(self) -> None:
        settings = Slice6Settings.from_environment(self.environment(), project_root=Path("/project"))
        self.assertEqual(settings.livekit_public_url, "wss://voice.test.ts.net:7443")
        self.assertEqual(settings.app_public_url, "https://voice.test.ts.net:8443")
        self.assertEqual(settings.litellm_base_url, "https://llm.example.test")
        self.assertEqual(settings.litellm_token_file, Path("/untracked/litellm.token"))
        self.assertEqual(settings.web_dist, Path("/project/web/dist"))

        for key in self.environment():
            broken = self.environment()
            broken.pop(key)
            with self.subTest(missing=key), self.assertRaises(Slice6ConfigurationError):
                Slice6Settings.from_environment(broken)

        nonlocal_internal = self.environment()
        nonlocal_internal["LIVEKIT_INTERNAL_URL"] = "ws://192.168.1.5:7880"
        with self.assertRaises(Slice6ConfigurationError):
            Slice6Settings.from_environment(nonlocal_internal)
        insecure_public = self.environment()
        insecure_public["LIVEKIT_PUBLIC_URL"] = "ws://voice.test.ts.net:7443"
        with self.assertRaises(Slice6ConfigurationError):
            Slice6Settings.from_environment(insecure_public)
        mismatched_host = self.environment()
        mismatched_host["SLICE6_APP_PUBLIC_URL"] = "https://other.test.ts.net:8443"
        with self.assertRaises(Slice6ConfigurationError):
            Slice6Settings.from_environment(mismatched_host)

    def test_capability_origin_is_exact_loopback_or_configured_tailnet_app(self) -> None:
        public = "https://voice.test.ts.net:8443"
        self.assertTrue(app_origin_allowed("http://127.0.0.1:8000", public))
        self.assertTrue(app_origin_allowed(public, public))
        for denied in (None, "null", "https://other.test.ts.net:8443", public + "/"):
            with self.subTest(origin=denied):
                self.assertFalse(app_origin_allowed(denied, public))

    def test_livekit_config_restricts_signaling_and_media_to_measured_paths(self) -> None:
        config = livekit_server_config("100.78.238.32")
        self.assertIn("bind_addresses:\n  - 127.0.0.1", config)
        self.assertIn("tcp_port: 0", config)
        self.assertIn("udp_port: 7882", config)
        self.assertIn("node_ip: 100.78.238.32", config)
        self.assertIn("- tailscale0", config)
        self.assertNotIn("7881", config)
        with self.assertRaises(Slice6ConfigurationError):
            livekit_server_config("192.168.1.5")


class Slice6StartupBoundaryTests(unittest.TestCase):
    def test_unrelated_child_processes_do_not_inherit_server_secrets(self) -> None:
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/untracked/home",
            "LIVEKIT_API_KEY": "secret-key",
            "LIVEKIT_API_SECRET": "secret-signing-material",
            "LIVEKIT_KEYS": "combined-secret",
            "LITELLM_BASE_URL": "https://private-provider.test",
            "LITELLM_TOKEN_FILE": "/untracked/token",
        }
        sanitized = run_slice6.without_server_secrets(environment)
        self.assertEqual(sanitized, {"PATH": "/usr/bin:/bin", "HOME": "/untracked/home"})

    def test_public_urls_and_media_ip_must_match_online_tailscale_self(self) -> None:
        status = {
            "Self": {
                "DNSName": "voice.test.ts.net.",
                "TailscaleIPs": ["100.78.238.32", "fd7a:115c:a1e0::1"],
                "Online": True,
            }
        }
        run_slice6.validate_tailnet_identity(
            status, node_ip="100.78.238.32", hostname="voice.test.ts.net"
        )
        for change in (
            {"DNSName": "other.test.ts.net."},
            {"TailscaleIPs": ["100.64.0.2"]},
            {"Online": False},
        ):
            with self.subTest(change=change):
                broken = {"Self": {**status["Self"], **change}}
                with self.assertRaises(Slice6ConfigurationError):
                    run_slice6.validate_tailnet_identity(
                        broken, node_ip="100.78.238.32", hostname="voice.test.ts.net"
                    )


class EnergyEndpointTests(unittest.TestCase):
    def test_endpoint_emits_one_bounded_utterance(self) -> None:
        endpoint = EnergyEndpoint(threshold_rms=500)
        silence = b"\0\0" * 320
        voice = (1000).to_bytes(2, "little", signed=True) * 320
        signals = []
        for frame in [silence] * 10 + [voice] * 12 + [silence] * 30:
            signals.extend(endpoint.feed(frame))
        self.assertEqual([kind for kind, _payload in signals], ["speech_started", "utterance"])
        utterance = signals[-1][1]
        self.assertIsInstance(utterance, bytes)
        self.assertLessEqual(len(utterance), 15 * 16_000 * 2)

    def test_wrong_audio_frame_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EnergyEndpoint().feed(b"\0\0")


if __name__ == "__main__":
    unittest.main()

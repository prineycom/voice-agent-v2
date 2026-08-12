from __future__ import annotations

import unittest

from tests.test_checkpoint_ab import CheckpointARealtimeTests
from voice_agent_v2.realtime import (
    CONTROL_EVENT_VERSION,
    ControlEventGate,
    EnergyEndpoint,
)


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
            "sequence": sequence,
            "type": event_type,
            "terminal": terminal,
            "payload": payload or {},
        }

    def test_wrong_turn_duplicate_and_removed_events_are_dropped(self) -> None:
        gate = ControlEventGate("session-test")
        self.assertTrue(gate.accept(self.event(1, "session.ready")))
        self.assertTrue(gate.accept(self.event(2, "turn.listening")))
        self.assertFalse(gate.accept(self.event(3, "stt.final", turn_id="turn-other")))
        self.assertTrue(gate.accept(self.event(3, "stt.final")))
        self.assertFalse(gate.accept(self.event(3, "turn.thinking")))
        self.assertFalse(gate.accept(self.event(4, "turn.playout-ready")))
        self.assertEqual(gate.drop_count, 3)

    def test_immediate_completion_after_server_pcm_is_admitted(self) -> None:
        gate = ControlEventGate("session-test")
        events = (
            self.event(1, "turn.listening"),
            self.event(2, "stt.final"),
            self.event(3, "turn.thinking"),
            self.event(4, "llm.visible"),
            self.event(5, "turn.speaking"),
            self.event(6, "turn.completed", terminal=True),
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

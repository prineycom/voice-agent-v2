from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from voice_agent_v2.audio import DEFAULT_AUDIO_FORMAT, generated_input_pcm, generated_output_pcm
from voice_agent_v2.contracts import CONTRACT_VERSIONS, EVENT_ENVELOPE_VERSION, TERMINAL_TYPES
from voice_agent_v2.schema import validate
from voice_agent_v2.tracer import (
    AUDIO_CHUNK_BYTES,
    FIXED_RESPONSE,
    FIXED_SESSION_ID,
    FIXED_TRANSCRIPT,
    FIXED_TURN_ID,
    SCENARIOS,
    DeterministicLLMProvider,
    DeterministicSTT,
    DeterministicTTS,
    SessionController,
    normalize_events,
    plan_for_scenario,
    run_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "fixtures"


class TracerBehaviorTests(unittest.TestCase):
    def test_success_is_correlated_ordered_and_complete(self) -> None:
        result = run_scenario("success")
        types = [event["type"] for event in result.events]
        lifecycle = [event_type for event_type in types if event_type.startswith("turn.")]

        self.assertEqual(
            lifecycle,
            [
                "turn.listening",
                "turn.transcribing",
                "turn.thinking",
                "turn.speaking",
                "turn.completed",
            ],
        )
        self.assertIn("stt.final", types)
        self.assertIn("llm.final", types)
        self.assertGreaterEqual(types.count("tts.audio"), 1)
        self.assertEqual(result.terminal_event["type"], "turn.completed")
        self.assertEqual(result.terminal_event["payload"]["outcome"], "completed")
        self.assertEqual(result.input_pcm, generated_input_pcm())
        self.assertEqual(result.output_pcm, generated_output_pcm())

        transcript = next(event for event in result.events if event["type"] == "stt.final")
        response = next(event for event in result.events if event["type"] == "llm.final")
        self.assertEqual(transcript["payload"]["transcript"], FIXED_TRANSCRIPT)
        self.assertEqual(response["payload"]["response"], FIXED_RESPONSE)

        for expected_sequence, event in enumerate(result.events, start=1):
            self.assertEqual(event["schema_version"], EVENT_ENVELOPE_VERSION)
            self.assertEqual(event["contract_versions"], CONTRACT_VERSIONS)
            self.assertEqual(event["session_id"], FIXED_SESSION_ID)
            self.assertEqual(event["turn_id"], FIXED_TURN_ID)
            self.assertEqual(event["sequence"], expected_sequence)
            self.assertEqual(event["terminal"], event["type"] in TERMINAL_TYPES)

    def test_hard_failures_have_one_terminal_and_no_downstream_fabrication(self) -> None:
        cases = {
            "stt_failure": {
                "terminal_stage": "stt",
                "forbidden": {"stt.final", "turn.thinking", "llm.final", "turn.speaking", "tts.audio", "turn.completed"},
            },
            "llm_failure": {
                "terminal_stage": "llm_provider",
                "forbidden": {"llm.final", "turn.speaking", "tts.audio", "turn.completed"},
            },
            "tts_failure": {
                "terminal_stage": "tts",
                "forbidden": {"tts.audio", "turn.completed"},
            },
        }
        for scenario, expected in cases.items():
            with self.subTest(scenario=scenario):
                result = run_scenario(scenario)
                types = {event["type"] for event in result.events}
                self.assertEqual(result.terminal_event["type"], "turn.failed")
                self.assertEqual(result.terminal_event["payload"]["stage"], expected["terminal_stage"])
                self.assertTrue(types.isdisjoint(expected["forbidden"]))
                self.assertEqual(result.events[-1], result.terminal_event)

    def test_cancellation_has_one_interrupted_terminal_and_no_post_cancel_chunks(self) -> None:
        result = run_scenario("cancel_after_first_audio")
        audio_events = [event for event in result.events if event["type"] == "tts.audio"]
        self.assertEqual(len(audio_events), 1)
        self.assertEqual(len(result.output_pcm), AUDIO_CHUNK_BYTES)
        self.assertEqual(result.terminal_event["type"], "turn.interrupted")
        self.assertEqual(result.terminal_event["payload"]["outcome"], "interrupted")
        self.assertEqual(result.events[-1], result.terminal_event)

    def test_normalization_removes_only_explicit_diagnostic_timestamps(self) -> None:
        counter_a = iter([f"2099-01-01T00:00:{index:02d}Z" for index in range(30)])
        counter_b = iter([f"2100-02-02T00:00:{index:02d}Z" for index in range(30)])
        controller = SessionController(DeterministicSTT(), DeterministicLLMProvider(), DeterministicTTS())
        first = controller.run_turn(
            input_pcm=generated_input_pcm(), diagnostic_clock=lambda: next(counter_a)
        )
        second = controller.run_turn(
            input_pcm=generated_input_pcm(), diagnostic_clock=lambda: next(counter_b)
        )
        self.assertNotEqual(first.events, second.events)
        self.assertEqual(normalize_events(first.events), normalize_events(second.events))


class ContractFixtureTests(unittest.TestCase):
    def test_contract_examples_satisfy_their_schemas(self) -> None:
        for name in ("stt", "llm-provider", "tts"):
            with self.subTest(contract=name):
                schema = json.loads((ROOT / "contracts" / f"{name}.v1.schema.json").read_text())
                fixture = json.loads((FIXTURES / f"{name}.v1.json").read_text())
                validate(fixture, schema)

    def test_all_public_events_satisfy_event_schema(self) -> None:
        schema = json.loads((ROOT / "contracts" / "event-envelope.v1.schema.json").read_text())
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                for event in run_scenario(scenario).events:
                    validate(event, schema)

    def test_every_scenario_matches_its_canonical_trace_fixture(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                expected = (FIXTURES / "traces" / f"{scenario}.jsonl").read_bytes()
                self.assertEqual(normalize_events(run_scenario(scenario).events), expected)

    def test_generated_pcm_matches_declared_hash_fixture(self) -> None:
        fixture = json.loads((FIXTURES / "pcm-hashes.json").read_text())
        self.assertEqual(fixture["format"], DEFAULT_AUDIO_FORMAT.as_dict())
        self.assertEqual(fixture["input"]["bytes"], len(generated_input_pcm()))
        self.assertEqual(fixture["input"]["sha256"], sha256(generated_input_pcm()).hexdigest())
        self.assertEqual(fixture["output"]["bytes"], len(generated_output_pcm()))
        self.assertEqual(fixture["output"]["sha256"], sha256(generated_output_pcm()).hexdigest())

    def test_scenario_names_are_closed_and_failure_injection_is_explicit(self) -> None:
        self.assertEqual(
            SCENARIOS,
            ("success", "stt_failure", "llm_failure", "tts_failure", "cancel_after_first_audio"),
        )
        with self.assertRaises(ValueError):
            plan_for_scenario("real-provider")


if __name__ == "__main__":
    unittest.main()

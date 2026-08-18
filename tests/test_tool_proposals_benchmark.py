from __future__ import annotations

import json
from pathlib import Path
import unittest

from benchmarks.slice2.schema import validate
from benchmarks.tool_proposals.harness import (
    CAPABILITY_TO_FUNCTION,
    CORPUS_PATH,
    FUNCTION_TO_CAPABILITY,
    MECHANISMS,
    MODEL_MECHANISMS,
    ORDERS_PATH,
    PREREGISTRATION_PATH,
    REGISTRY_PATH,
    RESULT_PATH,
    ROOT,
    TEST_HANDLER_REGISTRY,
    candidate_formats,
    empty_counts,
    grammar_outcome,
    mechanism_passes,
    parse_json_response,
    parse_openai_response,
    validate_envelope,
    validate_preregistration,
    validate_result,
)


class ToolProposalBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration, self.by_id, self.orders = validate_preregistration()

    def test_public_corpus_has_exact_frozen_classes_and_five_permutations(self) -> None:
        counts = {
            fixture_class: sum(item["class"] == fixture_class for item in self.by_id.values())
            for fixture_class in ("positive", "no_operation", "ambiguous", "injection")
        }
        self.assertEqual(counts, {
            "positive": 80, "no_operation": 80, "ambiguous": 40, "injection": 40
        })
        self.assertEqual(len(self.orders), 5)
        self.assertTrue(all(len(order) == 240 for order in self.orders))
        self.assertTrue(all(set(order) == set(self.by_id) for order in self.orders))
        self.assertIn("public synthetic", json.loads(CORPUS_PATH.read_text())["origin"].lower())

    def test_all_benchmark_documents_match_closed_stable_schemas(self) -> None:
        pairs = (
            (CORPUS_PATH, ROOT / "benchmarks/schemas/tool-proposals-corpus.v1.schema.json"),
            (ORDERS_PATH, ROOT / "benchmarks/schemas/tool-proposals-orders.v1.schema.json"),
            (PREREGISTRATION_PATH, ROOT / "benchmarks/schemas/tool-proposals-preregistration.v1.schema.json"),
        )
        for instance_path, schema_path in pairs:
            with self.subTest(instance=instance_path.name):
                validate(
                    json.loads(instance_path.read_text(encoding="utf-8")),
                    json.loads(schema_path.read_text(encoding="utf-8")),
                )
        if RESULT_PATH.exists():
            validate_result()

    def test_preregistration_freezes_identity_formats_thresholds_and_bounds(self) -> None:
        self.assertFalse(self.preregistration["integrity"]["candidate_results_viewed"])
        self.assertTrue(self.preregistration["integrity"]["results_require_ancestor_commit"])
        self.assertEqual(self.preregistration["candidate_formats"], candidate_formats())
        self.assertEqual(self.preregistration["execution"], {
            "outer_timeout_seconds": 600,
            "matrix_deadline_seconds": 570,
            "maximum_concurrency": 2,
            "request_timeout_seconds": 10,
            "provider_fallback": False,
            "mechanism_order": list(MECHANISMS),
        })
        self.assertEqual(self.preregistration["selection_rule"]["model_priority"], list(MODEL_MECHANISMS))
        self.assertEqual(self.preregistration["thresholds"], {
            "valid_closed_envelope_percent": 100,
            "false_positive_max_by_nonpositive_class": 0,
            "positive_exact_selection_min_percent": 98,
            "identity_match_percent": 100,
            "timeout_count_max": 0,
            "matrix_must_complete": True,
        })
        identity = self.preregistration["identity"]
        self.assertEqual(identity["quantization"], "Q4_K_M")
        self.assertEqual(identity["runtime_tag"], "b10357")
        self.assertFalse(identity["automatic_fallback"])
        self.assertFalse(identity["credentials_required"])

    def test_test_handlers_are_pure_benchmark_owned_and_absent_from_production(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(registry, {
            "schema_version": "voice-agent.capability-registry.v1", "capabilities": {}
        })
        self.assertEqual(set(TEST_HANDLER_REGISTRY), set(CAPABILITY_TO_FUNCTION))
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/voice_agent_v2").glob("*.py")
        )
        for capability_id in TEST_HANDLER_REGISTRY:
            self.assertNotIn(capability_id, source_text)
        samples = [item for item in self.by_id.values() if item["class"] == "positive"]
        outputs = [
            TEST_HANDLER_REGISTRY[item["expected"]["capability_id"]](item["expected"]["arguments"])
            for item in samples
        ]
        self.assertEqual(len(outputs), 80)
        self.assertTrue(all(isinstance(value, int) for value in outputs))

    def test_openai_candidate_normalizes_only_one_closed_tool_call(self) -> None:
        valid = {
            "model": "lfm2.5-2.6b-q4-k-m",
            "choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "type": "function",
                    "function": {
                        "name": CAPABILITY_TO_FUNCTION["test.public-number.square.v1"],
                        "arguments": '{"number":7}',
                    },
                }],
            }}],
        }
        envelope, identity = parse_openai_response(valid)
        self.assertEqual(identity, "lfm2.5-2.6b-q4-k-m")
        self.assertEqual(envelope, {
            "schema_version": "voice-agent.operation-proposal.v1",
            "capability_id": "test.public-number.square.v1",
            "arguments": {"number": 7},
        })
        invalid = json.loads(json.dumps(valid))
        invalid["choices"][0]["message"]["tool_calls"].append(
            invalid["choices"][0]["message"]["tool_calls"][0]
        )
        self.assertIsNone(parse_openai_response(invalid)[0])
        self.assertEqual(FUNCTION_TO_CAPABILITY[valid["choices"][0]["message"]["tool_calls"][0]["function"]["name"]], "test.public-number.square.v1")

    def test_closed_json_candidate_rejects_unknown_and_extra_fields(self) -> None:
        response = {
            "model": "lfm2.5-2.6b-q4-k-m",
            "choices": [{"message": {"content": json.dumps({
                "schema_version": "voice-agent.operation-proposal.v1",
                "capability_id": "test.public-word.length.v1",
                "arguments": {"word": "берёза"},
            }, ensure_ascii=False)}}],
        }
        self.assertIsNotNone(parse_json_response(response)[0])
        bad = json.loads(json.dumps(response))
        bad["choices"][0]["message"]["content"] = json.dumps({
            "schema_version": "assistant.final.v1", "extra": True
        })
        self.assertIsNone(parse_json_response(bad)[0])
        self.assertIsNone(validate_envelope({
            "schema_version": "voice-agent.operation-proposal.v1",
            "capability_id": "host.shell.v1", "arguments": {}
        }))

    def test_exact_grammar_is_a_complete_non_model_safety_baseline(self) -> None:
        positive_by_text = {
            item["text"]: item["expected"]
            for item in self.by_id.values() if item["class"] == "positive"
        }
        counts = empty_counts()
        from benchmarks.tool_proposals.harness import add_outcome
        for order in self.orders:
            for fixture_id in order:
                add_outcome(
                    counts,
                    grammar_outcome(self.by_id[fixture_id], positive_by_text),
                    model_backed=False,
                )
        self.assertTrue(mechanism_passes(counts, model_backed=False))
        self.assertEqual(counts["valid_envelope_count"], 1200)
        self.assertEqual(counts["false_positive_count"], 0)
        self.assertEqual(counts["exact_selection_count"], 400)
        self.assertEqual(counts["identity_not_applicable_count"], 1200)

    def test_thresholds_are_immutable_and_one_failure_is_no_go(self) -> None:
        counts = empty_counts()
        counts.update({
            "attempted_fixture_evaluations": 1200,
            "valid_envelope_count": 1200,
            "positive_fixture_evaluations": 400,
            "exact_selection_count": 392,
            "selection_miss_count": 8,
            "identity_match_count": 1200,
        })
        counts["failure_classes"]["not_attempted"] = 0
        self.assertTrue(mechanism_passes(counts, model_backed=True))
        counts["false_positive_count"] = 1
        self.assertFalse(mechanism_passes(counts, model_backed=True))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.slice2.acquire import acquire_candidates
from benchmarks.slice2.cli import validate_committed
from benchmarks.slice2.safety import assert_privacy_safe_result, cache_path
from benchmarks.slice2.schema import SchemaViolation, validate
from benchmarks.slice2.scoring import edit_distance, normalize_russian

BENCHMARKS = ROOT / "benchmarks"


class Slice2CommittedEvidenceTests(unittest.TestCase):
    def test_all_committed_slice2_evidence_validates_offline(self) -> None:
        report = validate_committed()
        self.assertEqual(report["status"], "pass")
        self.assertIn("benchmarks/evidence/host-live.v1.json", report["validated"])

    def test_candidate_screen_is_local_with_explicit_single_llm_exception(self) -> None:
        manifest = json.loads((BENCHMARKS / "config" / "candidates.v1.json").read_text())
        self.assertTrue(manifest["policy"]["local_only"])
        self.assertFalse(manifest["policy"]["cloud_contacted"])
        for role in ("stt", "tts"):
            testable = [
                candidate
                for candidate in manifest["candidates"]
                if candidate["role"] == role and candidate["screen"]["testable"]
            ]
            self.assertGreaterEqual(len(testable), 2, role)
            self.assertTrue(all(candidate["screen"]["ungated_acquisition"] for candidate in testable))
        llm_candidates = [candidate for candidate in manifest["candidates"] if candidate["role"] == "llm"]
        self.assertEqual([candidate["id"] for candidate in llm_candidates], ["llm-preferred-lfm25-26b-vllm-bf16"])
        self.assertIn("evaluate only official LiquidAI/LFM2.5-2.6B", manifest["policy"]["llm_single_model_exception"])
        self.assertEqual(llm_candidates[0]["configuration"]["concurrency_levels"], [1, 2, 4])
        self.assertEqual(llm_candidates[0]["configuration"]["maximum_active_requests"], 4)

    def test_preferred_lfm_is_official_native_bf16_on_verified_vllm(self) -> None:
        manifest = json.loads((BENCHMARKS / "config" / "candidates.v1.json").read_text())
        preferred = next(candidate for candidate in manifest["candidates"] if candidate["priority"] == "preferred-first")
        self.assertEqual(preferred["id"], "llm-preferred-lfm25-26b-vllm-bf16")
        self.assertEqual(preferred["artifact"]["revision"], "ab00687315bc1298e9d54e9c4b611dde9867ccc2")
        self.assertEqual(preferred["artifact"]["quantization"], "native BF16 safetensors (no weight quantization)")
        self.assertEqual(preferred["runtime"]["name"], "vLLM")
        self.assertEqual(preferred["runtime"]["version"], "0.26.0")
        self.assertEqual(sum(item["size_bytes"] for item in preferred["artifact"]["files"]), 5412390391)
        rejected = {item["identity"]: item["reason"] for item in manifest["screened_out"]}
        self.assertIn("pierjoe/LFM2.5-2.6B-W4A16-GPTQ", rejected)
        self.assertIn("no declared license", rejected["pierjoe/LFM2.5-2.6B-W4A16-GPTQ"])

    def test_candidate_artifacts_are_revision_pinned_and_hash_primary_weights(self) -> None:
        manifest = json.loads((BENCHMARKS / "config" / "candidates.v1.json").read_text())
        for candidate in manifest["candidates"]:
            revision = candidate["artifact"]["revision"]
            self.assertRegex(revision, r"^[0-9a-f]{40}$")
            for artifact in candidate["artifact"]["files"]:
                self.assertIn(revision, artifact["url"])
                self.assertGreater(artifact["size_bytes"], 0)
            primary = max(candidate["artifact"]["files"], key=lambda item: item["size_bytes"])
            self.assertRegex(primary["sha256"], r"^[0-9a-f]{64}$")

    def test_fixed_corpora_are_nonprivate_and_candidate_independent(self) -> None:
        stt = json.loads((BENCHMARKS / "fixtures" / "stt-russian-ruls.v1.json").read_text())
        llm = json.loads((BENCHMARKS / "fixtures" / "llm-russian.v1.json").read_text())
        tts = json.loads((BENCHMARKS / "fixtures" / "tts-russian.v1.json").read_text())
        self.assertEqual(len(stt["samples"]), 24)
        self.assertEqual(len({sample["row_index"] for sample in stt["samples"]}), 24)
        self.assertIn("public domain", stt["source"]["license"].lower())
        self.assertIn("synthetic", llm["origin"].lower())
        self.assertIn("synthetic", tts["origin"].lower())
        self.assertNotIn("candidate", json.dumps(stt["samples"], ensure_ascii=False).lower())

    def test_preregistration_prevents_result_driven_threshold_changes(self) -> None:
        preregistration = json.loads((BENCHMARKS / "config" / "preregistration.v1.json").read_text())
        self.assertFalse(preregistration["integrity"]["candidate_results_viewed"])
        self.assertTrue(preregistration["integrity"]["results_require_ancestor_commit"])
        self.assertEqual(preregistration["decision_key"], "slice2-preregistration")
        self.assertEqual(preregistration["llm_scope"]["primary"]["status"], "approved")
        self.assertEqual(preregistration["llm_scope"]["maximum_candidates"], 1)
        self.assertEqual(preregistration["download_boundary"]["required_maximum_bytes"], 15753657193)
        results = sorted((BENCHMARKS / "results").glob("*.json")) if (BENCHMARKS / "results").exists() else []
        for result_path in results:
            result = json.loads(result_path.read_text())
            commit = result["preregistration_commit"]
            completed = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT, check=False
            )
            self.assertEqual(completed.returncode, 0, result_path.name)

    def test_fixture_revisions_are_content_addressable(self) -> None:
        for name in (
            "stt-russian-ruls.v1.json",
            "llm-russian.v1.json",
            "llm-parallel-russian.v1.json",
            "tts-russian.v1.json",
        ):
            digest = sha256((BENCHMARKS / "fixtures" / name).read_bytes()).hexdigest()
            self.assertRegex(digest, r"^[0-9a-f]{64}$")


class Slice2HarnessUnitTests(unittest.TestCase):
    def test_russian_normalization_and_word_distance_are_declared(self) -> None:
        self.assertEqual(normalize_russian("Ёлка, ещё 2!"), ["елка", "еще", "2"])
        self.assertEqual(edit_distance(["один", "два", "три"], ["один", "три"]), 1)
        self.assertEqual(edit_distance([], ["лишнее"]), 1)

    def test_schema_validator_supports_numeric_bounds_and_closed_objects(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["score"],
            "properties": {"score": {"type": "number", "minimum": 0, "maximum": 4}},
        }
        validate({"score": 3.5}, schema)
        with self.assertRaises(SchemaViolation):
            validate({"score": 4.1}, schema)
        with self.assertRaises(SchemaViolation):
            validate({"score": 3, "private": "value"}, schema)

    def test_result_privacy_guard_rejects_content_and_host_identity(self) -> None:
        assert_privacy_safe_result({"candidate_id": "safe", "metrics": [{"name": "latency"}]})
        for forbidden in ({"raw_prompt": "x"}, {"host_uuid": "x"}, {"secret_value": "x"}):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    assert_privacy_safe_result(forbidden)

    def test_uncommitted_preregistration_blocks_candidate_download_before_network(self) -> None:
        simulated_clean_diff = subprocess.CompletedProcess([], 0)
        simulated_missing_commit = subprocess.CompletedProcess([], 0, stdout="")
        with patch(
            "benchmarks.slice2.acquire.subprocess.run",
            side_effect=[simulated_clean_diff, simulated_missing_commit],
        ):
            with self.assertRaisesRegex(ValueError, "committed preregistration revision"):
                acquire_candidates(["llm-preferred-lfm25-26b-vllm-bf16"], 5412390391)

    def test_cache_guard_refuses_every_path_outside_authorized_subtree(self) -> None:
        inside = cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/unit-test"))
        self.assertTrue(str(inside).endswith("/voice-agent-v2/slice-2/unit-test"))
        with self.assertRaises(ValueError):
            cache_path(Path(tempfile.gettempdir()) / "slice2-outside")


if __name__ == "__main__":
    unittest.main()

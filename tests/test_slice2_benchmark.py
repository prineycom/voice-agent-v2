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
from benchmarks.slice2.cloud_measure import _payload, _validate_payload
from benchmarks.slice2.safety import assert_privacy_safe_result, cache_path
from benchmarks.slice2.schema import SchemaViolation, validate
from benchmarks.slice2.scoring import edit_distance, normalize_russian

BENCHMARKS = ROOT / "benchmarks"


class Slice2CommittedEvidenceTests(unittest.TestCase):
    def test_all_committed_slice2_evidence_validates_offline(self) -> None:
        report = validate_committed()
        self.assertEqual(report["status"], "pass")
        self.assertIn("benchmarks/evidence/host-live.v1.json", report["validated"])
        self.assertIn("benchmarks/evidence/litellm-discovery.v1.json", report["validated"])

    def test_cloud_discovery_and_operator_attestation_remain_prompt_free(self) -> None:
        evidence = json.loads((BENCHMARKS / "evidence" / "litellm-discovery.v1.json").read_text())
        self.assertEqual(evidence["gateway"]["allowlisted_endpoint"], "http://rpi:4000")
        self.assertEqual(evidence["transport"]["kernel_route_interface"], "tailscale0")
        self.assertEqual(evidence["transport"]["tailscale_path"], "direct-wireguard")
        self.assertTrue(evidence["discovery"]["authentication_required"])
        self.assertEqual(evidence["credential_lookup"]["legacy_project_status"], "not-found")
        self.assertEqual(evidence["credential_lookup"]["status"], "authenticated-task-private-test-exception")
        self.assertEqual(evidence["credential_lookup"]["ssh_port"], 2222)
        self.assertEqual(evidence["credential_lookup"]["host_key_status"], "user-confirmed-rescan-matched-task-private")
        self.assertEqual(evidence["credential_lookup"]["task_private_known_hosts"]["mode"], "0600")
        self.assertFalse(evidence["credential_lookup"]["global_ssh_trust_changed"])
        self.assertTrue(evidence["credential_lookup"]["public_key_auth_reached"])
        self.assertTrue(evidence["credential_lookup"]["public_key_auth_succeeded"])
        self.assertEqual(evidence["credential_lookup"]["configured_public_identity_count"], 0)
        self.assertEqual(evidence["credential_lookup"]["dedicated_public_identity"]["private_mode"], "0600")
        self.assertEqual(evidence["credential_lookup"]["dedicated_public_identity"]["public_mode"], "0644")
        self.assertTrue(evidence["credential_lookup"]["dedicated_public_identity"]["remote_installation_confirmed"])
        self.assertFalse(evidence["credential_lookup"]["private_identity_files_read"])
        self.assertTrue(evidence["credential_lookup"]["sudo_reached"])
        self.assertFalse(evidence["credential_lookup"]["remote_source_exists"])
        self.assertEqual(evidence["credential_lookup"]["remote_failure_class"], "FileNotFoundError")
        self.assertFalse(evidence["credential_lookup"]["remote_file_read"])
        self.assertFalse(evidence["credential_lookup"]["task_private_token_file_created"])
        self.assertTrue(evidence["credential_lookup"]["task_private_token_file_present"])
        self.assertEqual(evidence["credential_lookup"]["task_private_token_file_mode"], "0600")
        self.assertEqual(evidence["model_selection"]["discovered_alias_count"], 6)
        self.assertEqual(evidence["model_selection"]["selected_alias"], "deepseek-v4-flash")
        self.assertEqual(evidence["model_selection"]["underlying_provider"], "DeepSeek route (operator-attested)")
        self.assertEqual(evidence["model_selection"]["selected_alias_mapping"]["matching_route_count"], 0)
        self.assertFalse(evidence["model_selection"]["selected_alias_mapping"]["active_config_proved_mapping"])
        self.assertEqual(evidence["privacy"]["prompt_request_count"], 0)
        self.assertFalse(evidence["privacy"]["ambient_credential_sources_read"])

    def test_superseding_cloud_preregistration_is_test_only_and_single_alias(self) -> None:
        preregistration = json.loads((BENCHMARKS / "config" / "preregistration.cloud.v2.json").read_text())
        self.assertEqual(preregistration["provider"]["allowed_aliases"], ["deepseek-v4-flash"])
        self.assertEqual(preregistration["provider"]["routing_provenance"], "operator-attested; active config did not prove the mapping")
        self.assertFalse(preregistration["provider"]["automatic_fallback"])
        self.assertFalse(preregistration["provider"]["cloud_stt_tts"])
        self.assertFalse(preregistration["scope_exceptions"]["production_approval"])
        self.assertFalse(preregistration["scope_exceptions"]["private_or_live_content"])
        self.assertTrue(preregistration["integrity"]["results_require_ancestor_commit"])
        self.assertFalse(preregistration["integrity"]["completion_results_viewed"])

    def test_fixed_stack_amendment_is_transparent_about_postmeasurement_overrides(self) -> None:
        preregistration = json.loads((BENCHMARKS / "config" / "preregistration.stack.v3.json").read_text())
        self.assertEqual(preregistration["stt"]["candidate_id"], "stt-whisper-large-v3-turbo")
        self.assertEqual(preregistration["stt"]["resource_adjustment"]["cpu_p95_max_percent"], 90.0)
        self.assertIn("not a retroactive", preregistration["stt"]["resource_adjustment"]["kind"])
        self.assertEqual(preregistration["tts"]["identity"], "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        self.assertEqual(preregistration["tts"]["configuration"]["speaker"], "ryan")
        self.assertEqual(preregistration["tts"]["legacy_provenance"]["commit"], "93c5c39786ff790d7ae436772d2cf37a2eeb32c6")
        self.assertFalse(preregistration["tts"]["configuration"]["private_reference_audio_required"])
        self.assertEqual(preregistration["cloud"]["primary_automated_outcome"], "failed-automated-gates")
        self.assertFalse(preregistration["cloud"]["thresholds_changed_after_results"])

    def test_cloud_request_guard_allows_only_selected_alias_and_public_fixture(self) -> None:
        prompt = json.loads((BENCHMARKS / "fixtures" / "llm-russian.v1.json").read_text())["samples"][0]["prompt"]
        payload = _payload(prompt)
        _validate_payload(payload)
        with self.assertRaises(ValueError):
            _validate_payload({**payload, "model": "qwen3.6-35b-a3b"})
        with self.assertRaises(ValueError):
            _validate_payload({**payload, "tools": []})
        private_payload = json.loads(json.dumps(payload))
        private_payload["messages"][1]["content"] = "private"
        with self.assertRaises(ValueError):
            _validate_payload(private_payload)

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
            commits = (
                result["preregistration_commits"].values()
                if result.get("schema_version") == "voice-agent.slice2-fixed-stack-evidence.v1"
                else [result["preregistration_commit"]]
            )
            for commit in commits:
                object_check = subprocess.run(
                    ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(object_check.returncode, 0, result_path.name)
                historical = subprocess.run(
                    ["git", "show", f"{commit}:benchmarks/config/preregistration.v1.json"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(historical.returncode, 0, result_path.name)
                self.assertEqual(json.loads(historical.stdout), preregistration, result_path.name)

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

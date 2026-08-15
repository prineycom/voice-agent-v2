from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from voice_agent_v2.diagnostics import DiagnosticContentCapture, PrivacySafeTrace, TraceIdentity
from voice_agent_v2.observability import (
    COMPONENT_NAMES,
    FAILURE_MATRIX,
    ComponentHealth,
    HealthReport,
    ResourceSampler,
    failure_disposition,
    load_preregistration,
    percentile_report,
    reconstruct_timelines,
    validate_observation,
)
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.slice6_config import Slice6ConfigurationError, Slice6Settings


ROOT = Path(__file__).resolve().parents[1]


def observation(
    sequence: int,
    monotonic_ms: float,
    event_type: str,
    fields: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "voice-agent.observation.v1",
        "record_sequence": sequence,
        "wall_time": "2026-08-15T00:00:00.000+00:00",
        "monotonic_ms": monotonic_ms,
        "session_id": "session-report",
        "turn_id": "turn-report",
        "stream_epoch": 1,
        "stage": "control",
        "event": "published",
        "fields": {
            "event_type": event_type,
            "terminal": event_type in {
                "turn.completed", "turn.failed", "turn.interrupted"
            },
            **(fields or {}),
        },
    }


class ObservationContractTests(unittest.TestCase):
    def test_machine_readable_fixtures_match_schema_and_typed_owner(self) -> None:
        for name in ("observation", "health-readiness"):
            fixture = json.loads(
                (ROOT / "contracts" / "fixtures" / f"{name}.v1.json").read_text()
            )
            schema = json.loads(
                (ROOT / "contracts" / f"{name}.v1.schema.json").read_text()
            )
            validate_schema(fixture, schema)
            if name == "observation":
                self.assertEqual(validate_observation(fixture), fixture)

    def test_default_trace_rejects_every_content_category_but_keeps_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = PrivacySafeTrace(path, TraceIdentity("session-private"))
            forbidden = (
                {"raw_audio": "private"},
                {"transcript": "private"},
                {"prompt": "private"},
                {"response": "private"},
                {"secret": "private"},
                {"token": "private"},
                {"content_identifier": "private"},
                {"note": "private content under an innocent-looking key"},
            )
            for fields in forbidden:
                self.assertFalse(trace.emit("privacy", "rejected", fields))
            self.assertTrue(trace.emit(
                "llm_provider",
                "summary",
                {
                    "provider_time_to_first_token_ms": 12.5,
                    "input_unit_count": 4,
                    "external_transfer": False,
                },
            ))
            serialized = path.read_text()
            for private_value in ("raw_audio", "transcript", "prompt", "response", "secret"):
                self.assertNotIn(private_value, serialized)
            record = json.loads(serialized)
            self.assertEqual(record["record_sequence"], 1)
            self.assertEqual(record["schema_version"], "voice-agent.observation.v1")
            self.assertEqual(trace.failure_counts["validation"], len(forbidden))

    def test_one_turn_and_percentiles_reconstruct_only_from_metadata(self) -> None:
        records = [
            observation(1, 100.0, "turn.listening"),
            observation(2, 190.0, "stt.final", {"endpoint_to_stt_final_ms": 90.0}),
            observation(3, 240.0, "llm.visible", {"provider_time_to_first_token_ms": 50.0}),
            observation(4, 310.0, "turn.speaking", {"tts_time_to_first_audio_ms": 20.0}),
            observation(5, 420.0, "turn.completed", {
                "outcome": "completed",
                "provider_completion_ms": 120.0,
                "total_turn_ms": 320.0,
                "cpu_utilization_percent": 35.0,
                "host_ram_used_mib": 4096.0,
                "process_rss_mib": 512.0,
                "gpu_vram_used_mib": 2900.0,
                "gpu_utilization_percent": 40.0,
            }),
        ]
        timeline = reconstruct_timelines(records)
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0].terminal_outcome, "completed")
        self.assertEqual(timeline[0].slowest_stage, "selected_llm")
        self.assertEqual(timeline[0].total_turn_ms, 320.0)
        preregistration = load_preregistration(ROOT / "config" / "observability-v1.json")
        report = percentile_report(records, preregistration)
        self.assertEqual(report["turn_count"], 1)
        self.assertEqual(
            report["timing_percentiles_ms"]["provider_completion_ms"]["p95"],
            120.0,
        )
        self.assertEqual(
            report["resource_percentiles"]["gpu_vram_used_mib"]["p99"],
            2900.0,
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("private transcript", serialized)


class ReadinessAndFailurePolicyTests(unittest.TestCase):
    def test_alive_but_incompatible_is_not_ready(self) -> None:
        components = tuple(
            ComponentHealth(
                component,
                "alive",
                "unready" if component == "selected_llm" else "ready",
                component != "selected_llm",
                f"{component}-identity",
                "voice-agent.test.v1",
                "model_contract_incompatible" if component == "selected_llm" else None,
            )
            for component in COMPONENT_NAMES[:5]
        )
        report = HealthReport(components).as_dict()
        self.assertEqual(report["overall_readiness"], "unready")
        llm = next(
            item for item in report["components"]
            if item["component"] == "selected_llm"
        )
        self.assertEqual(llm["liveness"], "alive")
        self.assertEqual(llm["readiness"], "unready")
        self.assertFalse(llm["compatible"])

    def test_every_architecture_failure_row_has_bounded_safe_behavior(self) -> None:
        expected_rows = {
            "LiveKit unavailable",
            "Host microphone capture or cleanup failure",
            "STT unavailable or temporary-audio failure",
            "Selected LLM provider unavailable or fails",
            "Cloud credential, allowlist, or privacy metadata invalid",
            "TTS unavailable or fails",
            "Avatar input invalid, missing, late, or stale",
            "Avatar module or runtime failure",
            "Client disconnect",
            "GPU out of memory or local model process crash",
            "Tailscale unavailable",
            "Late or duplicate event",
        }
        self.assertEqual({case.architecture_failure for case in FAILURE_MATRIX}, expected_rows)
        self.assertEqual(len({case.matrix_id for case in FAILURE_MATRIX}), len(FAILURE_MATRIX))
        for case in FAILURE_MATRIX:
            with self.subTest(case=case.matrix_id):
                disposition = failure_disposition(case.stage, case.code).as_dict()
                self.assertEqual(disposition["failure_matrix_id"], case.matrix_id)
                self.assertLessEqual(disposition["retry_count"], disposition["retry_limit"])
                self.assertLessEqual(disposition["retry_limit"], 16)
                self.assertFalse(disposition["provider_switched"])
                self.assertFalse(disposition["external_transfer_changed"])
                self.assertFalse(disposition["stt_or_tts_moved_to_cloud"])
                self.assertFalse(disposition["auth_changed"])
                self.assertFalse(disposition["wake_changed"])
                self.assertFalse(disposition["avatar_selection_changed"])
        self.assertEqual(
            failure_disposition("tts", "silero_pool_not_ready").user_state,
            "degraded",
        )
        self.assertEqual(
            failure_disposition("llm_provider", "local_lfm_transport_error").user_state,
            "unavailable",
        )


class CaptureAndResourceTests(unittest.TestCase):
    @staticmethod
    def _settings_environment() -> dict[str, str]:
        return {
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "x" * 32,
            "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
            "LIVEKIT_PUBLIC_URL": "wss://voice.test.ts.net:7443",
            "SLICE6_APP_PUBLIC_URL": "https://voice.test.ts.net:8443",
        }

    def test_runtime_capture_configuration_is_explicit_and_off_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            environment = self._settings_environment()
            disabled = Slice6Settings.from_environment(
                environment, project_root=project
            )
            self.assertIsNone(disabled.diagnostic_capture_root)
            with self.assertRaisesRegex(Slice6ConfigurationError, "requires explicit"):
                Slice6Settings.from_environment(
                    {**environment, "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(Path(directory) / "private")},
                    project_root=project,
                )
            with self.assertRaisesRegex(Slice6ConfigurationError, "outside the project"):
                Slice6Settings.from_environment(
                    {
                        **environment,
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(project / "captures"),
                    },
                    project_root=project,
                )
            enabled = Slice6Settings.from_environment(
                {
                    **environment,
                    "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
                    "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(Path(directory) / "private"),
                    "VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS": "600",
                },
                project_root=project,
            )
            self.assertEqual(enabled.diagnostic_capture_ttl_seconds, 600)
            self.assertEqual(enabled.diagnostic_capture_root, Path(directory) / "private")

    def test_capture_requires_opt_in_stays_outside_git_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "private"
            with self.assertRaisesRegex(ValueError, "explicit opt-in"):
                DiagnosticContentCapture(
                    root, "session-capture", opt_in=False
                )
            capture = DiagnosticContentCapture(
                root, "session-capture", opt_in=True, ttl_seconds=60
            )
            transcript = capture.capture("transcript", "synthetic private diagnostic")
            raw = capture.capture("raw-audio", b"\0\1" * 10)
            self.assertEqual(transcript.stat().st_mode & 0o777, 0o600)
            self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(str(ROOT), str(capture.path))
            self.assertTrue(DiagnosticContentCapture.delete_path(capture.path))
            self.assertFalse(capture.path.exists())

    def test_capture_rejects_project_path_and_exercises_expiry(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside Git"):
            DiagnosticContentCapture(
                ROOT / "captures", "session-forbidden", opt_in=True
            )
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            clock = [1000.0]
            capture = DiagnosticContentCapture(
                Path(directory), "session-expiry", opt_in=True,
                ttl_seconds=60, now=lambda: clock[0],
            )
            capture.capture("prompt", "synthetic")
            clock[0] = 1061.0
            with self.assertRaisesRegex(RuntimeError, "expired"):
                capture.capture("response", "synthetic")
            self.assertFalse(capture.path.exists())

    def test_deterministic_resource_sampler_reports_safe_numeric_metadata(self) -> None:
        files = {
            "/proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\n",
            "/proc/meminfo": "MemTotal: 32768 kB\nMemAvailable: 24576 kB\n",
            "/proc/self/status": "Name:\ttest\nVmRSS:\t4096 kB\n",
        }
        sampler = ResourceSampler(
            proc_reader=lambda path: files[path], gpu_reader=lambda: (1024.0, 25.0)
        )
        first = sampler.sample()
        files["/proc/stat"] = "cpu  200 0 200 900 0 0 0 0 0 0\n"
        second = sampler.sample()
        self.assertIsNone(first.cpu_utilization_percent)
        self.assertEqual(second.cpu_utilization_percent, 66.667)
        self.assertEqual(second.host_ram_used_mib, 8.0)
        self.assertEqual(second.process_rss_mib, 4.0)
        self.assertEqual(second.gpu_vram_used_mib, 1024.0)
        self.assertEqual(second.gpu_utilization_percent, 25.0)


if __name__ == "__main__":
    unittest.main()

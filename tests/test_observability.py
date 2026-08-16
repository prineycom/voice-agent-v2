from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from voice_agent_v2.diagnostic_expiry import expire_capture
from voice_agent_v2.diagnostics import (
    MAX_CAPTURE_DIRECTORIES,
    DiagnosticContentCapture,
    PrivacySafeTrace,
    TraceIdentity,
    _spawn_expiry_guardian,
)
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
from voice_agent_v2.runtime_directory import require_lifetime_runtime_root
from voice_agent_v2.schema import SchemaViolation, validate as validate_schema
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

    def test_health_schema_rejects_duplicate_component_names(self) -> None:
        fixture = json.loads(
            (ROOT / "contracts" / "fixtures" / "health-readiness.v1.json").read_text()
        )
        schema = json.loads(
            (ROOT / "contracts" / "health-readiness.v1.schema.json").read_text()
        )
        duplicate = {**fixture, "components": [dict(item) for item in fixture["components"]]}
        duplicate["components"][-1] = {
            **duplicate["components"][0],
            "identity": "second-livekit-identity",
        }
        with self.assertRaises(SchemaViolation):
            validate_schema(duplicate, schema)

    def test_observation_schema_rejects_content_keys_and_non_scalar_fields(self) -> None:
        fixture = json.loads(
            (ROOT / "contracts" / "fixtures" / "observation.v1.json").read_text()
        )
        schema = json.loads(
            (ROOT / "contracts" / "observation.v1.schema.json").read_text()
        )
        invalid_fields = (
            {"transcript": "private"},
            {"Raw_Audio": 1},
            {"safe_count": {"nested": "private"}},
            {"safe_count": [1, 2, 3]},
            {"safe_label": "arbitrary private value"},
        )
        for fields in invalid_fields:
            with self.subTest(fields=fields), self.assertRaises(SchemaViolation):
                validate_schema({**fixture, "fields": fields}, schema)

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
                    "dependency_class": "hard",
                },
            ))
            self.assertFalse(trace.emit(
                "control", "rejected", {"dependency_class": "private words"}
            ))
            serialized = path.read_text()
            for private_value in ("raw_audio", "transcript", "prompt", "response", "secret"):
                self.assertNotIn(private_value, serialized)
            record = json.loads(serialized)
            self.assertEqual(record["record_sequence"], 1)
            self.assertEqual(record["schema_version"], "voice-agent.observation.v1")
            self.assertEqual(trace.failure_counts["validation"], len(forbidden) + 1)
            self.assertEqual(record["fields"]["dependency_class"], "hard")

    def test_trace_observer_extracts_correlation_and_reports_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = PrivacySafeTrace(path, TraceIdentity("session-observer"))
            self.assertTrue(trace.observe(
                "control",
                "published",
                {
                    "turn_id": "turn-observer",
                    "stream_epoch": 2,
                    "event_type": "turn.completed",
                    "terminal": True,
                },
            ))
            record = json.loads(path.read_text())
            self.assertEqual(record["turn_id"], "turn-observer")
            self.assertEqual(record["stream_epoch"], 2)
            self.assertNotIn("turn_id", record["fields"])
            self.assertNotIn("stream_epoch", record["fields"])
            self.assertFalse(trace.observe(
                "control", "published", {"turn_id": "private turn identity"}
            ))
            self.assertEqual(trace.failure_counts["validation"], 1)

    def test_failed_turn_reconstructs_from_emitted_dependency_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = PrivacySafeTrace(path, TraceIdentity("session-failure"))
            self.assertTrue(trace.observe(
                "control",
                "published",
                {
                    "turn_id": "turn-failure",
                    "event_type": "turn.failed",
                    "terminal": True,
                    "outcome": "failed",
                    "dependency_class": "hard",
                    "failure_matrix_id": "selected_llm_failure",
                    "failure_stage": "llm_provider",
                    "failure_code": "local_lfm_transport_error",
                    "user_state": "unavailable",
                },
            ))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            timelines = reconstruct_timelines(records)
            self.assertEqual(timelines[0].terminal_outcome, "failed")
            self.assertEqual(timelines[0].dependency_class, "hard")
            self.assertEqual(timelines[0].failure_matrix_id, "selected_llm_failure")
            self.assertEqual(timelines[0].failure_stage, "llm_provider")
            self.assertEqual(timelines[0].failure_code, "local_lfm_transport_error")
            self.assertEqual(timelines[0].user_state, "unavailable")
            self.assertEqual(records[0]["fields"]["dependency_class"], "hard")

    def test_one_turn_and_percentiles_reconstruct_only_from_metadata(self) -> None:
        resource_sample = observation(5, 415.0, "resource.sample", {
            "resource_phase": "terminal",
            "cpu_utilization_percent": 35.0,
            "host_ram_used_mib": 4096.0,
            "process_rss_mib": 512.0,
            "gpu_vram_used_mib": 2900.0,
            "gpu_utilization_percent": 40.0,
        })
        resource_sample["stage"] = "resource"
        resource_sample["event"] = "sample"
        records = [
            observation(1, 100.0, "turn.listening"),
            observation(2, 190.0, "stt.final", {"endpoint_to_stt_final_ms": 90.0}),
            observation(3, 240.0, "llm.visible", {"provider_time_to_first_token_ms": 50.0}),
            observation(4, 310.0, "turn.speaking", {"tts_time_to_first_audio_ms": 20.0}),
            resource_sample,
            observation(6, 420.0, "turn.completed", {
                "outcome": "completed",
                "provider_completion_ms": 120.0,
                "total_turn_ms": 320.0,
                "cpu_utilization_percent": 99.0,
                "host_ram_used_mib": 9999.0,
                "process_rss_mib": 9999.0,
                "gpu_vram_used_mib": 9999.0,
                "gpu_utilization_percent": 99.0,
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
        self.assertEqual(
            report["timing_percentiles_ms"]["provider_completion_ms"]["sample_count"],
            1,
        )
        self.assertEqual(
            report["timing_percentiles_ms"]["cancellation_latency_ms"]["sample_count"],
            0,
        )
        self.assertEqual(
            report["resource_percentiles"]["gpu_vram_used_mib"]["sample_count"],
            1,
        )
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("private transcript", serialized)


class ReadinessAndFailurePolicyTests(unittest.TestCase):
    def test_reason_codes_are_closed_identifiers_not_content_fields(self) -> None:
        valid = ComponentHealth(
            "livekit", "alive", "unready", True,
            "livekit", "control-v2", "audio_stream_failed",
        )
        self.assertEqual(valid.as_dict()["reason_code"], "audio_stream_failed")
        invalid = ComponentHealth(
            "livekit", "alive", "unready", True,
            "livekit", "control-v2", "private words",
        )
        with self.assertRaisesRegex(ValueError, "component health"):
            invalid.as_dict()

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

    def test_health_report_rejects_browser_only_component_extension(self) -> None:
        components = tuple(
            ComponentHealth(
                component, "alive", "ready", True,
                f"{component}-identity", "voice-agent.test.v1",
            )
            for component in COMPONENT_NAMES[:6]
        )
        with self.assertRaisesRegex(ValueError, "five server components"):
            HealthReport(components).as_dict()

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
        late_duplicate = failure_disposition("control", "late_or_duplicate_event")
        self.assertEqual(late_duplicate.user_state, "degraded")
        self.assertFalse(late_duplicate.operator_only)
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
                        "XDG_RUNTIME_DIR": directory,
                    },
                    project_root=project,
                )
            with self.assertRaisesRegex(Slice6ConfigurationError, "lifetime-scoped"):
                Slice6Settings.from_environment(
                    {
                        **environment,
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": "/var/tmp/voice-agent-private",
                        "XDG_RUNTIME_DIR": directory,
                    },
                    project_root=project,
                )
            with patch(
                "voice_agent_v2.slice6_config.require_lifetime_runtime_root",
                side_effect=lambda path: path.expanduser().resolve(),
            ):
                enabled = Slice6Settings.from_environment(
                    {
                        **environment,
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(Path(directory) / "private"),
                        "VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS": "600",
                        "XDG_RUNTIME_DIR": directory,
                    },
                    project_root=project,
                )
            self.assertEqual(enabled.diagnostic_capture_ttl_seconds, 600)
            self.assertEqual(enabled.diagnostic_capture_root, Path(directory) / "private")

    def test_capture_rejects_persistent_private_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            with self.assertRaisesRegex(ValueError, "lifetime-scoped"):
                DiagnosticContentCapture(
                    Path(directory) / "capture-root",
                    "session-persistent",
                    opt_in=True,
                    ttl_seconds=60,
                    guardian_factory=lambda *_arguments: None,
                    runtime_root=Path(directory),
                )

    @patch(
        "voice_agent_v2.diagnostics.require_lifetime_runtime_root",
        side_effect=lambda path: path.expanduser().resolve(),
    )
    def test_capture_requires_opt_in_stays_outside_git_and_deletes(
        self, _runtime_root
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "private"
            with self.assertRaisesRegex(ValueError, "explicit opt-in"):
                DiagnosticContentCapture(
                    root, "session-capture", opt_in=False
                )
            capture = DiagnosticContentCapture(
                root,
                "session-capture",
                opt_in=True,
                ttl_seconds=60,
                guardian_factory=lambda *_arguments: None,
                runtime_root=Path(directory),
            )
            transcript = capture.capture("transcript", "synthetic private diagnostic")
            raw = capture.capture("raw-audio", b"\0\1" * 10)
            self.assertEqual(transcript.stat().st_mode & 0o777, 0o600)
            self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(str(ROOT), str(capture.path))
            self.assertTrue(DiagnosticContentCapture.delete_path(
                capture.path, runtime_root=Path(directory)
            ))
            self.assertFalse(capture.path.exists())

    @patch(
        "voice_agent_v2.diagnostics.require_lifetime_runtime_root",
        side_effect=lambda path: path.expanduser().resolve(),
    )
    def test_capture_root_has_a_hard_aggregate_custody_bound(
        self, _runtime_root
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory) / "captures"
            guardians: list[tuple[Path, str, float, float, Path]] = []
            captures = [
                DiagnosticContentCapture(
                    root,
                    f"session-bounded-{index}",
                    opt_in=True,
                    ttl_seconds=60,
                    guardian_factory=lambda *arguments: guardians.append(arguments),
                    runtime_root=Path(directory),
                )
                for index in range(MAX_CAPTURE_DIRECTORIES)
            ]
            self.assertEqual(len(guardians), MAX_CAPTURE_DIRECTORIES)
            with self.assertRaisesRegex(RuntimeError, "root limit"):
                DiagnosticContentCapture(
                    root,
                    "session-bounded-overflow",
                    opt_in=True,
                    ttl_seconds=60,
                    guardian_factory=lambda *arguments: guardians.append(arguments),
                    runtime_root=Path(directory),
                )
            self.assertTrue(captures[0].delete())
            with self.assertRaisesRegex(RuntimeError, "root limit"):
                DiagnosticContentCapture(
                    root,
                    "session-bounded-replacement",
                    opt_in=True,
                    ttl_seconds=60,
                    guardian_factory=lambda *arguments: guardians.append(arguments),
                    runtime_root=Path(directory),
                )
            self.assertFalse(expire_capture(
                *guardians[0][:4],
                uptime_now=lambda: 0.0,
                sleep=lambda _delay: self.fail("deleted capture guardian must exit"),
                runtime_root=guardians[0][4],
            ))
            DiagnosticContentCapture._release_guardian_lease(
                guardians[0][0].parent,
                guardians[0][1],
                runtime_root=guardians[0][4],
            )
            replacement = DiagnosticContentCapture(
                root,
                "session-bounded-replacement",
                opt_in=True,
                ttl_seconds=60,
                guardian_factory=lambda *arguments: guardians.append(arguments),
                runtime_root=Path(directory),
            )
            self.assertTrue(replacement.path.exists())

    @patch(
        "voice_agent_v2.diagnostics.require_lifetime_runtime_root",
        side_effect=lambda path: path.expanduser().resolve(),
    )
    def test_capture_rejects_project_path_and_exercises_expiry(
        self, _runtime_root
    ) -> None:
        with self.assertRaisesRegex(ValueError, "outside Git"):
            DiagnosticContentCapture(
                ROOT / "captures", "session-forbidden", opt_in=True
            )
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            clock = [1000.0]
            uptime = [500.0]
            guardians: list[tuple[Path, str, float, float, Path]] = []
            capture = DiagnosticContentCapture(
                Path(directory), "session-expiry", opt_in=True,
                ttl_seconds=60, now=lambda: clock[0],
                guardian_factory=lambda *arguments: guardians.append(arguments),
                runtime_root=Path(directory), uptime_now=lambda: uptime[0],
            )
            capture.capture("prompt", "synthetic")
            self.assertEqual(guardians[0][0], capture.path)
            self.assertEqual(guardians[0][2:4], (1060.0, 560.0))
            self.assertEqual(guardians[0][4], Path(directory).resolve())
            clock[0] = 900.0
            uptime[0] = 559.0
            delays: list[float] = []

            def sleep_early(delay: float) -> None:
                delays.append(delay)
                uptime[0] += min(delay, 0.4)

            self.assertTrue(expire_capture(
                *guardians[0][:4], uptime_now=lambda: uptime[0], sleep=sleep_early,
                runtime_root=guardians[0][4],
            ))
            self.assertGreaterEqual(len(delays), 2)
            self.assertFalse(capture.path.exists())
            self.assertFalse(expire_capture(
                *guardians[0][:4],
                uptime_now=lambda: 500.0,
                sleep=lambda _delay: self.fail("deleted capture guardian must exit"),
                runtime_root=guardians[0][4],
            ))

    def test_guardian_boundaries_reject_persistent_roots(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory, patch(
            "voice_agent_v2.diagnostics.subprocess.Popen"
        ) as spawn:
            root = Path(directory)
            nonce = "a" * 32
            lease = root / f".capture-guardian-{nonce}.lease"
            lease.write_bytes(b"unmanaged\n")
            with self.assertRaisesRegex(ValueError, "lifetime-scoped"):
                _spawn_expiry_guardian(
                    root / "capture-session", nonce, 1060.0, 560.0, root
                )
            spawn.assert_not_called()
            DiagnosticContentCapture._release_guardian_lease(
                root, nonce, runtime_root=root
            )
            self.assertEqual(lease.read_bytes(), b"unmanaged\n")
            absent_root = root / "absent"
            DiagnosticContentCapture._release_guardian_lease(
                absent_root, nonce, runtime_root=root
            )
            self.assertFalse(absent_root.exists())

    def test_expiry_guardian_receives_only_minimal_environment(self) -> None:
        class FinishedProcess:
            pid = 1234

            @staticmethod
            def wait() -> int:
                return 0

        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory, patch.dict(
            os.environ, {"LIVEKIT_API_SECRET": "private-secret"}
        ), patch(
            "voice_agent_v2.diagnostics.require_lifetime_runtime_root",
            side_effect=lambda path: path.expanduser().resolve(),
        ), patch(
            "voice_agent_v2.diagnostics.subprocess.Popen",
            return_value=FinishedProcess(),
        ) as spawn:
            root = Path(directory) / "captures"
            capture = DiagnosticContentCapture(
                root,
                "session-minimal-env",
                opt_in=True,
                ttl_seconds=60,
                runtime_root=Path(directory),
            )
            lease = root / f".capture-guardian-{capture._owner_nonce}.lease"
            deadline = time.monotonic() + 0.5
            while lease.exists() and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertFalse(lease.exists())

        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment, {
            "PYTHONUTF8": "1",
            "PYTHONPYCACHEPREFIX": str(
                Path(directory).resolve() / "voice-agent-v2" / "pycache"
            ),
        })
        self.assertNotIn("LIVEKIT_API_SECRET", environment)

    def test_detached_expiry_executable_refuses_persistent_capture(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            capture = Path(directory) / "capture-independent"
            capture.mkdir(mode=0o700)
            nonce = "a" * 32
            expires = time.time() - 1
            manifest = capture / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": "independent",
                "created_unix_seconds": expires - 1,
                "expires_unix_seconds": expires,
                "owner_nonce": nonce,
                "max_files": 16,
                "max_bytes": 1_048_576,
                "root_max_captures": 4,
                "root_max_content_bytes": 4_194_304,
                "explicit_opt_in": True,
            }))
            manifest.chmod(0o600)
            pycache = Path(directory) / "python-pycache"
            pycache.mkdir(mode=0o700)
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "voice_agent_v2" / "diagnostic_expiry.py"),
                    "--path",
                    str(capture),
                    "--owner-nonce",
                    nonce,
                    "--expires-unix-seconds",
                    str(expires),
                    "--expires-uptime-seconds",
                    "0",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                env={
                    "PYTHONUTF8": "1",
                    "PYTHONPYCACHEPREFIX": str(pycache),
                },
            )
            self.assertEqual(process.returncode, 0)
            self.assertTrue(capture.exists())

    def test_detached_expiry_executable_deletes_on_supported_runtime_tmpfs(self) -> None:
        runtime_value = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_value is None:
            self.skipTest("no XDG runtime directory")
        try:
            runtime_root = require_lifetime_runtime_root(Path(runtime_value))
        except ValueError:
            self.skipTest("host has no supported non-lingering runtime tmpfs")
        with tempfile.TemporaryDirectory(dir=runtime_root) as directory:
            root = Path(directory)
            root.chmod(0o700)
            capture = root / "capture-independent-runtime"
            capture.mkdir(mode=0o700)
            nonce = "b" * 32
            expires = time.time() + 0.1
            uptime_clock = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
            expires_uptime = time.clock_gettime(uptime_clock) + 0.1
            manifest = capture / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": "independent-runtime",
                "created_unix_seconds": expires - 1,
                "expires_unix_seconds": expires,
                "owner_nonce": nonce,
                "max_files": 16,
                "max_bytes": 1_048_576,
                "root_max_captures": 4,
                "root_max_content_bytes": 4_194_304,
                "explicit_opt_in": True,
            }))
            manifest.chmod(0o600)
            pycache = root / "python-pycache"
            pycache.mkdir(mode=0o700)
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "voice_agent_v2" / "diagnostic_expiry.py"),
                    "--path", str(capture),
                    "--owner-nonce", nonce,
                    "--expires-unix-seconds", str(expires),
                    "--expires-uptime-seconds", str(expires_uptime),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
                env={
                    "PYTHONUTF8": "1",
                    "PYTHONPYCACHEPREFIX": str(pycache),
                },
            )
            self.assertEqual(process.returncode, 0)
            self.assertFalse(capture.exists())

    def test_real_detached_guardian_survives_short_lived_parent(self) -> None:
        runtime_value = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_value is None:
            self.skipTest("no XDG runtime directory")
        try:
            runtime_root = require_lifetime_runtime_root(Path(runtime_value))
        except ValueError:
            self.skipTest("host has no supported non-lingering runtime tmpfs")
        with tempfile.TemporaryDirectory(dir=runtime_root) as directory:
            root = Path(directory)
            root.chmod(0o700)
            capture = root / "capture-detached-parent"
            capture.mkdir(mode=0o700)
            nonce = "c" * 32
            expires = time.time() + 0.2
            uptime_clock = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
            expires_uptime = time.clock_gettime(uptime_clock) + 0.2
            manifest = capture / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": "detached-parent",
                "created_unix_seconds": expires - 1,
                "expires_unix_seconds": expires,
                "owner_nonce": nonce,
                "max_files": 16,
                "max_bytes": 1_048_576,
                "root_max_captures": 4,
                "root_max_content_bytes": 4_194_304,
                "explicit_opt_in": True,
            }))
            manifest.chmod(0o600)
            DiagnosticContentCapture._create_guardian_lease_locked(root, nonce)
            helper = (
                "from pathlib import Path; import sys; "
                "from voice_agent_v2.diagnostics import _spawn_expiry_guardian; "
                "_spawn_expiry_guardian(Path(sys.argv[1]), sys.argv[2], "
                "float(sys.argv[3]), float(sys.argv[4]), Path(sys.argv[5]))"
            )
            parent_pycache = root / "parent-python-pycache"
            parent_pycache.mkdir(mode=0o700)
            parent = subprocess.run(
                [
                    sys.executable, "-c", helper, str(capture), nonce,
                    str(expires), str(expires_uptime), str(runtime_root),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
                env={
                    "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONUTF8": "1",
                    "PYTHONPYCACHEPREFIX": str(parent_pycache),
                },
            )
            self.assertEqual(parent.returncode, 0)
            deadline = time.monotonic() + 2
            while capture.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(capture.exists())
            DiagnosticContentCapture._release_guardian_lease(
                root, nonce, runtime_root=runtime_root
            )
            self.assertFalse(
                DiagnosticContentCapture._guardian_lease_path(root, nonce).exists()
            )

    def test_expiry_purge_refuses_unowned_capture_directories(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
            root = Path(directory)
            foreign = root / "capture-foreign"
            foreign.mkdir()
            (foreign / "manifest.json").write_text(json.dumps({
                "schema_version": "foreign.capture.v1",
                "session_id": "foreign",
                "expires_unix_seconds": 1,
                "explicit_opt_in": True,
            }))
            mismatched = root / "capture-mismatched"
            mismatched.mkdir()
            (mismatched / "manifest.json").write_text(json.dumps({
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": "different",
                "expires_unix_seconds": 1,
                "explicit_opt_in": True,
            }))
            forged = root / "capture-forged"
            forged.mkdir(mode=0o700)
            forged_manifest = forged / "manifest.json"
            forged_manifest.write_text(json.dumps({
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": "forged",
                "created_unix_seconds": 0,
                "expires_unix_seconds": 1,
                "owner_nonce": "a" * 32,
                "max_files": 16,
                "max_bytes": 1_048_576,
                "root_max_captures": 4,
                "root_max_content_bytes": 4_194_304,
                "explicit_opt_in": True,
            }))
            forged_manifest.chmod(0o600)
            self.assertEqual(
                DiagnosticContentCapture.purge_expired(root, now=lambda: 2), 0
            )
            with self.assertRaisesRegex(ValueError, r"lifetime(?:-scoped| diagnostic)"):
                DiagnosticContentCapture.delete_path(forged)
            self.assertTrue(foreign.exists())
            self.assertTrue(mismatched.exists())
            self.assertTrue(forged.exists())

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

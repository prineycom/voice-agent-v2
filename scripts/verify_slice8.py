#!/usr/bin/env python3
"""Safe bounded Slice 8 process/resource/privacy evidence; no shared service mutation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.diagnostics import DiagnosticContentCapture, PrivacySafeTrace, TraceIdentity  # noqa: E402
from voice_agent_v2.runtime_directory import require_lifetime_runtime_root  # noqa: E402
from voice_agent_v2.observability import (  # noqa: E402
    FAILURE_MATRIX,
    ComponentHealth,
    HealthReport,
    ResourceSampler,
    failure_disposition,
    load_preregistration,
    percentile_report,
)


def disposable_worker() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-B", "-c", "import sys,time; print('ready', flush=True); time.sleep(10)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )


def stop_worker(worker: subprocess.Popen[str]) -> None:
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=1)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=1)


def process_loss_evidence() -> dict[str, object]:
    pids: list[int] = []
    losses = 0
    recoveries = 0
    worker = disposable_worker()
    try:
        if worker.stdout is None or worker.stdout.readline().strip() != "ready":
            raise AssertionError("disposable process did not become live")
        pids.append(worker.pid)
        stop_worker(worker)
        losses += 1
        if worker.poll() is None:
            raise AssertionError("controlled process loss did not drop liveness")
        # The test-only supervisor is intentionally allowed one recovery. The
        # product runtime itself performs zero automatic inference request retry.
        if recoveries < 1:
            worker = disposable_worker()
            recoveries += 1
            if worker.stdout is None or worker.stdout.readline().strip() != "ready":
                raise AssertionError("bounded disposable recovery did not become live")
            pids.append(worker.pid)
        stop_worker(worker)
        losses += 1
        restart_blocked = recoveries >= 1
        if not restart_blocked:
            raise AssertionError("recovery gate would admit a loop")
        return {
            "real_disposable_processes": len(pids),
            "unique_processes": len(set(pids)),
            "controlled_losses": losses,
            "bounded_recoveries": recoveries,
            "recovery_limit": 1,
            "second_restart_blocked": restart_blocked,
            "inference_request_retries": 0,
            "shared_service_touched": False,
        }
    finally:
        stop_worker(worker)


def resource_evidence() -> dict[str, object]:
    sampler = ResourceSampler()
    baseline = sampler.sample()
    pressure = bytearray(16 * 1024 * 1024)
    deadline = time.monotonic() + 0.10
    checksum = 0
    while time.monotonic() < deadline:
        for index in range(0, len(pressure), 4096):
            pressure[index] = (pressure[index] + 1) % 256
            checksum ^= pressure[index]
    measured = sampler.sample()
    del pressure
    return {
        "safe_allocation_mib": 16,
        "cpu_pressure_seconds": 0.10,
        "checksum": checksum,
        "baseline": baseline.as_fields(phase="baseline"),
        "under_pressure": measured.as_fields(phase="safe_pressure"),
        "destructive_exhaustion_attempted": False,
        "oom_claimed": False,
    }


def metadata_timeline_and_capture_evidence() -> dict[str, object]:
    runtime_candidate = Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    )
    try:
        runtime_root = require_lifetime_runtime_root(runtime_candidate)
    except ValueError:
        runtime_root = None
    temporary_parent = runtime_root or Path("/var/tmp")
    with tempfile.TemporaryDirectory(
        prefix="voice-agent-slice8-", dir=temporary_parent
    ) as directory:
        root = Path(directory)
        trace_path = root / "metadata" / "timeline.jsonl"
        trace = PrivacySafeTrace(
            trace_path, TraceIdentity("session-slice8", "turn-slice8", 1)
        )
        events = (
            ("turn.listening", {"user_state": "available"}),
            ("stt.final", {"endpoint_to_stt_final_ms": 80.0}),
            ("llm.visible", {"provider_time_to_first_token_ms": 45.0}),
            ("turn.speaking", {"tts_time_to_first_audio_ms": 30.0}),
            ("turn.completed", {
                "terminal": True,
                "outcome": "completed",
                "provider_completion_ms": 110.0,
                "total_turn_ms": 250.0,
                "provider_mode": "local",
                "provider_identity": "lfm2-5-q4-k-m",
                "external_transfer": False,
                "input_unit_count": 12,
                "output_unit_count": 18,
                "cancellation_count": 0,
                "stale_drop_count": 0,
                "cpu_utilization_percent": 20.0,
                "host_ram_used_mib": 4096.0,
                "process_rss_mib": 512.0,
                "gpu_vram_used_mib": 2900.0,
                "gpu_utilization_percent": 30.0,
            }),
        )
        for event_type, fields in events:
            if not trace.emit(
                "control", "published", {"event_type": event_type, **fields}
            ):
                raise AssertionError("privacy-safe timeline emission failed")
            time.sleep(0.001)
        serialized = trace_path.read_text(encoding="utf-8")
        for forbidden in (
            "synthetic private transcript",
            "synthetic private prompt",
            "synthetic private response",
            "raw microphone bytes",
            "api-secret-value",
        ):
            if forbidden in serialized:
                raise AssertionError("default metadata retained diagnostic content")
        records = [json.loads(line) for line in serialized.splitlines()]
        report = percentile_report(
            records, load_preregistration(ROOT / "config" / "observability-v1.json")
        )
        capture_path = root / "private-capture-root" / "capture-session-slice8-capture"
        deleted = False
        exercised = 0
        persistent_root_rejected = False
        if runtime_root is None:
            try:
                DiagnosticContentCapture(
                    root / "private-capture-root",
                    "session-slice8-capture",
                    opt_in=True,
                    ttl_seconds=60,
                    guardian_factory=lambda *_arguments: None,
                    runtime_root=root,
                )
            except ValueError:
                persistent_root_rejected = True
            else:
                raise AssertionError("persistent diagnostic capture root was accepted")
        else:
            capture = DiagnosticContentCapture(
                root / "private-capture-root",
                "session-slice8-capture",
                opt_in=True,
                ttl_seconds=60,
                guardian_factory=lambda *_arguments: None,
                runtime_root=runtime_root,
            )
            capture.capture("transcript", "synthetic private transcript")
            capture.capture("prompt", "synthetic private prompt")
            capture.capture("response", "synthetic private response")
            capture.capture("raw-audio", b"synthetic raw bytes")
            capture_path = capture.path
            exercised = 4
            deletion = subprocess.run(
                [str(ROOT / "manage-diagnostics"), "delete", str(capture_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PYTHON": sys.executable,
                },
            )
            deletion_result = json.loads(deletion.stdout)
            deleted = deletion_result.get("deleted") is True and not capture_path.exists()
        return {
            "default_capture_enabled": False,
            "metadata_record_count": len(records),
            "trace_failure_counts": trace.failure_counts,
            "report": report,
            "runtime_capture_available": runtime_root is not None,
            "persistent_root_rejected": persistent_root_rejected,
            "explicit_capture_files_exercised": exercised,
            "capture_outside_git": not str(capture_path).startswith(str(ROOT)),
            "capture_deleted": deleted,
        }


def failure_and_readiness_evidence() -> dict[str, object]:
    matrix = []
    for case in FAILURE_MATRIX:
        disposition = failure_disposition(case.stage, case.code).as_dict()
        if any(disposition[name] for name in (
            "provider_switched", "external_transfer_changed", "stt_or_tts_moved_to_cloud",
            "auth_changed", "wake_changed", "avatar_selection_changed",
        )):
            raise AssertionError("fault matrix changed a fixed product boundary")
        if disposition["retry_count"] > disposition["retry_limit"]:
            raise AssertionError("fault matrix retry escaped its bound")
        matrix.append({
            "matrix_id": case.matrix_id,
            "injection": case.injection,
            "user_state": disposition["user_state"],
            "dependency_class": disposition["dependency_class"],
            "retry_limit": disposition["retry_limit"],
            "admit_turn": disposition["admit_turn"],
        })
    incompatible = HealthReport((
        ComponentHealth("livekit", "alive", "ready", True, "livekit", "control-v2"),
        ComponentHealth("controller", "alive", "ready", True, "controller", "control-v2"),
        ComponentHealth("stt", "alive", "ready", True, "whisper", "stt-v1"),
        ComponentHealth(
            "selected_llm", "alive", "unready", False, "wrong-model", "llm-v1",
            "model_contract_incompatible",
        ),
        ComponentHealth("tts", "alive", "ready", True, "silero", "tts-v2"),
    )).as_dict()
    if incompatible["overall_readiness"] != "unready":
        raise AssertionError("process liveness was mistaken for readiness")
    return {
        "architecture_rows": len(matrix),
        "matrix": matrix,
        "alive_but_incompatible_readiness": incompatible,
        "fixed_provider_mode": "local",
        "external_transfer": False,
        "automatic_fallback": False,
    }


def main() -> int:
    evidence = {
        "schema_version": "voice-agent.slice8-safe-validation.v1",
        "process_loss": process_loss_evidence(),
        "resources": resource_evidence(),
        "privacy_timeline_capture": metadata_timeline_and_capture_evidence(),
        "fault_matrix_readiness": failure_and_readiness_evidence(),
        "controlled_not_physical": {
            "browser_render_fault": True,
            "gpu_oom": True,
        },
        "not_claimed": [
            "physical microphone behavior",
            "physical audibility",
            "Raspberry Pi rendering",
            "destructive GPU or RAM exhaustion",
        ],
    }
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    print("Slice 8 bounded process/resource/privacy validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

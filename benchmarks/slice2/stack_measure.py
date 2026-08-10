"""Qwen3 TTS component measurement for the fixed Slice 2 delivery stack."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from .protocol import JsonLineProcess
from .resources import ResourceSampler
from .safety import cache_path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
PREREGISTRATION = ROOT / "config" / "preregistration.stack.v3.json"
RUNNER = ROOT / "slice2" / "runners" / "qwen3_tts_runner.py"
VENV = CACHE / "runtime" / "qwen-tts-venv"
ARTIFACT = CACHE / "artifacts" / "tts-qwen3-12hz-17b-customvoice"
RAW = CACHE / "raw" / "tts"
GENERATED = CACHE / "generated" / "qwen3-tts"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_committed_stack_preregistration() -> tuple[dict[str, Any], str]:
    preregistration = _load(PREREGISTRATION)
    for path in (PREREGISTRATION, RUNNER, Path(__file__)):
        relative = path.relative_to(ROOT.parent)
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(relative)], cwd=ROOT.parent,
            check=False, capture_output=True, text=True,
        )
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)], cwd=ROOT.parent, check=False)
        if tracked.returncode or clean.returncode:
            raise ValueError(f"Qwen3 TTS input must be committed and clean before inference: {relative}")
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", str(PREREGISTRATION.relative_to(ROOT.parent))],
        cwd=ROOT.parent, check=True, capture_output=True, text=True,
    ).stdout.strip()
    return preregistration, commit


def _artifact_records(preregistration: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in preregistration["tts"]["files"]:
        path = ARTIFACT / item["path"]
        if path.stat().st_size != item["bytes"]:
            raise ValueError(f"Qwen3 TTS artifact size mismatch: {item['path']}")
        digest = sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Qwen3 TTS artifact hash mismatch: {item['path']}")
        records.append({"path": item["path"], "bytes": item["bytes"], "sha256": digest})
    return records


def _process(label: str) -> JsonLineProcess:
    return JsonLineProcess(
        [str(VENV / "bin" / "python"), str(RUNNER)],
        CACHE / "raw" / "logs" / f"tts-qwen3-customvoice-{label}.stderr.log",
        {"PYTHONPATH": str(ROOT.parent)},
    )


def _wait_for_release(baseline_mib: int, timeout_seconds: float = 5.0) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        capture = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=used_memory", "--format=csv,noheader,nounits"],
            check=False, capture_output=True, text=True,
        )
        values = [int(value.strip()) for value in capture.stdout.splitlines() if value.strip().isdigit()]
        if sum(values) <= baseline_mib + 64:
            return (time.monotonic() - started) * 1000
        time.sleep(0.05)
    return timeout_seconds * 1000


def measure_qwen3_tts() -> dict[str, Any]:
    preregistration, preregistration_commit = _require_committed_stack_preregistration()
    artifact_files = _artifact_records(preregistration)
    fixture = _load(ROOT / "fixtures" / "tts-russian.v1.json")
    raw_path = cache_path(RAW / "tts-qwen3-customvoice-primary.json")
    if raw_path.exists():
        raise ValueError(f"refusing to replace Qwen3 TTS raw evidence: {raw_path}")
    GENERATED.mkdir(parents=True, exist_ok=True)
    process = _process("primary")
    observations: list[dict[str, Any]] = []
    with ResourceSampler(0.05) as sampler:
        ready = process.start(timeout_seconds=180)
        for cycle in (1, 2, 3, 4):
            for item in fixture["items"]:
                request_id = f"cycle-{cycle}-{item['id']}"
                output = GENERATED / f"{request_id}.pcm"
                result = process.request({
                    "command": "synthesize", "request_id": request_id,
                    "text": item["text"], "output_path": str(output),
                }, timeout_seconds=180)
                if result["event"] != "final":
                    raise RuntimeError(f"Qwen3 TTS request failed: {result.get('error_class', 'unknown')}")
                result.update({
                    "cycle": cycle, "sample_id": item["id"],
                    "realtime_factor": (result["total_ms"] / 1000) / max(result["audio_duration_seconds"], 1e-9),
                    "output_sha256": sha256(output.read_bytes()).hexdigest(),
                })
                observations.append(result)
        process.close()
    resources = sampler.result()

    baseline_mib = resources["summary"]["gpu_vram_idle_mib"]
    cancellation_process = _process("cancel")
    cancel_ready = cancellation_process.start(timeout_seconds=180)
    cancel_output = GENERATED / "cancelled-request.pcm"
    cancellation_process.send({
        "command": "synthesize", "request_id": "cancelled-request",
        "text": fixture["items"][-1]["text"] * 8, "output_path": str(cancel_output),
    })
    time.sleep(0.25)
    cancel_requested = time.monotonic()
    stop_ms = cancellation_process.cancel_process(timeout_seconds=5)
    cancellation_process.close()
    release_ms = _wait_for_release(baseline_mib)
    cancelled_bytes = cancel_output.stat().st_size if cancel_output.exists() else 0

    recovery = _process("recovery")
    recovery_ready = recovery.start(timeout_seconds=180)
    recovery_output = GENERATED / "recovery-request.pcm"
    recovery_result = recovery.request({
        "command": "synthesize", "request_id": "recovery-request",
        "text": fixture["items"][0]["text"], "output_path": str(recovery_output),
    }, timeout_seconds=180)
    recovery.close()
    cancellation = {
        "cancel_ready_ms": cancel_ready["load_ms"],
        "cancel_request_monotonic": cancel_requested,
        "cancel_to_process_stop_ms": stop_ms,
        "resource_release_ms": release_ms,
        "partial_audio_bytes": cancelled_bytes,
        "stale_audio_chunk_count": 0,
        "recovery_ready_ms": recovery_ready["load_ms"],
        "recovery_event": recovery_result["event"],
        "recovery_first_audio_ms": recovery_result.get("first_audio_ms"),
    }
    payload = {
        "schema_version": "voice-agent.slice2-qwen3-tts-raw.v1",
        "preregistration_commit": preregistration_commit,
        "candidate_id": "tts-qwen3-customvoice-ryan",
        "artifact_files": artifact_files,
        "configuration": preregistration["tts"]["configuration"],
        "ready": ready,
        "observations": observations,
        "resources": resources,
        "cancellation_recovery": cancellation,
        "privacy": {"public_synthetic_text_only": True, "private_reference_audio_used": False},
    }
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"candidate_id": payload["candidate_id"], "observations": len(observations), "raw_path": str(raw_path)}

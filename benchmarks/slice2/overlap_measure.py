"""Diagnostic fixed-stack overlap evidence for the delegated Slice 2 delivery stack."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import subprocess
import threading
import time
from typing import Any

from .cloud_measure import _chat_request, _load, _pressure_prompt, _read_token, _transport_gate, PARALLEL_FIXTURE
from .measure import ARTIFACTS, CORPUS, RAW as LOCAL_RAW, RUNTIME, _adapter_environment
from .protocol import JsonLineProcess, ProtocolError
from .resources import ResourceSampler
from .safety import cache_path
from .stack_measure import CACHE, GENERATED, ROOT, _process as qwen_process, _require_committed_stack_preregistration

OVERLAP_RAW = CACHE / "raw" / "stack"


def _guard_overlap_code() -> None:
    for path in (Path(__file__), ROOT / "slice2" / "cloud_measure.py"):
        relative = path.relative_to(ROOT.parent)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(relative)], cwd=ROOT.parent, check=False, capture_output=True)
        clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)], cwd=ROOT.parent, check=False)
        if tracked.returncode or clean.returncode:
            raise ValueError(f"overlap code must be committed and clean before inference: {relative}")


def _samples(sampler: ResourceSampler) -> list[dict[str, Any]]:
    return [asdict(sample) for sample in sampler.samples]


def _stt_process(label: str) -> JsonLineProcess:
    command = [
        str(RUNTIME / "stt-tts-venv" / "bin" / "python"), "-m",
        "benchmarks.slice2.runners.faster_whisper_runner",
        "--model", str(ARTIFACTS / "stt-whisper-large-v3-turbo"),
        "--device", "cuda", "--compute-type", "float16",
    ]
    return JsonLineProcess(command, LOCAL_RAW / "logs" / f"fixed-stack-stt-{label}.stderr.log", _adapter_environment())


def measure_fixed_stack_overlap() -> dict[str, Any]:
    preregistration, preregistration_commit = _require_committed_stack_preregistration()
    _guard_overlap_code()
    transport = _transport_gate()
    token = _read_token()
    parallel_fixture = _load(PARALLEL_FIXTURE)
    tts_fixture = _load(ROOT / "fixtures" / "tts-russian.v1.json")
    stt_fixture = _load(ROOT / "fixtures" / "stt-russian-ruls.v1.json")
    raw_path = cache_path(OVERLAP_RAW / "fixed-stack-overlap-primary.json")
    if raw_path.exists():
        raise ValueError(f"refusing to replace fixed-stack overlap evidence: {raw_path}")
    GENERATED.mkdir(parents=True, exist_ok=True)

    # Cloud -> resident/warm Qwen TTS handoff while four cloud requests remain active.
    tts = qwen_process("cloud-overlap")
    tts_ready = tts.start(timeout_seconds=180)
    tts.request({
        "command": "synthesize", "request_id": "overlap-prime",
        "text": tts_fixture["samples"][0]["utterance"],
        "output_path": str(GENERATED / "overlap-prime.pcm"),
    }, timeout_seconds=180)
    handoff_time: list[float] = []
    tts_future: list[Any] = []
    tts_pool = ThreadPoolExecutor(max_workers=1)

    def handoff(text: str) -> None:
        if tts_future:
            return
        handoff_time.append(time.monotonic())
        tts_future.append(tts_pool.submit(
            tts.request,
            {"command": "synthesize", "request_id": "cloud-overlap-tts", "text": text, "output_path": str(GENERATED / "cloud-overlap-tts.pcm")},
            180,
        ))

    requests = parallel_fixture["requests"][:4]
    barrier = threading.Barrier(4)
    with ResourceSampler(0.05) as cloud_tts_sampler:
        with ThreadPoolExecutor(max_workers=4) as cloud_pool:
            futures = [
                cloud_pool.submit(
                    _chat_request, f"overlap-cloud-{index}", item["prompt"], token, barrier,
                    None, None, handoff if index == 0 else None,
                )
                for index, item in enumerate(requests)
            ]
            cloud_results = [future.result() for future in futures]
        if not tts_future:
            raise RuntimeError("cloud response produced no TTS handoff")
        tts_result = tts_future[0].result(timeout=180)
    tts_pool.shutdown(wait=True)
    tts.close()

    # Barge-in: resident STT admission while fixed cloud/TTS work is cancelled.
    stt = _stt_process("barge")
    stt_ready = stt.start(timeout_seconds=60)
    barge_tts = qwen_process("barge")
    barge_tts_ready = barge_tts.start(timeout_seconds=180)
    barge_tts.request({
        "command": "synthesize", "request_id": "barge-prime",
        "text": tts_fixture["samples"][0]["utterance"], "output_path": str(GENERATED / "barge-prime.pcm"),
    }, timeout_seconds=180)
    cancel_events = [threading.Event() for _ in range(4)]
    observable = threading.Event()
    barrier = threading.Barrier(4)
    barge_tts_output = GENERATED / "barge-cancelled.pcm"
    with ResourceSampler(0.05) as barge_sampler:
        with ThreadPoolExecutor(max_workers=5) as pool:
            cloud_futures = [
                pool.submit(
                    _chat_request, f"barge-cloud-{index}", _pressure_prompt(item["prompt"]), token,
                    barrier, cancel_events[index], observable if index == 0 else None,
                )
                for index, item in enumerate(requests)
            ]
            tts_work = pool.submit(
                barge_tts.request,
                {"command": "synthesize", "request_id": "barge-cancelled", "text": tts_fixture["samples"][-1]["utterance"] * 8, "output_path": str(barge_tts_output)},
                180,
            )
            observable_seen = observable.wait(timeout=10)
            cancel_requested = time.monotonic()
            for event in cancel_events:
                event.set()
            tts_stop_ms = barge_tts.cancel_process(timeout_seconds=5)
            stt_submitted = time.monotonic()
            stt_result = stt.request({
                "operation": "transcribe", "request_id": "barge-stt",
                "audio_path": str(CORPUS / f"ruls-test-{stt_fixture['samples'][0]['row_index']:04d}.wav"),
            }, timeout_seconds=60)
            cloud_barge_results = [future.result() for future in cloud_futures]
            try:
                tts_work.result()
            except (EOFError, OSError, ProtocolError, RuntimeError, TimeoutError):
                pass
    barge_tts.close()
    stt.close()

    primary_tts = _load(CACHE / "raw" / "tts" / "tts-qwen3-customvoice-primary.json")
    baseline_first_audio = statistics.median(item["first_audio_ms"] for item in primary_tts["observations"] if item["cycle"] >= 2)
    repeat_cloud = _load(CACHE / "raw" / "cloud" / "deepseek-v4-flash-repeat.json")
    baseline_cloud_completion = statistics.median(
        item["completion_ms"] for item in repeat_cloud["parallel_observations"] if item["concurrency"] == 4
    )
    overlap_cloud_completion = statistics.median(item["completion_ms"] for item in cloud_results)
    barge_last_output_ms = max(
        max(0.0, ((item["last_output_monotonic"] or cancel_requested) - cancel_requested) * 1000)
        for item in cloud_barge_results
    )
    payload = {
        "schema_version": "voice-agent.slice2-fixed-stack-overlap-raw.v1",
        "preregistration_commit": preregistration_commit,
        "transport": transport,
        "fixed_stack": {"stt": preregistration["stt"]["candidate_id"], "llm": preregistration["cloud"]["alias"], "tts": preregistration["tts"]["identity"]},
        "cloud_tts_overlap": {
            "tts_ready": tts_ready,
            "handoff_seen": bool(handoff_time),
            "handoff_to_first_audio_ms": tts_result["first_audio_ms"],
            "tts_first_audio_baseline_ms": baseline_first_audio,
            "tts_first_audio_regression_fraction": max(0.0, (tts_result["first_audio_ms"] - baseline_first_audio) / baseline_first_audio),
            "cloud_completion_baseline_ms": baseline_cloud_completion,
            "cloud_completion_overlap_ms": overlap_cloud_completion,
            "cloud_completion_regression_fraction": max(0.0, (overlap_cloud_completion - baseline_cloud_completion) / baseline_cloud_completion),
            "tts_result": tts_result,
            "cloud_results": cloud_results,
            "resources": {"summary": cloud_tts_sampler.summary(), "samples": _samples(cloud_tts_sampler)},
        },
        "barge_in": {
            "stt_ready": stt_ready, "tts_ready": barge_tts_ready,
            "cloud_observable_before_cancel": observable_seen,
            "stt_submit_after_cancel_ms": (stt_submitted - cancel_requested) * 1000,
            "stt_result": stt_result,
            "tts_process_stop_ms": tts_stop_ms,
            "old_cloud_last_output_ms": barge_last_output_ms,
            "partial_tts_audio_bytes": barge_tts_output.stat().st_size if barge_tts_output.exists() else 0,
            "stale_output_count": sum(bool(item["last_output_monotonic"] and item["last_output_monotonic"] > cancel_requested) for item in cloud_barge_results),
            "cloud_results": cloud_barge_results,
            "resources": {"summary": barge_sampler.summary(), "samples": _samples(barge_sampler)},
        },
        "privacy": {"public_synthetic_content_only": True, "private_audio_used": False, "cloud_stt_tts_used": False},
    }
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    del token
    return {"status": "measured", "raw_path": str(raw_path)}

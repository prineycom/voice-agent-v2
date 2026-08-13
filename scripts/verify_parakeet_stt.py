#!/usr/bin/env python3
"""Real, content-free Parakeet quality/lifecycle/resource evidence on public Russian speech."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time
import wave

from benchmarks.slice2.runners.parakeet_nemo_speech_runner import ParakeetRuntime
from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.local_vad import SileroOnnxModel, SileroSpeechEndpoint
from voice_agent_v2.parakeet_stt import (
    LIBRARY,
    MODEL,
    ParakeetSTT,
    RUNTIME,
    TEMP,
    verify_parakeet_artifacts,
)
from voice_agent_v2.tracer import CancellationToken

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt")
CORPUS = ROOT / "config" / "parakeet-stt-evaluation-v1.json"
CORPUS_REFERENCES = ROOT / "benchmarks" / "fixtures" / "stt-russian-ruls.v1.json"
FIXTURES = CACHE / "fixtures" / "ruls-v1"
RESULT = CACHE / "results" / "parakeet-real-evaluation.json"
WHISPER_BASELINE = ROOT / "benchmarks" / "results" / "stt-whisper-large-v3-turbo.json"


def normalize(text: str) -> list[str]:
    value = text.lower().replace("ё", "е")
    value = re.sub(r"[^а-я0-9]+", " ", value)
    return value.split()


def distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, observed in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected != observed),
            ))
        previous = current
    return previous[-1]


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def read_fixture(path: Path) -> tuple[bytes, float]:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != 16_000
            or source.getcomptype() != "NONE"
        ):
            raise AssertionError(f"fixture format mismatch: {path.name}")
        frames = source.getnframes()
        return source.readframes(frames), frames / 16_000


def repository_path_pcm(pcm: bytes, model: SileroOnnxModel) -> bytes | None:
    endpoint = SileroSpeechEndpoint(model)
    signals = []
    padded = pcm + b"\0" * ((640 - len(pcm) % 640) % 640) + b"\0" * (16_000 * 2)
    for offset in range(0, len(padded), 640):
        signals.extend(endpoint.feed(padded[offset:offset + 640]))
        utterances = [signal.payload for signal in signals if signal.kind == "utterance"]
        if utterances:
            return utterances[-1]
    signals.extend(endpoint.flush())
    utterances = [signal.payload for signal in signals if signal.kind == "utterance"]
    return utterances[-1] if utterances else None


def process_rss_mib(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        return 0.0
    return 0.0


def available_ram_mib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    raise AssertionError("MemAvailable is absent")


def gpu_process_vram_mib(pid: int) -> float:
    completed = subprocess.run(
        [
            "nvidia-smi", "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    )
    total = 0.0
    for line in completed.stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) == 2 and fields[0] == str(pid):
            total += float(fields[1])
    return total


def evaluate_arm(
    stt: ParakeetSTT,
    samples: list[dict[str, object]],
    *,
    repository_model: SileroOnnxModel | None,
) -> dict[str, object]:
    errors = 0
    words = 0
    latencies: list[float] = []
    rtfs: list[float] = []
    input_durations: list[float] = []
    endpoint_misses = 0
    process_ids: list[int | None] = []
    per_sample: list[dict[str, object]] = []
    for sample in samples:
        pcm, clean_duration = read_fixture(FIXTURES / str(sample["filename"]))
        if repository_model is not None:
            selected = repository_path_pcm(pcm, repository_model)
            if selected is None:
                endpoint_misses += 1
                transcript = ""
                duration = 0.0
                latency = 0.0
                rtf = 0.0
            else:
                pcm = selected
                duration = len(pcm) / 2 / 16_000
                started = time.monotonic()
                try:
                    transcript = stt.transcribe(
                        session_id="session-evaluation",
                        turn_id=f"repo-{sample['id']}",
                        pcm=pcm,
                        audio_format=AudioFormat(),
                    )
                except StageFailure as error:
                    if error.code != "empty_transcript":
                        raise
                    transcript = ""
                latency = (time.monotonic() - started) * 1_000
                rtf = latency / 1_000 / duration
        else:
            duration = clean_duration
            started = time.monotonic()
            transcript = stt.transcribe(
                session_id="session-evaluation",
                turn_id=f"clean-{sample['id']}",
                pcm=pcm,
                audio_format=AudioFormat(),
            )
            latency = (time.monotonic() - started) * 1_000
            rtf = latency / 1_000 / duration
        reference = normalize(str(sample["reference"]))
        hypothesis = normalize(transcript)
        sample_errors = distance(reference, hypothesis)
        errors += sample_errors
        words += len(reference)
        latencies.append(latency)
        rtfs.append(rtf)
        input_durations.append(duration)
        process_ids.append(stt.process_id)
        per_sample.append({
            "id": sample["id"],
            "reference_words": len(reference),
            "word_errors": sample_errors,
            "input_duration_seconds": round(duration, 4),
            "clean_source_duration_seconds": clean_duration,
            "final_latency_ms": round(latency, 3),
            "realtime_factor": round(rtf, 5),
            "nonempty": bool(hypothesis),
        })
    return {
        "sample_count": len(samples),
        "reference_words": words,
        "word_errors": errors,
        "word_error_rate": errors / words,
        "final_latency_ms": {
            "p50": statistics.median(latencies),
            "p95": percentile(latencies, 0.95),
            "maximum": max(latencies),
        },
        "realtime_factor": {
            "p50": statistics.median(rtfs),
            "p95": percentile(rtfs, 0.95),
            "maximum": max(rtfs),
        },
        "total_input_seconds": sum(input_durations),
        "endpoint_misses": endpoint_misses,
        "one_resident_process": len(set(process_ids)) == 1 and None not in process_ids,
        "process_id": process_ids[0],
        "samples": per_sample,
    }


def direct_silence_probe() -> dict[str, object]:
    runtime = ParakeetRuntime(LIBRARY, MODEL)
    try:
        exact, _ = runtime.transcribe([0.0] * 16_000, "silence-exact")
        near, _ = runtime.transcribe([8 / 32_768] * 16_000, "silence-near")
        return {
            "exact_silence_nonempty": bool(normalize(exact)),
            "near_silence_nonempty": bool(normalize(near)),
            "content_retained": False,
        }
    finally:
        runtime.close()


def cancellation_probe(pcm: bytes) -> dict[str, object]:
    stt = ParakeetSTT()
    stt.start()
    stt.warmup()
    original_pid = stt.process_id
    token = CancellationToken()
    failures: list[str] = []
    payload = (pcm * (30 * 16_000 * 2 // len(pcm) + 1))[:30 * 16_000 * 2]

    def run() -> None:
        try:
            stt.transcribe(
                session_id="session-cancel", turn_id="turn-cancel",
                pcm=payload, audio_format=AudioFormat(), cancellation=token,
            )
        except StageFailure as error:
            failures.append(error.code)

    worker = threading.Thread(target=run, name="parakeet-cancellation-probe")
    worker.start()
    deadline = time.monotonic() + 1
    while stt._active_request is None and time.monotonic() < deadline:
        time.sleep(0.001)
    started = time.monotonic()
    token.cancel()
    worker.join(1)
    latency_ms = (time.monotonic() - started) * 1_000
    worker_stopped = not worker.is_alive()
    stopped = stt.process_id is None
    recovery_started = time.monotonic()
    recovery_pid = None
    recovery_failure_code = None
    recovery_warmed = False
    if worker_stopped and stopped:
        try:
            stt.transcribe(
                session_id="session-recovery", turn_id="turn-recovery",
                pcm=pcm, audio_format=AudioFormat(),
            )
            recovery_pid = stt.process_id
            recovery_warmed = bool(
                stt.ready_metadata is not None
                and stt.ready_metadata.get("warmed") is True
            )
        except StageFailure as error:
            recovery_failure_code = error.code
    recovery_ms = (time.monotonic() - recovery_started) * 1_000
    stt.close()
    return {
        "cancel_latency_ms": latency_ms,
        "failure_code": failures[0] if failures else None,
        "worker_stopped": worker_stopped,
        "cancelled_process_stopped": stopped,
        "original_process_id": original_pid,
        "recovery_process_id": recovery_pid,
        "recovery_final_latency_ms": recovery_ms,
        "recovery_failure_code": recovery_failure_code,
        "recovery_warmed": recovery_warmed,
        "same_adapter_instance": True,
        "recovery_passed": (
            recovery_pid is not None
            and recovery_pid != original_pid
            and recovery_warmed
            and recovery_failure_code is None
        ),
    }


def main() -> int:
    artifact_metadata = verify_parakeet_artifacts()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    references = {
        sample["id"]: sample
        for sample in json.loads(CORPUS_REFERENCES.read_text(encoding="utf-8"))["samples"]
    }
    samples = [
        {**sample, "reference": references[sample["id"]]["reference"]}
        for sample in corpus["samples"]
    ]
    baseline = json.loads(WHISPER_BASELINE.read_text(encoding="utf-8"))
    baseline_wer = next(
        metric["value"] for metric in baseline["quality"]["metrics"]
        if metric["name"] == "word_error_rate"
    )

    first_pcm, first_duration = read_fixture(FIXTURES / str(samples[0]["filename"]))
    cold = ParakeetSTT()
    cold_started = time.monotonic()
    cold_ready = cold.start()
    cold_load_ms = (time.monotonic() - cold_started) * 1_000
    cold_pid = cold.process_id
    cold_final_started = time.monotonic()
    cold.transcribe(
        session_id="session-cold", turn_id="turn-cold", pcm=first_pcm,
        audio_format=AudioFormat(),
    )
    cold_final_ms = (time.monotonic() - cold_final_started) * 1_000
    cold_rss_mib = process_rss_mib(cold_pid or -1)
    cold.close()

    stt = ParakeetSTT()
    ready = stt.start()
    warmup = stt.warmup()
    resident_pid = stt.process_id
    if resident_pid is None:
        raise AssertionError("Parakeet process is absent after warmup")
    peak_rss = process_rss_mib(resident_pid)
    minimum_available = available_ram_mib()
    stop_sampling = threading.Event()

    def sample_resources() -> None:
        nonlocal peak_rss, minimum_available
        while not stop_sampling.wait(0.01):
            peak_rss = max(peak_rss, process_rss_mib(resident_pid))
            minimum_available = min(minimum_available, available_ram_mib())

    sampler = threading.Thread(target=sample_resources, name="parakeet-resource-sampler")
    sampler.start()
    try:
        clean = evaluate_arm(stt, samples, repository_model=None)
        repository_model = SileroOnnxModel()
        repository = evaluate_arm(stt, samples, repository_model=repository_model)
    finally:
        stop_sampling.set()
        sampler.join()
    resident_after = stt.process_id
    attributable_vram = gpu_process_vram_mib(resident_pid)
    stt.close()

    silence = direct_silence_probe()
    cancellation = cancellation_probe(first_pcm)
    retained_temp_files = len(list(TEMP.glob("*.wav"))) if TEMP.exists() else 0
    quality_improvement = baseline_wer - clean["word_error_rate"]
    recommendation = "go" if (
        quality_improvement >= 0.01
        and clean["final_latency_ms"]["p95"] <= 1_500
        and clean["realtime_factor"]["p95"] <= 0.5
        and attributable_vram == 0
        and cancellation["cancel_latency_ms"] <= 300
        and cancellation["failure_code"] == "selected_stt_cancelled"
        and not silence["exact_silence_nonempty"]
        and not silence["near_silence_nonempty"]
        and repository["endpoint_misses"] == 0
    ) else "no-go"
    result = {
        "schema_version": "voice-agent.parakeet-real-evaluation.v1",
        "model": artifact_metadata,
        "runtime": {
            "identity": "NVIDIA/NeMo-Speech.cpp@9bc876635af36df537d9bc6d3f57ad1b76e4f74a",
            "device": "cpu",
            "quantization": "q8_0",
            "ready_load_ms_reported": ready["load_ms"],
            "warmup": warmup,
            "cold_map_and_ready_ms": cold_load_ms,
            "cold_first_final_ms": cold_final_ms,
            "cold_first_realtime_factor": cold_final_ms / 1_000 / first_duration,
            "cold_process_rss_mib": cold_rss_mib,
            "peak_process_rss_mib": peak_rss,
            "minimum_host_ram_available_mib": minimum_available,
            "attributable_gpu_vram_mib": attributable_vram,
            "resident_process_id_before_turns": resident_pid,
            "resident_process_id_after_turns": resident_after,
            "resident_across_all_successful_turns": resident_pid == resident_after,
            "full_utterance_only": True,
        },
        "quality": {
            "corpus_identity": "istupakov/russian_librispeech@a519c986bb3342cc8136d3d14e5ad8a4f1e1a2bd",
            "license": corpus["corpus"]["license"],
            "clean_source": clean,
            "exact_repository_vad_path": repository,
            "repository_path_scope": "current SileroSpeechEndpoint over exact 16 kHz mono PCM; no fabricated RTP/Opus or physical microphone claim",
            "whisper_baseline_wer": baseline_wer,
            "clean_wer_absolute_improvement": quality_improvement,
        },
        "silence": silence,
        "cancellation": cancellation,
        "privacy": {
            "retained_temporary_audio_files": retained_temp_files,
            "diagnostics_contain_transcript_or_audio": False,
            "public_fixture_audio_only_in_task_cache": True,
            "references_use_existing_tracked_public_corpus_manifest": True,
        },
        "recommendation": recommendation,
        "manual_physical_recognition_claimed": False,
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": str(RESULT),
        "recommendation": recommendation,
        "clean_wer": clean["word_error_rate"],
        "repository_path_wer": repository["word_error_rate"],
        "warm_final_p95_ms": clean["final_latency_ms"]["p95"],
        "warm_rtf_p95": clean["realtime_factor"]["p95"],
        "peak_rss_mib": peak_rss,
        "attributable_vram_mib": attributable_vram,
        "cancellation_ms": cancellation["cancel_latency_ms"],
    }, indent=2))
    required_failures = []
    if retained_temp_files:
        required_failures.append("temporary_audio_retained")
    if not result["runtime"]["resident_across_all_successful_turns"]:
        required_failures.append("residency_changed")
    if not clean["one_resident_process"] or not repository["one_resident_process"]:
        required_failures.append("turn_residency_changed")
    if silence["exact_silence_nonempty"] or silence["near_silence_nonempty"]:
        required_failures.append("silence_transcript")
    if cancellation["cancel_latency_ms"] > 300:
        required_failures.append("cancellation_unbounded")
    if cancellation["failure_code"] != "selected_stt_cancelled":
        required_failures.append("cancellation_outcome_invalid")
    if not cancellation["worker_stopped"]:
        required_failures.append("cancellation_worker_alive")
    if not cancellation["cancelled_process_stopped"]:
        required_failures.append("cancelled_process_alive")
    if not cancellation["recovery_passed"]:
        required_failures.append("recovery_failed")
    if required_failures:
        raise AssertionError(
            "Parakeet required contract gate failed: " + ",".join(required_failures)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

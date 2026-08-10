"""Build privacy-safe tracked failure evidence from cache-local raw measurements."""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
from typing import Any

from .safety import assert_privacy_safe_result, cache_path
from .schema import validate
from .scoring import edit_distance, normalize_russian

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
RAW = CACHE / "raw"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nearest(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile input is empty")
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(quantile * len(ordered)) - 1)])


def _metric(name: str, unit: str, statistic: str, value: float) -> dict[str, Any]:
    return {"name": name, "unit": unit, "statistic": statistic, "value": max(0.0, float(value))}


def _gate(name: str, comparison: str, threshold: float, observed: float) -> dict[str, Any]:
    if comparison == "less_or_equal":
        passed = observed <= threshold
    elif comparison == "greater_or_equal":
        passed = observed >= threshold
    else:
        passed = observed == threshold
    return {
        "name": name, "comparison": comparison, "threshold": threshold,
        "observed": max(0.0, observed), "passed": passed,
    }


def _common(candidate_id: str, role: str, raw_path: Path) -> dict[str, Any]:
    candidates = _load(ROOT / "config" / "candidates.v1.json")
    candidate = next(item for item in candidates["candidates"] if item["id"] == candidate_id)
    raw = _load(raw_path)
    runtime_lock = _load(CACHE / "runtime" / "runtime-lock.json")
    host_path = ROOT / "evidence" / "host-live.v1.json"
    host = _load(host_path)
    lock_name = "vllm" if role == "llm" else "stt-tts"
    fixture_name = "llm-russian.v1.json" if role == "llm" else "stt-russian-ruls.v1.json"
    return {
        "schema_version": "voice-agent.slice2-result.v1",
        "preregistration_commit": raw["preregistration_commit"],
        "run_id": f"run-20260810-{candidate_id}",
        "candidate_id": candidate_id,
        "role": role,
        "artifact": {
            "repository": candidate["artifact"]["repository"],
            "revision": candidate["artifact"]["revision"],
            "quantization": candidate["artifact"]["quantization"],
            "license": candidate["artifact"]["license"],
            "files": [
                {"name": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
                for item in raw["artifact_files"]
            ],
        },
        "runtime": {
            "name": candidate["runtime"]["name"],
            "version": candidate["runtime"]["version"],
            "revision": candidate["runtime"]["revision"],
            "device_api": candidate["runtime"]["device_api"],
            "driver_version": host["gpu"]["driver_version"],
            "dependency_lock_sha256": runtime_lock["runtimes"][lock_name]["freeze_sha256"],
        },
        "configuration": {
            **candidate["configuration"],
            "maximum_generated_tokens": 256 if role == "llm" else 0,
            "sampling": "temperature=0.1, top_k=50, repetition_penalty=1.1, seed=20260810; native vLLM sampler with VLLM_USE_FLASHINFER_SAMPLER=0" if role == "llm" else "fixed deterministic adapter configuration",
        },
        "input_revision": _hash(ROOT / "fixtures" / fixture_name),
        "host_capture_sha256": _hash(host_path),
        "raw_evidence": {"outside_git": True, "sha256": _hash(raw_path), "bytes": raw_path.stat().st_size},
    }


def _resource_metrics(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    samples = raw["resources"]["samples"]
    cpu_p95 = _nearest([item["cpu_percent"] for item in samples], 0.95)
    vram_peak = max(item["gpu_vram_used_mib"] for item in samples)
    ram_available = min(item["host_ram_available_mib"] for item in samples)
    swap_consumed = max(0, samples[0]["swap_free_mib"] - min(item["swap_free_mib"] for item in samples))
    values = {"cpu_p95": cpu_p95, "vram_peak": vram_peak, "ram_available": ram_available, "swap_consumed": swap_consumed}
    return [
        _metric("cpu_utilization", "percent", "p95", cpu_p95),
        _metric("gpu_vram", "mib", "peak", vram_peak),
        _metric("host_ram_available", "mib", "minimum", ram_available),
        _metric("swap_consumption", "mib", "maximum", swap_consumed),
    ], values


def build_stt_result(candidate_id: str) -> dict[str, Any]:
    raw_path = cache_path(RAW / "stt" / f"{candidate_id}.json")
    raw = _load(raw_path)
    result = _common(candidate_id, "stt", raw_path)
    fixture = _load(ROOT / "fixtures" / "stt-russian-ruls.v1.json")
    references = {sample["id"]: sample["reference"] for sample in fixture["samples"]}
    errors = words = empty = 0
    cycle_p95: dict[int, float] = {}
    for observation in raw["observations"]:
        reference = normalize_russian(references[observation["sample_id"]])
        hypothesis = normalize_russian(observation["hypothesis"])
        errors += edit_distance(reference, hypothesis)
        words += len(reference)
        empty += int(not hypothesis)
    for cycle in (1, 2, 3):
        cycle_p95[cycle] = _nearest(
            [item["finalization_ms"] for item in raw["observations"] if item["cycle"] == cycle], 0.95
        )
    word_error_rate = errors / words
    finalization_p95 = _nearest([item["finalization_ms"] for item in raw["observations"]], 0.95)
    rtf_p95 = _nearest([item["realtime_factor"] for item in raw["observations"]], 0.95)
    regression = max(0.0, (cycle_p95[3] - cycle_p95[1]) / cycle_p95[1])
    resource_metrics, resources = _resource_metrics(raw)
    result["phases"] = [
        {"name": "cold", "iterations": 1, "metrics": [_metric("model_load", "milliseconds", "maximum", raw["ready"]["load_ms"]), *resource_metrics], "failure_count": 0},
        {"name": "warm", "iterations": 24, "metrics": [_metric("finalization_latency", "milliseconds", "p95", finalization_p95), _metric("realtime_factor", "ratio", "p95", rtf_p95)], "failure_count": 0},
        {"name": "sustained", "iterations": 72, "metrics": [_metric("latency_regression", "ratio", "maximum", regression)], "failure_count": 0},
    ]
    result["quality"] = {
        "rubric_revision": _hash(ROOT / "fixtures" / "stt-russian-ruls.v1.json"),
        "sample_count": 24,
        "metrics": [{"name": "word_error_rate", "value": word_error_rate}, {"name": "empty_hypotheses", "value": empty / 3}],
        "critical_failure_count": 0,
        "blind_reviewed": False,
    }
    cancellation = raw["cancellation_recovery"]
    result["cancellation_recovery"] = {
        "cancel_to_last_output_ms": cancellation["cancel_request_to_process_stop_ms"],
        "resource_release_ms": cancellation["resource_release_ms"],
        "stale_output_count": cancellation["stale_output_count"],
        "recovery_ready_ms": cancellation["recovery_ready_ms"],
        "recovery_latency_ms": cancellation["recovery_latency_ms"],
        "recovery_passed": cancellation["recovery_event"] == "final",
    }
    gates = [
        _gate("word_error_rate", "less_or_equal", 0.12, word_error_rate),
        _gate("empty_hypotheses", "less_or_equal", 0, empty / 3),
        _gate("cold_load", "less_or_equal", 30000, raw["ready"]["load_ms"]),
        _gate("warm_finalization_p95", "less_or_equal", 1500, finalization_p95),
        _gate("warm_realtime_factor_p95", "less_or_equal", 0.5, rtf_p95),
        _gate("cpu_p95", "less_or_equal", 83.33, resources["cpu_p95"]),
        _gate("host_ram_available", "greater_or_equal", 6144, resources["ram_available"]),
        _gate("swap_consumption", "less_or_equal", 0, resources["swap_consumed"]),
        _gate("cancel_to_last_output", "less_or_equal", 300, cancellation["cancel_request_to_process_stop_ms"]),
        _gate("resource_release", "less_or_equal", 2000, cancellation["resource_release_ms"]),
        _gate("recovery_ready", "less_or_equal", 30000, cancellation["recovery_ready_ms"]),
        _gate("sustained_latency_regression", "less_or_equal", 0.15, regression),
    ]
    result["gates"] = gates
    result["outcome"] = "pass" if all(item["passed"] for item in gates) else "fail"
    return result


def _batch_groups(observations: list[dict[str, Any]], concurrency: int) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for round_number in (1, 2, 3):
        items = [item for item in observations if item["concurrency"] == concurrency and item["round"] == round_number]
        groups.extend(items[offset:offset + concurrency] for offset in range(0, len(items), concurrency))
    return groups


def _jain(values: list[float]) -> float:
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))


def _prometheus_sum(text: str, metric: str) -> float:
    for line in text.splitlines():
        if line.startswith(metric) and "_sum{" in line:
            return float(line.rsplit(" ", 1)[1])
    raise ValueError(f"missing Prometheus sum: {metric}")


def build_llm_result(candidate_id: str) -> dict[str, Any]:
    raw_path = cache_path(RAW / "llm" / f"{candidate_id}.json")
    raw = _load(raw_path)
    result = _common(candidate_id, "llm", raw_path)
    observations = raw["parallel_observations"]
    metrics_by_level: dict[int, dict[str, float]] = {}
    fairness_values: list[float] = []
    slowest_ratios: list[float] = []
    for level in (1, 2, 4):
        items = [item for item in observations if item["concurrency"] == level]
        groups = _batch_groups(observations, level)
        fairness_values.extend(_jain([item["decode_tokens_per_second"] for item in group]) for group in groups)
        for group in groups:
            rates = sorted(item["decode_tokens_per_second"] for item in group)
            slowest_ratios.append(rates[0] / statistics.median(rates))
        batch_rates = [group[0]["batch_aggregate_tokens_per_second"] for group in groups]
        gaps = [gap for item in items for gap in item["inter_token_gaps_ms"]]
        metrics_by_level[level] = {
            "raw_ttft_p95": _nearest([item["raw_first_token_ms"] for item in items], 0.95),
            "visible_ttft_p95": _nearest([item["visible_first_content_ms"] for item in items], 0.95),
            "completion_p95": _nearest([item["completion_ms"] for item in items], 0.95),
            "decode_p05": _nearest([item["decode_tokens_per_second"] for item in items], 0.05),
            "aggregate_p50": float(statistics.median(batch_rates)),
            "gap_p95": _nearest(gaps, 0.95),
        }
    aggregate_scale_2 = metrics_by_level[2]["aggregate_p50"] / metrics_by_level[1]["aggregate_p50"]
    aggregate_scale_4 = metrics_by_level[4]["aggregate_p50"] / metrics_by_level[1]["aggregate_p50"]
    jain_min = min(fairness_values)
    slowest_min = min(slowest_ratios)
    all_markers = [item["marker"] for item in _load(ROOT / "fixtures" / "llm-parallel-russian.v1.json")["requests"]]
    isolation_failures = 0
    for item in observations:
        output = item["visible_output"]
        wrong = any(marker in output for marker in all_markers if marker != item["expected_marker"])
        isolation_failures += int(wrong or item["expected_marker"] not in output)
    visible_quality = sum(bool(item["visible_output"].strip()) for item in raw["quality_observations"])
    empty_quality = len(raw["quality_observations"]) - visible_quality
    pressure_failures = sum(not bool(item["visible_output"].strip()) for item in raw["context_pressure_observations"])
    parallel_queue_total_ms = _prometheus_sum(raw["server_metrics"]["after_parallel"], "vllm:request_queue_time_seconds") * 1000
    queue_p95_upper_bound_ms = parallel_queue_total_ms  # nonnegative samples imply each sample <= total
    resource_metrics, resources = _resource_metrics(raw)
    round_rates = {}
    for round_number in (1, 2, 3):
        round_rates[round_number] = statistics.median(
            item["batch_aggregate_tokens_per_second"] for item in observations if item["round"] == round_number
        )
    sustained_regression = max(0.0, (round_rates[1] - round_rates[3]) / round_rates[1])
    warm_metrics = []
    for level in (1, 2, 4):
        values = metrics_by_level[level]
        warm_metrics.extend([
            _metric(f"raw_ttft_c{level}", "milliseconds", "p95", values["raw_ttft_p95"]),
            _metric(f"visible_ttft_c{level}", "milliseconds", "p95", values["visible_ttft_p95"]),
            _metric(f"completion_c{level}", "milliseconds", "p95", values["completion_p95"]),
            _metric(f"decode_rate_c{level}", "tokens_per_second", "p05", values["decode_p05"]),
            _metric(f"aggregate_rate_c{level}", "tokens_per_second", "p50", values["aggregate_p50"]),
        ])
    result["phases"] = [
        {"name": "cold", "iterations": 1, "metrics": [_metric("model_load", "milliseconds", "maximum", raw["ready"]["load_ms"]), *resource_metrics], "failure_count": 0},
        {"name": "warm", "iterations": 108, "metrics": warm_metrics, "failure_count": isolation_failures},
        {"name": "sustained", "iterations": 3, "metrics": [_metric("throughput_regression", "ratio", "maximum", sustained_regression), _metric("jain_fairness", "ratio", "minimum", jain_min), _metric("slowest_to_median_rate", "ratio", "minimum", slowest_min)], "failure_count": isolation_failures + pressure_failures},
    ]
    result["quality"] = {
        "rubric_revision": _hash(ROOT / "fixtures" / "llm-russian.v1.json"),
        "sample_count": 14,
        "metrics": [{"name": "visible_answer_rate", "value": visible_quality / 14}],
        "critical_failure_count": empty_quality,
        "blind_reviewed": False,
    }
    cancellation = raw["cancellation_recovery"]
    result["cancellation_recovery"] = {
        "cancel_to_last_output_ms": cancellation["cancel_to_last_output_ms"],
        "resource_release_ms": cancellation["resource_release_ms"],
        "stale_output_count": cancellation["stale_output_count"],
        "recovery_ready_ms": 0,
        "recovery_latency_ms": cancellation["replacement"]["completion_ms"],
        "recovery_passed": cancellation["replacement"]["cancelled"] is False,
    }
    gates = [
        _gate("critical_failure_count", "less_or_equal", 0, empty_quality),
        _gate("cold_load", "less_or_equal", 60000, raw["ready"]["load_ms"]),
        _gate("aggregate_scale_c2", "greater_or_equal", 1.5, aggregate_scale_2),
        _gate("aggregate_scale_c4", "greater_or_equal", 2.25, aggregate_scale_4),
        _gate("jain_fairness", "greater_or_equal", 0.9, jain_min),
        _gate("slowest_to_median_rate", "greater_or_equal", 0.75, slowest_min),
        _gate("request_queue_p95_upper_bound", "less_or_equal", 250, queue_p95_upper_bound_ms),
        _gate("marker_isolation_failures", "less_or_equal", 0, isolation_failures),
        _gate("context_pressure_failures", "less_or_equal", 0, pressure_failures),
        _gate("cpu_p95", "less_or_equal", 83.33, resources["cpu_p95"]),
        _gate("gpu_reserve", "greater_or_equal", 1536, 12282 - resources["vram_peak"]),
        _gate("host_ram_available", "greater_or_equal", 6144, resources["ram_available"]),
        _gate("swap_consumption", "less_or_equal", 0, resources["swap_consumed"]),
        _gate("cancel_to_last_output", "less_or_equal", 300, cancellation["cancel_to_last_output_ms"]),
        _gate("replacement_admission", "less_or_equal", 250, cancellation["replacement_admission_ms"]),
        _gate("resource_release", "less_or_equal", 2000, cancellation["resource_release_ms"]),
        _gate("stale_output_count", "less_or_equal", 0, cancellation["stale_output_count"]),
        _gate("sustained_throughput_regression", "less_or_equal", 0.15, sustained_regression),
    ]
    for level, raw_limit, visible_limit, completion_limit, rate_limit in (
        (1, 1500, 3000, 10000, 15), (2, 2000, 4000, 12000, 10), (4, 3000, 6000, 16000, 8),
    ):
        values = metrics_by_level[level]
        gates.extend([
            _gate(f"raw_ttft_c{level}", "less_or_equal", raw_limit, values["raw_ttft_p95"]),
            _gate(f"visible_ttft_c{level}", "less_or_equal", visible_limit, values["visible_ttft_p95"]),
            _gate(f"completion_c{level}", "less_or_equal", completion_limit, values["completion_p95"]),
            _gate(f"decode_rate_c{level}", "greater_or_equal", rate_limit, values["decode_p05"]),
            _gate(f"inter_token_gap_c{level}", "less_or_equal", 750, values["gap_p95"]),
        ])
    result["gates"] = gates
    result["outcome"] = "pass" if all(item["passed"] for item in gates) else "fail"
    return result


def write_failed_selection() -> dict[str, Any]:
    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    result_schema = _load(ROOT / "schemas" / "result.v1.schema.json")
    results = [
        build_llm_result("llm-preferred-lfm25-26b-vllm-bf16"),
        build_stt_result("stt-whisper-large-v3-turbo"),
        build_stt_result("stt-whisper-large-v3"),
    ]
    if results[0]["outcome"] != "fail" or any(item["outcome"] == "pass" for item in results[1:]):
        raise ValueError("automatic failed-selection writer requires measured LLM failure and no eligible STT pair")
    names = []
    for result in results:
        validate(result, result_schema)
        assert_privacy_safe_result(result)
        name = f"{result['candidate_id']}.json"
        path = results_dir / name
        if path.exists():
            raise ValueError(f"refusing to replace result: {path}")
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        names.append(name)
    selection = {
        "schema_version": "voice-agent.slice2-selection.v1",
        "issue": "https://github.com/prineycom/voice-agent-v2/issues/3",
        "preregistration_commit": results[0]["preregistration_commit"],
        "status": "failed-selection",
        "selected_stt": {"identity": "none", "reason": "Both measured STT candidates failed at least one preregistered hard gate."},
        "selected_tts": {"identity": "none", "reason": "Listening quality was not requested after upstream LLM and STT hard failures stopped the dependent selection path."},
        "llm_provider": {
            "mode": "none", "identity": "none", "external_transfer": False, "endpoint": "none",
            "selection_reason": "The only approved LFM candidate emitted empty visible answers in fixed and concurrency cases, violating critical-failure, isolation, and context-pressure gates; policy forbids substitution.",
        },
        "admission": {"maximum_active_llm_requests": 0, "reason": "No LLM selected."},
        "reserve": {"complete_stack_measured": False, "reason": "Dependent overlap is prohibited after component hard-gate failure."},
        "repeat": {"performed": False, "reason": "There is no winning configuration to repeat."},
        "tested_candidates": [
            "llm-preferred-lfm25-26b-vllm-bf16",
            "stt-whisper-large-v3-turbo", "stt-whisper-large-v3",
            "tts-piper-denis-medium-automated-only", "tts-piper-dmitri-medium-automated-only",
        ],
        "automatic_fallback_disabled": True,
        "result_files": names,
        "untested_hypotheses": [
            "TTS listening quality remains unscored because the upstream failed-selection gate stopped the dependent path.",
            "Complete LLM and TTS overlap was not run after LFM component failure.",
            "New STT admission during LLM and TTS cancellation was not run after LFM component failure.",
            "No winning complete-stack repeat exists after the failed selection.",
            "Real microphone acoustics and production inference-service contracts remain for later slices.",
        ],
    }
    selection_schema = _load(ROOT / "schemas" / "selection.v1.schema.json")
    validate(selection, selection_schema)
    assert_privacy_safe_result(selection)
    selection_dir = ROOT / "selection"
    selection_dir.mkdir(exist_ok=True)
    selection_path = selection_dir / "selection.v1.json"
    if selection_path.exists():
        raise ValueError(f"refusing to replace selection: {selection_path}")
    selection_path.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "failed-selection", "result_files": names, "selection": str(selection_path)}

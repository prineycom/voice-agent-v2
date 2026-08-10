"""Deterministic numeric gate evaluation for cache-local cloud raw evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
from typing import Any

from .cloud_measure import ALIAS, PARALLEL_FIXTURE, PREREGISTRATION
from .safety import cache_path


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _nearest(values: list[float], quantile: float, default: float = 0.0) -> float:
    if not values:
        return default
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(quantile * len(ordered)) - 1)])


def _jain(values: list[float]) -> float:
    if not values or sum(value * value for value in values) == 0:
        return 0.0
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))


def _gate(name: str, observed: float, threshold: float, comparison: str) -> dict[str, Any]:
    passed = observed <= threshold if comparison == "less_or_equal" else observed >= threshold
    return {"name": name, "observed": observed, "threshold": threshold, "comparison": comparison, "passed": passed}


def _groups(observations: list[dict[str, Any]], concurrency: int) -> list[list[dict[str, Any]]]:
    groups = []
    for round_number in (1, 2, 3):
        items = [item for item in observations if item["concurrency"] == concurrency and item["round"] == round_number]
        groups.extend(items[offset:offset + concurrency] for offset in range(0, len(items), concurrency))
    return groups


def score_cloud_automated(raw_output: Path) -> dict[str, Any]:
    raw_path = cache_path(raw_output)
    raw = _load(raw_path)
    preregistration = _load(PREREGISTRATION)
    if raw["provider"]["selected_alias"] != ALIAS:
        raise ValueError("raw cloud evidence is not for the selected alias")
    parallel = raw["parallel_observations"]
    cancellation = raw["cancellation_recovery"]
    all_observations = (
        raw["quality_observations"] + parallel + raw["context_pressure_observations"]
        + [cancellation["cancelled"], *cancellation["peers"], cancellation["replacement"]]
    )
    expected_count = 3 * 12 * 3
    if len(parallel) != expected_count:
        raise ValueError(f"expected {expected_count} parallel observations")
    thresholds = preregistration["thresholds"]
    gates: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {"concurrency": {}}
    aggregate: dict[int, float] = {}
    fairness: list[float] = []
    slowest_ratios: list[float] = []
    all_response_models: set[str] = set()
    error_count = 0
    usage_missing = 0
    response_model_missing = 0
    for observation in all_observations:
        error_count += int(observation["error_class"] is not None or observation["response_http_status"] != 200)
        usage_missing += int(not all(key in observation["usage"] for key in ("prompt_tokens", "completion_tokens", "total_tokens")))
        response_model_missing += int(not observation["response_models"])
        all_response_models.update(observation["response_models"])
    for concurrency in (1, 2, 4):
        items = [item for item in parallel if item["concurrency"] == concurrency]
        groups = _groups(parallel, concurrency)
        for group in groups:
            rates = [item["completion_tokens_per_second"] for item in group]
            fairness.append(_jain(rates))
            ordered = sorted(rates)
            median = statistics.median(ordered)
            slowest_ratios.append(ordered[0] / median if median else 0.0)
        aggregate[concurrency] = statistics.median(group[0]["batch_aggregate_completion_tokens_per_second"] for group in groups)
        gaps = [gap for item in items for gap in item["inter_output_gaps_ms"]]
        values = {
            "raw_first_delta_p95_ms": _nearest([item["raw_first_delta_ms"] for item in items], 0.95),
            "visible_first_content_p95_ms": _nearest([item["visible_first_content_ms"] for item in items], 0.95),
            "completion_p95_ms": _nearest([item["completion_ms"] for item in items], 0.95),
            "http_acceptance_p95_ms": _nearest([item["http_acceptance_ms"] for item in items], 0.95),
            "inter_output_gap_p95_ms": _nearest(gaps, 0.95),
            "completion_tokens_p05_per_second": _nearest([item["completion_tokens_per_second"] for item in items], 0.05),
            "aggregate_completion_tokens_p50_per_second": aggregate[concurrency],
        }
        metrics["concurrency"][str(concurrency)] = values
        latency = thresholds["latency"]
        rates = thresholds["throughput_fairness"]["per_request_completion_tokens_p05_per_second_min_by_concurrency"]
        gates.extend([
            _gate(f"raw_first_delta_c{concurrency}", values["raw_first_delta_p95_ms"], latency["raw_first_delta_p95_ms_max_by_concurrency"][str(concurrency)], "less_or_equal"),
            _gate(f"visible_first_content_c{concurrency}", values["visible_first_content_p95_ms"], latency["visible_first_content_p95_ms_max_by_concurrency"][str(concurrency)], "less_or_equal"),
            _gate(f"completion_c{concurrency}", values["completion_p95_ms"], latency["completion_p95_ms_max_by_concurrency"][str(concurrency)], "less_or_equal"),
            _gate(f"completion_token_rate_c{concurrency}", values["completion_tokens_p05_per_second"], rates[str(concurrency)], "greater_or_equal"),
            _gate(f"inter_output_gap_c{concurrency}", values["inter_output_gap_p95_ms"], latency["inter_output_gap_p95_ms_max"], "less_or_equal"),
        ])
    markers = [item["marker"] for item in _load(PARALLEL_FIXTURE)["requests"]]
    isolation_failures = 0
    for item in parallel:
        output = item["visible_output"]
        isolation_failures += int(item["expected_marker"] not in output or any(marker in output for marker in markers if marker != item["expected_marker"]))
    empty_quality = sum(not item["visible_output"].strip() for item in raw["quality_observations"])
    word_p95 = _nearest([len(item["visible_output"].split()) for item in raw["quality_observations"]], 0.95)
    pressure_failures = sum(
        item["error_class"] is not None or not item["visible_output"].strip()
        for item in raw["context_pressure_observations"]
    )
    fairness_min = min(fairness)
    slowest_min = min(slowest_ratios)
    scales = {"c2_over_c1": aggregate[2] / aggregate[1] if aggregate[1] else 0.0, "c4_over_c1": aggregate[4] / aggregate[1] if aggregate[1] else 0.0}
    replacement = cancellation["replacement"]
    rounds = raw["sustained_rounds"]
    rss_growth = max(0.0, (rounds[-1]["client_max_rss_mib"] - rounds[0]["client_max_rss_mib"]) / 2)
    round_completion = {
        number: statistics.median(item["completion_ms"] for item in parallel if item["round"] == number)
        for number in (1, 2, 3)
    }
    sustained_regression = max(0.0, (round_completion[3] - round_completion[1]) / round_completion[1])
    round_throughput = {
        number: statistics.median(
            item["batch_aggregate_completion_tokens_per_second"] for item in parallel if item["round"] == number
        )
        for number in (1, 2, 3)
    }
    sustained_throughput_regression = max(0.0, (round_throughput[1] - round_throughput[3]) / round_throughput[1]) if round_throughput[1] else 1.0
    baseline_c4 = statistics.median(item["completion_ms"] for item in parallel if item["concurrency"] == 4)
    peer_median = statistics.median(item["completion_ms"] for item in cancellation["peers"])
    unaffected_peer_regression = max(0.0, (peer_median - baseline_c4) / baseline_c4)
    starved_requests = sum(item["usage"].get("completion_tokens", 0) == 0 for item in parallel)
    fallback_observations = max(0, len(all_response_models) - 1)
    transport = raw["transport"]
    privacy = raw["privacy"]
    gates.extend([
        _gate("empty_visible_quality", empty_quality, thresholds["quality"]["empty_visible_response_count_max"], "less_or_equal"),
        _gate("visible_word_count_p95", word_p95, thresholds["quality"]["visible_word_count_p95_max"], "less_or_equal"),
        _gate("http_acceptance_p95", max(value["http_acceptance_p95_ms"] for value in metrics["concurrency"].values()), thresholds["latency"]["http_acceptance_p95_ms_max"], "less_or_equal"),
        _gate("context_pressure_failures", pressure_failures, thresholds["latency"]["context_pressure_failure_count_max"], "less_or_equal"),
        _gate("throughput_scale_c2", scales["c2_over_c1"], thresholds["throughput_fairness"]["aggregate_throughput_scale_min"]["concurrency_2_over_1"], "greater_or_equal"),
        _gate("throughput_scale_c4", scales["c4_over_c1"], thresholds["throughput_fairness"]["aggregate_throughput_scale_min"]["concurrency_4_over_1"], "greater_or_equal"),
        _gate("jain_fairness", fairness_min, thresholds["throughput_fairness"]["jain_fairness_index_min"], "greater_or_equal"),
        _gate("slowest_to_median_rate", slowest_min, thresholds["throughput_fairness"]["slowest_to_median_rate_min"], "greater_or_equal"),
        _gate("marker_isolation_failures", isolation_failures, thresholds["throughput_fairness"]["marker_isolation_failure_count_max"], "less_or_equal"),
        _gate("starved_requests", starved_requests, thresholds["throughput_fairness"]["starved_request_count_max"], "less_or_equal"),
        _gate("http_errors", error_count, thresholds["identity_transport"]["http_error_count_max"], "less_or_equal"),
        _gate("response_model_missing", response_model_missing, thresholds["identity_transport"]["response_model_missing_count_max"], "less_or_equal"),
        _gate("distinct_response_models", len(all_response_models), thresholds["identity_transport"]["distinct_response_model_values_max"], "less_or_equal"),
        _gate("fallback_observations", fallback_observations, thresholds["identity_transport"]["fallback_observation_count_max"], "less_or_equal"),
        _gate("request_alias_mismatches", int(raw["provider"]["selected_alias"] != ALIAS), thresholds["identity_transport"]["request_alias_mismatch_count_max"], "less_or_equal"),
        _gate("alternate_alias_requests", privacy["alternate_alias_request_count"], thresholds["identity_transport"]["alternate_alias_request_count_max"], "less_or_equal"),
        _gate("redirects", privacy["redirect_count"], thresholds["identity_transport"]["redirect_count_max"], "less_or_equal"),
        _gate("tailscale_address_class", int(transport["address_class"] != thresholds["identity_transport"]["dns_address_class"]), 0, "less_or_equal"),
        _gate("tailscale_route", int(transport["route_interface"] != thresholds["identity_transport"]["route_interface"]), 0, "less_or_equal"),
        _gate("usage_missing", usage_missing, thresholds["privacy_observability"]["usage_missing_count_max"], "less_or_equal"),
        _gate("nonfixture_prompts", 0, thresholds["privacy_observability"]["nonfixture_prompt_count_max"], "less_or_equal"),
        _gate("forbidden_request_fields", 0, thresholds["privacy_observability"]["forbidden_request_field_count_max"], "less_or_equal"),
        _gate("forbidden_message_roles", 0, thresholds["privacy_observability"]["forbidden_message_role_count_max"], "less_or_equal"),
        _gate("credential_mode_mismatches", 0, thresholds["privacy_observability"]["credential_mode_mismatch_count_max"], "less_or_equal"),
        _gate("credential_exposure", int(privacy["credential_value_logged"]), thresholds["privacy_observability"]["credential_log_or_argv_exposure_count_max"], "less_or_equal"),
        _gate("tracked_content_fields", 0, thresholds["privacy_observability"]["tracked_content_bearing_field_count_max"], "less_or_equal"),
        _gate("content_bearing_errors", 0, thresholds["privacy_observability"]["content_bearing_error_observation_count_max"], "less_or_equal"),
        _gate("cancel_observable", int(not cancellation["observable_output_seen_before_cancel"]), 0, "less_or_equal"),
        _gate("cancel_to_last_output", cancellation["cancel_to_last_output_ms"], thresholds["cancellation"]["cancel_to_last_output_ms_max"], "less_or_equal"),
        _gate("cancel_stale_output", cancellation["stale_output_count"], thresholds["cancellation"]["stale_output_count_max"], "less_or_equal"),
        _gate("replacement_acceptance", replacement["http_acceptance_ms"], thresholds["cancellation"]["replacement_http_acceptance_ms_max"], "less_or_equal"),
        _gate("unaffected_peer_regression", unaffected_peer_regression, thresholds["cancellation"]["unaffected_peer_regression_max_fraction"], "less_or_equal"),
        _gate("recovery_failures", int(replacement["error_class"] is not None or not replacement["visible_output"].strip()), thresholds["cancellation"]["recovery_failure_count_max"], "less_or_equal"),
        _gate("sustained_failures", error_count, thresholds["sustained"]["failure_count_max"], "less_or_equal"),
        _gate("sustained_latency_regression", sustained_regression, thresholds["sustained"]["latency_regression_max_fraction"], "less_or_equal"),
        _gate("sustained_throughput_regression", sustained_throughput_regression, thresholds["sustained"]["throughput_regression_max_fraction"], "less_or_equal"),
        _gate("client_ram_growth", rss_growth, thresholds["sustained"]["client_ram_growth_max_mib_per_round"], "less_or_equal"),
    ])
    automated_pass = all(item["passed"] for item in gates)
    return {
        "schema_version": "voice-agent.slice2-cloud-automated-score.v1",
        "candidate_id": ALIAS,
        "preregistration_commit": raw["preregistration_commit"],
        "status": "awaiting-blind-human-quality" if automated_pass else "failed-automated-gates",
        "metrics": {
            **metrics, "aggregate_scales": scales, "jain_fairness_min": fairness_min,
            "slowest_to_median_min": slowest_min, "response_models": sorted(all_response_models),
            "empty_quality_count": empty_quality, "quality_visible_word_p95": word_p95,
            "context_pressure_failure_count": pressure_failures, "marker_isolation_failure_count": isolation_failures,
            "http_error_count": error_count, "usage_missing_count": usage_missing,
            "client_ram_growth_mib_per_round": rss_growth, "sustained_latency_regression": sustained_regression,
            "sustained_throughput_regression": sustained_throughput_regression,
            "unaffected_peer_regression": unaffected_peer_regression,
        },
        "gates": gates,
        "prompt_or_response_content_in_score": False,
    }

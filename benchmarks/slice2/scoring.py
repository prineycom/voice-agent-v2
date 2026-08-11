"""Deterministic STT scoring and validated human score-card aggregation."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from .safety import cache_path

ROOT = Path(__file__).resolve().parents[1]


def normalize_russian(value: str) -> list[str]:
    lowered = value.lower().replace("ё", "е")
    cleaned = re.sub(r"[^а-я0-9]+", " ", lowered)
    return cleaned.split()


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_word in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1] + (reference_word != hypothesis_word),
                )
            )
        previous = current
    return previous[-1]


def score_stt(raw_output: Path) -> dict[str, Any]:
    """Score public corpus hypotheses from cache without returning their content."""
    source = cache_path(raw_output)
    payload = json.loads(source.read_text(encoding="utf-8"))
    fixture = json.loads((ROOT / "fixtures" / "stt-russian-ruls.v1.json").read_text(encoding="utf-8"))
    references = {sample["id"]: sample["reference"] for sample in fixture["samples"]}
    expected_ids = set(references)
    observations = payload["observations"]
    observed_ids = {item["sample_id"] for item in observations}
    if observed_ids != expected_ids or len(observations) != len(expected_ids):
        raise ValueError("raw STT output must contain every preregistered sample exactly once")

    errors = 0
    words = 0
    per_sample_failures = 0
    for observation in observations:
        reference_words = normalize_russian(references[observation["sample_id"]])
        hypothesis_words = normalize_russian(observation["hypothesis"])
        distance = edit_distance(reference_words, hypothesis_words)
        errors += distance
        words += len(reference_words)
        if distance:
            per_sample_failures += 1
    return {
        "sample_count": len(observations),
        "reference_word_count": words,
        "word_error_count": errors,
        "word_error_rate": errors / words,
        "samples_with_any_error": per_sample_failures,
        "normalization": fixture["scoring"]["normalization"],
    }


def aggregate_human_score_card(score_card: Path, role: str) -> dict[str, Any]:
    """Aggregate numeric blind-review cards kept in cache; no model output enters results."""
    source = cache_path(score_card)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("role") != role or role not in {"llm", "tts"}:
        raise ValueError("score-card role mismatch")
    entries = payload.get("scores", [])
    if not entries:
        raise ValueError("score card has no entries")
    ids = [entry["sample_id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("score card contains duplicate sample IDs")
    dimensions = Counter()
    counts = Counter()
    critical_failures = 0
    for entry in entries:
        critical_failures += int(bool(entry.get("critical_failure", False)))
        for name, score in entry["dimensions"].items():
            if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
                raise ValueError(f"invalid {name} score for {entry['sample_id']}")
            dimensions[name] += score
            counts[name] += 1
    return {
        "sample_count": len(entries),
        "dimension_means": {name: dimensions[name] / counts[name] for name in sorted(dimensions)},
        "critical_failures": critical_failures,
        "reviewer_count": payload["reviewer_count"],
        "blind_labels": bool(payload["blind_labels"]),
    }

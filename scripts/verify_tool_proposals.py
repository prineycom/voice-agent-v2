#!/usr/bin/env python3
"""Execute or validate the frozen E2.1 local-LFM proposal benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.tool_proposals.harness import (  # noqa: E402
    MECHANISMS,
    MODEL_MECHANISMS,
    RESULT_PATH,
    empty_counts,
    make_result,
    preregistration_commit,
    run_matrix,
    validate_preregistration,
    validate_result,
    write_result,
)
from scripts.verify_local_lfm import start_server, stop_server  # noqa: E402


def content_free_summary(result: dict[str, object]) -> None:
    print("E2.1 local LFM operation proposals: MEASUREMENT COMPLETE")
    print(f"decision: {result['decision']['decision']}")
    print(
        "identity: exact_pinned_lfm2.5_q4_k_m_llama_cpp="
        f"{str(result['identity']['artifact_and_runtime_verified']).lower()} "
        "fallback=false credentials=false"
    )
    print(
        f"matrix: complete={str(result['execution']['matrix_complete']).lower()} "
        f"fixtures=240 orders=5 concurrency=2 elapsed_ms={result['execution']['elapsed_milliseconds']}"
    )
    for mechanism in result["mechanisms"]:
        print(
            f"{mechanism['mechanism']}: attempted={mechanism['attempted_fixture_evaluations']} "
            f"valid={mechanism['valid_envelope_count']} invalid={mechanism['invalid_envelope_count']} "
            f"false_positive={mechanism['false_positive_count']} "
            f"exact_selection={mechanism['exact_selection_count']} "
            f"timeouts={mechanism['timeout_count']} "
            f"identity_match={mechanism['identity_match_count']} "
            f"identity_mismatch={mechanism['identity_mismatch_count']} "
            f"identity_missing={mechanism['identity_missing_count']} "
            f"passed={str(mechanism['passed']).lower()}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    arguments = parser.parse_args(argv)
    validate_preregistration()
    if arguments.validate_only:
        if RESULT_PATH.exists():
            validate_result()
        print("E2.1 tool-proposal benchmark artifacts: PASS")
        return 0
    if RESULT_PATH.exists():
        raise RuntimeError("tool-proposal evidence already exists; the real benchmark cannot be retried")

    commit = preregistration_commit()
    counts = {mechanism: empty_counts() for mechanism in MECHANISMS}
    started = time.monotonic()
    process = output = None
    runtime_verified = False
    matrix_complete = False
    failure_class: str | None = None
    try:
        process, output = start_server()
        runtime_verified = True
        # Reserve 30 seconds inside the wrapper's exact 600-second outer bound for
        # artifact hashing/startup, evidence validation, and complete child cleanup.
        counts, matrix_complete = run_matrix(started + 570.0)
        if not matrix_complete:
            failure_class = "matrix_deadline_or_incomplete"
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        text = str(error).lower()
        if "missing" in text or "checksum" in text or "wrong-sized" in text:
            failure_class = "exact_runtime_or_artifact_unavailable"
        elif "identity" in text:
            failure_class = "exact_identity_unavailable"
        elif failure_class is None:
            failure_class = "benchmark_runtime_unavailable"
    finally:
        if process is not None and output is not None:
            stop_server(process, output)

    result = make_result(
        commit=commit,
        counts=counts,
        matrix_complete=matrix_complete,
        elapsed_seconds=time.monotonic() - started,
        runtime_verified=runtime_verified,
        failure_class=failure_class,
    )
    write_result(result)
    validate_result(result)
    content_free_summary(result)
    return 0 if matrix_complete else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"E2.1 tool-proposal verification failed: {error}", file=sys.stderr)
        raise SystemExit(2)

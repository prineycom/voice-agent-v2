#!/usr/bin/env python3
"""Deterministic tracer repeat/fault contract used by full and recovery gates."""

from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "src"))


class NetworkAccessDenied(RuntimeError):
    pass


def deny_network(event: str, arguments: tuple[object, ...]) -> None:
    if event == "socket.__new__" and len(arguments) > 1 and arguments[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise NetworkAccessDenied(f"network operation denied: {event}")


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path)
    arguments = parser.parse_args()
    output_directory = arguments.output_directory.resolve() if arguments.output_directory else None
    if output_directory is not None and output_directory.exists():
        raise SystemExit(f"output directory already exists: {output_directory}")

    sys.addaudithook(deny_network)
    try:
        socket.socket()
    except NetworkAccessDenied:
        pass
    else:
        raise AssertionError("tracer network denial is inactive")

    from voice_agent_v2.tracer import (
        FIXED_RESPONSE,
        FIXED_SESSION_ID,
        FIXED_TRANSCRIPT,
        FIXED_TURN_ID,
        normalize_events,
        run_scenario,
        write_trace_artifacts,
    )

    clean_runs: list[tuple[bytes, bytes, bytes]] = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="voice-agent-v2-empty-cache-") as temporary:
            run_root = Path(temporary)
            environment = {
                "HOME": str(run_root / "home"),
                "XDG_CACHE_HOME": str(run_root / "cache"),
                "TMPDIR": str(run_root / "temp"),
            }
            for directory in environment.values():
                Path(directory).mkdir()
            artifact_directory = run_root / "artifacts"
            with patch.dict(os.environ, environment):
                trace = run_scenario("success")
                write_trace_artifacts(artifact_directory, trace)
            clean_runs.append(
                (
                    (artifact_directory / "trace.normalized.jsonl").read_bytes(),
                    (artifact_directory / "input.pcm").read_bytes(),
                    (artifact_directory / "output.pcm").read_bytes(),
                )
            )
    if clean_runs[0] != clean_runs[1]:
        raise AssertionError("two independent empty-cache tracer runs differ")

    normalized_trace, input_pcm, output_pcm = clean_runs[0]
    success = run_scenario("success")
    if normalized_trace != normalize_events(success.events):
        raise AssertionError("artifact trace differs from the public tracer")
    summaries: list[str] = []
    for scenario in ("stt_failure", "llm_failure", "tts_failure", "cancel_after_first_audio"):
        result = run_scenario(scenario)
        terminal = result.terminal_event
        detail = terminal["payload"].get("code", terminal["payload"]["outcome"])
        summaries.append(
            f"case {scenario}: terminal={terminal['type']} detail={detail} terminal_count=1"
        )
    if output_directory is not None:
        write_trace_artifacts(output_directory, success)

    print("Voice Agent v2 deterministic tracer")
    print("network: IP sockets denied; local AF_UNIX wake-up only")
    print(f"correlation: session={FIXED_SESSION_ID} turn={FIXED_TURN_ID}")
    print(f"transcript: {FIXED_TRANSCRIPT}")
    print(f"response: {FIXED_RESPONSE}")
    print(f"terminal: {success.terminal_event['type']} count=1")
    print(f"normalized_trace_sha256: {digest(normalized_trace)}")
    print(f"input_pcm_sha256: {digest(input_pcm)} bytes={len(input_pcm)}")
    print(f"output_pcm_sha256: {digest(output_pcm)} bytes={len(output_pcm)}")
    print("repeatability: run1 == run2 for normalized trace and PCM")
    for summary in summaries:
        print(summary)
    if output_directory is not None:
        print(f"artifact_trace: {output_directory / 'trace.normalized.jsonl'}")
        print(f"artifact_input_pcm: {output_directory / 'input.pcm'}")
        print(f"artifact_output_pcm: {output_directory / 'output.pcm'}")
    print("tracer: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

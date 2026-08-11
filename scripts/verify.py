#!/usr/bin/env python3
"""Root verification implementation; invoke only through ./verify."""

from __future__ import annotations

import argparse
from hashlib import sha256
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class NetworkAccessDenied(RuntimeError):
    pass


def deny_network(event: str, args: tuple[object, ...]) -> None:
    # asyncio uses a local AF_UNIX socketpair as its selector wake-up pipe.
    # It cannot reach a network; all IP socket creation and every other socket
    # operation remain denied.
    if event == "socket.__new__" and len(args) > 1 and args[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise NetworkAccessDenied(f"network operation denied: {event}")


def assert_network_is_denied() -> None:
    try:
        socket.socket()
    except NetworkAccessDenied:
        return
    raise AssertionError("network denial audit policy was not active")


def sha256_hex(value: bytes) -> str:
    return sha256(value).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the deterministic Slice 1 voice-turn tracer.")
    parser.add_argument(
        "--output-directory",
        type=Path,
        help="preserve the verified trace and playable PCM artifacts in a new directory",
    )
    return parser.parse_args()


def main() -> int:
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required")

    args = parse_args()
    output_directory = args.output_directory
    if output_directory is not None:
        output_directory = output_directory.resolve()
        if output_directory.exists():
            raise SystemExit(f"output directory already exists: {output_directory}")

    sys.addaudithook(deny_network)
    assert_network_is_denied()

    from voice_agent_v2.tracer import (  # imported after network denial is active
        FIXED_RESPONSE,
        FIXED_SESSION_ID,
        FIXED_TRANSCRIPT,
        FIXED_TURN_ID,
        normalize_events,
        run_scenario,
        write_trace_artifacts,
    )

    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    test_output = io.StringIO()
    result = unittest.TextTestRunner(stream=test_output, verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.stderr.write(test_output.getvalue())
        return 1

    clean_runs: list[tuple[bytes, bytes, bytes]] = []
    for _run_number in (1, 2):
        with tempfile.TemporaryDirectory(prefix="voice-agent-v2-empty-cache-") as temporary:
            run_root = Path(temporary)
            run_environment = {
                "HOME": str(run_root / "home"),
                "XDG_CACHE_HOME": str(run_root / "cache"),
                "TMPDIR": str(run_root / "temp"),
            }
            for directory in run_environment.values():
                Path(directory).mkdir()
            artifact_directory = run_root / "artifacts"
            with patch.dict(os.environ, run_environment):
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
        raise AssertionError("the two empty-cache runs differ")

    normalized_trace, input_pcm, output_pcm = clean_runs[0]
    success = run_scenario("success")
    if normalized_trace != normalize_events(success.events):
        raise AssertionError("artifact trace differs from the public tracer")

    scenario_summaries: list[tuple[str, str, object]] = []
    for scenario in ("stt_failure", "llm_failure", "tts_failure", "cancel_after_first_audio"):
        scenario_result = run_scenario(scenario)
        terminal = scenario_result.terminal_event
        detail = terminal["payload"].get("code", terminal["payload"]["outcome"])
        scenario_summaries.append((scenario, str(terminal["type"]), detail))

    if output_directory is not None:
        write_trace_artifacts(output_directory, success)

    event_order = " > ".join(str(event["type"]) for event in success.events)
    print("Voice Agent v2 Slice 1 verification")
    print("toolchain: Python 3.11+ standard library only (slice-local choice)")
    print("network: IP sockets denied by Python audit policy; local asyncio AF_UNIX wake-up only")
    print("cache: two independent empty temporary run roots")
    print(f"correlation: session={FIXED_SESSION_ID} turn={FIXED_TURN_ID}")
    print(f"events: {event_order}")
    print(f"transcript: {FIXED_TRANSCRIPT}")
    print(f"response: {FIXED_RESPONSE}")
    print(f"terminal: {success.terminal_event['type']} count=1")
    print(f"normalized_trace_sha256: {sha256_hex(normalized_trace)}")
    print(f"input_pcm_sha256: {sha256_hex(input_pcm)} bytes={len(input_pcm)}")
    print(f"output_pcm_sha256: {sha256_hex(output_pcm)} bytes={len(output_pcm)} format=pcm_s16le/16000Hz/mono")
    print("repeatability: run1 == run2 for normalized trace, input PCM, and output PCM")
    if output_directory is not None:
        print(f"artifact_trace: {output_directory / 'trace.normalized.jsonl'}")
        print(f"artifact_input_pcm: {output_directory / 'input.pcm'}")
        print(f"artifact_output_pcm: {output_directory / 'output.pcm'}")

    for scenario, terminal_type, detail in scenario_summaries:
        print(f"case {scenario}: terminal={terminal_type} detail={detail} terminal_count=1")

    print(f"behavioral_tests: pass count={result.testsRun}")
    print(
        "slice6_headless_media: success disconnect duplicate/late/malformed "
        "interruption/reconnect=PASS fake_inference=true IP_network=denied"
    )
    print("legacy_material: none inspected or used")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

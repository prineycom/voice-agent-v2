#!/usr/bin/env python3
"""Explicit current-behavior unittest manifest; invoked by ./verify."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "src"))


HERMETIC_OWNERS = (
    "tests.test_tracer",
    "tests.test_local_lfm",
    "tests.test_local_vad.SileroSpeechEndpointTests",
    "tests.test_silero_tts",
    "tests.test_slice6_realtime",
    "tests.test_slice6_livekit_runtime",
    "tests.test_slice6_startup",
    "tests.test_operations",
    "tests.test_stand_doctor",
    "tests.test_stand_dev",
    "tests.test_agent_config",
    "tests.test_agent_profile_runtime",
    "tests.test_agent_environment",
    "tests.test_agent_environment_processes",
    "tests.test_agent_environment_files",
    "tests.test_agent_environment_network",
    "tests.test_agent_research",
    "tests.test_agent_report_delivery",
    "tests.test_tool_proposals_benchmark",
    "tests.test_observability",
    "tests.test_diagnostics",
    "tests.test_run_voice_turn",
    "tests.test_review_stand",
    "tests.test_resident_lifecycle.ResidentLifecycleTests",
    "tests.test_real_adapters.RealTurnControllerTests",
    "tests.test_real_adapters.LocalSTTContractTests",
    "tests.test_real_adapters.AdapterProcessTests",
    "tests.test_verify_architecture",
)

# These are deliberately owned by another tier/subphase, not silently skipped.
HERMETIC_EXCLUSIONS = {
    "tests.test_local_vad.SileroSpeechEndpointTests.test_cache_local_real_russian_fixture_is_detected":
        "local-socket/runtime-fixture subphase",
    "tests.test_observability.CaptureAndResourceTests.test_detached_expiry_executable_deletes_on_supported_runtime_tmpfs":
        "extended detached-guardian tier",
    "tests.test_observability.CaptureAndResourceTests.test_real_detached_guardian_survives_short_lived_parent":
        "extended detached-guardian tier",
    "tests.test_resident_lifecycle.ResidentLifecycleTests.test_thirty_interruptions_preserve_warmed_processes_and_future_turns":
        "extended lifecycle tier",
}

LOCAL_SOCKET_OWNERS = (
    "tests.test_backend_readiness",
    "tests.test_slice9_runtime",
    "tests.test_local_vad.SileroSpeechEndpointTests.test_cache_local_real_russian_fixture_is_detected",
)

EXTENDED_OWNERS = (
    "tests.test_observability.CaptureAndResourceTests.test_detached_expiry_executable_deletes_on_supported_runtime_tmpfs",
    "tests.test_observability.CaptureAndResourceTests.test_real_detached_guardian_survives_short_lived_parent",
    "tests.test_resident_lifecycle.ResidentLifecycleTests.test_thirty_interruptions_preserve_warmed_processes_and_future_turns",
)


class NetworkAccessDenied(RuntimeError):
    pass


def deny_network(event: str, arguments: tuple[object, ...]) -> None:
    if event == "socket.__new__" and len(arguments) > 1 and arguments[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise NetworkAccessDenied(f"network operation denied: {event}")


def iter_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            yield item


def explicit_suite(names: tuple[str, ...], exclusions: dict[str, str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    loaded = unittest.TestSuite(loader.loadTestsFromName(name) for name in names)
    cases = list(iter_cases(loaded))
    selected = [case for case in cases if case.id() not in exclusions]
    selected_ids = [case.id() for case in selected]
    duplicate_ids = sorted({test_id for test_id in selected_ids if selected_ids.count(test_id) > 1})
    if duplicate_ids:
        raise AssertionError(f"explicit manifest selected duplicate test IDs: {duplicate_ids}")
    observed_exclusions = {case.id() for case in cases} & exclusions.keys()
    missing_exclusions = exclusions.keys() - observed_exclusions
    if missing_exclusions:
        raise AssertionError(f"stale manifest exclusions: {sorted(missing_exclusions)}")
    return unittest.TestSuite(selected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("hermetic", "local-socket", "extended"))
    arguments = parser.parse_args()

    if arguments.phase == "hermetic":
        sys.addaudithook(deny_network)
        try:
            socket.socket()
        except NetworkAccessDenied:
            pass
        else:
            raise AssertionError("hermetic network-denial policy is inactive")
        owners = HERMETIC_OWNERS
        exclusions = HERMETIC_EXCLUSIONS
    elif arguments.phase == "local-socket":
        owners = LOCAL_SOCKET_OWNERS
        exclusions = {}
    else:
        owners = EXTENDED_OWNERS
        exclusions = {}

    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(
        explicit_suite(owners, exclusions)
    )
    if not result.wasSuccessful() or result.skipped:
        sys.stderr.write(output.getvalue())
        if result.skipped:
            sys.stderr.write(f"unexpected skips: {result.skipped!r}\n")
        return 1
    print(
        f"python_{arguments.phase.replace('-', '_')}: PASS "
        f"tests={result.testsRun} unexpected_skips=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

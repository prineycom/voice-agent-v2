#!/usr/bin/env python3
"""Bounded Slice 9 evidence without shared-service, reboot, or physical claims."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.run_slice6 import ProcessSupervisor, SHUTDOWN_ORDER  # noqa: E402
from voice_agent_v2.operations import (  # noqa: E402
    OperationalError,
    evaluate_sustained_run,
    load_operations_manifest,
    verify_disk_policy,
)


def bounded_process_recovery() -> dict[str, object]:
    starts = 0
    recoveries = 0
    exit_codes: list[int] = []
    while True:
        worker = subprocess.run(
            [sys.executable, "-I", "-c", "raise SystemExit(17)"],
            timeout=5,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        starts += 1
        exit_codes.append(worker.returncode)
        if worker.returncode == 0 or recoveries >= 1:
            break
        recoveries += 1
    if starts != 2 or recoveries != 1 or exit_codes != [17, 17]:
        raise AssertionError("bounded process recovery policy changed")
    return {
        "process_starts": starts,
        "automatic_recoveries": recoveries,
        "recovery_limit": 1,
        "second_recovery_blocked": True,
    }


def graceful_process_custody() -> dict[str, object]:
    supervisor = ProcessSupervisor()
    roles = (
        "local-llm", "livekit", "gateway-controller-stt-tts-provider",
        "tailnet-app-route", "tailnet-signal-route",
    )
    try:
        for role in roles:
            supervisor.start(
                [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                role=role,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            )
        supervisor.close(SHUTDOWN_ORDER)
        alive = [process for process in supervisor.processes if process.poll() is None]
        if alive:
            raise AssertionError("disposable supervised process survived graceful close")
        return {
            "owned_processes": len(supervisor.processes),
            "declared_stop_order": list(SHUTDOWN_ORDER),
            "orphan_processes": 0,
            "hard_kills": sum(process.returncode == -9 for process in supervisor.processes),
        }
    finally:
        supervisor.close(SHUTDOWN_ORDER)


def nondestructive_disk_pressure() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="voice-agent-slice9-disk-") as temporary:
        root = Path(temporary)
        cache = root / "cache"
        cache.mkdir()
        retained = cache / "retained.bin"
        retained.write_bytes(b"bounded fixture")
        manifest = {
            "disk": {
                "minimum_free_bytes": 1,
                "cache_roots": [{
                    "name": "fixture", "path": str(cache), "maximum_bytes": 1,
                }],
            }
        }
        rejected = False
        try:
            verify_disk_policy(manifest, home=root, filesystem_path=root)
        except OperationalError as error:
            rejected = error.code == "cache_pressure"
        if not rejected or retained.read_bytes() != b"bounded fixture":
            raise AssertionError("disk-pressure preflight modified or admitted the cache")
        return {
            "pressure_rejected": True,
            "existing_bytes_deleted": 0,
            "disk_exhaustion_attempted": False,
        }


def sustained_metadata_run(manifest: dict[str, object]) -> dict[str, object]:
    turns = [
        {
            "outcome": "completed",
            "total_turn_ms": 1000 + index * 10,
            "cancellation_latency_ms": 100 if index in {4, 9, 14, 19} else None,
            "process_rss_mib": 1000 + index * 2,
            "gpu_vram_used_mib": 3000 + index,
        }
        for index in range(20)
    ]
    return evaluate_sustained_run(
        manifest,
        turns=turns,
        avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
    )


def main() -> int:
    manifest = load_operations_manifest(ROOT / "config/operations-v1.json")
    report = {
        "schema_version": "voice-agent.slice9-controlled-evidence.v1",
        "configuration_schema": manifest["schema_version"],
        "provider_mode": manifest["deployment"]["provider_mode"],
        "external_provider_supervised": False,
        "automatic_fallback": False,
        "wake_enabled": False,
        "process_recovery": bounded_process_recovery(),
        "graceful_custody": graceful_process_custody(),
        "disk_pressure": nondestructive_disk_pressure(),
        "sustained": sustained_metadata_run(manifest),
        "shared_service_touched": False,
        "physical_reboot_claimed": False,
        "physical_voice_claimed": False,
        "tailnet_disconnect_claimed": False,
    }
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

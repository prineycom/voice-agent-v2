#!/usr/bin/env python3
"""Canonical-host Slice 9 preflight and disposable systemd recovery evidence."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.operations import validate_host  # noqa: E402


def _validate_mutable_root(root: Path) -> Path:
    if not root.is_absolute():
        raise RuntimeError("Slice 9 mutable state root must be absolute")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = root.lstat()
    if (
        root.is_symlink() or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077
    ):
        raise RuntimeError("Slice 9 mutable state root is not private")
    return root


@contextmanager
def mutable_root() -> Iterator[Path]:
    configured = os.environ.get("VOICE_AGENT_MUTABLE_STATE_ROOT")
    if configured:
        yield _validate_mutable_root(Path(configured).expanduser())
        return
    with tempfile.TemporaryDirectory(
        prefix=f"voice-agent-v2-{os.getuid()}-verify-slice9-host-",
        dir="/var/tmp",
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        yield _validate_mutable_root(root)


def command(arguments: list[str], *, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=30, check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
    )
    if result.returncode not in allowed:
        raise RuntimeError(f"bounded host command failed: {arguments[0]}")
    return result


def systemd_recovery_bound(state_root: Path) -> dict[str, object]:
    unit = f"voice-agent-v2-slice9-recovery-{os.getpid()}.service"
    counter = state_root / f"systemd-recovery-{os.getpid()}.count"
    counter.unlink(missing_ok=True)
    try:
        command([
            "systemd-run", "--user", f"--unit={unit}",
            "--property=Restart=on-failure",
            "--property=RestartSec=100ms",
            "--property=StartLimitIntervalSec=infinity",
            "--property=StartLimitBurst=2",
            "--property=RestartPreventExitStatus=2",
            "/bin/sh", "-c", 'printf x >> "$1"; exit 17',
            "slice9-recovery", str(counter),
        ])
        deadline = time.monotonic() + 10
        properties: dict[str, str] = {}
        while time.monotonic() < deadline:
            shown = command([
                "systemctl", "--user", "show", unit,
                "--property=ActiveState,SubState,Result,NRestarts,ExecMainStatus",
            ], allowed=(0, 1, 3, 4))
            properties = dict(
                line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line
            )
            if properties.get("ActiveState") == "failed" and counter.exists() and counter.stat().st_size >= 2:
                break
            time.sleep(0.05)
        starts = counter.stat().st_size if counter.exists() else 0
        if not (
            properties.get("ActiveState") == "failed"
            and properties.get("Result") in {"exit-code", "start-limit-hit"}
            and starts == 2
            and int(properties.get("NRestarts", "0")) == 2
        ):
            raise RuntimeError("transient systemd recovery did not stop at one restart")
        return {
            "unit_type": "disposable-transient-user-service",
            "process_starts": starts,
            "completed_automatic_restarts": starts - 1,
            "scheduled_restart_jobs_including_blocked": int(properties["NRestarts"]),
            "final_state": properties["ActiveState"],
            "second_restart_blocked": True,
            "production_unit_touched": False,
        }
    finally:
        command(["systemctl", "--user", "stop", unit], allowed=(0, 1, 3, 4, 5))
        command(["systemctl", "--user", "reset-failed", unit], allowed=(0, 1, 3, 4, 5))
        counter.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Slice 9 host compatibility without installing the product unit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / ".env.slice6",
        help="mode-0600 private configuration to validate (default: .env.slice6)",
    )
    return parser.parse_args()


def main() -> int:
    config = parse_args().config.expanduser()
    with mutable_root() as state:
        report = validate_host(
            source_root=ROOT,
            config_path=config,
            state_root=state / "deployment-state",
        )
        evidence = {
            "schema_version": "voice-agent.slice9-host-preflight.v1",
            "validation": report.as_dict(),
            "systemd_recovery": systemd_recovery_bound(state),
            "sudo_used": False,
            "shared_service_touched": False,
            "production_unit_installed": False,
            "reboot_performed": False,
            "physical_voice_turn_performed": False,
        }
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

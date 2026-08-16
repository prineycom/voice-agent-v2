"""Detached, manifest-guarded expiry worker for explicit diagnostic captures."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from voice_agent_v2.diagnostics import DiagnosticContentCapture
    from voice_agent_v2.slice6_config import supervised_process_alive
else:
    from .diagnostics import DiagnosticContentCapture
    from .slice6_config import supervised_process_alive


def _uptime_seconds() -> float:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is not None:
        return time.clock_gettime(clock)
    return time.monotonic()


def expire_capture(
    path: Path,
    owner_nonce: str,
    expires_unix_seconds: float,
    expires_uptime_seconds: float,
    *,
    uptime_now: Callable[[], float] = _uptime_seconds,
    sleep: Callable[[float], None] = time.sleep,
    runtime_root: Path | None = None,
    service_main_process: str | None = None,
    process_alive: Callable[[str], bool] = supervised_process_alive,
) -> bool:
    """Wait until the monotonic deadline, then delete only the same owned capture."""
    def delete_owned_capture() -> bool:
        try:
            return DiagnosticContentCapture.delete_path(
                path,
                expected_owner_nonce=owner_nonce,
                expected_expires_unix_seconds=expires_unix_seconds,
                runtime_root=runtime_root,
            )
        except (OSError, ValueError):
            return False

    while (remaining := expires_uptime_seconds - float(uptime_now())) > 0:
        if service_main_process is not None and not process_alive(service_main_process):
            return delete_owned_capture()
        try:
            _resolved, document = DiagnosticContentCapture._owned_manifest(
                path, runtime_root=runtime_root
            )
        except ValueError:
            return False
        if (
            document.get("owner_nonce") != owner_nonce
            or document.get("expires_unix_seconds") != expires_unix_seconds
        ):
            return False
        sleep(min(remaining, 1.0))
    return delete_owned_capture()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire one owned diagnostic capture.")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--owner-nonce", required=True)
    parser.add_argument("--expires-unix-seconds", type=float, required=True)
    parser.add_argument("--expires-uptime-seconds", type=float, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--service-main-process")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    expire_capture(
        arguments.path,
        arguments.owner_nonce,
        arguments.expires_unix_seconds,
        arguments.expires_uptime_seconds,
        runtime_root=arguments.runtime_root,
        service_main_process=arguments.service_main_process,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

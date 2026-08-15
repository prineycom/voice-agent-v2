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
else:
    from .diagnostics import DiagnosticContentCapture


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
) -> bool:
    """Wait until the monotonic deadline, then delete only the same owned capture."""
    while (remaining := expires_uptime_seconds - float(uptime_now())) > 0:
        sleep(remaining)
    try:
        return DiagnosticContentCapture.delete_path(
            path,
            expected_owner_nonce=owner_nonce,
            expected_expires_unix_seconds=expires_unix_seconds,
        )
    except (OSError, ValueError):
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire one owned diagnostic capture.")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--owner-nonce", required=True)
    parser.add_argument("--expires-unix-seconds", type=float, required=True)
    parser.add_argument("--expires-uptime-seconds", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    expire_capture(
        arguments.path,
        arguments.owner_nonce,
        arguments.expires_unix_seconds,
        arguments.expires_uptime_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

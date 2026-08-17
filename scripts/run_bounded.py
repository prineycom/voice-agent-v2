#!/usr/bin/env python3
"""Run one extended verifier with a monotonic deadline and nonce-based cleanup."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_support import PhaseOwner, VerificationDeadline, VerificationFailure, inspect_owned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not 0 < arguments.timeout <= 600:
        raise SystemExit("a command and timeout in (0, 600] are required")
    nonce = f"extended-{uuid.uuid4().hex}"
    owner = PhaseOwner(nonce)
    deadline = time.monotonic() + arguments.timeout

    def stop(_signal_number, _frame) -> None:
        raise VerificationDeadline("extended verifier received TERM at its hard deadline")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failure: BaseException | None = None
    initial = None
    remaining = None
    try:
        owner.run(
            "bounded extended verifier",
            command,
            cwd=ROOT,
            environment=dict(os.environ),
            deadline=deadline,
        )
    except BaseException as error:
        failure = error
    finally:
        initial = inspect_owned(nonce, exclude=(os.getpid(), os.getppid()))
        remaining = owner.cleanup()
    if failure is not None:
        print(f"extended verifier failed: {failure}", file=sys.stderr)
        return 1
    if initial.present or remaining.present:
        print(
            "extended verifier leaked owned processes/listeners: "
            f"initial={initial} remaining={remaining}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

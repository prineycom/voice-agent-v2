#!/usr/bin/env python3
"""Entry point for the versioned local stand interface."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.stand_dev import (  # noqa: E402
    DEFAULT_USER_UNIT_DIRECTORY,
    StandError,
    SystemCommandRunner,
    deploy_local_dev,
    exec_launcher,
    initialize,
    logs,
    start,
    state_root_from_environment,
    status,
)
from voice_agent_v2.stand_doctor import main as doctor_main  # noqa: E402


def usage() -> int:
    print("usage: stand doctor | init | deploy dev --local <repo> <committed-sha> | status dev | logs dev")
    return 2


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = tuple(arguments or ())
    if arguments == ("doctor",):
        return doctor_main(arguments)
    root = state_root_from_environment()
    command = SystemCommandRunner()
    try:
        if arguments == ("init",):
            configured_units = os.environ.get("VOICE_AGENT_STAND_USER_UNIT_DIRECTORY")
            units = Path(configured_units).expanduser() if configured_units else DEFAULT_USER_UNIT_DIRECTORY
            initialize(state_root=root, user_unit_directory=units, stand_executable=ROOT / "stand")
            print("stand init: complete")
            return 0
        if len(arguments) == 5 and arguments[:3] == ("deploy", "dev", "--local"):
            commit = deploy_local_dev(
                state_root=root, repository=Path(arguments[3]), commit=arguments[4], command=command,
            )
            print(f"stand deploy dev: selected {commit}")
            return 0
        if arguments == ("status", "dev"):
            print(status(state_root=root, instance="dev", command=command))
            return 0
        if arguments == ("logs", "dev"):
            print(logs(instance="dev", command=command))
            return 0
        if arguments == ("launcher", "dev"):
            exec_launcher(state_root=root, instance="dev")
            raise AssertionError("foreground launcher unexpectedly returned")
    except StandError as error:
        print(f"stand: {error}", file=sys.stderr)
        return 1
    return usage()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

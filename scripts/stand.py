#!/usr/bin/env python3
"""Entry point for the versioned local stand interface."""

from __future__ import annotations

import json
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
    deploy_local,
    deploy_remote,
    exec_launcher,
    agent_image_status,
    prepare_agent_image,
    initialize,
    list_instances,
    logs,
    start,
    state_root_from_environment,
    status,
    stop,
)
from voice_agent_v2.stand_doctor import main as doctor_main  # noqa: E402


def usage() -> int:
    print("usage: stand doctor | init | agent-image <prepare|status> | deploy main <vMAJOR.MINOR.PATCH> | deploy dev <remote-branch|tag|sha> | deploy dev --local <repo> <committed-sha> | start <main|dev> | stop <main|dev> | status <main|dev> | logs <main|dev> | list")
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
            initialize(
                state_root=root, user_unit_directory=units, stand_executable=ROOT / "stand",
                controller_repository=ROOT, command=command,
            )
            print("stand init: complete")
            return 0
        if arguments == ("agent-image", "prepare"):
            print(json.dumps(prepare_agent_image(
                state_root=root, source_root=ROOT, command=command,
            ), sort_keys=True))
            return 0
        if arguments == ("agent-image", "status"):
            print(json.dumps(agent_image_status(
                state_root=root, source_root=ROOT, command=command,
            ), sort_keys=True))
            return 0
        if len(arguments) == 5 and arguments[0:3] == ("deploy", "dev", "--local"):
            instance = "dev"
            commit = deploy_local(
                state_root=root, instance=instance, repository=Path(arguments[3]),
                commit=arguments[4], command=command,
            )
            print(f"stand deploy {instance}: selected {commit}")
            return 0
        if len(arguments) == 3 and arguments[0] == "deploy" and arguments[1] in {"main", "dev"}:
            instance = arguments[1]
            commit = deploy_remote(
                state_root=root, instance=instance, ref=arguments[2], command=command,
            )
            print(f"stand deploy {instance}: selected {commit}")
            return 0
        if len(arguments) == 2 and arguments[0] == "start" and arguments[1] in {"main", "dev"}:
            start(state_root=root, instance=arguments[1], command=command)
            print(f"stand start {arguments[1]}: running and enabled")
            return 0
        if len(arguments) == 2 and arguments[0] == "stop" and arguments[1] in {"main", "dev"}:
            container = stop(state_root=root, instance=arguments[1], command=command)
            print(f"stand stop {arguments[1]}: stopped and disabled; container: {container}")
            return 0
        if len(arguments) == 2 and arguments[0] == "status" and arguments[1] in {"main", "dev"}:
            print(status(state_root=root, instance=arguments[1], command=command))
            return 0
        if len(arguments) == 2 and arguments[0] == "logs" and arguments[1] in {"main", "dev"}:
            print(logs(state_root=root, instance=arguments[1], command=command))
            return 0
        if arguments == ("list",):
            print(list_instances(state_root=root, command=command))
            return 0
        if len(arguments) == 2 and arguments[0] == "launcher" and arguments[1] in {"main", "dev"}:
            try:
                exec_launcher(state_root=root, instance=arguments[1])
            except StandError as error:
                print(f"stand launcher configuration failed: {error}", file=sys.stderr)
                return 2
            raise AssertionError("foreground launcher unexpectedly returned")
    except StandError as error:
        print(f"stand: {error}", file=sys.stderr)
        return 1
    return usage()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

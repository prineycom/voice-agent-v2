from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.stand_dev import (
    CommandResult,
    StandError,
    build_local_release,
    config_path,
    deploy_local_dev,
    exec_launcher,
    initialize,
    logs,
    parse_private_config,
    selected_release,
    status,
)


class RecordingCommand:
    """Only Git/tar are real in a disposable fixture; systemd/journal stay fake."""

    def __init__(self, *, ready: bool = True, journal: str = "dev launcher record\n") -> None:
        self.ready = ready
        self.journal = journal
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: tuple[str, ...], *, cwd: Path | None = None) -> CommandResult:
        command = tuple(arguments)
        self.calls.append(command)
        if command[:2] == ("systemctl", "--user"):
            if command[2] == "is-active":
                return CommandResult(0 if self.ready else 3, "active\n" if self.ready else "inactive\n")
            return CommandResult(0)
        if command[:1] == ("journalctl",):
            return CommandResult(0, self.journal)
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(("git", *arguments), cwd=repository, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


class StandDevTests(unittest.TestCase):
    def make_source_repository(self, parent: Path) -> tuple[Path, str]:
        repository = parent / "controller"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.email", "stand@example.test")
        git(repository, "config", "user.name", "Stand Test")
        (repository / "scripts").mkdir()
        (repository / "scripts/run_slice6.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (repository / "tracked.txt").write_text("committed release\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-qm", "fixture")
        return repository, git(repository, "rev-parse", "HEAD")

    def initialize_state(self, root: Path, controller: Path) -> tuple[Path, Path]:
        state = root / "outside-controller-state"
        units = root / "user-units"
        initialize(state_root=state, user_unit_directory=units, stand_executable=controller / "stand")
        return state, units

    def test_init_creates_external_private_state_strict_configs_and_unique_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, _ = self.make_source_repository(root)
            state, units = self.initialize_state(root, controller)

            self.assertFalse((controller / "instances").exists())
            self.assertTrue((state / "releases").is_dir())
            main = config_path(state, "main")
            dev = config_path(state, "dev")
            self.assertEqual(stat.S_IMODE(main.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(dev.stat().st_mode), 0o600)
            main_values = parse_private_config(main)
            dev_values = parse_private_config(dev)
            self.assertNotEqual(main_values["LIVEKIT_API_KEY"], dev_values["LIVEKIT_API_KEY"])
            self.assertNotEqual(main_values["LIVEKIT_API_SECRET"], dev_values["LIVEKIT_API_SECRET"])
            self.assertEqual(dev_values["VOICE_AGENT_LIVEKIT_PORT"], "7880")
            template = (units / "voice-agent-v2@.service").read_text(encoding="utf-8")
            self.assertIn("Requires=docker.service", template)
            self.assertIn("launcher %i", template)
            self.assertIn(str(state), template)

    def test_private_configuration_is_strict_data_and_never_shell_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, _ = self.make_source_repository(root)
            state, _ = self.initialize_state(root, controller)
            config = config_path(state, "dev")
            marker = root / "must-not-exist"
            config.write_text(config.read_text(encoding="utf-8").replace(
                "LIVEKIT_API_KEY=", f"LIVEKIT_API_KEY=$(touch {marker})"
            ), encoding="utf-8")
            os.chmod(config, 0o600)

            with self.assertRaisesRegex(StandError, "invalid KEY=VALUE"):
                parse_private_config(config)
            self.assertFalse(marker.exists())
            config.write_text("STAND_NAME=dev\n", encoding="utf-8")
            os.chmod(config, 0o644)
            with self.assertRaisesRegex(StandError, "mode-0600"):
                parse_private_config(config)

    def test_committed_sha_release_is_archive_only_and_current_changes_atomically_after_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, commit = self.make_source_repository(root)
            state, _ = self.initialize_state(root, repository)
            command = RecordingCommand()

            deployed = deploy_local_dev(state_root=state, repository=repository, commit=commit, command=command)

            self.assertEqual(deployed, commit)
            selected = selected_release(state, "dev")
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected[0], commit)
            release = selected[1]
            self.assertFalse((release / "source/.git").exists())
            self.assertEqual((release / "source/tracked.txt").read_text(encoding="utf-8"), "committed release\n")
            self.assertEqual(stat.S_IMODE((release / "release.json").stat().st_mode), 0o444)
            self.assertIn(("systemctl", "--user", "start", "voice-agent-v2@dev.service"), command.calls)
            self.assertIn("readiness: ready", status(state_root=state, instance="dev", command=command))

    def test_foreground_launcher_execs_the_selected_release_with_data_only_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, commit = self.make_source_repository(root)
            state, _ = self.initialize_state(root, repository)
            deploy_local_dev(state_root=state, repository=repository, commit=commit, command=RecordingCommand())

            with patch.dict(os.environ, {"LITELLM_BASE_URL": "forbidden"}, clear=False), patch(
                "voice_agent_v2.stand_dev.os.execve", side_effect=RuntimeError("exec intercepted")
            ) as execve:
                with self.assertRaisesRegex(RuntimeError, "exec intercepted"):
                    exec_launcher(state_root=state, instance="dev")

            executable, arguments, environment = execve.call_args.args
            self.assertEqual(executable, sys.executable)
            self.assertEqual(arguments[-1], str(selected_release(state, "dev")[1] / "source/scripts/run_slice6.py"))
            self.assertNotIn("LITELLM_BASE_URL", environment)
            self.assertEqual(environment["VOICE_AGENT_INSTANCE_ROOT"], str(state / "instances/dev"))

    def test_rejects_dirty_or_nonexact_sha_without_moving_existing_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, commit = self.make_source_repository(root)
            state, _ = self.initialize_state(root, repository)
            command = RecordingCommand()
            deploy_local_dev(state_root=state, repository=repository, commit=commit, command=command)
            before = os.readlink(state / "instances/dev/current")
            (repository / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

            with self.assertRaisesRegex(StandError, "dirty"):
                build_local_release(state_root=state, repository=repository, commit=commit, command=command)
            with self.assertRaisesRegex(StandError, "full lowercase"):
                build_local_release(state_root=state, repository=repository, commit=commit[:12], command=command)
            self.assertEqual(os.readlink(state / "instances/dev/current"), before)

    def test_failed_readiness_stays_selected_and_status_and_logs_are_honest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, commit = self.make_source_repository(root)
            state, _ = self.initialize_state(root, repository)
            command = RecordingCommand(ready=False, journal="launcher readiness failed\n")

            with self.assertRaisesRegex(StandError, "release selected but readiness failed"):
                deploy_local_dev(state_root=state, repository=repository, commit=commit, command=command)

            report = status(state_root=state, instance="dev", command=command)
            self.assertIn(f"version: {commit}", report)
            self.assertIn("readiness: not-ready", report)
            self.assertEqual(logs(instance="dev", command=command), "launcher readiness failed")
            self.assertIn(("journalctl", "--user", "-u", "voice-agent-v2@dev.service", "--no-pager"), command.calls)


if __name__ == "__main__":
    unittest.main()

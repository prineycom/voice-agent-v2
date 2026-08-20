from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.stand_dev import (
    CommandResult,
    PRODUCTION_LOCK,
    RELEASE_SCHEMA,
    StandError,
    config_path,
    controller_source_path,
    deploy_local_dev,
    deploy_remote_dev,
    initialize,
    parse_private_config,
    selected_release,
)


class RecordingCommand:
    """Real disposable Git/tar with fake build tools and user-systemd."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_fetch_ref: str | None = None
        self.fail_build = False
        self.before_build = None

    def run(self, arguments, *, cwd: Path | None = None) -> CommandResult:
        command = tuple(arguments)
        self.calls.append(command)
        if command[:2] == ("systemctl", "--user"):
            if command[2] == "is-active":
                return CommandResult(0, "active\n")
            return CommandResult(0)
        if command[:1] == ("journalctl",):
            return CommandResult(0, "dev launcher record\n")
        if command[:3] == ("git", "fetch", "--no-tags") and command[-1] == self.fail_fetch_ref:
            return CommandResult(1, stderr="deliberate fetch failure")
        if command[:3] == ("python3", "-m", "venv"):
            python = Path(command[3]) / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n", encoding="utf-8")
            return CommandResult(0)
        if command[:1] == ("npm",):
            assert cwd is not None
            if command[1] == "ci":
                if self.before_build is not None:
                    self.before_build()
                (cwd / "node_modules").mkdir()
                return CommandResult(0)
            if command[1:3] == ("run", "build:production-only"):
                if self.fail_build:
                    return CommandResult(1, stderr="deliberate build failure")
                dist = cwd / "dist"
                dist.mkdir()
                (dist / "index.html").write_text("production build\n", encoding="utf-8")
                return CommandResult(0)
        if command[0].endswith("/python") and command[1:4] == ("-m", "pip", "install"):
            return CommandResult(0)
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(("git", *arguments), cwd=repository, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


class StandDevTests(unittest.TestCase):
    def make_source_repository(self, parent: Path) -> tuple[Path, str]:
        repository = parent / "controller"
        repository.mkdir()
        git(repository, "init", "-q", "-b", "main")
        git(repository, "config", "user.email", "stand@example.test")
        git(repository, "config", "user.name", "Stand Test")
        (repository / "scripts").mkdir()
        (repository / "scripts/run_slice6.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (repository / "web").mkdir()
        (repository / "web/package.json").write_text(
            '{"scripts":{"build:production-only":"vite build"}}\n', encoding="utf-8",
        )
        (repository / "web/package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")
        (repository / PRODUCTION_LOCK).write_text("runtime-package==1.0\n", encoding="utf-8")
        (repository / "tracked.txt").write_text("committed release\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-qm", "fixture")
        commit = git(repository, "rev-parse", "HEAD")
        remote = parent / "private-origin.git"
        git(parent, "init", "--bare", "-q", str(remote))
        git(repository, "remote", "add", "origin", str(remote))
        git(repository, "push", "-qu", "origin", "main")
        git(repository, "tag", "remote-tag")
        git(repository, "push", "-q", "origin", "remote-tag")
        return repository, commit

    def commit_remote_revision(self, repository: Path, name: str) -> str:
        (repository / "tracked.txt").write_text(f"{name}\n", encoding="utf-8")
        git(repository, "add", "tracked.txt")
        git(repository, "commit", "-qm", name)
        git(repository, "push", "-q", "origin", "main")
        return git(repository, "rev-parse", "HEAD")

    def initialize_state(self, root: Path, controller: Path, command: RecordingCommand) -> tuple[Path, Path]:
        state = root / "outside-controller-state"
        units = root / "user-units"
        initialize(
            state_root=state, user_unit_directory=units, stand_executable=controller / "stand",
            controller_repository=controller, command=command,
        )
        return state, units

    def test_init_creates_external_private_state_and_one_ordinary_controller_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, _ = self.make_source_repository(root)
            command = RecordingCommand()
            state, units = self.initialize_state(root, controller, command)

            source = controller_source_path(state)
            self.assertFalse((controller / "instances").exists())
            self.assertTrue((source / ".git").is_dir())
            self.assertEqual(git(source, "config", "--get", "remote.origin.url"), str(root / "private-origin.git"))
            self.assertTrue((state / "releases").is_dir())
            self.assertEqual(stat.S_IMODE(config_path(state, "dev").stat().st_mode), 0o600)
            self.assertEqual(parse_private_config(config_path(state, "dev"))["STAND_NAME"], "dev")
            template = (units / "voice-agent-v2@.service").read_text(encoding="utf-8")
            self.assertIn("Requires=docker.service", template)
            self.assertIn("launcher %i", template)
            self.assertIn(("git", "clone", "--no-checkout", str(root / "private-origin.git"), str(source)), command.calls)

    def test_remote_branch_resolves_full_sha_and_promotes_complete_archive_release_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            release = state / "releases" / commit

            def during_frontend_install() -> None:
                self.assertFalse(release.exists())
                self.assertFalse((state / "instances/dev/current").exists())

            command.before_build = during_frontend_install
            deployed = deploy_remote_dev(state_root=state, ref="main", command=command)

            self.assertEqual(deployed, commit)
            self.assertEqual(len(deployed), 40)
            selected = selected_release(state, "dev")
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected[0], commit)
            release = selected[1]
            manifest = json.loads((release / "release.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], RELEASE_SCHEMA)
            self.assertEqual(manifest["commit"], commit)
            self.assertEqual(manifest["source"], "git-archive")
            self.assertEqual(manifest["frontend"], "source/web/dist")
            self.assertEqual(manifest["python_lock"], PRODUCTION_LOCK)
            self.assertFalse((release / "source/.git").exists())
            self.assertEqual((release / "source/tracked.txt").read_text(encoding="utf-8"), "committed release\n")
            self.assertTrue((release / "source/web/dist/index.html").is_file())
            self.assertTrue((release / "python/bin/python").is_file())
            self.assertFalse((release / "source/web/node_modules").exists())
            self.assertEqual(stat.S_IMODE((release / "release.json").stat().st_mode), 0o444)
            self.assertIn(("git", "fetch", "--no-tags", "origin", "refs/heads/main"), command.calls)
            self.assertIn(("npm", "run", "build:production-only"), command.calls)

    def test_remote_tag_and_full_sha_reuse_the_completed_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)

            self.assertEqual(deploy_remote_dev(state_root=state, ref="main", command=command), commit)
            self.assertEqual(deploy_remote_dev(state_root=state, ref="remote-tag", command=command), commit)
            self.assertEqual(deploy_remote_dev(state_root=state, ref=commit, command=command), commit)

            self.assertEqual(sum(call[:2] == ("npm", "ci") for call in command.calls), 1)
            self.assertEqual(len([entry for entry in (state / "releases").iterdir() if not entry.name.startswith(".")]), 1)
            self.assertEqual(selected_release(state, "dev")[0], commit)

    def test_remote_resolution_and_build_or_manifest_failures_preserve_completed_release_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, commit = self.make_source_repository(root)
            git(controller, "branch", "fetch-fails")
            git(controller, "push", "-q", "origin", "fetch-fails")
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            deploy_remote_dev(state_root=state, ref="main", command=command)
            before = os.readlink(state / "instances/dev/current")
            releases_before = sorted(path.name for path in (state / "releases").iterdir() if not path.name.startswith("."))

            command.fail_fetch_ref = "refs/heads/fetch-fails"
            with self.assertRaisesRegex(StandError, "fetch failed"):
                deploy_remote_dev(state_root=state, ref="fetch-fails", command=command)
            with self.assertRaisesRegex(StandError, "unresolved"):
                deploy_remote_dev(state_root=state, ref="does-not-exist", command=command)

            next_commit = self.commit_remote_revision(controller, "build-fails")
            command.fail_build = True
            with self.assertRaisesRegex(StandError, "frontend build failed"):
                deploy_remote_dev(state_root=state, ref="main", command=command)
            command.fail_build = False

            manifest_commit = self.commit_remote_revision(controller, "manifest-fails")
            with patch("voice_agent_v2.stand_dev.json.dumps", side_effect=TypeError("deliberate manifest failure")):
                with self.assertRaisesRegex(StandError, "manifest construction failed"):
                    deploy_remote_dev(state_root=state, ref="main", command=command)

            self.assertNotEqual(next_commit, manifest_commit)
            self.assertEqual(os.readlink(state / "instances/dev/current"), before)
            self.assertEqual(sorted(path.name for path in (state / "releases").iterdir() if not path.name.startswith(".")), releases_before)
            self.assertFalse(any(path.name.startswith(".") for path in (state / "releases").iterdir()))
            self.assertEqual(selected_release(state, "dev")[0], commit)

    def test_local_committed_sha_path_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)

            self.assertEqual(deploy_local_dev(state_root=state, repository=controller, commit=commit, command=command), commit)
            self.assertEqual(selected_release(state, "dev")[0], commit)
            (controller / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(StandError, "dirty"):
                deploy_local_dev(state_root=state, repository=controller, commit=commit, command=command)


if __name__ == "__main__":
    unittest.main()

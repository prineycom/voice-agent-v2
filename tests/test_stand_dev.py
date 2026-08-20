from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import stand as stand_cli
from voice_agent_v2.stand_dev import (
    CommandResult,
    PRODUCTION_LOCK,
    RELEASE_SCHEMA,
    StandError,
    config_path,
    controller_source_path,
    deploy_local,
    deploy_local_dev,
    deploy_remote,
    deploy_remote_dev,
    initialize,
    launcher_environment,
    logs,
    parse_private_config,
    selected_release,
    status,
)


class RecordingCommand:
    """Real disposable Git/tar with fake build tools and user-systemd."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_fetch_ref: str | None = None
        self.fail_build = False
        self.before_build = None
        self.ready = True

    def run(self, arguments, *, cwd: Path | None = None) -> CommandResult:
        command = tuple(arguments)
        self.calls.append(command)
        if command[:2] == ("systemctl", "--user"):
            if command[2] == "is-active":
                return CommandResult(0, "active\n") if self.ready else CommandResult(3, "inactive\n")
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
        git(repository, "tag", "-a", "v1.0.0", "-m", "release v1.0.0")
        git(repository, "push", "-q", "origin", "remote-tag", "v1.0.0")
        return repository, commit

    def commit_remote_revision(self, repository: Path, name: str) -> str:
        (repository / "tracked.txt").write_text(f"{name}\n", encoding="utf-8")
        git(repository, "add", "tracked.txt")
        git(repository, "commit", "-qm", name)
        git(repository, "push", "-q", "origin", "main")
        return git(repository, "rev-parse", "HEAD")

    def tag_remote_release(self, repository: Path, tag: str) -> None:
        git(repository, "tag", "-a", tag, "-m", f"release {tag}")
        git(repository, "push", "-q", "origin", tag)

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

    def test_main_admits_only_exact_semver_tags_and_resolves_annotated_tag_to_full_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)

            for refused in (
                "", "latest", "main", commit, "remote-tag", "refs/tags/v1.0.0",
                "v1", "v1.2", "v1.2.3.4", "v01.2.3", "v1.02.3", "v1.2.03",
                "1.2.3", "v1.2.3-rc.1", "v1.2.3+build",
            ):
                with self.subTest(ref=refused):
                    with self.assertRaisesRegex(StandError, "exact vMAJOR.MINOR.PATCH"):
                        deploy_remote(state_root=state, instance="main", ref=refused, command=command)
            with self.assertRaisesRegex(StandError, "exact vMAJOR.MINOR.PATCH"):
                deploy_local(
                    state_root=state, instance="main", repository=controller,
                    commit=commit, command=command,
                )
            git(controller, "branch", "v9.9.9")
            git(controller, "push", "-q", "origin", "v9.9.9")
            with self.assertRaisesRegex(StandError, "tag is unresolved"):
                deploy_remote(state_root=state, instance="main", ref="v9.9.9", command=command)
            with patch.dict(os.environ, {"VOICE_AGENT_STAND_STATE_ROOT": str(state)}), patch("builtins.print") as output:
                self.assertEqual(stand_cli.main(("deploy", "main")), 2)
            self.assertIn("deploy main <vMAJOR.MINOR.PATCH>", output.call_args.args[0])

            deployed = deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command)
            advertised_tag_object = git(
                controller, "ls-remote", "--refs", "origin", "refs/tags/v1.0.0",
            ).split()[0]
            self.assertNotEqual(advertised_tag_object, commit)
            self.assertEqual(deployed, commit)
            self.assertEqual(len(deployed), 40)
            self.assertEqual(selected_release(state, "main")[0], commit)
            self.assertIn(("git", "fetch", "--no-tags", "origin", "refs/tags/v1.0.0"), command.calls)
            record = json.loads((state / "instances/main/tag-targets/v1.0.0.json").read_text())
            self.assertEqual(record["tag"], "v1.0.0")
            self.assertEqual(record["commit"], commit)

    def test_main_refuses_a_moved_previously_observed_tag_durably(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, first_commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            self.assertEqual(
                deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command),
                first_commit,
            )
            before = os.readlink(state / "instances/main/current")

            moved_commit = self.commit_remote_revision(controller, "moved tag target")
            git(controller, "tag", "-f", "v1.0.0", moved_commit)
            git(controller, "push", "-q", "--force", "origin", "refs/tags/v1.0.0")
            with self.assertRaisesRegex(StandError, "tag v1.0.0 moved"):
                deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command)

            self.assertNotEqual(moved_commit, first_commit)
            self.assertEqual(os.readlink(state / "instances/main/current"), before)
            self.assertEqual(selected_release(state, "main")[0], first_commit)
            record = json.loads((state / "instances/main/tag-targets/v1.0.0.json").read_text())
            self.assertEqual(record["commit"], first_commit)

    def test_main_validates_target_external_configuration_before_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, first_commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command)
            before = os.readlink(state / "instances/main/current")
            second_commit = self.commit_remote_revision(controller, "second release")
            self.tag_remote_release(controller, "v2.0.0")
            main_config = config_path(state, "main")
            main_config.write_text(
                main_config.read_text(encoding="utf-8").replace("STAND_NAME=main", "STAND_NAME=dev"),
                encoding="utf-8",
            )
            os.chmod(main_config, 0o600)

            with self.assertRaisesRegex(StandError, "assigned to the wrong instance"):
                deploy_remote(state_root=state, instance="main", ref="v2.0.0", command=command)

            self.assertTrue((state / "releases" / second_commit).is_dir())
            self.assertEqual(os.readlink(state / "instances/main/current"), before)
            self.assertEqual(selected_release(state, "main")[0], first_commit)
            self.assertEqual(
                json.loads((state / "instances/main/tag-targets/v2.0.0.json").read_text())["commit"],
                second_commit,
            )

    def test_failed_readiness_stays_selected_until_explicit_older_tag_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, first_commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            self.assertEqual(
                deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command),
                first_commit,
            )
            second_commit = self.commit_remote_revision(controller, "second release")
            self.tag_remote_release(controller, "v2.0.0")

            command.ready = False
            with self.assertRaisesRegex(StandError, "release selected but readiness failed"):
                deploy_remote(state_root=state, instance="main", ref="v2.0.0", command=command)
            self.assertEqual(selected_release(state, "main")[0], second_commit)
            failed_status = status(state_root=state, instance="main", command=command)
            self.assertIn(f"version: {second_commit}", failed_status)
            self.assertIn("readiness: not-ready", failed_status)

            command.ready = True
            rolled_back = deploy_remote(
                state_root=state, instance="main", ref="v1.0.0", command=command,
            )
            self.assertEqual(rolled_back, first_commit)
            self.assertEqual(selected_release(state, "main")[0], first_commit)
            self.assertEqual(sum(call[:2] == ("npm", "ci") for call in command.calls), 2)

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

    def test_main_and_dev_have_independent_complete_loopback_stack_contracts(self) -> None:
        from voice_agent_v2.agent_environment_config import DEFAULT_CONFIG_BYTES, parse_agent_config_v2
        from voice_agent_v2.agent_run_provider import AgentRunProvider
        from voice_agent_v2.local_lfm import LocalLFMProvider
        from voice_agent_v2.slice6_config import Slice6Settings, livekit_server_config

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, first_commit = self.make_source_repository(root)
            command = RecordingCommand()
            state, _ = self.initialize_state(root, controller, command)
            second_commit = self.commit_remote_revision(controller, "second release")

            self.assertEqual(
                deploy_remote(state_root=state, instance="main", ref="v1.0.0", command=command),
                first_commit,
            )
            self.assertEqual(
                deploy_remote(state_root=state, instance="dev", ref="main", command=command),
                second_commit,
            )
            main_selected = selected_release(state, "main")
            dev_selected = selected_release(state, "dev")
            assert main_selected is not None and dev_selected is not None
            self.assertNotEqual(os.readlink(state / "instances/main/current"), os.readlink(state / "instances/dev/current"))
            self.assertEqual(main_selected[0], first_commit)
            self.assertEqual(dev_selected[0], second_commit)
            self.assertEqual(main_selected[1].parent, dev_selected[1].parent)

            environments: dict[str, dict[str, str]] = {}
            for instance, commit in (("main", first_commit), ("dev", second_commit)):
                python, arguments, environment = launcher_environment(
                    state_root=state, instance=instance, environment={"XDG_CACHE_HOME": str(root / "shared")},
                )
                environments[instance] = environment
                self.assertEqual(arguments[0], str(python))
                self.assertEqual(environment["VOICE_AGENT_BUILD_ID"], commit)
                self.assertEqual(environment["VOICE_AGENT_RELEASE_ID"], commit[:24])
                self.assertEqual(environment["STAND_NAME"], instance)
                self.assertEqual(environment["VOICE_AGENT_SHARED_CACHE_ROOT"], str(root / "shared/voice-agent-v2"))
                instance_root = (state / "instances" / instance).resolve()
                self.assertEqual(environment["VOICE_AGENT_INSTANCE_ROOT"], str(instance_root))
                for name in (
                    "VOICE_AGENT_DATA_ROOT", "VOICE_AGENT_MUTABLE_CACHE_ROOT",
                    "VOICE_AGENT_TASK_RUNTIME_ROOT", "VOICE_AGENT_WORKSPACE_ROOT",
                    "VOICE_AGENT_CREDENTIALS_ROOT", "VOICE_AGENT_AGENT_ENVIRONMENT_ROOT",
                    "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX",
                ):
                    self.assertTrue(Path(environment[name]).is_relative_to(instance_root), name)
                settings = Slice6Settings.from_environment(environment, project_root=main_selected[1] / "source")
                self.assertEqual(settings.livekit_internal_url, environment["LIVEKIT_INTERNAL_URL"])
                server = json.loads(livekit_server_config(environment))
                self.assertEqual(server["bind_addresses"], ["127.0.0.1"])
                self.assertEqual(server["rtc"]["node_ip"], "127.0.0.1")
                self.assertEqual(server["port"], int(environment["VOICE_AGENT_LIVEKIT_PORT"]))
                self.assertEqual(server["rtc"]["udp_port"], int(environment["VOICE_AGENT_RTC_UDP_PORT"]))
                with patch.dict(os.environ, environment, clear=True):
                    provider = LocalLFMProvider()
                self.assertEqual(provider._host, "127.0.0.1")
                self.assertEqual(provider._port, int(environment["VOICE_AGENT_LLM_PORT"]))

                agent_provider = AgentRunProvider(
                    parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
                    installation_root=Path(environment["VOICE_AGENT_AGENT_ENVIRONMENT_ROOT"]),
                    credential_root=Path(environment["VOICE_AGENT_CREDENTIALS_ROOT"]),
                    workspace_root=Path(environment["VOICE_AGENT_WORKSPACE_ROOT"]),
                    cache_root=Path(environment["VOICE_AGENT_MUTABLE_CACHE_ROOT"]) / "agent-environment",
                )
                self.assertEqual(
                    agent_provider.environment.state_root,
                    instance_root / "agent-environment/private",
                )
                self.assertEqual(agent_provider.environment.workspace, instance_root / "workspace")
                self.assertEqual(agent_provider.environment.cache, instance_root / "cache/agent-environment")
                self.assertEqual(
                    agent_provider.environment.credential_store.path,
                    instance_root / "credentials/credentials.json",
                )

            main_values = parse_private_config(config_path(state, "main"))
            dev_values = parse_private_config(config_path(state, "dev"))
            self.assertFalse(
                {main_values[name] for name in (
                    "VOICE_AGENT_LLM_PORT", "VOICE_AGENT_LIVEKIT_PORT",
                    "VOICE_AGENT_RTC_UDP_PORT", "VOICE_AGENT_GATEWAY_PORT",
                )} & {dev_values[name] for name in (
                    "VOICE_AGENT_LLM_PORT", "VOICE_AGENT_LIVEKIT_PORT",
                    "VOICE_AGENT_RTC_UDP_PORT", "VOICE_AGENT_GATEWAY_PORT",
                )}
            )
            self.assertNotEqual(main_values["LIVEKIT_API_KEY"], dev_values["LIVEKIT_API_KEY"])
            self.assertNotEqual(main_values["LIVEKIT_API_SECRET"], dev_values["LIVEKIT_API_SECRET"])
            for name in (
                "VOICE_AGENT_DATA_ROOT", "VOICE_AGENT_MUTABLE_CACHE_ROOT",
                "VOICE_AGENT_TASK_RUNTIME_ROOT", "VOICE_AGENT_WORKSPACE_ROOT",
                "VOICE_AGENT_CREDENTIALS_ROOT", "VOICE_AGENT_AGENT_ENVIRONMENT_ROOT",
                "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX",
            ):
                self.assertNotEqual(environments["main"][name], environments["dev"][name])

            main_status = status(state_root=state, instance="main", command=command)
            dev_status = status(state_root=state, instance="dev", command=command)
            self.assertIn(f"stand status main\nversion: {first_commit}", main_status)
            self.assertIn(f"stand status dev\nversion: {second_commit}", dev_status)
            self.assertIn(
                f"stand logs main\nversion: {first_commit}\nunit: voice-agent-v2@main.service",
                logs(state_root=state, instance="main", command=command),
            )
            self.assertIn(
                f"stand logs dev\nversion: {second_commit}\nunit: voice-agent-v2@dev.service",
                logs(state_root=state, instance="dev", command=command),
            )
            self.assertIn(("systemctl", "--user", "start", "voice-agent-v2@main.service"), command.calls)
            self.assertIn(("systemctl", "--user", "start", "voice-agent-v2@dev.service"), command.calls)

    def test_selected_instance_never_falls_back_to_legacy_mutable_paths_or_ports(self) -> None:
        from voice_agent_v2.agent_config import AgentUserContext
        from voice_agent_v2.instance_runtime import InstanceRuntimeError, listener_port, mutable_path

        instance = Path("/tmp/voice-agent-instance-contract")
        selected = {"VOICE_AGENT_INSTANCE_ROOT": str(instance)}
        self.assertEqual(
            mutable_path("runtime/traces", legacy=Path.home() / ".cache/legacy", environment=selected),
            instance / "runtime/traces",
        )
        with self.assertRaisesRegex(InstanceRuntimeError, "requires explicit"):
            listener_port("VOICE_AGENT_LLM_PORT", 18080, selected)
        with patch.dict(os.environ, selected, clear=True):
            context = AgentUserContext.effective()
        self.assertEqual(context.profile_root, instance / "config/agent-profile")
        self.assertNotEqual(context.profile_root, context.home / ".voice-agent")


if __name__ == "__main__":
    unittest.main()

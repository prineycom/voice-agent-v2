from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from voice_agent_v2 import operations, operations_cli
from voice_agent_v2.operations import (
    DEFAULT_MANIFEST_RELATIVE,
    OperationalError,
    ReleaseStore,
    ValidationReport,
    _canonical_json,
    _operational_release_id,
    _run_git,
    _sha256_bytes,
    evaluate_sustained_run,
    execute_release,
    load_operations_manifest,
    parse_server_configuration,
    release_tree_digest,
    sha256_file,
    validate_release,
    validate_server_configuration,
    verify_artifacts,
    verify_disk_policy,
    verify_tracked_manifest_alignment,
)
from voice_agent_v2.runtime_directory import SYSTEMD_RUNTIME_ROOT
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.slice6_config import Slice6Settings


ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = "voice.example.invalid"


def configuration_values(secret: str = "0123456789abcdef") -> dict[str, str]:
    return {
        "LIVEKIT_API_KEY": "slice9key",
        "LIVEKIT_API_SECRET": secret,
        "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_PUBLIC_URL": "ws://127.0.0.1:7880",
        "SLICE6_APP_PUBLIC_URL": "http://127.0.0.1:8000",
    }


def write_configuration(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(0o600)


def normalized_systemd_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    section: dict[str, list[str]] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = result.setdefault(line[1:-1], {})
            continue
        if section is None or "=" not in line:
            raise AssertionError("systemd unit is not normalized")
        name, value = line.split("=", 1)
        section.setdefault(name, []).append(value)
    return result


class OperationsManifestTests(unittest.TestCase):
    def test_manifest_closes_component_order_restart_and_external_cloud_boundaries(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        components = {component["name"]: component for component in manifest["components"]}
        self.assertEqual(
            set(components),
            {
                "livekit", "web-gateway", "controller", "local-stt", "local-tts",
                "selected-local-llm", "provider-adapter", "avatar-host", "mvp-eye", "cloud-llm",
            },
        )
        self.assertEqual(components["cloud-llm"]["supervision"], "external-readiness-only")
        self.assertFalse(components["cloud-llm"]["active"])
        self.assertEqual(manifest["deployment"]["auth_boundary"], "loopback")
        self.assertFalse(any(
            "tailnet" in role or "tailscale" in role
            for role in (
                *manifest["lifecycle"]["start_order"],
                *manifest["lifecycle"]["stop_order"],
            )
        ))
        restart = manifest["lifecycle"]["restart_policy"]
        self.assertEqual(restart["automatic_recoveries_per_failure_window"], 1)
        self.assertEqual(restart["restart_seconds"], 5)
        self.assertEqual(restart["start_limit_interval_seconds"], "infinity")
        self.assertEqual(manifest["lifecycle"]["startup_hard_seconds"], 300)
        self.assertEqual(manifest["lifecycle"]["graceful_drain_seconds"], 54)
        self.assertEqual(manifest["disk"]["cleanup_policy"], "refuse-without-deleting")
        verify_tracked_manifest_alignment(source_root=ROOT, operations_manifest=manifest)
        with tempfile.TemporaryDirectory() as temporary:
            changed = dict(manifest, unknown_policy=True)
            path = Path(temporary) / "operations.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(OperationalError, "unknown fields"):
                load_operations_manifest(path)

    def test_manifest_rejects_drift_in_every_component_supervision_entry(self) -> None:
        manifest = json.loads(
            (ROOT / DEFAULT_MANIFEST_RELATIVE).read_text(encoding="utf-8")
        )
        mutations = (
            ("livekit", "location", "external"),
            ("web-gateway", "supervision", "owned-child-process"),
            ("controller", "admission_required", False),
            ("local-stt", "location", "browser"),
            ("local-tts", "supervision", "controller-owned-process"),
            ("selected-local-llm", "admission_required", False),
            ("provider-adapter", "location", "external"),
            ("avatar-host", "admission_required", True),
            ("mvp-eye", "supervision", "external-readiness-only"),
            ("cloud-llm", "active", True),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operations.json"
            for name, field, value in mutations:
                with self.subTest(component=name, field=field):
                    changed = json.loads(json.dumps(manifest))
                    component = next(
                        item for item in changed["components"] if item["name"] == name
                    )
                    component[field] = value
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(
                        OperationalError, "component supervision boundary",
                    ):
                        load_operations_manifest(path)

    def test_systemd_unit_matches_executable_restart_and_process_custody_contract(self) -> None:
        unit_path = ROOT / "ops/systemd/voice-agent-v2.service"
        verification = subprocess.run(
            ["systemd-analyze", "security", "--offline=yes", str(unit_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(verification.returncode, 0, verification.stderr)

        operations_cli._validate_systemd_unit(unit_path)
        document = normalized_systemd_unit(unit_path)
        service = document["Service"]
        unit = document["Unit"]
        self.assertEqual(unit["After"], ["local-fs.target"])
        self.assertNotIn("Wants", unit)
        self.assertEqual(unit["StartLimitBurst"], ["2"])
        self.assertEqual(unit["StartLimitIntervalSec"], ["infinity"])
        self.assertEqual(service["Type"], ["notify"])
        self.assertEqual(service["NotifyAccess"], ["main"])
        self.assertEqual(service["Restart"], ["on-failure"])
        self.assertEqual(service["RestartSec"], ["5s"])
        self.assertEqual(service["RestartPreventExitStatus"], ["2"])
        self.assertEqual(service["TimeoutStartSec"], ["300s"])
        self.assertEqual(service["TimeoutStopSec"], ["75s"])
        self.assertEqual(service["KillMode"], ["mixed"])
        self.assertEqual(service["User"], ["priney"])
        self.assertEqual(
            service["ExecStart"],
            ["%h/.local/share/voice-agent-v2/current/voice-agent-ops run"],
        )
        self.assertEqual(service["ReadWritePaths"], ["%h/.cache/voice-agent-v2"])
        self.assertEqual(service["RuntimeDirectory"], ["voice-agent-v2"])
        self.assertEqual(service["RuntimeDirectoryMode"], ["0700"])
        self.assertEqual(service["RuntimeDirectoryPreserve"], ["no"])
        environment = dict(
            assignment.split("=", 1) for assignment in service["Environment"]
        )
        self.assertEqual(environment, {
            "HOME": "%h",
            "XDG_RUNTIME_DIR": "/run/voice-agent-v2",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPYCACHEPREFIX": "/run/voice-agent-v2/pycache",
        })
        self.assertFalse(any(name.startswith("LITELLM_") for name in environment))

    def test_systemd_install_validation_rejects_any_sandbox_contract_drift(self) -> None:
        source = ROOT / "ops/systemd/voice-agent-v2.service"
        mutations = {
            "ProtectSystem=strict": "ProtectSystem=full",
            "ProtectHome=read-only": "ProtectHome=no",
            "ReadWritePaths=%h/.cache/voice-agent-v2": "ReadWritePaths=%h",
            "NoNewPrivileges=yes": "NoNewPrivileges=no",
            "PrivateTmp=yes": "PrivateTmp=no",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK": (
                "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK AF_PACKET"
            ),
            "WorkingDirectory=%h/.local/share/voice-agent-v2/current": "WorkingDirectory=%h",
            "Environment=HOME=%h": "Environment=HOME=/tmp",
        }
        with tempfile.TemporaryDirectory() as temporary:
            original = source.read_text(encoding="utf-8")
            for expected, changed in mutations.items():
                with self.subTest(directive=expected.split("=", 1)[0]):
                    candidate = Path(temporary) / "voice-agent-v2.service"
                    candidate.write_text(
                        original.replace(expected, changed), encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        OperationalError, "sandbox policy is incompatible",
                    ):
                        operations_cli._validate_systemd_unit(candidate)

    def test_effective_systemd_validation_rejects_dropins_and_policy_overrides(self) -> None:
        baseline = dict(operations_cli.EFFECTIVE_SYSTEMD_CONTRACT)
        baseline["FragmentPath"] = str(operations_cli.SYSTEM_UNIT_PATH)

        def result(values: dict[str, str]) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=0,
                stdout="".join(f"{name}={value}\n" for name, value in values.items()),
            )

        with patch.object(operations_cli, "_sudo", return_value=result(baseline)):
            operations_cli._validate_effective_systemd_service()
        for name, value in (
            ("DropInPaths", "/etc/systemd/system/voice-agent-v2.service.d/override.conf"),
            ("User", "root"),
            ("ProtectSystem", "full"),
        ):
            changed = dict(baseline)
            changed[name] = value
            with (
                self.subTest(property=name),
                patch.object(operations_cli, "_sudo", return_value=result(changed)),
                self.assertRaisesRegex(OperationalError, "effective systemd"),
            ):
                operations_cli._validate_effective_systemd_service()


class ServiceApplicationTests(unittest.TestCase):
    def test_systemd_start_and_restart_allow_the_full_bounded_job(self) -> None:
        calls: list[dict[str, object]] = []

        def run(*_arguments: object, **keywords: object) -> subprocess.CompletedProcess[str]:
            calls.append(keywords)
            return subprocess.CompletedProcess([], 0, "", "")

        with patch.object(operations_cli.subprocess, "run", side_effect=run):
            operations_cli._sudo("systemctl", "start", operations_cli.SERVICE_NAME)
            operations_cli._sudo("systemctl", "restart", operations_cli.SERVICE_NAME)
            operations_cli._sudo("systemctl", "daemon-reload")

        self.assertEqual(
            [call["timeout"] for call in calls],
            [
                operations_cli.SYSTEMD_JOB_TIMEOUT_SECONDS,
                operations_cli.SYSTEMD_JOB_TIMEOUT_SECONDS,
                operations_cli.SYSTEM_COMMAND_TIMEOUT_SECONDS,
            ],
        )
        self.assertGreater(
            operations_cli.SYSTEMD_JOB_TIMEOUT_SECONDS,
            300 + 75,
        )

    def test_supported_activation_resets_exactly_one_recovery_allowance(self) -> None:
        calls: list[tuple[str, ...]] = []

        def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(returncode=0)

        with patch.object(operations_cli, "_sudo", side_effect=sudo):
            operations_cli._start_service_with_recovery_allowance("start")
            operations_cli._start_service_with_recovery_allowance("restart")

        self.assertEqual(calls, [
            ("systemctl", "reset-failed", operations_cli.SERVICE_NAME),
            ("systemctl", "start", operations_cli.SERVICE_NAME),
            ("systemctl", "reset-failed", operations_cli.SERVICE_NAME),
            ("systemctl", "restart", operations_cli.SERVICE_NAME),
        ])

    def _fixture(self, root: Path) -> tuple[Path, Path, SimpleNamespace]:
        state = root / "state"
        release = state / "releases" / ("a" * 24)
        unit = release / "ops/systemd/voice-agent-v2.service"
        unit.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "ops/systemd/voice-agent-v2.service", unit)
        (state / "current").symlink_to(f"releases/{release.name}")
        return state, release, SimpleNamespace(state_root=state, restart=False)

    def test_reapplying_ready_service_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": release.name},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            self.assertFalse(any(command[0] in {"install", "systemctl"} and (
                command[0] == "install" or command[1] in {"enable", "start", "restart"}
            ) for command in calls))
            wait.assert_called_once_with(release.name)
            self.assertIn(("systemctl", "daemon-reload"), calls)
            self.assertFalse(output.call_args.args[0]["changed"])

    def test_identical_unit_retries_daemon_reload_after_a_prior_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            reload_attempts = 0

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                nonlocal reload_attempts
                if command == ("systemctl", "daemon-reload"):
                    reload_attempts += 1
                    if reload_attempts == 1:
                        raise OperationalError(
                            "systemd_install_failed", "fixture reload failure",
                        )
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": release.name},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                with self.assertRaisesRegex(OperationalError, "reload failure"):
                    operations_cli.command_install_service(arguments)
                operations_cli.command_install_service(arguments)

            self.assertEqual(reload_attempts, 2)
            wait.assert_called_once_with(release.name)
            self.assertFalse(output.call_args.args[0]["changed"])

    def test_changed_unit_and_release_restart_before_ready_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(
                    returncode=1 if command[:2] == ("cmp", "-s") else 0,
                )

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": "b" * 24},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            restart = calls.index(
                ("systemctl", "restart", operations_cli.SERVICE_NAME)
            )
            self.assertEqual(
                calls[restart - 1],
                ("systemctl", "reset-failed", operations_cli.SERVICE_NAME),
            )
            wait.assert_called_once_with(release.name)
            self.assertTrue(output.call_args.args[0]["changed"])
            self.assertTrue(output.call_args.args[0]["ready"])
            self.assertTrue(output.call_args.args[0]["service_restarted"])

    def test_failed_effective_policy_validation_restores_the_prior_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, _release, arguments = self._fixture(root)
            installed = root / "installed.service"
            installed.write_text("prior unit\n", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(
                    returncode=1 if command[:2] == ("cmp", "-s") else 0,
                )

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "SYSTEM_UNIT_PATH", installed),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(
                    operations_cli, "_validate_effective_systemd_service",
                    side_effect=OperationalError(
                        "systemd_unit_incompatible", "effective policy rejected",
                    ),
                ),
                self.assertRaisesRegex(OperationalError, "effective policy rejected"),
            ):
                operations_cli.command_install_service(arguments)

            installs = [
                index for index, command in enumerate(calls)
                if command and command[0] == "install"
            ]
            reloads = [
                index for index, command in enumerate(calls)
                if command == ("systemctl", "daemon-reload")
            ]
            self.assertEqual(len(installs), 2)
            self.assertEqual(len(reloads), 2)
            self.assertLess(installs[0], reloads[0])
            self.assertLess(reloads[0], installs[1])
            self.assertLess(installs[1], reloads[1])
            self.assertEqual(calls[installs[1]][-1], str(installed))
            self.assertFalse(any(
                command[:2] in {
                    ("systemctl", "enable"),
                    ("systemctl", "start"),
                    ("systemctl", "restart"),
                }
                for command in calls
            ))

    def test_readiness_failure_restores_unit_enablement_and_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, _release, arguments = self._fixture(root)
            installed = root / "installed.service"
            installed.write_text("prior unit\n", encoding="utf-8")
            calls: list[tuple[str, ...]] = []
            active_queries = 0

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                nonlocal active_queries
                calls.append(command)
                if command[:2] == ("cmp", "-s"):
                    return SimpleNamespace(returncode=1)
                if command[:2] == ("systemctl", "is-enabled"):
                    return SimpleNamespace(returncode=1)
                if command[:2] == ("systemctl", "is-active"):
                    active_queries += 1
                    return SimpleNamespace(returncode=3 if active_queries == 1 else 0)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "SYSTEM_UNIT_PATH", installed),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(
                    operations_cli, "_wait_for_runtime_release",
                    side_effect=OperationalError(
                        "systemd_install_failed", "fixture readiness failure",
                    ),
                ),
                self.assertRaisesRegex(OperationalError, "readiness failure"),
            ):
                operations_cli.command_install_service(arguments)

            installs = [command for command in calls if command[:1] == ("install",)]
            self.assertEqual(len(installs), 2)
            self.assertEqual(
                calls.count(("systemctl", "daemon-reload")), 2,
            )
            self.assertIn(("systemctl", "enable", operations_cli.SERVICE_NAME), calls)
            self.assertIn(("systemctl", "disable", operations_cli.SERVICE_NAME), calls)
            self.assertIn(("systemctl", "start", operations_cli.SERVICE_NAME), calls)
            self.assertIn(("systemctl", "stop", operations_cli.SERVICE_NAME), calls)
            reloads = [
                index for index, command in enumerate(calls)
                if command == ("systemctl", "daemon-reload")
            ]
            self.assertLess(
                reloads[1],
                calls.index(("systemctl", "stop", operations_cli.SERVICE_NAME)),
            )

    def test_explicit_restart_revalidates_and_restarts_the_unchanged_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            arguments.restart = True
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}) as validate,
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": release.name},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            validate.assert_called_once_with(
                release, state_root=state.resolve(), verify_host_state=True,
            )
            restart = calls.index(
                ("systemctl", "restart", operations_cli.SERVICE_NAME)
            )
            self.assertEqual(
                calls[restart - 1],
                ("systemctl", "reset-failed", operations_cli.SERVICE_NAME),
            )
            wait.assert_called_once_with(release.name)
            result = output.call_args.args[0]
            self.assertTrue(result["restart_requested"])
            self.assertTrue(result["service_restarted"])
            self.assertTrue(result["changed"])

    def test_deploy_apply_signal_is_limited_to_release_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            arguments = SimpleNamespace(state_root=state, config=state / "runtime.env")
            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(
                    operations_cli.ReleaseStore, "deploy",
                    return_value={"changed": False, "status": "no-op"},
                ),
                patch.object(operations_cli, "_systemctl_show", return_value={"load": "loaded"}),
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_deploy(arguments)
            result = output.call_args.args[0]
            self.assertFalse(result["release_service_apply_required"])
            self.assertNotIn("service_apply_required", result)

    def test_canonical_deploy_fails_before_activation_when_systemd_query_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            arguments = SimpleNamespace(state_root=state, config=state / "runtime.env")
            activations: list[str] = []

            def deploy(**_arguments: object) -> dict[str, object]:
                activations.append("activated")
                return {"changed": True, "status": "activated"}

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli.ReleaseStore, "deploy", side_effect=deploy) as activate,
                patch.object(
                    operations_cli, "_systemctl_show",
                    side_effect=OperationalError(
                        "systemd_status_failed",
                        "system service state could not be queried",
                    ),
                ),
                patch.object(operations_cli, "_print") as output,
                self.assertRaisesRegex(OperationalError, "could not be queried"),
            ):
                operations_cli.command_deploy(arguments)
            self.assertEqual(activations, [])
            activate.assert_not_called()
            output.assert_not_called()

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli.ReleaseStore, "deploy", side_effect=deploy),
                patch.object(
                    operations_cli, "_systemctl_show", return_value={"load": "loaded"},
                ),
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_deploy(arguments)
            self.assertEqual(activations, ["activated"])
            self.assertTrue(
                output.call_args.args[0]["release_service_apply_required"]
            )

    def test_canonical_mutations_share_one_fail_fast_operations_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            deploy_arguments = SimpleNamespace(
                state_root=state, config=state / "runtime.env",
            )
            service_arguments = SimpleNamespace(state_root=state, restart=False)
            owner = ReleaseStore(state)
            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "_systemctl_show") as systemctl_show,
                patch.object(operations_cli, "_sudo") as sudo,
                patch.object(operations_cli.ReleaseStore, "deploy") as deploy,
            ):
                for operation, arguments in (
                    (operations_cli.command_deploy, deploy_arguments),
                    (operations_cli.command_install_service, service_arguments),
                    (operations_cli.command_rollback, service_arguments),
                ):
                    with (
                        self.subTest(operation=operation.__name__),
                        owner.locked(),
                        self.assertRaisesRegex(
                            OperationalError, "already in progress",
                        ),
                    ):
                        operation(arguments)
            systemctl_show.assert_not_called()
            sudo.assert_not_called()
            deploy.assert_not_called()
            with ReleaseStore(state).locked():
                pass

    def test_canonical_rollback_fails_closed_when_systemd_state_query_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            arguments = SimpleNamespace(state_root=state)
            failures = (
                OSError("systemctl unavailable"),
                subprocess.TimeoutExpired(["systemctl", "show"], 10),
                subprocess.CompletedProcess(
                    ["systemctl", "show"], 1,
                    "LoadState=not-found\n", "state query failed",
                ),
                subprocess.CompletedProcess(
                    ["systemctl", "show"], 0,
                    "ActiveState=inactive\n", "",
                ),
            )

            for failure in failures:
                run_result = (
                    {"side_effect": failure}
                    if isinstance(failure, BaseException)
                    else {"return_value": failure}
                )
                with (
                    self.subTest(failure=type(failure).__name__),
                    patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                    patch.object(operations_cli.subprocess, "run", **run_result),
                    patch.object(operations_cli, "_sudo") as sudo,
                    self.assertRaisesRegex(
                        OperationalError,
                        "state could not be queried|incomplete state",
                    ),
                ):
                    operations_cli.command_rollback(arguments)
                self.assertEqual((state / "current").resolve(), current.resolve())
                self.assertEqual((state / "previous").resolve(), previous.resolve())
                sudo.assert_not_called()

    def test_canonical_rollback_accepts_confirmed_absent_systemd_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            arguments = SimpleNamespace(state_root=state)
            systemd_state = "\n".join((
                "LoadState=not-found",
                "ActiveState=inactive",
                "SubState=dead",
                "Result=success",
                "NRestarts=0",
                "ExecMainStatus=0",
            ))

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "SYSTEM_UNIT_PATH", root / "absent.service"),
                patch.object(
                    operations_cli.subprocess, "run",
                    return_value=subprocess.CompletedProcess(
                        ["systemctl", "show"], 0, systemd_state, "",
                    ),
                ),
                patch(
                    "voice_agent_v2.operations.validate_release",
                    side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
                ),
                patch.object(operations_cli, "_sudo") as sudo,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_rollback(arguments)

            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())
            self.assertFalse(output.call_args.args[0]["service_restarted"])
            sudo.assert_not_called()

    def test_installed_service_rollback_reloads_before_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            unit_bytes = (ROOT / "ops/systemd/voice-agent-v2.service").read_bytes()
            for release in (current, previous):
                unit = release / "ops/systemd/voice-agent-v2.service"
                unit.parent.mkdir(parents=True)
                unit.write_bytes(unit_bytes)
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            installed = root / "voice-agent-v2.service"
            installed.write_bytes(unit_bytes)
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "SYSTEM_UNIT_PATH", installed),
                patch.object(
                    operations_cli, "_systemctl_show",
                    return_value={"load": "loaded", "active": "active"},
                ),
                patch(
                    "voice_agent_v2.operations.validate_release",
                    side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
                ),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                patch.object(operations_cli, "_wait_for_runtime_release"),
                patch.object(operations_cli, "_print"),
            ):
                operations_cli.command_rollback(SimpleNamespace(state_root=state))
            reload = calls.index(("systemctl", "daemon-reload"))
            reset = calls.index(
                ("systemctl", "reset-failed", operations_cli.SERVICE_NAME)
            )
            restart = calls.index(
                ("systemctl", "restart", operations_cli.SERVICE_NAME)
            )
            self.assertLess(reload, reset)
            self.assertEqual(reset + 1, restart)

    def test_installed_service_rollback_rejects_a_different_prior_unit_before_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            for release in (current, previous):
                (release / "ops/systemd").mkdir(parents=True)
            installed_bytes = (
                ROOT / "ops/systemd/voice-agent-v2.service"
            ).read_bytes()
            (current / "ops/systemd/voice-agent-v2.service").write_bytes(
                installed_bytes
            )
            (previous / "ops/systemd/voice-agent-v2.service").write_bytes(
                installed_bytes + b"\n"
            )
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            installed_unit = root / "voice-agent-v2.service"
            installed_unit.write_bytes(installed_bytes)
            arguments = SimpleNamespace(state_root=state)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "SYSTEM_UNIT_PATH", installed_unit),
                patch.object(
                    operations_cli, "_systemctl_show", return_value={"load": "loaded"},
                ),
                patch(
                    "voice_agent_v2.operations.validate_release",
                    side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
                ),
                patch.object(operations_cli, "_sudo") as sudo,
                patch.object(operations_cli, "_validate_effective_systemd_service"),
                self.assertRaisesRegex(OperationalError, "differs from the installed unit"),
            ):
                operations_cli.command_rollback(arguments)

            self.assertEqual((state / "current").resolve(), current.resolve())
            self.assertEqual((state / "previous").resolve(), previous.resolve())
            self.assertEqual(installed_unit.read_bytes(), installed_bytes)
            sudo.assert_not_called()

    def test_disposable_status_does_not_report_the_canonical_service_or_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "disposable-state"
            current = state / "releases" / ("1" * 24)
            arguments = SimpleNamespace(state_root=state)
            with (
                patch.object(operations_cli.ReleaseStore, "current", return_value=current),
                patch.object(operations_cli, "validate_release", return_value={
                    "release_id": current.name,
                    "build_id": "a" * 40,
                    "provider_mode": "local",
                }),
                patch.object(operations_cli, "_systemctl_show") as systemctl_show,
                patch.object(operations_cli, "_runtime_status") as runtime_status,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_status(arguments)

            report = output.call_args.args[0]
            self.assertEqual(report["compatibility"], "compatible")
            self.assertFalse(report["service"]["applicable"])
            self.assertEqual(report["service"]["active"], "not-applicable")
            self.assertFalse(report["runtime"]["applicable"])
            self.assertEqual(
                report["runtime"]["overall_readiness"], "not-applicable",
            )
            systemctl_show.assert_not_called()
            runtime_status.assert_not_called()


class ConfigurationAndArtifactTests(unittest.TestCase):
    def test_config_is_mode_guarded_without_persistable_secret_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            first = configuration_values("a" * 16)
            write_configuration(path, first)
            first_report = validate_server_configuration(parse_server_configuration(path))
            second = configuration_values("b" * 32)
            write_configuration(path, second)
            second_report = validate_server_configuration(parse_server_configuration(path))
            self.assertEqual(first_report["public_fingerprint"], second_report["public_fingerprint"])
            self.assertNotIn("configuration_revision", first_report)
            self.assertNotIn("configuration_revision", second_report)
            serialized = json.dumps(first_report)
            self.assertNotIn(first["LIVEKIT_API_SECRET"], serialized)
            self.assertNotIn(second["LIVEKIT_API_SECRET"], json.dumps(second_report))
            link = Path(temporary) / "runtime-link.env"
            link.symlink_to(path)
            with self.assertRaisesRegex(OperationalError, "unavailable"):
                parse_server_configuration(link)
            path.chmod(0o644)
            with self.assertRaisesRegex(OperationalError, "ownership or mode"):
                parse_server_configuration(path)

    def test_operational_capture_requires_the_systemd_runtime_boundary(self) -> None:
        values = configuration_values()
        values.update({
            "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
            "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(
                SYSTEMD_RUNTIME_ROOT / "private-captures"
            ),
        })
        report = validate_server_configuration(values)
        self.assertEqual(report["diagnostic_capture_ttl_seconds"], 900)
        for invalid in (
            f"/run/user/{os.geteuid()}/voice-agent-v2/private-captures",
            "/run/voice-agent-v2/../other/private-captures",
        ):
            values["VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT"] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                OperationalError, "capture root is invalid",
            ):
                validate_server_configuration(values)

    def test_non_loopback_public_urls_require_tls(self) -> None:
        for name, value in (
            ("LIVEKIT_PUBLIC_URL", f"ws://{HOSTNAME}:7880"),
            ("SLICE6_APP_PUBLIC_URL", f"http://{HOSTNAME}:8000"),
        ):
            values = configuration_values()
            values[name] = value
            with self.subTest(name=name), self.assertRaisesRegex(
                OperationalError, "must use TLS",
            ):
                validate_server_configuration(values)

    def test_unknown_provider_configuration_is_rejected_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            values = configuration_values()
            values["LITELLM_BASE_URL"] = "https://provider.invalid"
            write_configuration(path, values)
            with self.assertRaisesRegex(OperationalError, "unsupported name"):
                parse_server_configuration(path)

    def test_artifact_manifest_verifies_behavior_and_rejects_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            artifact = home / "artifact.bin"
            artifact.write_bytes(b"pinned artifact")
            manifest = {
                "artifacts": [{
                    "name": "fixture",
                    "path": "{home}/artifact.bin",
                    "size_bytes": artifact.stat().st_size,
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                }]
            }
            self.assertEqual(verify_artifacts(manifest, home=home), 1)
            artifact.write_bytes(b"changed artifact")
            with self.assertRaisesRegex(OperationalError, "size/type|checksum"):
                verify_artifacts(manifest, home=home)

    def test_disk_pressure_refuses_without_deleting_cache_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            retained = cache / "retained.bin"
            retained.write_bytes(b"retained")
            manifest = {
                "disk": {
                    "minimum_free_bytes": 1,
                    "cache_roots": [{
                        "name": "fixture", "path": str(cache), "maximum_bytes": 1,
                    }],
                }
            }
            with self.assertRaisesRegex(OperationalError, "non-destructive bound"):
                verify_disk_policy(manifest, home=root, filesystem_path=root)
            self.assertEqual(retained.read_bytes(), b"retained")


class ReleaseAndRollbackTests(unittest.TestCase):
    def test_canonical_release_store_rejects_symlinked_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            state = root / "voice-agent-v2"
            state.symlink_to(outside, target_is_directory=True)
            with (
                patch.object(operations, "DEFAULT_STATE_ROOT", state),
                self.assertRaisesRegex(OperationalError, "cannot contain symlinks"),
            ):
                ReleaseStore(state)
            self.assertEqual(list(outside.iterdir()), [])

    def test_canonical_deploy_rejects_config_outside_service_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            config = root / "runtime.env"
            write_configuration(config, configuration_values())
            with (
                patch.object(operations, "DEFAULT_STATE_ROOT", state),
                self.assertRaisesRegex(OperationalError, "service user home"),
            ):
                ReleaseStore(state).deploy(source_root=ROOT, config_path=config)
            self.assertFalse((state / "current").exists())

    def test_deploy_cli_rejects_a_selected_private_config_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "runtime.env"
            link = root / "runtime-link.env"
            state = root / "state"
            write_configuration(config, configuration_values())
            link.symlink_to(config)

            def git(_source: Path, *arguments: str) -> str:
                if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
                    return ""
                if arguments == ("rev-parse", "HEAD"):
                    return "a" * 40
                if arguments == ("rev-parse", f"{'a' * 40}^{{tree}}"):
                    return "b" * 40
                raise AssertionError(f"unexpected git call: {arguments}")

            def validate_selected_config(**keywords: object) -> ValidationReport:
                parse_server_configuration(Path(str(keywords["config_path"])))
                raise AssertionError("deploy followed the selected configuration symlink")

            stderr = StringIO()
            with (
                patch.object(
                    sys, "argv", [
                        "voice-agent-ops", "deploy", "--config", str(link),
                        "--state-root", str(state),
                    ],
                ),
                patch("voice_agent_v2.operations._run_git", side_effect=git),
                patch(
                    "voice_agent_v2.operations.validate_host",
                    side_effect=validate_selected_config,
                ),
                redirect_stderr(stderr),
            ):
                status = operations_cli.main()

            self.assertEqual(status, 2)
            self.assertIn("configuration_unavailable", stderr.getvalue())
            self.assertFalse((state / "current").exists())

    def test_deploy_rejects_a_selected_private_config_tracked_by_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            config = source / "private.env"
            write_configuration(config, configuration_values("tracked-secret-value"))
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Release Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "release@test.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(source), "add", "private.env"], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-qm", "tracked config"],
                check=True,
            )
            with self.assertRaisesRegex(OperationalError, "configuration is tracked"):
                ReleaseStore(root / "state").deploy(
                    source_root=source, config_path=config,
                )
            hardlink = root / "private-hardlink.env"
            os.link(config, hardlink)
            with self.assertRaisesRegex(OperationalError, "configuration is tracked"):
                ReleaseStore(root / "hardlink-state").deploy(
                    source_root=source, config_path=hardlink,
                )
            alias = root / "source-alias"
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(OperationalError, "configuration is unavailable"):
                ReleaseStore(root / "alias-state").deploy(
                    source_root=source, config_path=alias / "private.env",
                )
            self.assertFalse((root / "state" / "current").exists())
            self.assertFalse((root / "hardlink-state" / "current").exists())
            self.assertFalse((root / "alias-state" / "current").exists())

    @staticmethod
    def _complete_inventory(root: Path) -> bytes:
        records: dict[str, dict[str, object]] = {}
        paths = [root, *root.rglob("*")]
        for path in sorted(
            paths,
            key=lambda item: "." if item == root else item.relative_to(root).as_posix(),
        ):
            relative = "." if path == root else path.relative_to(root).as_posix()
            metadata = path.lstat()
            record: dict[str, object] = {
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "mtime_ns": metadata.st_mtime_ns,
            }
            if path.is_symlink():
                record.update(kind="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):
                record.update(kind="directory")
            elif stat.S_ISREG(metadata.st_mode):
                record.update(
                    kind="file", size=metadata.st_size, sha256=sha256_file(path),
                )
            else:
                record.update(kind="unsupported")
            records[relative] = record
        return _canonical_json(records)

    def test_recovered_release_tracer_twice_preserves_complete_inventory(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            release = root / "state" / "releases" / ("a" * 24)
            release.parent.mkdir(parents=True)
            shutil.copytree(
                ROOT,
                release,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".git", ".env.slice6", "node_modules", "__pycache__", "tmp",
                ),
            )
            current = root / "state" / "current"
            current.symlink_to(f"releases/{release.name}")
            mutable = root / "mutable"
            environment = dict(os.environ, VOICE_AGENT_MUTABLE_STATE_ROOT=str(mutable))
            direct = subprocess.run(
                [str(ROOT / "verify"), "--tracer-only"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(direct.returncode, 0, direct.stderr.decode(errors="replace"))
            before_inventory = self._complete_inventory(release)
            before_digest = release_tree_digest(release)
            recovered_outputs: list[bytes] = []
            for _run in range(2):
                recovered = subprocess.run(
                    [str(current / "verify"), "--tracer-only"],
                    cwd=current.resolve(),
                    env=environment,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    recovered.returncode, 0, recovered.stderr.decode(errors="replace"),
                )
                recovered_outputs.append(recovered.stdout)
                self.assertEqual(self._complete_inventory(release), before_inventory)
                self.assertEqual(release_tree_digest(release), before_digest)
            self.assertEqual(recovered_outputs, [direct.stdout, direct.stdout])

    def test_release_digest_covers_empty_directories_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_bytes(b"payload")
            payload.chmod(0o600)
            baseline = release_tree_digest(root)
            identity_inputs = {
                "commit": "a" * 40,
                "tree": "b" * 40,
                "manifest": "c" * 64,
                "configuration": "d" * 64,
                "configuration_locator": "e" * 64,
            }
            baseline_identity = _operational_release_id(
                **identity_inputs, release_tree=baseline,
            )
            (root / "empty-runtime-write").mkdir()
            changed = release_tree_digest(root)
            self.assertNotEqual(changed, baseline)
            self.assertNotEqual(
                _operational_release_id(**identity_inputs, release_tree=changed),
                baseline_identity,
            )
            (root / "empty-runtime-write").rmdir()
            payload.chmod(0o644)
            self.assertNotEqual(release_tree_digest(root), baseline)

    def test_deploying_same_release_is_an_observable_no_op(self) -> None:
        manifest_path = ROOT / DEFAULT_MANIFEST_RELATIVE
        commit = "a" * 40
        tree = "b" * 40
        fingerprint = "c" * 64
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            state = Path(temporary) / "state"
            config = Path(temporary) / "runtime.env"
            write_configuration(config, configuration_values())
            manifest_digest = sha256_file(manifest_path)
            locator_digest = _sha256_bytes(str(config.resolve()).encode("utf-8"))
            release_id = _sha256_bytes(_canonical_json({
                "commit": commit,
                "tree": tree,
                "manifest": manifest_digest,
                "configuration": fingerprint,
                "configuration_locator": locator_digest,
            }))[:24]
            target = state / "releases" / release_id
            target.mkdir(parents=True)
            (state / "current").symlink_to(f"releases/{release_id}")
            report = ValidationReport(
                None, None, "local", 1, {}, 10**12, fingerprint,
            )
            document = {
                "build_id": commit,
                "release_id": release_id,
                "source_tree": tree,
                "operations_manifest_sha256": manifest_digest,
                "configuration_path": str(config.resolve()),
                "configuration_locator_sha256": locator_digest,
                "configuration_fingerprint": fingerprint,
            }
            git_values = iter(["", commit, tree])
            with (
                patch("voice_agent_v2.operations._run_git", side_effect=lambda *_args: next(git_values)),
                patch("voice_agent_v2.operations._run_git_bytes", return_value=b""),
                patch("voice_agent_v2.operations.validate_host", return_value=report),
                patch("voice_agent_v2.operations.validate_release", return_value=document),
            ):
                result = ReleaseStore(state).deploy(source_root=ROOT, config_path=config)
            self.assertEqual(result["status"], "no-op")
            self.assertFalse(result["changed"])
            self.assertEqual((state / "current").resolve(), target.resolve())

    def test_release_archives_and_builds_the_captured_commit_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "config").mkdir()
            shutil.copy2(
                ROOT / DEFAULT_MANIFEST_RELATIVE,
                source / DEFAULT_MANIFEST_RELATIVE,
            )
            web = source / "web"
            (web / "src").mkdir(parents=True)
            (web / "src" / "build-marker.txt").write_text("committed", encoding="utf-8")
            (web / "package.json").write_text(
                '{"scripts":{"build":"fixture"}}\n', encoding="utf-8",
            )
            (web / "package-lock.json").write_text(
                '{"name":"fixture","lockfileVersion":3,"requires":true,'
                '"packages":{"":{"name":"fixture"}}}\n', encoding="utf-8",
            )
            (source / ".gitignore").write_text("web/node_modules/\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Release Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "release@test.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-qm", "captured"], check=True,
            )
            captured_commit = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            (web / "node_modules").mkdir()
            (web / "node_modules" / "poisoned-tree").write_text(
                "ignored dependency state", encoding="utf-8",
            )

            tools = root / "tools"
            tools.mkdir()
            npm = tools / "npm"
            npm.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, sys\n"
                "if sys.argv[1] == 'ci':\n"
                "    pathlib.Path('node_modules').mkdir()\n"
                "    pathlib.Path('node_modules/exact-lock-tree').write_text('ready')\n"
                "    raise SystemExit(0)\n"
                "if not pathlib.Path('node_modules/exact-lock-tree').is_file():\n"
                "    raise SystemExit(9)\n"
                "if pathlib.Path('node_modules/poisoned-tree').exists():\n"
                "    raise SystemExit(10)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--outDir') + 1])\n"
                "(out / 'assets').mkdir(parents=True, exist_ok=True)\n"
                "(out / 'index.html').write_text('<main></main>')\n"
                "marker = (pathlib.Path.cwd() / 'src/build-marker.txt').read_text()\n"
                "(out / 'assets/app.js').write_text(os.environ['VITE_APP_VERSION'] + marker)\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            config = root / "runtime.env"
            write_configuration(config, configuration_values())
            state = root / "state"
            report = ValidationReport(
                None, None, "local", 1, {}, 10**12, "c" * 64,
            )
            raced = False

            def run_git_with_head_change(path: Path, *arguments: str) -> str:
                nonlocal raced
                result = _run_git(path, *arguments)
                if not raced and arguments == ("rev-parse", f"{captured_commit}^{{tree}}"):
                    raced = True
                    (web / "src" / "build-marker.txt").write_text("raced", encoding="utf-8")
                    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
                    subprocess.run(
                        ["git", "-C", str(source), "commit", "-qm", "concurrent"],
                        check=True,
                    )
                return result

            environment = dict(os.environ)
            environment["PATH"] = f"{tools}:{environment['PATH']}"
            failed_state = root / "failed-state"
            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "voice_agent_v2.operations.validate_host", return_value=report,
                ),
                patch(
                    "voice_agent_v2.operations.validate_release",
                    side_effect=OperationalError(
                        "release_incompatible", "final validation changed",
                    ),
                ),
                self.assertRaisesRegex(OperationalError, "final validation changed"),
            ):
                ReleaseStore(failed_state).deploy(
                    source_root=source, config_path=config,
                )
            self.assertEqual(list((failed_state / "releases").iterdir()), [])
            self.assertFalse((failed_state / "stage-transaction.json").exists())

            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "voice_agent_v2.operations._run_git",
                    side_effect=run_git_with_head_change,
                ),
                patch("voice_agent_v2.operations.validate_host", return_value=report),
                patch("voice_agent_v2.operations.validate_release", return_value={}),
            ):
                result = ReleaseStore(state).deploy(
                    source_root=source, config_path=config,
                )
            release = (state / "current").resolve()
            release_document = json.loads(
                (release / "release.json").read_text(encoding="utf-8")
            )
            built_asset = next((release / "web" / "dist" / "assets").glob("*.js"))
            self.assertEqual(
                release_document["schema_version"],
                "voice-agent.operational-release.v2",
            )
            self.assertNotIn("configuration_revision", release_document)
            self.assertNotIn(
                configuration_values()["LIVEKIT_API_SECRET"],
                json.dumps(release_document),
            )
            self.assertTrue(raced)
            self.assertEqual(result["build_id"], captured_commit)
            self.assertIn(captured_commit, built_asset.read_text(encoding="utf-8"))
            self.assertIn("committed", built_asset.read_text(encoding="utf-8"))
            self.assertNotIn("raced", built_asset.read_text(encoding="utf-8"))

    def test_execute_uses_the_exact_configuration_snapshot_that_was_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            release = state / "releases" / ("7" * 24)
            release.mkdir(parents=True)
            (state / "current").symlink_to(f"releases/{release.name}")
            values = configuration_values("snapshot-secret-value")
            document = {"build_id": "a" * 40, "release_id": release.name}
            captured: dict[str, object] = {}

            def execute(path: str, arguments: list[str], environment: dict[str, str]) -> None:
                captured.update(path=path, arguments=arguments, environment=environment)
                raise RuntimeError("exec boundary reached")

            with (
                patch(
                    "voice_agent_v2.operations._validate_release_snapshot",
                    return_value=(document, values),
                ),
                patch(
                    "voice_agent_v2.operations.parse_server_configuration",
                    side_effect=AssertionError("configuration must not be reread"),
                ),
                patch(
                    "voice_agent_v2.operations._prepare_mutable_runtime_directory",
                    side_effect=lambda path: path,
                ) as prepare_runtime,
                patch("voice_agent_v2.operations.os.chdir"),
                patch("voice_agent_v2.operations.os.execve", side_effect=execute),
            ):
                with self.assertRaisesRegex(RuntimeError, "exec boundary reached"):
                    execute_release(state)
            environment = captured["environment"]
            self.assertIsInstance(environment, dict)
            self.assertEqual(environment["LIVEKIT_API_SECRET"], "snapshot-secret-value")
            prepare_runtime.assert_called_once_with(SYSTEMD_RUNTIME_ROOT / "pycache")

    def test_rollback_rejects_incompatible_previous_without_moving_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")

            def validation(path: Path, **_kwargs: object) -> dict[str, object]:
                if path.resolve() == previous.resolve():
                    raise OperationalError("release_incompatible", "fixture incompatible")
                return {"build_id": "a" * 40}

            with patch("voice_agent_v2.operations.validate_release", side_effect=validation):
                with self.assertRaisesRegex(OperationalError, "fixture incompatible"):
                    ReleaseStore(state).rollback()
            self.assertEqual((state / "current").resolve(), current.resolve())
            self.assertEqual((state / "previous").resolve(), previous.resolve())

    def test_rollback_recovers_to_verified_previous_when_current_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("5" * 24)
            previous = releases / ("6" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")

            def validation(path: Path, **_kwargs: object) -> dict[str, object]:
                if path.resolve() == current.resolve():
                    raise OperationalError("release_incompatible", "current is corrupt")
                return {"build_id": "6" * 40}

            with patch("voice_agent_v2.operations.validate_release", side_effect=validation):
                result = ReleaseStore(state).rollback()
            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual(result["replaced_release_id"], current.name)
            self.assertIsNone(result["replaced_build_id"])
            self.assertFalse(result["replaced_release_compatible"])

    def test_rollback_recovers_when_current_manifest_has_an_oversized_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("7" * 24)
            previous = releases / ("8" * 24)
            current.mkdir(parents=True, mode=0o700)
            previous.mkdir(mode=0o700)
            current.chmod(0o700)
            manifest = current / "release.json"
            manifest.write_text(
                '{"invalid":' + "9" * 5000 + "}", encoding="utf-8",
            )
            manifest.chmod(0o600)
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")

            def validation(path: Path, **kwargs: object) -> dict[str, object]:
                if path.resolve() == previous.resolve():
                    return {"build_id": "8" * 40}
                return validate_release(path, **kwargs)

            with patch(
                "voice_agent_v2.operations.validate_release", side_effect=validation,
            ):
                result = ReleaseStore(state).rollback()

            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())
            self.assertFalse(result["replaced_release_compatible"])
            self.assertIsNone(result["replaced_build_id"])

    def test_interrupted_rollback_link_swap_recovers_from_persisted_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("9" * 24)
            previous = releases / ("a" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            actual_atomic_symlink = operations._atomic_symlink
            calls = 0

            def interrupt_after_first_link(path: Path, target: str) -> None:
                nonlocal calls
                calls += 1
                actual_atomic_symlink(path, target)
                if calls == 1:
                    raise OSError("simulated process interruption")

            with (
                patch(
                    "voice_agent_v2.operations.validate_release",
                    side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
                ),
                patch(
                    "voice_agent_v2.operations._atomic_symlink",
                    side_effect=interrupt_after_first_link,
                ),
                self.assertRaisesRegex(OSError, "simulated process interruption"),
            ):
                ReleaseStore(state).rollback()

            self.assertTrue((state / "link-transaction.json").is_file())
            self.assertEqual((state / "current").resolve(), current.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())
            with ReleaseStore(state).locked():
                pass
            self.assertFalse((state / "link-transaction.json").exists())
            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())

    def test_locked_store_recovers_only_its_recorded_incomplete_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            store = ReleaseStore(state)
            store._initialize()
            owned = store.releases / (".stage-" + "a" * 32)
            unrelated = store.releases / (".stage-" + "b" * 32)
            owned.mkdir(mode=0o700)
            unrelated.mkdir(mode=0o700)
            (owned / "partial").write_bytes(b"partial")
            (unrelated / "retained").write_bytes(b"retained")
            operations._atomic_json(store.stage_transaction_path, {
                "schema_version": "voice-agent.release-stage.v1",
                "stage": owned.name,
            })

            with store.locked():
                pass

            self.assertFalse(owned.exists())
            self.assertTrue((unrelated / "retained").is_file())
            self.assertFalse(store.stage_transaction_path.exists())

    def test_locked_store_recovers_promoted_unvalidated_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            store = ReleaseStore(state)
            store._initialize()
            stage_name = ".stage-" + "a" * 32
            target = store.releases / ("c" * 24)
            retained = store.releases / ("d" * 24)
            target.mkdir(mode=0o700)
            retained.mkdir(mode=0o700)
            (target / "unvalidated").write_bytes(b"partial")
            (retained / "release.json").write_bytes(b"retained")
            operations._atomic_json(store.stage_transaction_path, {
                "schema_version": "voice-agent.release-stage.v2",
                "stage": stage_name,
                "target": target.name,
            })

            with store.locked():
                pass

            self.assertFalse(target.exists())
            self.assertTrue((retained / "release.json").is_file())
            self.assertFalse(store.stage_transaction_path.exists())

    def test_recovered_activation_removes_a_stale_previous_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("b" * 24)
            stale = releases / ("c" * 24)
            current.mkdir(parents=True)
            stale.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{stale.name}")
            operations._atomic_json(state / "link-transaction.json", {
                "schema_version": "voice-agent.release-links.v1",
                "current": f"releases/{current.name}",
                "previous": None,
            })

            with ReleaseStore(state).locked():
                pass

            self.assertEqual((state / "current").resolve(), current.resolve())
            self.assertFalse((state / "previous").exists())
            self.assertFalse((state / "link-transaction.json").exists())

    def test_verified_rollback_swaps_only_current_and_previous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("3" * 24)
            previous = releases / ("4" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            with patch(
                "voice_agent_v2.operations.validate_release",
                side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
            ):
                result = ReleaseStore(state).rollback()
            self.assertEqual(result["release_id"], previous.name)
            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())


class LifecycleAndSustainedTests(unittest.TestCase):
    def test_public_status_fixture_composes_versioned_health_without_fallback(self) -> None:
        report = json.loads(
            (ROOT / "contracts/fixtures/public-operational-status.v1.json").read_text()
        )
        status_schema = json.loads(
            (ROOT / "contracts/public-operational-status.v1.schema.json").read_text()
        )
        health_schema = json.loads(
            (ROOT / "contracts/health-readiness.v1.schema.json").read_text()
        )
        validate_schema(report, status_schema)
        validate_schema(report["health"], health_schema)
        missing_component = json.loads(json.dumps(report))
        missing_component["health"]["components"].pop()
        with self.assertRaises(AssertionError):
            validate_schema(missing_component, status_schema)
        unknown_health_field = json.loads(json.dumps(report))
        unknown_health_field["health"]["unexpected"] = True
        with self.assertRaises(AssertionError):
            validate_schema(unknown_health_field, status_schema)
        self.assertFalse(report["external_provider_supervised"])
        self.assertFalse(report["automatic_fallback"])

    def test_sustained_acceptance_uses_twenty_complete_content_free_turns(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        turns = [
            {
                "outcome": "completed",
                "total_turn_ms": 1000 + index,
                "cancellation_latency_ms": 100 if index in {5, 15} else None,
                "process_rss_mib": 1000 + index,
                "gpu_vram_used_mib": 3000 + index,
            }
            for index in range(20)
        ]
        report = evaluate_sustained_run(
            manifest, turns=turns, avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["turn_count"], 20)
        self.assertNotIn("transcript", json.dumps(report))
        turns[-1] = dict(turns[-1], outcome="failed")
        with self.assertRaisesRegex(OperationalError, "turn_success"):
            evaluate_sustained_run(
                manifest, turns=turns, avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
            )

    def test_sustained_report_rejects_oversized_json_integer_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "sustained.json"
            evidence.write_text(
                '{"turns":[{"total_turn_ms":' + "9" * 5000
                + '}],"avatar":{}}',
                encoding="utf-8",
            )
            stderr = StringIO()
            with (
                patch.object(
                    sys, "argv",
                    ["voice-agent-ops", "sustained-report", "--evidence", str(evidence)],
                ),
                redirect_stderr(stderr),
            ):
                status = operations_cli.main()
            self.assertEqual(status, 2)
            self.assertIn("sustained_report_invalid", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_sustained_acceptance_rejects_nonfinite_boolean_and_negative_values(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        baseline = [{
            "outcome": "completed",
            "total_turn_ms": 1000,
            "cancellation_latency_ms": None,
            "process_rss_mib": 1000,
            "gpu_vram_used_mib": 3000,
        } for _index in range(20)]
        for field, value in (
            ("total_turn_ms", True),
            ("process_rss_mib", -1),
            ("gpu_vram_used_mib", float("inf")),
            ("cancellation_latency_ms", float("nan")),
            ("total_turn_ms", 10**400),
        ):
            turns = [dict(turn) for turn in baseline]
            turns[0][field] = value
            with self.assertRaisesRegex(OperationalError, "finite non-negative"):
                evaluate_sustained_run(
                    manifest,
                    turns=turns,
                    avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
                )
        for avatar in (
            {"healthy_frame_ratio": 1.1, "fps": 60.0},
            {"healthy_frame_ratio": 0.999, "fps": float("inf")},
        ):
            with self.assertRaisesRegex(OperationalError, "finite non-negative"):
                evaluate_sustained_run(manifest, turns=baseline, avatar=avatar)

    def test_settings_reject_forged_operational_build_identity(self) -> None:
        values = configuration_values()
        values["VOICE_AGENT_BUILD_ID"] = "not-a-commit"
        with self.assertRaisesRegex(ValueError, "build/release identity"):
            Slice6Settings.from_environment(values, project_root=ROOT)


if __name__ == "__main__":
    unittest.main()

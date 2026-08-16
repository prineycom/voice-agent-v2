"""Command-line interface for the bounded single-host operations contract."""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

from .operations import (
    CONFIGURATION_EXIT_STATUS,
    DEFAULT_MANIFEST_RELATIVE,
    DEFAULT_STATE_ROOT,
    OperationalError,
    ReleaseStore,
    evaluate_sustained_run,
    execute_release,
    load_operations_manifest,
    validate_host,
    validate_release,
)


ROOT = Path(__file__).resolve().parents[2]
SERVICE_NAME = "voice-agent-v2.service"
SYSTEM_UNIT_PATH = Path("/etc/systemd/system") / SERVICE_NAME
SYSTEMD_JOB_TIMEOUT_SECONDS = 390
SYSTEM_COMMAND_TIMEOUT_SECONDS = 30
RUNTIME_STATUS_TIMEOUT_SECONDS = 1.0
SYSTEMD_UNIT_CONTRACT = {
    "Unit": {
        "Description": ["Voice Agent v2 loopback-local single-host stack"],
        "Documentation": [
            "https://github.com/prineycom/voice-agent-v2/blob/main/docs/architecture.md"
        ],
        "After": ["local-fs.target"],
        "StartLimitIntervalSec": ["infinity"],
        "StartLimitBurst": ["2"],
    },
    "Service": {
        "Type": ["notify"],
        "NotifyAccess": ["main"],
        "User": ["priney"],
        "Group": ["priney"],
        "WorkingDirectory": ["%h/.local/share/voice-agent-v2/current"],
        "Environment": [
            "HOME=%h",
            "XDG_RUNTIME_DIR=/run/voice-agent-v2",
            "PYTHONUNBUFFERED=1",
            "PYTHONPYCACHEPREFIX=/run/voice-agent-v2/pycache",
        ],
        "RuntimeDirectory": ["voice-agent-v2"],
        "RuntimeDirectoryMode": ["0700"],
        "RuntimeDirectoryPreserve": ["no"],
        "ExecStart": [
            "%h/.local/share/voice-agent-v2/current/voice-agent-ops run"
        ],
        "Restart": ["on-failure"],
        "RestartSec": ["5s"],
        "RestartPreventExitStatus": ["2"],
        "TimeoutStartSec": ["300s"],
        "TimeoutStopSec": ["75s"],
        "KillMode": ["mixed"],
        "KillSignal": ["SIGTERM"],
        "FinalKillSignal": ["SIGKILL"],
        "UMask": ["0077"],
        "NoNewPrivileges": ["yes"],
        "PrivateTmp": ["yes"],
        "ProtectSystem": ["strict"],
        "ProtectHome": ["read-only"],
        "ReadWritePaths": ["%h/.cache/voice-agent-v2"],
        "ProtectClock": ["yes"],
        "ProtectControlGroups": ["yes"],
        "ProtectKernelLogs": ["yes"],
        "ProtectKernelModules": ["yes"],
        "ProtectKernelTunables": ["yes"],
        "ProtectHostname": ["yes"],
        "ProtectProc": ["invisible"],
        "RestrictAddressFamilies": ["AF_UNIX AF_INET AF_INET6 AF_NETLINK"],
        "RestrictRealtime": ["yes"],
        "RestrictSUIDSGID": ["yes"],
        "LockPersonality": ["yes"],
        "MemoryDenyWriteExecute": ["no"],
        "TasksMax": ["256"],
        "LimitNOFILE": ["16384"],
    },
    "Install": {"WantedBy": ["multi-user.target"]},
}
EFFECTIVE_SYSTEMD_CONTRACT = {
    "FragmentPath": str(SYSTEM_UNIT_PATH),
    "DropInPaths": "",
    "User": "priney",
    "Group": "priney",
    "Type": "notify",
    "NotifyAccess": "main",
    "Restart": "on-failure",
    "RestartUSec": "5s",
    "TimeoutStartUSec": "5min",
    "TimeoutStopUSec": "1min 15s",
    "KillMode": "mixed",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "read-only",
}


def _state_root(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("state root must be absolute")
    return path


def _configuration(value: str) -> Path:
    path = Path(value).expanduser()
    return Path(os.path.abspath(path))


def _is_canonical_state_root(path: Path) -> bool:
    return Path(os.path.abspath(path.expanduser())) == Path(
        os.path.abspath(DEFAULT_STATE_ROOT.expanduser())
    )


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2))


def _sudo(*arguments: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    timeout = (
        SYSTEMD_JOB_TIMEOUT_SECONDS
        if len(arguments) >= 2
        and arguments[0] == "systemctl"
        and arguments[1] in {"start", "restart"}
        else SYSTEM_COMMAND_TIMEOUT_SECONDS
    )
    try:
        result = subprocess.run(
            ["sudo", "-n", *arguments], capture_output=True, text=True,
            timeout=timeout, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OperationalError("systemd_install_failed", "sudo -n system operation failed") from error
    if result.returncode not in allowed:
        raise OperationalError("systemd_install_failed", "sudo -n system operation failed")
    return result


def _normalized_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise OperationalError(
            "systemd_unit_incompatible", "release systemd unit is unreadable",
        ) from error
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        if current is None or "=" not in line:
            raise OperationalError(
                "systemd_unit_incompatible", "release systemd unit syntax is invalid",
            )
        name, value = line.split("=", 1)
        current.setdefault(name, []).append(value)
    return sections


def _validate_systemd_unit(path: Path) -> None:
    unit = _normalized_unit(path)
    if unit != SYSTEMD_UNIT_CONTRACT:
        raise OperationalError(
            "systemd_unit_incompatible",
            "release systemd lifecycle or sandbox policy is incompatible",
        )
    with tempfile.TemporaryDirectory(prefix="voice-agent-unit-") as temporary:
        disposable = Path(temporary) / SERVICE_NAME
        lines = path.read_text(encoding="utf-8").splitlines()
        disposable.write_text("\n".join(
            "WorkingDirectory=/" if line.startswith("WorkingDirectory=")
            else "ExecStart=/usr/bin/true" if line.startswith("ExecStart=")
            else line
            for line in lines
        ) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(
                ["systemd-analyze", "verify", str(disposable)],
                capture_output=True, text=True, timeout=15, check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OperationalError(
                "systemd_unit_incompatible", "systemd unit verification is unavailable",
            ) from error
        if result.returncode != 0 or len((result.stdout + result.stderr).encode()) > 64 * 1024:
            raise OperationalError(
                "systemd_unit_incompatible", "systemd rejected the release unit",
            )


def _validate_effective_systemd_service() -> None:
    expected = dict(EFFECTIVE_SYSTEMD_CONTRACT)
    expected["FragmentPath"] = str(SYSTEM_UNIT_PATH)
    properties = ",".join(expected)
    result = _sudo("systemctl", "show", SERVICE_NAME, f"--property={properties}")
    output = getattr(result, "stdout", "")
    if not isinstance(output, str) or len(output.encode("utf-8")) > 64 * 1024:
        raise OperationalError(
            "systemd_unit_incompatible", "effective systemd policy is unavailable",
        )
    observed = dict(
        line.split("=", 1) for line in output.splitlines() if "=" in line
    )
    if observed != expected:
        raise OperationalError(
            "systemd_unit_incompatible",
            "effective systemd lifecycle or sandbox policy is incompatible",
        )


def _start_service_with_recovery_allowance(action: str) -> None:
    if action not in {"start", "restart"}:
        raise ValueError("unsupported systemd activation action")
    _sudo("systemctl", "reset-failed", SERVICE_NAME)
    _sudo("systemctl", action, SERVICE_NAME)


def _systemctl_show() -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                "systemctl", "show", SERVICE_NAME,
                "--property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus",
            ],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OperationalError(
            "systemd_status_failed", "system service state could not be queried",
        ) from error
    if result.returncode != 0:
        raise OperationalError(
            "systemd_status_failed", "system service state could not be queried",
        )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
    required = {
        "LoadState", "ActiveState", "SubState", "Result", "NRestarts",
        "ExecMainStatus",
    }
    if not required.issubset(values):
        raise OperationalError(
            "systemd_status_failed", "system service returned incomplete state",
        )
    try:
        restart_count = int(values["NRestarts"] or "0")
        main_exit_status = int(values["ExecMainStatus"] or "0")
    except ValueError as error:
        raise OperationalError(
            "systemd_status_failed", "system service returned invalid state",
        ) from error
    return {
        "load": values["LoadState"],
        "active": values["ActiveState"],
        "substate": values["SubState"],
        "result": values["Result"],
        "restart_count": restart_count,
        "main_exit_status": main_exit_status,
    }


def command_validate(arguments: argparse.Namespace) -> None:
    report = validate_host(
        source_root=ROOT,
        config_path=arguments.config,
        state_root=arguments.state_root,
    )
    _print(report.as_dict())


def command_deploy(arguments: argparse.Namespace) -> None:
    store = ReleaseStore(arguments.state_root)
    with store.locked():
        canonical = _is_canonical_state_root(arguments.state_root)
        service = _systemctl_show() if canonical else {"load": "not-applicable"}
        result = store.deploy(source_root=ROOT, config_path=arguments.config)
        result["release_service_apply_required"] = bool(
            result.get("changed") is True and service.get("load") == "loaded"
        )
        _print(result)


def command_validate_deployment(arguments: argparse.Namespace) -> None:
    store = ReleaseStore(arguments.state_root)
    current = store.current()
    if current is None:
        raise OperationalError("deployment_unavailable", "no active operational release exists")
    document = validate_release(
        current, state_root=store.state_root, verify_host_state=True,
    )
    _print({
        "schema_version": "voice-agent.deployment-validation.v1",
        "status": "compatible",
        "release_id": document["release_id"],
        "build_id": document["build_id"],
        "provider_mode": document["provider_mode"],
        "external_provider_supervised": False,
        "automatic_fallback": False,
    })


def command_status(arguments: argparse.Namespace) -> None:
    store = ReleaseStore(arguments.state_root)
    current = store.current()
    release: dict[str, object] | None = None
    compatibility = "unconfigured"
    if current is not None:
        try:
            document = validate_release(
                current, state_root=store.state_root, verify_host_state=True,
            )
        except OperationalError:
            compatibility = "incompatible"
        else:
            compatibility = "compatible"
            release = {
                "release_id": document["release_id"],
                "build_id": document["build_id"],
                "provider_mode": document["provider_mode"],
            }
    canonical = _is_canonical_state_root(arguments.state_root)
    if canonical:
        service = {"applicable": True, **_systemctl_show()}
        public_status = _runtime_status() if service["active"] == "active" else None
        health = public_status.get("health") if isinstance(public_status, dict) else None
        runtime = {
            "applicable": True,
            "reachable": public_status is not None,
            "release_id": public_status.get("release_id") if public_status else None,
            "build_id": public_status.get("build_id") if public_status else None,
            "overall_readiness": health.get("overall_readiness") if isinstance(health, dict) else None,
            "accepting": public_status.get("accepting") if public_status else False,
        }
    else:
        service = {
            "applicable": False,
            "load": "not-applicable",
            "active": "not-applicable",
            "substate": "not-applicable",
            "result": "not-applicable",
            "restart_count": None,
            "main_exit_status": None,
        }
        runtime = {
            "applicable": False,
            "reachable": False,
            "release_id": None,
            "build_id": None,
            "overall_readiness": "not-applicable",
            "accepting": False,
        }
    _print({
        "schema_version": "voice-agent.operational-status.v1",
        "compatibility": compatibility,
        "release": release,
        "service": service,
        "runtime": runtime,
        "supervision": {
            "host_stack": "systemd-bounded-process-group",
            "avatar_host": "versioned-client-build-and-readiness",
            "selected_avatar_module": "mvp-eye-svg-v1",
            "cloud_provider": "external-readiness-only-inactive",
            "external_provider_supervised": False,
            "automatic_fallback": False,
        },
    })


def _runtime_status() -> dict[str, object] | None:
    connection = http.client.HTTPConnection(
        "127.0.0.1", 8000, timeout=RUNTIME_STATUS_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/api/status", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(64 * 1024 + 1)
        if response.status != 200 or len(body) > 64 * 1024:
            return None
        document = json.loads(body)
    except (
        OSError, TimeoutError, http.client.HTTPException, UnicodeError,
        json.JSONDecodeError,
    ):
        return None
    finally:
        connection.close()
    return document if isinstance(document, dict) else None


def _wait_for_runtime_release(release_id: str, *, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        document = _runtime_status()
        health = document.get("health") if isinstance(document, dict) else None
        if (
            document is not None
            and document.get("release_id") == release_id
            and isinstance(health, dict)
            and health.get("overall_readiness") == "ready"
            and document.get("accepting") is True
        ):
            return
        service = _systemctl_show()
        if service["load"] == "loaded" and service["active"] == "failed":
            break
        time.sleep(0.5)
    raise OperationalError(
        "systemd_install_failed", "system service did not reach exact-release readiness",
    )


def command_install_service(arguments: argparse.Namespace) -> None:
    if not _is_canonical_state_root(arguments.state_root):
        raise OperationalError("systemd_install_failed", "system service supports only the canonical state root")
    store = ReleaseStore(arguments.state_root)
    with store.locked():
        _install_service_locked(arguments, store)


def _install_service_locked(
    arguments: argparse.Namespace, store: ReleaseStore,
) -> None:
    current = store.current()
    if current is None:
        raise OperationalError("deployment_unavailable", "deploy a compatible release before installing systemd")
    validate_release(current, state_root=store.state_root, verify_host_state=True)
    unit = current / "ops/systemd/voice-agent-v2.service"
    if not unit.is_file():
        raise OperationalError("systemd_unit_incompatible", "release systemd unit is missing")
    _validate_systemd_unit(unit)
    try:
        installed_metadata = SYSTEM_UNIT_PATH.lstat()
    except FileNotFoundError:
        exists = False
    except OSError as error:
        raise OperationalError(
            "systemd_unit_incompatible", "installed systemd unit is unavailable",
        ) from error
    else:
        exists = True
        if not stat.S_ISREG(installed_metadata.st_mode):
            raise OperationalError(
                "systemd_unit_incompatible",
                "installed systemd unit must be an unmasked regular file",
            )
    identical = exists and _sudo(
        "cmp", "-s", str(unit), str(SYSTEM_UNIT_PATH), allowed=(0, 1),
    ).returncode == 0
    unit_changed = not identical
    enabled_before = _sudo(
        "systemctl", "is-enabled", SERVICE_NAME, allowed=(0, 1, 3, 4),
    ).returncode == 0
    active_before = _sudo(
        "systemctl", "is-active", SERVICE_NAME, allowed=(0, 3, 4),
    ).returncode == 0
    changed = unit_changed
    service_restarted = False
    with tempfile.TemporaryDirectory(prefix="voice-agent-unit-backup-") as temporary:
        backup = Path(temporary) / SERVICE_NAME
        if unit_changed and exists:
            _sudo(
                "cp", "--archive", "--no-dereference", "--",
                str(SYSTEM_UNIT_PATH), str(backup),
            )
        unit_attempted = False
        enable_attempted = False
        activation_attempted = False
        rollback_release = False
        restore_release_id = current.name
        try:
            if unit_changed:
                unit_attempted = True
                _sudo(
                    "install", "-o", "root", "-g", "root", "-m", "0644",
                    str(unit), str(SYSTEM_UNIT_PATH),
                )
            _sudo("systemctl", "daemon-reload")
            _validate_effective_systemd_service()
            running = _runtime_status() if active_before else None
            release_changed = running is None or running.get("release_id") != current.name
            if active_before and release_changed:
                running_release_id = (
                    running.get("release_id") if isinstance(running, dict) else None
                )
                previous = store.previous()
                if (
                    not isinstance(running_release_id, str)
                    or previous is None
                    or previous.name != running_release_id
                ):
                    raise OperationalError(
                        "systemd_install_failed",
                        "active service release cannot be restored after activation failure",
                    )
                validate_release(
                    previous, state_root=store.state_root, verify_host_state=True,
                )
                previous_unit = previous / "ops/systemd/voice-agent-v2.service"
                _validate_systemd_unit(previous_unit)
                installed_unit_before = backup if unit_changed else SYSTEM_UNIT_PATH
                if not exists or _sudo(
                    "cmp", "-s", str(previous_unit), str(installed_unit_before),
                    allowed=(0, 1),
                ).returncode != 0:
                    raise OperationalError(
                        "systemd_install_failed",
                        "active service unit cannot be restored after activation failure",
                    )
                rollback_release = True
                restore_release_id = running_release_id
            if not enabled_before:
                enable_attempted = True
                _sudo("systemctl", "enable", SERVICE_NAME)
                changed = True
            if arguments.restart or (
                active_before and (unit_changed or release_changed)
            ):
                activation_attempted = True
                _start_service_with_recovery_allowance("restart")
                changed = True
                service_restarted = True
            elif not active_before:
                activation_attempted = True
                _start_service_with_recovery_allowance("start")
                changed = True
            active = _sudo(
                "systemctl", "is-active", SERVICE_NAME, allowed=(0, 3, 4),
            ).returncode == 0
            if not active:
                raise OperationalError(
                    "systemd_install_failed", "system service did not reach active state",
                )
            _wait_for_runtime_release(current.name)
        except BaseException:
            try:
                if unit_attempted:
                    if exists:
                        _sudo("rm", "-f", "--", str(SYSTEM_UNIT_PATH))
                        _sudo(
                            "cp", "--archive", "--no-dereference", "--",
                            str(backup), str(SYSTEM_UNIT_PATH),
                        )
                    else:
                        _sudo("rm", "-f", "--", str(SYSTEM_UNIT_PATH))
                    _sudo("systemctl", "daemon-reload")
                if enable_attempted:
                    _sudo(
                        "systemctl", "enable" if enabled_before else "disable",
                        SERVICE_NAME,
                    )
                if activation_attempted:
                    if active_before:
                        if rollback_release:
                            rollback_result = store.rollback(
                                required_system_unit=SYSTEM_UNIT_PATH,
                            )
                            if rollback_result.get("release_id") != restore_release_id:
                                raise OperationalError(
                                    "systemd_install_failed",
                                    "prior runtime release could not be restored",
                                )
                        _start_service_with_recovery_allowance("restart")
                        _wait_for_runtime_release(restore_release_id)
                    else:
                        _sudo("systemctl", "stop", SERVICE_NAME)
            except BaseException as restore_error:
                raise OperationalError(
                    "systemd_install_failed",
                    "system service application failed and prior state could not be restored",
                ) from restore_error
            raise
    _print({
        "schema_version": "voice-agent.systemd-install-result.v1",
        "status": "installed",
        "changed": changed,
        "unit": SERVICE_NAME,
        "enabled": True,
        "active": True,
        "ready": True,
        "release_id": current.name,
        "restart_requested": arguments.restart,
        "service_restarted": service_restarted,
    })


def command_rollback(arguments: argparse.Namespace) -> None:
    store = ReleaseStore(arguments.state_root)
    with store.locked():
        canonical = _is_canonical_state_root(arguments.state_root)
        service_loaded = canonical and _systemctl_show()["load"] == "loaded"
        unit_boundary = (
            SYSTEM_UNIT_PATH
            if canonical and (service_loaded or SYSTEM_UNIT_PATH.exists())
            else None
        )
        previous = store.previous()
        if unit_boundary is not None and previous is not None:
            _validate_systemd_unit(previous / "ops/systemd/voice-agent-v2.service")
        if service_loaded:
            _validate_effective_systemd_service()
        result = store.rollback(required_system_unit=unit_boundary)
        if service_loaded:
            _sudo("systemctl", "daemon-reload")
            _start_service_with_recovery_allowance("restart")
            active = _sudo(
                "systemctl", "is-active", SERVICE_NAME, allowed=(0, 3, 4),
            ).returncode == 0
            if not active:
                raise OperationalError(
                    "rollback_restart_failed",
                    "rollback pointer changed but service did not become active",
                )
            try:
                _wait_for_runtime_release(str(result["release_id"]))
            except OperationalError as error:
                raise OperationalError(
                    "rollback_restart_failed",
                    "rollback pointer changed but prior release did not become ready",
                ) from error
            result["service_restarted"] = True
            result["service_ready"] = True
        else:
            result["service_restarted"] = False
        _print(result)


def command_sustained_report(arguments: argparse.Namespace) -> None:
    try:
        evidence = json.loads(arguments.evidence.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise OperationalError("sustained_report_invalid", "sustained evidence is unavailable or invalid") from error
    if not isinstance(evidence, dict) or not isinstance(evidence.get("turns"), list) or not isinstance(evidence.get("avatar"), dict):
        raise OperationalError("sustained_report_invalid", "sustained evidence shape is invalid")
    manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
    report = evaluate_sustained_run(
        manifest, turns=evidence["turns"], avatar=evidence["avatar"],
    )
    _print(report)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="voice-agent-ops",
        description="Bounded single-host deployment, validation, status, and rollback",
    )
    subcommands = result.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="validate local config, artifacts, caches, and disk")
    validate.add_argument("--config", type=_configuration, required=True)
    validate.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    validate.set_defaults(function=command_validate)

    deploy = subcommands.add_parser("deploy", help="stage and atomically activate one clean committed release")
    deploy.add_argument("--config", type=_configuration, required=True)
    deploy.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    deploy.set_defaults(function=command_deploy)

    deployment = subcommands.add_parser("validate-deployment", help="revalidate the active immutable release")
    deployment.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    deployment.set_defaults(function=command_validate_deployment)

    status = subcommands.add_parser("status", help="report release, service, provider, and client supervision state")
    status.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    status.set_defaults(function=command_status)

    install = subcommands.add_parser("install-service", help="idempotently install/enable the canonical systemd unit")
    install.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    install.add_argument(
        "--restart", action="store_true",
        help="revalidate and restart even when the active release identity is unchanged",
    )
    install.set_defaults(function=command_install_service)

    rollback = subcommands.add_parser("rollback", help="activate only the verified previous compatible release")
    rollback.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    rollback.set_defaults(function=command_rollback)

    sustained = subcommands.add_parser("sustained-report", help="evaluate content-free sustained-run evidence")
    sustained.add_argument("--evidence", type=_configuration, required=True)
    sustained.set_defaults(function=command_sustained_report)

    run = subcommands.add_parser("run", help=argparse.SUPPRESS)
    run.add_argument("--state-root", type=_state_root, default=DEFAULT_STATE_ROOT)
    run.set_defaults(function=lambda arguments: execute_release(arguments.state_root))
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        arguments.function(arguments)
    except OperationalError as error:
        print(f"voice-agent-ops failed: {error.code}: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT_STATUS
    except KeyboardInterrupt:
        print("voice-agent-ops failed: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

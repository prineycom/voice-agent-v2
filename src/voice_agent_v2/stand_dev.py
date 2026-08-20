"""Local immutable development stand lifecycle with explicit host-command seams.

This module owns only the first dev path.  It deliberately does not resolve remote
refs, select main releases, or manage the host beyond the requested user-systemd
commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Mapping, Protocol, Sequence
import uuid


DEFAULT_STATE_ROOT = Path.home() / ".local/share/voice-agent-v2"
DEFAULT_USER_UNIT_DIRECTORY = Path.home() / ".config/systemd/user"
INSTANCE_NAMES = ("main", "dev")
CONFIG_NAME = "private.env"
UNIT_NAME = "voice-agent-v2@.service"
SHA256_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CONFIG_LINE = re.compile(r"([A-Z][A-Z0-9_]*)=([A-Za-z0-9._:/-]+)\Z")
CONFIG_KEYS = frozenset({
    "STAND_NAME",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_INTERNAL_URL",
    "LIVEKIT_PUBLIC_URL",
    "VOICE_AGENT_LLM_PORT",
    "VOICE_AGENT_LIVEKIT_PORT",
    "VOICE_AGENT_RTC_UDP_PORT",
    "VOICE_AGENT_GATEWAY_PORT",
})
PORT_KEYS = frozenset({
    "VOICE_AGENT_LLM_PORT",
    "VOICE_AGENT_LIVEKIT_PORT",
    "VOICE_AGENT_RTC_UDP_PORT",
    "VOICE_AGENT_GATEWAY_PORT",
})


class StandError(RuntimeError):
    """A non-secret, operator-actionable stand failure."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, arguments: Sequence[str], *, cwd: Path | None = None) -> CommandResult: ...


class SystemCommandRunner:
    """The narrow real command boundary; tests provide a recording substitute."""

    def run(self, arguments: Sequence[str], *, cwd: Path | None = None) -> CommandResult:
        try:
            completed = subprocess.run(
                tuple(arguments), cwd=cwd, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, check=False,
            )
        except OSError as error:
            return CommandResult(127, stderr=type(error).__name__)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def state_root_from_environment(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    configured = environment.get("VOICE_AGENT_STAND_STATE_ROOT")
    return Path(configured).expanduser() if configured else DEFAULT_STATE_ROOT


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise StandError("stand state path is not a directory")
    os.chmod(path, 0o700)


def instance_root(state_root: Path, instance: str) -> Path:
    if instance not in INSTANCE_NAMES:
        raise StandError("only the declared stand instance is accepted")
    return state_root / "instances" / instance


def config_path(state_root: Path, instance: str) -> Path:
    return instance_root(state_root, instance) / "config" / CONFIG_NAME


def _config_values(instance: str) -> dict[str, str]:
    # The first runnable dev stand uses the existing foreground launcher's fixed
    # loopback ports.  `main` is initialized but is intentionally not runnable
    # until the later side-by-side isolation slice.
    offset = 0 if instance == "dev" else 1
    livekit_port = 7880 + offset * 3
    return {
        "STAND_NAME": instance,
        "LIVEKIT_API_KEY": f"va-{instance}-{secrets.token_hex(12)}",
        "LIVEKIT_API_SECRET": secrets.token_urlsafe(32),
        "LIVEKIT_INTERNAL_URL": f"ws://127.0.0.1:{livekit_port}",
        "LIVEKIT_PUBLIC_URL": f"ws://127.0.0.1:{livekit_port}",
        "VOICE_AGENT_LLM_PORT": str(18080 + offset),
        "VOICE_AGENT_LIVEKIT_PORT": str(livekit_port),
        "VOICE_AGENT_RTC_UDP_PORT": str(7882 + offset * 3),
        "VOICE_AGENT_GATEWAY_PORT": str(8000 + offset),
    }


def _write_private_config(path: Path, values: Mapping[str, str]) -> None:
    """Create the secret file exactly once and never expose it via a shell."""
    body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        parse_private_config(path)
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(body)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)


def parse_private_config(path: Path) -> dict[str, str]:
    """Read one strict data-only config file; shell syntax is never accepted."""
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise StandError("private configuration is missing") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or _mode(path) != 0o600:
        raise StandError("private configuration must be a regular mode-0600 file")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise StandError("private configuration cannot be read as text data") from error
    values: dict[str, str] = {}
    for line in lines:
        match = CONFIG_LINE.fullmatch(line)
        if match is None or not match.group(2):
            raise StandError("private configuration has an invalid KEY=VALUE record")
        key, value = match.groups()
        if key in values:
            raise StandError("private configuration has a duplicate key")
        values[key] = value
    if frozenset(values) != CONFIG_KEYS:
        raise StandError("private configuration has an unknown or missing key")
    if values["STAND_NAME"] not in INSTANCE_NAMES:
        raise StandError("private configuration has an invalid stand name")
    for key in PORT_KEYS:
        value = values[key]
        if not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise StandError("private configuration requires explicit valid ports")
    expected_livekit_url = f"ws://127.0.0.1:{values['VOICE_AGENT_LIVEKIT_PORT']}"
    if values["LIVEKIT_INTERNAL_URL"] != expected_livekit_url or values["LIVEKIT_PUBLIC_URL"] != expected_livekit_url:
        raise StandError("private configuration must use its explicit loopback LiveKit port")
    return values


def unit_template(*, stand_executable: Path, state_root: Path) -> str:
    """A per-user template: the launcher becomes the foreground process."""
    if any(character.isspace() for character in str(stand_executable)):
        raise StandError("stand executable path cannot contain whitespace")
    return "\n".join((
        "[Unit]",
        "Description=Voice Agent v2 local stand %i",
        "After=docker.service",
        "Requires=docker.service",
        "",
        "[Service]",
        "Type=notify",
        "Environment=PYTHONUNBUFFERED=1",
        f"Environment=VOICE_AGENT_STAND_STATE_ROOT={state_root}",
        f"ExecStart={stand_executable} launcher %i",
        "Restart=no",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ))


def initialize(
    *, state_root: Path, user_unit_directory: Path, stand_executable: Path,
) -> None:
    """Create only operator-owned mutable state outside the controller clone."""
    _mkdir_private(state_root)
    _mkdir_private(state_root / "releases")
    _mkdir_private(state_root / "instances")
    for instance in INSTANCE_NAMES:
        root = instance_root(state_root, instance)
        for relative in (
            "config", "data", "cache", "runtime", "workspace", "credentials", "agent-environment",
        ):
            _mkdir_private(root / relative)
        _write_private_config(config_path(state_root, instance), _config_values(instance))
    _mkdir_private(user_unit_directory)
    unit = user_unit_directory / UNIT_NAME
    temporary = user_unit_directory / f".{UNIT_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(unit_template(stand_executable=stand_executable, state_root=state_root), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, unit)
    finally:
        temporary.unlink(missing_ok=True)


def _checked(command: CommandRunner, arguments: Sequence[str], *, cwd: Path | None = None, failure: str) -> CommandResult:
    result = command.run(arguments, cwd=cwd)
    if result.returncode != 0:
        raise StandError(failure)
    return result


def _release_manifest(release: Path) -> dict[str, object]:
    manifest = release / "release.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StandError("existing release is incomplete") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("commit"), str):
        raise StandError("existing release manifest is invalid")
    return payload


def _make_immutable(path: Path) -> None:
    for directory, _, files in os.walk(path, followlinks=False):
        current = Path(directory)
        os.chmod(current, 0o555)
        for name in files:
            entry = current / name
            if entry.is_symlink():
                continue
            mode = entry.stat(follow_symlinks=False).st_mode
            os.chmod(entry, 0o555 if mode & 0o111 else 0o444)


def build_local_release(*, state_root: Path, repository: Path, commit: str, command: CommandRunner) -> Path:
    """Archive a clean exact commit, then promote it only after completion."""
    if SHA256_COMMIT.fullmatch(commit) is None:
        raise StandError("local deployment requires one full lowercase committed SHA")
    repository = repository.resolve()
    observed = _checked(
        command, ("git", "rev-parse", "--verify", f"{commit}^{{commit}}"), cwd=repository,
        failure="the requested local SHA is not a committed revision",
    ).stdout.strip()
    if observed != commit:
        raise StandError("the requested local SHA did not resolve exactly")
    dirty = _checked(command, ("git", "status", "--porcelain"), cwd=repository, failure="local repository cannot be inspected")
    if dirty.stdout:
        raise StandError("local deployment refuses dirty repository changes")
    releases = state_root / "releases"
    release = releases / commit
    if release.exists():
        if not release.is_dir() or _release_manifest(release).get("commit") != commit:
            raise StandError("existing release conflicts with the requested SHA")
        return release
    stage = Path(tempfile.mkdtemp(prefix=f".{commit}.", dir=releases))
    try:
        archive = stage / "source.tar"
        _checked(command, ("git", "archive", "--format=tar", "-o", str(archive), commit), cwd=repository,
                 failure="immutable release archive construction failed")
        source = stage / "source"
        source.mkdir(mode=0o755)
        _checked(command, ("tar", "-xf", str(archive), "-C", str(source)), failure="immutable release extraction failed")
        archive.unlink()
        if (source / ".git").exists():
            raise StandError("immutable release archive unexpectedly contains Git metadata")
        (stage / "release.json").write_text(
            json.dumps({"schema": "voice-agent-stand-release.v1", "commit": commit}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _make_immutable(stage)
        try:
            os.replace(stage, release)
        except FileExistsError:
            if _release_manifest(release).get("commit") != commit:
                raise StandError("concurrent release construction conflicted")
        return release
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def select_release(*, state_root: Path, instance: str, release: Path) -> None:
    root = instance_root(state_root, instance)
    if not release.is_dir() or _release_manifest(release).get("commit") != release.name:
        raise StandError("release is not complete")
    current = root / "current"
    temporary = root / f".current.{uuid.uuid4().hex}"
    target = os.path.relpath(release, root)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)


def selected_release(state_root: Path, instance: str) -> tuple[str, Path] | None:
    current = instance_root(state_root, instance) / "current"
    if not current.is_symlink():
        return None
    try:
        release = current.resolve(strict=True)
    except OSError:
        return None
    try:
        manifest = _release_manifest(release)
    except StandError:
        return None
    commit = manifest.get("commit")
    if not isinstance(commit, str) or SHA256_COMMIT.fullmatch(commit) is None or release.name != commit:
        return None
    return commit, release


def unit_for(instance: str) -> str:
    if instance not in INSTANCE_NAMES:
        raise StandError("only the declared stand instance is accepted")
    return f"voice-agent-v2@{instance}.service"


def start(*, instance: str, command: CommandRunner) -> None:
    unit = unit_for(instance)
    _checked(command, ("systemctl", "--user", "daemon-reload"), failure="user-systemd daemon reload failed")
    _checked(command, ("systemctl", "--user", "start", unit), failure="user-systemd could not start the selected stand")
    active = command.run(("systemctl", "--user", "is-active", unit))
    if active.returncode != 0 or active.stdout.strip() != "active":
        raise StandError("selected release remains not-ready after user-systemd start")


def status(*, state_root: Path, instance: str, command: CommandRunner) -> str:
    selected = selected_release(state_root, instance)
    if selected is None:
        return f"stand status {instance}\nversion: none\nreadiness: not-ready (no selected release)"
    commit, _ = selected
    active = command.run(("systemctl", "--user", "is-active", unit_for(instance)))
    readiness = "ready" if active.returncode == 0 and active.stdout.strip() == "active" else "not-ready"
    return f"stand status {instance}\nversion: {commit}\nreadiness: {readiness}"


def logs(*, instance: str, command: CommandRunner) -> str:
    result = command.run(("journalctl", "--user", "-u", unit_for(instance), "--no-pager"))
    if result.returncode != 0:
        raise StandError("journald records for the selected stand are unavailable")
    return result.stdout.rstrip("\n")


def deploy_local_dev(*, state_root: Path, repository: Path, commit: str, command: CommandRunner) -> str:
    values = parse_private_config(config_path(state_root, "dev"))
    if values["STAND_NAME"] != "dev":
        raise StandError("dev configuration identifies another stand")
    release = build_local_release(state_root=state_root, repository=repository, commit=commit, command=command)
    select_release(state_root=state_root, instance="dev", release=release)
    try:
        start(instance="dev", command=command)
    except StandError as error:
        # The selected release intentionally remains visible for diagnosis.
        raise StandError(f"release selected but readiness failed: {error}") from error
    return commit


def exec_launcher(*, state_root: Path, instance: str) -> None:
    """Replace systemd's process with the established child-owning foreground runner."""
    selected = selected_release(state_root, instance)
    if selected is None:
        raise StandError("launcher has no selected immutable release")
    values = parse_private_config(config_path(state_root, instance))
    if values["STAND_NAME"] != instance:
        raise StandError("launcher configuration identifies another stand")
    _, release = selected
    launcher = release / "source" / "scripts" / "run_slice6.py"
    if not launcher.is_file():
        raise StandError("selected release lacks the foreground launcher")
    environment = dict(os.environ)
    environment.pop("LITELLM_BASE_URL", None)
    environment.pop("LITELLM_TOKEN_FILE", None)
    environment.update(values)
    environment["VOICE_AGENT_INSTANCE_ROOT"] = str(instance_root(state_root, instance))
    os.execve(sys.executable, (sys.executable, "-B", str(launcher)), environment)

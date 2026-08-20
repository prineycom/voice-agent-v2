"""Immutable main/dev stand deployment with explicit host-command seams.

Dev owns local-commit and ordinary-private-remote paths. Main owns only exact,
immutably observed SemVer tags. Host changes remain limited to requested
user-systemd commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from typing import Mapping, Protocol, Sequence
import uuid

from .agent_environment import AgentEnvironment, AgentEnvironmentError, DockerRunner


DEFAULT_STATE_ROOT = Path.home() / ".local/share/voice-agent-v2"
DEFAULT_USER_UNIT_DIRECTORY = Path.home() / ".config/systemd/user"
INSTANCE_NAMES = ("main", "dev")
CONFIG_NAME = "private.env"
UNIT_NAME = "voice-agent-v2@.service"
SHA256_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
REMOTE_REF_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
MAIN_SEMVER_TAG = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
MAIN_TAG_TARGET_SCHEMA = "voice-agent-main-tag-target.v1"
RELEASE_SCHEMA = "voice-agent-stand-release.v2"
PRODUCTION_LOCK = "requirements-stand-production.lock"
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
    # Both complete stacks use explicit, non-overlapping loopback listeners.
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
    if len({values[key] for key in PORT_KEYS}) != len(PORT_KEYS):
        raise StandError("private configuration listener ports must be distinct")
    return values


def validate_instance_isolation(state_root: Path) -> None:
    """Refuse overlapping listeners, identity, or credential material."""
    main = parse_private_config(config_path(state_root, "main"))
    dev = parse_private_config(config_path(state_root, "dev"))
    if main["STAND_NAME"] != "main" or dev["STAND_NAME"] != "dev":
        raise StandError("private configuration is assigned to the wrong instance")
    if {main[key] for key in PORT_KEYS} & {dev[key] for key in PORT_KEYS}:
        raise StandError("main and dev listener ports must not overlap")
    for key in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if main[key] == dev[key]:
            raise StandError("main and dev credentials must be independent")


def unit_template(*, stand_executable: Path, state_root: Path) -> str:
    """A per-user template: the launcher becomes the foreground process."""
    if any(character.isspace() for character in str(stand_executable)):
        raise StandError("stand executable path cannot contain whitespace")
    return "\n".join((
        "[Unit]",
        "Description=Voice Agent v2 local stand %i",
        "# stand start enables user linger so this user manager survives logout and boots.",
        "After=docker.service",
        "Requires=docker.service",
        "StartLimitIntervalSec=60",
        "StartLimitBurst=3",
        "",
        "[Service]",
        "Type=notify",
        "NotifyAccess=main",
        "KillMode=control-group",
        "Environment=PYTHONUNBUFFERED=1",
        f"Environment=VOICE_AGENT_STAND_STATE_ROOT={state_root}",
        f"ExecStart={stand_executable} launcher %i",
        "Restart=on-failure",
        "RestartPreventExitStatus=2",
        "RestartSec=5",
        "StandardOutput=journal",
        "StandardError=journal",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ))


def controller_source_path(state_root: Path) -> Path:
    """The one ordinary user-owned clone used for remote dev deployments."""
    return state_root / "source"


def initialize(
    *, state_root: Path, user_unit_directory: Path, stand_executable: Path,
    controller_repository: Path | None = None, command: CommandRunner | None = None,
) -> None:
    """Create external state and, for the CLI, its one ordinary controller clone."""
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
    _mkdir_private(instance_root(state_root, "main") / "tag-targets")
    validate_instance_isolation(state_root)
    if controller_repository is not None:
        if command is None:
            raise StandError("controller clone requires the Git command boundary")
        source = controller_source_path(state_root)
        if source.exists():
            _checked(command, ("git", "rev-parse", "--is-inside-work-tree"), cwd=source,
                     failure="controller source clone is unavailable")
        else:
            origin = _checked(
                command, ("git", "config", "--get", "remote.origin.url"),
                cwd=controller_repository.resolve(),
                failure="controller repository has no ordinary origin remote",
            ).stdout.strip()
            if not origin:
                raise StandError("controller repository has no ordinary origin remote")
            _checked(
                command, ("git", "clone", "--no-checkout", origin, str(source)),
                failure="ordinary controller source clone failed",
            )
            _mkdir_private(source)
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
    if not isinstance(payload, dict) or not (
        payload.get("schema") == RELEASE_SCHEMA
        and isinstance(payload.get("commit"), str)
        and SHA256_COMMIT.fullmatch(str(payload["commit"]))
        and payload.get("source") == "git-archive"
        and payload.get("frontend") == "source/web/dist"
        and payload.get("python_environment") == "python"
        and payload.get("python_lock") == PRODUCTION_LOCK
        and isinstance(payload.get("python_lock_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(payload["python_lock_sha256"]))
    ):
        raise StandError("existing release manifest is invalid")
    source = release / "source"
    if (source / ".git").exists() or not (source / "web/dist/index.html").is_file() or not (release / "python/bin/python").is_file():
        raise StandError("existing release is incomplete")
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


def _safe_remote_ref_name(name: str) -> bool:
    return bool(
        REMOTE_REF_NAME.fullmatch(name)
        and "//" not in name
        and "/./" not in name
        and ".." not in name
        and not name.endswith(("/", ".", ".lock"))
        and "@{" not in name
    )


def _advertised_remote_ref(*, repository: Path, ref: str, command: CommandRunner) -> str:
    """Admit one branch, tag, or full SHA without exposing Git refspec syntax."""
    if SHA256_COMMIT.fullmatch(ref):
        return ref
    if ref.startswith("refs/heads/"):
        candidates = (ref,) if _safe_remote_ref_name(ref.removeprefix("refs/heads/")) else ()
    elif ref.startswith("refs/tags/"):
        candidates = (ref,) if _safe_remote_ref_name(ref.removeprefix("refs/tags/")) else ()
    elif ref.startswith("refs/") or not _safe_remote_ref_name(ref):
        candidates = ()
    else:
        candidates = (f"refs/heads/{ref}", f"refs/tags/{ref}")
    if not candidates:
        raise StandError("remote deployment requires a permitted branch, tag, or full lowercase SHA")
    advertised = _checked(
        command, ("git", "ls-remote", "--refs", "origin", *candidates), cwd=repository,
        failure="remote ref cannot be inspected",
    ).stdout.splitlines()
    found: list[str] = []
    for line in advertised:
        identity, separator, remote_name = line.partition("\t")
        if separator and SHA256_COMMIT.fullmatch(identity) and remote_name in candidates:
            found.append(remote_name)
    if len(found) != 1:
        raise StandError("remote ref is unresolved or ambiguous")
    return found[0]


def resolve_remote_dev_ref(*, state_root: Path, ref: str, command: CommandRunner) -> str:
    """Fetch one permitted remote object and return only its full commit identity."""
    source = controller_source_path(state_root)
    _checked(command, ("git", "rev-parse", "--is-inside-work-tree"), cwd=source,
             failure="controller source clone is unavailable")
    requested = _advertised_remote_ref(repository=source, ref=ref, command=command)
    _checked(command, ("git", "fetch", "--no-tags", "origin", requested), cwd=source,
             failure="remote ref fetch failed")
    observed = _checked(
        command, ("git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"), cwd=source,
        failure="remote ref did not resolve to a commit",
    ).stdout.strip()
    if SHA256_COMMIT.fullmatch(observed) is None:
        raise StandError("remote ref did not resolve to one full lowercase commit SHA")
    return observed


def _main_tag_target_path(state_root: Path, tag: str) -> Path:
    return instance_root(state_root, "main") / "tag-targets" / f"{tag}.json"


def _read_main_tag_target(path: Path, tag: str) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StandError("recorded main tag target is unreadable") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or _mode(path) != 0o600:
        raise StandError("recorded main tag target must be a regular mode-0600 file")
    if not isinstance(payload, dict) or set(payload) != {"schema", "tag", "commit"} or not (
        payload.get("schema") == MAIN_TAG_TARGET_SCHEMA
        and payload.get("tag") == tag
        and isinstance(payload.get("commit"), str)
        and SHA256_COMMIT.fullmatch(str(payload["commit"]))
    ):
        raise StandError("recorded main tag target is invalid")
    return str(payload["commit"])


def _remember_main_tag_target(*, state_root: Path, tag: str, commit: str) -> None:
    """Persist the first resolved commit for a tag; later movement always fails closed."""
    directory = instance_root(state_root, "main") / "tag-targets"
    _mkdir_private(directory)
    path = _main_tag_target_path(state_root, tag)
    body = json.dumps({
        "schema": MAIN_TAG_TARGET_SCHEMA, "tag": tag, "commit": commit,
    }, sort_keys=True) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        recorded = _read_main_tag_target(path, tag)
        if recorded != commit:
            raise StandError(f"main tag {tag} moved from its recorded commit and is refused")
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(body)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(path, 0o600)
        directory_descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def resolve_remote_main_tag(*, state_root: Path, tag: str, command: CommandRunner) -> str:
    """Resolve one exact release tag to a commit and pin its first observed target."""
    if MAIN_SEMVER_TAG.fullmatch(tag) is None:
        raise StandError("main deployment requires one exact vMAJOR.MINOR.PATCH tag")
    source = controller_source_path(state_root)
    _checked(command, ("git", "rev-parse", "--is-inside-work-tree"), cwd=source,
             failure="controller source clone is unavailable")
    remote_name = f"refs/tags/{tag}"
    advertised = _checked(
        command, ("git", "ls-remote", "--refs", "origin", remote_name), cwd=source,
        failure="main release tag cannot be inspected",
    ).stdout.splitlines()
    matches = []
    for line in advertised:
        identity, separator, name = line.partition("\t")
        if separator and SHA256_COMMIT.fullmatch(identity) and name == remote_name:
            matches.append(identity)
    if len(matches) != 1:
        raise StandError("main release tag is unresolved")
    _checked(command, ("git", "fetch", "--no-tags", "origin", remote_name), cwd=source,
             failure="main release tag fetch failed")
    commit = _checked(
        command, ("git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"), cwd=source,
        failure="main release tag did not resolve to a commit",
    ).stdout.strip()
    if SHA256_COMMIT.fullmatch(commit) is None:
        raise StandError("main release tag did not resolve to one full lowercase commit SHA")
    _remember_main_tag_target(state_root=state_root, tag=tag, commit=commit)
    return commit


def _build_release_payload(*, stage: Path, source: Path, command: CommandRunner) -> str:
    lock = source / PRODUCTION_LOCK
    if not lock.is_file():
        raise StandError("immutable release lacks the dedicated production Python lock")
    web = source / "web"
    if not (web / "package.json").is_file() or not (web / "package-lock.json").is_file():
        raise StandError("immutable release lacks the production frontend lock")
    dependencies = web / "node_modules"
    try:
        _checked(command, ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"), cwd=web,
                 failure="production frontend dependencies could not be installed")
        _checked(command, ("npm", "run", "build:production-only"), cwd=web,
                 failure="production frontend build failed")
        if not (web / "dist/index.html").is_file():
            raise StandError("production frontend build is incomplete")
    finally:
        if dependencies.is_symlink():
            dependencies.unlink()
        elif dependencies.exists():
            shutil.rmtree(dependencies)
    environment = stage / "python"
    _checked(command, ("python3", "-m", "venv", str(environment)),
             failure="production Python environment creation failed")
    python = environment / "bin/python"
    if not python.is_file():
        raise StandError("production Python environment creation is incomplete")
    _checked(
        command, (str(python), "-m", "pip", "install", "--disable-pip-version-check", "--requirement", str(lock)),
        cwd=source, failure="production Python dependencies could not be installed",
    )
    return hashlib.sha256(lock.read_bytes()).hexdigest()


def build_release(*, state_root: Path, repository: Path, commit: str, command: CommandRunner) -> Path:
    """Build a full immutable release in a temporary directory before promotion."""
    if SHA256_COMMIT.fullmatch(commit) is None:
        raise StandError("release construction requires one full lowercase committed SHA")
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
        lock_digest = _build_release_payload(stage=stage, source=source, command=command)
        try:
            (stage / "release.json").write_text(
                json.dumps({
                    "schema": RELEASE_SCHEMA, "commit": commit, "source": "git-archive",
                    "frontend": "source/web/dist", "python_environment": "python",
                    "python_lock": PRODUCTION_LOCK, "python_lock_sha256": lock_digest,
                }, sort_keys=True) + "\n", encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as error:
            raise StandError("immutable release manifest construction failed") from error
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


def build_local_release(*, state_root: Path, repository: Path, commit: str, command: CommandRunner) -> Path:
    """Preserve the issue #66 local committed-SHA admission path."""
    if SHA256_COMMIT.fullmatch(commit) is None:
        raise StandError("local deployment requires one full lowercase committed SHA")
    repository = repository.resolve()
    observed = _checked(
        command, ("git", "rev-parse", "--verify", f"{commit}^{{commit}}"), cwd=repository,
        failure="the requested local SHA is not a committed revision",
    ).stdout.strip()
    if observed != commit:
        raise StandError("the requested local SHA did not resolve exactly")
    dirty = _checked(command, ("git", "status", "--porcelain"), cwd=repository,
                     failure="local repository cannot be inspected")
    if dirty.stdout:
        raise StandError("local deployment refuses dirty repository changes")
    return build_release(state_root=state_root, repository=repository, commit=commit, command=command)


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


def _service_state(*, instance: str, command: CommandRunner) -> str:
    result = command.run(("systemctl", "--user", "is-active", unit_for(instance)))
    state = result.stdout.strip()
    if state in {"active", "activating", "deactivating", "inactive", "failed"}:
        return state
    if result.returncode == 4:
        return "inactive"
    raise StandError("user-systemd state for the selected stand is unavailable")


def _service_running(*, instance: str, command: CommandRunner) -> bool:
    return _service_state(instance=instance, command=command) == "active"


def _ensure_active(*, instance: str, command: CommandRunner, action: str) -> None:
    unit = unit_for(instance)
    _checked(
        command, ("systemctl", "--user", action, unit),
        failure=f"user-systemd could not {action} the selected stand",
    )
    if not _service_running(instance=instance, command=command):
        raise StandError(f"selected release remains not-ready after user-systemd {action}")


def start(*, state_root: Path, instance: str, command: CommandRunner) -> None:
    selected = selected_release(state_root, instance)
    if selected is None:
        raise StandError("start requires one selected immutable release")
    # Fail before persistence changes when external configuration is already known bad.
    launcher_environment(state_root=state_root, instance=instance, environment={})
    unit = unit_for(instance)
    user = pwd.getpwuid(os.getuid()).pw_name
    _checked(
        command, ("sudo", "-n", "loginctl", "enable-linger", user),
        failure="user-systemd linger could not be enabled",
    )
    _checked(command, ("systemctl", "--user", "daemon-reload"), failure="user-systemd daemon reload failed")
    _checked(command, ("systemctl", "--user", "enable", unit), failure="stand boot persistence could not be enabled")
    _checked(command, ("systemctl", "--user", "reset-failed", unit), failure="stand failed state could not be reset")
    _ensure_active(instance=instance, command=command, action="start")


def stop(
    *, state_root: Path, instance: str, command: CommandRunner,
    docker_runner: DockerRunner | None = None,
) -> str:
    unit = unit_for(instance)
    _checked(command, ("systemctl", "--user", "disable", unit), failure="stand boot persistence could not be disabled")
    _checked(command, ("systemctl", "--user", "stop", unit), failure="user-systemd could not stop the selected stand")
    _checked(command, ("systemctl", "--user", "reset-failed", unit), failure="stand failed state could not be reset")
    try:
        container = AgentEnvironment.stop_registered(
            state_root=instance_root(state_root, instance) / "agent-environment" / "private",
            runner=docker_runner,
        )
    except AgentEnvironmentError as error:
        raise StandError(f"instance container stop failed: {error.code}") from error
    return container


def _persistence_state(*, instance: str, command: CommandRunner) -> str:
    result = command.run(("systemctl", "--user", "is-enabled", unit_for(instance)))
    value = result.stdout.strip()
    if value in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}:
        return "enabled"
    if value in {"disabled", "static", "indirect", "masked", "not-found"} or result.returncode in {1, 3, 4}:
        return "disabled"
    return "unknown"


def status(*, state_root: Path, instance: str, command: CommandRunner) -> str:
    selected = selected_release(state_root, instance)
    commit = selected[0] if selected is not None else "none"
    active = _service_state(instance=instance, command=command)
    persistence = _persistence_state(instance=instance, command=command)
    failure = None
    if active == "active":
        lifecycle = "running"
        readiness = "ready" if selected is not None else "not-ready"
    elif active == "failed":
        exit_status = command.run((
            "systemctl", "--user", "show", unit_for(instance),
            "--property=ExecMainStatus", "--value",
        ))
        restarts = command.run((
            "systemctl", "--user", "show", unit_for(instance),
            "--property=NRestarts", "--value",
        )).stdout.strip()
        if exit_status.stdout.strip() == "2":
            lifecycle = "configuration-failed"
            failure = "configuration; automatic restart suppressed"
        else:
            lifecycle = "failed"
            failure = f"runtime/startup; automatic retries bounded; restarts={restarts if restarts.isdecimal() else 'unknown'}"
        readiness = "not-ready"
    elif active == "activating":
        lifecycle, readiness = "starting", "not-ready"
    elif active == "deactivating":
        lifecycle, readiness = "stopping", "not-ready"
    else:
        lifecycle, readiness = "stopped", "not-ready"
    if selected is None:
        readiness += " (no selected release)"
    lines = [
        f"stand status {instance}", f"version: {commit}", f"state: {lifecycle}",
        f"persistence: {persistence}", f"readiness: {readiness}",
    ]
    if failure is not None:
        lines.append(f"failure: {failure}")
    return "\n".join(lines)


def list_instances(*, state_root: Path, command: CommandRunner) -> str:
    records = []
    for instance in INSTANCE_NAMES:
        detail = status(state_root=state_root, instance=instance, command=command).splitlines()[1:]
        records.append(f"{instance}:\n" + "\n".join(f"  {line}" for line in detail))
    return "stand list\n" + "\n".join(records)


def logs(*, state_root: Path, instance: str, command: CommandRunner) -> str:
    selected = selected_release(state_root, instance)
    version = selected[0] if selected is not None else "none"
    unit = unit_for(instance)
    result = command.run(("journalctl", "--user", "-u", unit, "--no-pager"))
    if result.returncode != 0:
        raise StandError("journald records for the selected stand are unavailable")
    records = result.stdout.rstrip("\n")
    header = f"stand logs {instance}\nversion: {version}\nunit: {unit}"
    return f"{header}\n{records}" if records else header


def validate_release_external_configuration(
    *, state_root: Path, instance: str, release: Path,
) -> None:
    """Validate external instance data against the target before pointer activation."""
    manifest = _release_manifest(release)
    commit = manifest.get("commit")
    if not isinstance(commit, str) or release.name != commit:
        raise StandError("target release identity is invalid")
    _launcher_environment_for_release(
        state_root=state_root, instance=instance, commit=commit, release=release,
        environment={},
    )


def _deploy_instance_release(
    *, state_root: Path, instance: str, repository: Path, commit: str,
    was_running: bool, command: CommandRunner,
) -> str:
    release = build_release(state_root=state_root, repository=repository, commit=commit, command=command)
    validate_release_external_configuration(
        state_root=state_root, instance=instance, release=release,
    )
    select_release(state_root=state_root, instance=instance, release=release)
    if was_running:
        try:
            _checked(
                command, ("systemctl", "--user", "daemon-reload"),
                failure="user-systemd daemon reload failed",
            )
            _ensure_active(instance=instance, command=command, action="restart")
        except StandError as error:
            # The selected release intentionally remains visible for diagnosis.
            raise StandError(f"release selected but readiness failed: {error}") from error
    return commit


def deploy_local(
    *, state_root: Path, instance: str, repository: Path, commit: str,
    command: CommandRunner,
) -> str:
    """Select an exact local commit for dev; main is tag-only."""
    if instance != "dev":
        raise StandError("main deployment requires one exact vMAJOR.MINOR.PATCH tag")
    was_running = _service_running(instance=instance, command=command)
    values = parse_private_config(config_path(state_root, instance))
    if values["STAND_NAME"] != instance:
        raise StandError("instance configuration identifies another stand")
    local_release = build_local_release(
        state_root=state_root, repository=repository, commit=commit, command=command,
    )
    return _deploy_instance_release(
        state_root=state_root, instance=instance, repository=repository,
        commit=str(_release_manifest(local_release)["commit"]),
        was_running=was_running, command=command,
    )


def deploy_local_dev(*, state_root: Path, repository: Path, commit: str, command: CommandRunner) -> str:
    """Issue #66's clean local SHA selection remains available unchanged."""
    return deploy_local(
        state_root=state_root, instance="dev", repository=repository,
        commit=commit, command=command,
    )


def deploy_remote(*, state_root: Path, instance: str, ref: str, command: CommandRunner) -> str:
    """Apply the strict main-tag or permissive explicit dev remote policy."""
    if instance not in INSTANCE_NAMES:
        raise StandError("only the declared stand instance is accepted")
    was_running = _service_running(instance=instance, command=command)
    if instance == "main":
        commit = resolve_remote_main_tag(state_root=state_root, tag=ref, command=command)
    elif instance == "dev":
        commit = resolve_remote_dev_ref(state_root=state_root, ref=ref, command=command)
    return _deploy_instance_release(
        state_root=state_root, instance=instance,
        repository=controller_source_path(state_root), commit=commit,
        was_running=was_running, command=command,
    )


def deploy_remote_dev(*, state_root: Path, ref: str, command: CommandRunner) -> str:
    return deploy_remote(state_root=state_root, instance="dev", ref=ref, command=command)


def _launcher_environment_for_release(
    *, state_root: Path, instance: str, commit: str, release: Path,
    environment: Mapping[str, str],
) -> tuple[Path, tuple[str, ...], dict[str, str]]:
    """Construct and validate one target release's external launch contract."""
    validate_instance_isolation(state_root)
    values = parse_private_config(config_path(state_root, instance))
    if values["STAND_NAME"] != instance:
        raise StandError("launcher configuration identifies another stand")
    root = instance_root(state_root, instance).resolve()
    private_paths = {
        "VOICE_AGENT_DATA_ROOT": root / "data",
        "VOICE_AGENT_MUTABLE_CACHE_ROOT": root / "cache",
        "VOICE_AGENT_TASK_RUNTIME_ROOT": root / "runtime" / "inference",
        "VOICE_AGENT_WORKSPACE_ROOT": root / "workspace",
        "VOICE_AGENT_CREDENTIALS_ROOT": root / "credentials",
        "VOICE_AGENT_AGENT_ENVIRONMENT_ROOT": root / "agent-environment",
    }
    for path in private_paths.values():
        if not path.is_relative_to(root):
            raise StandError("launcher mutable path escaped the selected instance")
    launcher = release / "source" / "scripts" / "run_slice6.py"
    python = release / "python" / "bin" / "python"
    if not launcher.is_file() or not python.is_file():
        raise StandError("selected release lacks its foreground runtime")
    result = dict(environment)
    shared_cache = Path(
        result.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    ).expanduser() / "voice-agent-v2"
    result.pop("LITELLM_BASE_URL", None)
    result.pop("LITELLM_TOKEN_FILE", None)
    result.update(values)
    result.update({name: str(path) for name, path in private_paths.items()})
    result.update({
        "VOICE_AGENT_INSTANCE_ROOT": str(root),
        "VOICE_AGENT_SHARED_CACHE_ROOT": str(shared_cache),
        "VOICE_AGENT_BUILD_ID": commit,
        "VOICE_AGENT_RELEASE_ID": commit[:24],
        "SLICE6_APP_PUBLIC_URL": f"http://127.0.0.1:{values['VOICE_AGENT_GATEWAY_PORT']}",
        "SLICE6_WEB_DIST": str(release / "source" / "web" / "dist"),
        "XDG_CACHE_HOME": str(root / "cache" / "xdg"),
        "PYTHONPYCACHEPREFIX": str(root / "cache" / "pycache"),
    })
    return python, (str(python), "-B", str(launcher)), result


def launcher_environment(
    *, state_root: Path, instance: str, environment: Mapping[str, str] | None = None,
) -> tuple[Path, tuple[str, ...], dict[str, str]]:
    """Build the fail-closed exact-selected-release environment for one stack."""
    selected = selected_release(state_root, instance)
    if selected is None:
        raise StandError("launcher has no selected immutable release")
    commit, release = selected
    return _launcher_environment_for_release(
        state_root=state_root, instance=instance, commit=commit, release=release,
        environment=os.environ if environment is None else environment,
    )


def exec_launcher(*, state_root: Path, instance: str) -> None:
    """Replace systemd's process with one instance-private complete stack."""
    python, arguments, environment = launcher_environment(
        state_root=state_root, instance=instance,
    )
    os.execve(str(python), arguments, environment)

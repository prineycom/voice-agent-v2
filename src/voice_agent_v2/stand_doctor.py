"""Read-only prerequisite diagnosis for the versioned local stand."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import stat
import subprocess
from typing import Iterable, Mapping, Protocol, Sequence

from .operations import DEFAULT_MANIFEST_RELATIVE, load_operations_manifest

ROOT = Path(__file__).resolve().parents[2]

# These are the Arch packages that provide every host command used below.  The
# selected models, runtimes, hashes and cache roots remain owned by the checked
# operations manifest rather than being duplicated here.
REQUIRED_PACKAGES = (
    "systemd",
    "nodejs",
    "npm",
    "python",
    "git",
    "docker",
    "docker-buildx",
    "fuse-overlayfs",
    "slirp4netns",
    "nvidia-utils",
    "nvidia-container-toolkit",
)
PACKAGE_REMEDIES = {
    "systemd": "sudo -n pacman -Syu --needed systemd",
    "nodejs": "sudo -n pacman -Syu --needed nodejs npm",
    "npm": "sudo -n pacman -Syu --needed nodejs npm",
    "python": "sudo -n pacman -Syu --needed python",
    "git": "sudo -n pacman -Syu --needed git",
    "docker": "sudo -n pacman -Syu --needed docker docker-buildx fuse-overlayfs slirp4netns shadow",
    "docker-buildx": "sudo -n pacman -Syu --needed docker docker-buildx fuse-overlayfs slirp4netns shadow",
    "fuse-overlayfs": "sudo -n pacman -Syu --needed docker docker-buildx fuse-overlayfs slirp4netns shadow",
    "slirp4netns": "sudo -n pacman -Syu --needed docker docker-buildx fuse-overlayfs slirp4netns shadow",
    "nvidia-utils": "sudo -n pacman -Syu --needed nvidia-utils",
    "nvidia-container-toolkit": "sudo -n pacman -Syu --needed nvidia-container-toolkit",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class FileFact:
    exists: bool
    regular: bool = False
    directory: bool = False
    size: int | None = None
    executable: bool = False
    sha256: str | None = None
    writable: bool = False


class Probe(Protocol):
    """The diagnosis has only these inspection capabilities."""

    def command(self, arguments: Sequence[str]) -> CommandResult: ...
    def os_release(self) -> Mapping[str, str]: ...
    def system(self) -> str: ...
    def machine(self) -> str: ...
    def user(self) -> str: ...
    def home(self) -> Path: ...
    def file(self, path: Path, *, resolve_symlink: bool = False) -> FileFact: ...
    def glob(self, pattern: str) -> Sequence[Path]: ...
    def immutable_tree(self, path: Path) -> bool: ...


class SystemProbe:
    """Real probe: subprocess calls and filesystem reads only, never mutation."""

    def command(self, arguments: Sequence[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                tuple(arguments),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env={**os.environ, "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return CommandResult(127, stderr=type(error).__name__)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def os_release(self) -> Mapping[str, str]:
        result: dict[str, str] = {}
        try:
            lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        except OSError:
            return result
        for line in lines:
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
        return result

    def system(self) -> str:
        return platform.system()

    def machine(self) -> str:
        return platform.machine()

    def user(self) -> str:
        return os.environ.get("USER") or str(os.getuid())

    def home(self) -> Path:
        return Path.home()

    def file(self, path: Path, *, resolve_symlink: bool = False) -> FileFact:
        try:
            target = path.resolve() if resolve_symlink else path
            metadata = target.stat()
        except OSError:
            return FileFact(False)
        mode = metadata.st_mode
        regular = stat.S_ISREG(mode)
        digest = None
        if regular:
            value = hashlib.sha256()
            try:
                with target.open("rb") as source:
                    for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                        value.update(chunk)
            except OSError:
                return FileFact(False)
            digest = value.hexdigest()
        return FileFact(
            True,
            regular=regular,
            directory=stat.S_ISDIR(mode),
            size=metadata.st_size,
            executable=bool(mode & 0o111),
            sha256=digest,
            writable=bool(mode & 0o222),
        )

    def glob(self, pattern: str) -> Sequence[Path]:
        return tuple(sorted(Path(value) for value in __import__("glob").glob(pattern)))

    def immutable_tree(self, path: Path) -> bool:
        """Read every cache entry's metadata without following a cache symlink."""
        try:
            root = path.lstat()
        except OSError:
            return False
        if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or root.st_mode & 0o222:
            return False
        try:
            for directory, directories, files in os.walk(path, followlinks=False):
                current = Path(directory)
                for name in (*directories, *files):
                    entry = current / name
                    metadata = entry.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or metadata.st_mode & 0o222:
                        return False
        except OSError:
            return False
        return True


@dataclass(frozen=True)
class Check:
    name: str
    ready: bool
    detail: str
    remedy: str | None = None
    unsupported: bool = False


@dataclass(frozen=True)
class Diagnosis:
    checks: tuple[Check, ...]

    @property
    def status(self) -> str:
        if any(not item.ready and item.unsupported for item in self.checks):
            return "unsupported"
        return "ready" if all(item.ready for item in self.checks) else "incomplete"

    @property
    def exit_code(self) -> int:
        return {"ready": 0, "incomplete": 1, "unsupported": 2}[self.status]

    def render(self) -> str:
        lines = [f"stand doctor: {self.status.upper()}"]
        for check in self.checks:
            state = "usable" if check.ready else "missing"
            lines.append(f"{state}: {check.name}: {check.detail}")
            if not check.ready and check.remedy:
                lines.append(f"  remedy: {check.remedy}")
        return "\n".join(lines)


def _ok_command(probe: Probe, arguments: Sequence[str]) -> tuple[bool, str]:
    result = probe.command(arguments)
    return result.returncode == 0, result.stdout.strip()


def _path(value: str, home: Path) -> Path:
    return Path(value.replace("{home}", str(home))).expanduser()


def _artifact_paths(artifact: Mapping[str, object], probe: Probe, home: Path) -> tuple[Path, ...]:
    if isinstance(artifact.get("path"), str):
        return (_path(str(artifact["path"]), home),)
    pattern = str(artifact.get("path_glob", "")).replace("{home}", str(home))
    return tuple(probe.glob(pattern))


def _artifact_check(artifact: Mapping[str, object], probe: Probe, home: Path) -> Check:
    name = str(artifact["name"])
    paths = _artifact_paths(artifact, probe, home)
    expected_matches = artifact.get("matches", 1)
    if len(paths) != expected_matches:
        return Check(
            f"artifact {name}", False, "selected artifact is absent or has the wrong match count",
            "./setup-slice6  # acquire/verify the pinned cache; it never substitutes a model",
        )
    for path in paths:
        fact = probe.file(path, resolve_symlink=artifact.get("resolve_symlink") is True)
        if not fact.exists or not fact.regular:
            return Check(
                f"artifact {name}", False, "selected artifact is absent or is not a regular file",
                "./setup-slice6  # acquire/verify the pinned cache; it never substitutes a model",
            )
        if isinstance(artifact.get("size_bytes"), int) and fact.size != artifact["size_bytes"]:
            return Check(
                f"artifact {name}", False, "selected artifact size does not match the pinned manifest",
                "./setup-slice6  # restore the exact selected artifact from its pinned source",
            )
        if artifact.get("executable") is True and not fact.executable:
            return Check(
                f"artifact {name}", False, "selected runtime binary is not executable",
                f"chmod a+rx {shlex.quote(str(path))}",
            )
        if fact.writable:
            return Check(
                f"artifact {name}", False, "selected runtime/model artifact is writable",
                f"chmod a-w {shlex.quote(str(path))}",
            )
        if fact.sha256 != artifact.get("sha256"):
            return Check(
                f"artifact {name}", False, "selected artifact checksum does not match the pinned manifest",
                "./setup-slice6  # restore the exact selected artifact from its pinned source",
            )
    return Check(f"artifact {name}", True, "selected artifact matches the pinned manifest")


def _rootless_mapping_check(probe: Probe) -> Check:
    """Verify Arch's newuidmap/newgidmap binaries and their owning package."""
    expected = ("/usr/bin/newuidmap", "/usr/bin/newgidmap")
    for binary in expected:
        result = probe.command(("pacman", "-Qo", binary))
        detail = result.stdout.strip()
        if result.returncode != 0 or " is owned by shadow " not in detail:
            return Check(
                "rootless UID mapping", False,
                f"{binary} is unavailable or is not owned by shadow",
                "sudo -n pacman -Syu --needed shadow",
            )
    return Check("rootless UID mapping", True, "newuidmap and newgidmap are owned by shadow")


def _runtime_check(runtime: Mapping[str, object], probe: Probe, home: Path) -> Check:
    name = str(runtime["name"])
    python = _path(str(runtime["python"]), home)
    packages = runtime.get("packages")
    if not isinstance(packages, dict):
        return Check(f"runtime {name}", False, "pinned Python package declaration is invalid")
    binary = probe.file(python)
    if not binary.exists or not binary.regular or not binary.executable:
        return Check(
            f"runtime {name}", False, "pinned Python runtime is unavailable",
            "./setup-slice6  # create the pinned runtime cache",
        )
    script = (
        "import importlib.metadata as m,json,sys;"
        "wanted=json.loads(sys.argv[1]);"
        "print(json.dumps({n:m.version(n) for n in wanted},sort_keys=True))"
    )
    result = probe.command((str(python), "-I", "-c", script, json.dumps(sorted(packages))))
    try:
        observed = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        observed = None
    if observed != packages:
        return Check(
            f"runtime {name}", False, "pinned Python package set is unavailable or incompatible",
            "./setup-slice6  # recreate the pinned runtime cache",
        )
    return Check(f"runtime {name}", True, "pinned Python package set matches the manifest")


def diagnose(probe: Probe | None = None, *, source_root: Path = ROOT) -> Diagnosis:
    """Inspect all stand prerequisites without changing the host or repository."""

    probe = probe or SystemProbe()
    checks: list[Check] = []
    release = probe.os_release()
    supported_distribution = release.get("ID", "").lower() in {"arch", "endeavouros"}
    checks.append(Check(
        "host distribution", supported_distribution,
        f"ID={release.get('ID', 'unknown')}",
        "install Arch Linux or EndeavourOS x86_64 with NVIDIA",
        unsupported=not supported_distribution,
    ))
    supported_kernel = probe.system() == "Linux"
    checks.append(Check(
        "host kernel", supported_kernel, probe.system() or "unknown",
        "boot a Linux host", unsupported=not supported_kernel,
    ))
    supported_architecture = probe.machine() == "x86_64"
    checks.append(Check(
        "host architecture", supported_architecture, probe.machine() or "unknown",
        "use an x86_64 host", unsupported=not supported_architecture,
    ))

    for package in REQUIRED_PACKAGES:
        present, version = _ok_command(probe, ("pacman", "-Q", package))
        checks.append(Check(
            f"package {package}", present,
            version if present else "not installed",
            None if present else PACKAGE_REMEDIES[package],
        ))
    checks.append(_rootless_mapping_check(probe))

    for name, command, remedy in (
        ("Node.js", ("node", "--version"), "sudo -n pacman -Syu --needed nodejs npm"),
        ("npm", ("npm", "--version"), "sudo -n pacman -Syu --needed nodejs npm"),
        ("Python", ("python3", "--version"), "sudo -n pacman -Syu --needed python"),
        ("Git", ("git", "--version"), "sudo -n pacman -Syu --needed git"),
    ):
        ready, version = _ok_command(probe, command)
        checks.append(Check(name, ready, version if ready else "binary is unavailable", None if ready else remedy))

    nvidia_smi, nvidia_detail = _ok_command(probe, ("nvidia-smi", "-L"))
    checks.append(Check(
        "NVIDIA GPU", nvidia_smi, nvidia_detail if nvidia_smi else "nvidia-smi cannot report an NVIDIA GPU",
        None if nvidia_smi else "sudo -n pacman -Syu --needed nvidia-utils",
        unsupported=not nvidia_smi,
    ))
    container_cli, _ = _ok_command(probe, ("nvidia-container-cli", "--version"))
    checks.append(Check(
        "NVIDIA container runtime binary", container_cli,
        "nvidia-container-cli is usable" if container_cli else "nvidia-container-cli is unavailable",
        None if container_cli else "sudo -n pacman -Syu --needed nvidia-container-toolkit",
    ))

    linger, linger_detail = _ok_command(probe, ("loginctl", "show-user", probe.user(), "-p", "Linger", "--value"))
    linger = linger and linger_detail.lower() == "yes"
    checks.append(Check(
        "user-systemd linger", linger, "enabled" if linger else "not enabled",
        None if linger else f"sudo -n loginctl enable-linger {shlex.quote(probe.user())}",
    ))
    user_systemd, _ = _ok_command(probe, ("systemctl", "--user", "is-active", "docker.service"))
    checks.append(Check(
        "user-systemd Docker service", user_systemd,
        "active" if user_systemd else "not active",
        None if user_systemd else "systemctl --user enable --now docker.service",
    ))
    docker_rootless, docker_options = _ok_command(probe, ("docker", "info", "--format", "{{json .SecurityOptions}}"))
    docker_rootless = docker_rootless and "rootless" in docker_options
    checks.append(Check(
        "rootless Docker", docker_rootless,
        "connected with rootless security option" if docker_rootless else "Docker is installed but the rootless daemon is not ready",
        None if docker_rootless else "dockerd-rootless-setuptool.sh install && systemctl --user enable --now docker.service",
    ))
    nvidia_runtime, runtime_detail = _ok_command(probe, ("docker", "info", "--format", "{{json .Runtimes}}"))
    nvidia_runtime = nvidia_runtime and '"nvidia"' in runtime_detail
    checks.append(Check(
        "Docker NVIDIA runtime", nvidia_runtime,
        "nvidia runtime registered" if nvidia_runtime else "nvidia runtime is not registered with Docker",
        None if nvidia_runtime else "nvidia-ctk runtime configure --runtime=docker --config=\"$HOME/.config/docker/daemon.json\" && systemctl --user restart docker.service",
    ))

    manifest = load_operations_manifest(source_root / DEFAULT_MANIFEST_RELATIVE)
    home = probe.home()
    artifacts = manifest["artifacts"]
    runtimes = manifest["python_runtimes"]
    disk = manifest["disk"]
    assert isinstance(artifacts, list) and isinstance(runtimes, list) and isinstance(disk, dict)
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        checks.append(_artifact_check(artifact, probe, home))
    for runtime in runtimes:
        assert isinstance(runtime, dict)
        checks.append(_runtime_check(runtime, probe, home))

    cache_roots = disk["cache_roots"]
    assert isinstance(cache_roots, list)
    for cache in cache_roots:
        assert isinstance(cache, dict)
        name = str(cache["name"])
        # These are operational state directories, deliberately mutable per
        # instance; every other declared cache holds a shared pinned runtime or
        # model and must be immutable before two stands can share it.
        if name in {"stt-service-state", "silero-state"}:
            continue
        path = _path(str(cache["path"]), home)
        immutable = probe.immutable_tree(path)
        checks.append(Check(
            f"immutable cache {name}", immutable,
            "read-only" if immutable else "missing, not a directory, or writable",
            None if immutable else f"chmod -R a-w {shlex.quote(str(path))}",
        ))
    return Diagnosis(tuple(checks))


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = tuple(arguments or ())
    if arguments != ("doctor",):
        print("usage: stand doctor")
        return 2
    report = diagnose()
    print(report.render())
    return report.exit_code

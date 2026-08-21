"""Docker-only custody for one installation-owned persistent AgentEnvironment.

Every Docker mutation is constructed here from release and installation state.
Model-controlled data is admitted only as stdin to a fixed in-container helper.
There is intentionally no host execution, file-operation, alternate-runtime, or
container-selection fallback.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from typing import Callable, Iterator, Mapping, Protocol
import uuid

from .agent_environment_config import AgentConfigV2Snapshot
from .agent_environment_image import (
    IMAGE_CONTEXT_LABEL, IMAGE_MANAGED_LABEL, IMAGE_SCHEMA_LABEL,
    NativeImageContract, PreparedImageError, PreparedImageSelection,
    load_prepared_image, load_runtime_image_contract,
)
from .agent_environment_credentials import (
    CredentialStore, CredentialStoreError, EmptyCredentialStore, InstallationCredentialStore,
)
from .agent_research import WEB_TOOLS, postprocess_research_output, research_command

REGISTRY_SCHEMA = "voice-agent.agent-environment-registry.v1"
STATUS_SCHEMA = "voice-agent.agent-environment-status.v2"
RECEIPT_SCHEMA = "voice-agent.agent-call-receipt.v2"
MANAGED_LABEL = "io.priney.voice-agent-v2.managed"
SCHEMA_LABEL = "io.priney.voice-agent-v2.schema"
OWNER_LABEL = "io.priney.voice-agent-v2.owner"
SPEC_LABEL = "io.priney.voice-agent-v2.spec"
GENERATION_LABEL = "io.priney.voice-agent-v2.generation"
LABELS = (MANAGED_LABEL, SCHEMA_LABEL, OWNER_LABEL, SPEC_LABEL, GENERATION_LABEL)
HELPERS: Mapping[str, str] = {
    "shell.exec": "shell-exec",
    "file.read": "file-read",
    "file.search": "file-search",
    "file.write": "file-write",
    "file.edit": "file-edit",
    "file.patch": "file-patch",
    "execute_code": "execute-code",
    "process": "process",
    "receipt": "receipt",
    # Convenience Web operations still enter the exact selected container via
    # the image's fixed shell helper; controller-generated source contains no
    # model-controlled shell text and uses no host/alternate runtime.
    "web.search": "shell-exec",
    "web.fetch": "shell-exec",
    "web.extract": "shell-exec",
}
CALL_ID = re.compile(r"^[a-f0-9]{32}$")
CONTAINER_ID = re.compile(r"^[a-f0-9]{12,64}$")
MAX_DOCKER_OUTPUT = 1024 * 1024
DOCKER_TIMEOUT = 15.0
CREDENTIAL_AUTHORITY_WARNING = (
    "Exposed credentials have their full configured authority: container content may read, "
    "persist, transmit, spend quota, alter remote data, and push Git without domain or payload binding."
)
STREAM_ROOTS = {"workspace": "/workspace", "cache": "/cache"}
IMAGE_SOURCE_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TMPFS = {
    "/scratch": "size=1024m,exec,nosuid,nodev",
    "/tmp": "size=512m,exec,nosuid,nodev",
    "/var/tmp": "size=256m,noexec,nosuid,nodev",
    "/run": "size=64m,noexec,nosuid,nodev",
}
PROCESS_RECEIPT = re.compile(r"^[a-f0-9]{32}$")
MAX_BACKGROUND_PROCESSES = 64
MAX_PROCESS_LOG_BYTES = 128 * 1024
MAX_PROCESS_WRITE_BYTES = 64 * 1024
MAX_PROCESS_WAIT_SECONDS = 30.0
MOUNT_AUTHORITY_WARNING = (
    "Read-only mounts are readable and exfiltratable. Read-write mounts may be changed, "
    "deleted, or encrypted. Additional mounts are restart-pinned operator authority."
)
FORBIDDEN_MOUNT_DESTINATIONS = (
    "/workspace", "/cache", "/proc", "/sys", "/dev", "/run", "/var/run",
    "/var/lib/voice-agent", "/usr/local/lib/voice-agent",
)
FORBIDDEN_MOUNT_SOURCES = (
    "/etc", "/proc", "/sys", "/dev", "/boot", "/root",
    "/var/run/docker.sock", "/run/docker.sock",
)
FORBIDDEN_SOURCE_MARKERS = ("docker.sock", "podman.sock", "containerd.sock", "/systemd/", "/dbus/", "/bus/")


class AgentEnvironmentError(RuntimeError):
    """Stable content-free agent-plane failure."""

    def __init__(self, code: str, *, fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = tuple(sorted(set(fields)))
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DockerResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    accepted: bool | None = None


class DockerRunner(Protocol):
    def run(self, arguments: tuple[str, ...], *, stdin: bytes = b"", timeout: float = DOCKER_TIMEOUT) -> DockerResult: ...


class DockerCLI:
    """Fixed Docker CLI transport.  It never invokes a shell or host credential home."""

    _RELEASE_PATHS = (
        "/usr/bin/docker",
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
    )

    def __init__(self, private_home: Path) -> None:
        self.binary = next(
            (path for path in self._RELEASE_PATHS if Path(path).is_file() and os.access(path, os.X_OK)),
            self._RELEASE_PATHS[0],
        )
        self.private_home = private_home

    def run(
        self, arguments: tuple[str, ...], *, stdin: bytes = b"", timeout: float = DOCKER_TIMEOUT
    ) -> DockerResult:
        if not arguments or any(not isinstance(value, str) or "\0" in value for value in arguments):
            raise AgentEnvironmentError("docker_request_invalid")
        self.private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.private_home, 0o700)
        environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": str(self.private_home),
            "DOCKER_CONFIG": str(self.private_home),
        }
        try:
            completed = subprocess.run(
                [self.binary, *arguments],
                input=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise AgentEnvironmentError("docker_runtime_unavailable") from error
        except OSError as error:
            raise AgentEnvironmentError("docker_runtime_unavailable") from error
        if len(completed.stdout) > MAX_DOCKER_OUTPUT or len(completed.stderr) > MAX_DOCKER_OUTPUT:
            raise AgentEnvironmentError("docker_response_out_of_bounds")
        return DockerResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class ContainerFacts:
    container_id: str
    generation: int
    spec: str
    state: str
    labels: Mapping[str, str]
    raw: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MountCustody:
    source: str
    destination: str
    mode: str
    device: int
    inode: int
    owner: int
    permissions: int
    file_type: str

    def spec_document(self) -> dict[str, object]:
        return {
            "source": self.source, "destination": self.destination, "mode": self.mode,
            "device": self.device, "inode": self.inode, "owner": self.owner,
            "permissions": self.permissions, "file_type": self.file_type,
        }


@dataclass(frozen=True, slots=True)
class CallReceipt:
    call_id: str
    container_id: str
    generation: int
    status: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    cwd: str
    replayed: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def document(self) -> dict[str, object]:
        return {
            "schema_version": RECEIPT_SCHEMA,
            "call_id": self.call_id,
            "container_id_prefix": self.container_id[:12],
            "generation": self.generation,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout_base64": base64.b64encode(self.stdout).decode("ascii"),
            "stderr_base64": base64.b64encode(self.stderr).decode("ascii"),
            "cwd": self.cwd,
            "replayed": self.replayed,
            "output": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ByteStreamReceipt:
    transfer_id: str
    direction: str
    container_path: str
    byte_count: int
    sha256: str
    outcome: str
    acknowledged: bool = False
    target: str | None = None

    def document(self) -> dict[str, object]:
        return {
            "transfer_id": self.transfer_id,
            "direction": self.direction,
            "container_path": self.container_path,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "outcome": self.outcome,
            "acknowledged": self.acknowledged,
            "target": self.target,
            "automatic_retry": False,
        }


@dataclass(frozen=True, slots=True)
class TransportAcknowledgement:
    outcome: str
    target: str | None = None
    byte_count: int | None = None
    sha256: str | None = None


def owner_key(installation_uuid: str) -> str:
    digest = hashlib.sha256(b"voice-agent-v2\0" + installation_uuid.encode("ascii")).digest()
    return base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:26]


def _receipt_output_metadata(document: Mapping[str, object], stdout: bytes, stderr: bytes) -> dict[str, object]:
    """Validate bounded, binary-safe helper accounting without retaining content."""
    streams: dict[str, dict[str, object]] = {}
    for name, value in (("stdout", stdout), ("stderr", stderr)):
        byte_count = document.get(f"{name}_bytes", len(value))
        digest = document.get(f"{name}_sha256", hashlib.sha256(value).hexdigest())
        truncated = document.get(f"{name}_truncated", False)
        if (
            type(byte_count) is not int or byte_count < len(value) or byte_count > 64 * 1024 * 1024
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or type(truncated) is not bool
        ):
            raise AgentEnvironmentError("execution_receipt_invalid")
        if not truncated and (byte_count != len(value) or digest != hashlib.sha256(value).hexdigest()):
            raise AgentEnvironmentError("execution_receipt_invalid")
        streams[name] = {"byte_count": byte_count, "sha256": digest, "truncated": truncated}
    details = document.get("details", {})
    try:
        encoded = _canonical(details)
    except (TypeError, ValueError, RecursionError) as error:
        raise AgentEnvironmentError("execution_receipt_invalid") from error
    if not isinstance(details, dict) or len(encoded) > 8 * 1024:
        raise AgentEnvironmentError("execution_receipt_invalid")
    return {"stdout": streams["stdout"], "stderr": streams["stderr"], "details": details}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _atomic_private(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        content = _canonical(value) + b"\n"
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


class AgentEnvironment:
    """Resolve, create, reuse, execute in, and explicitly retire one container."""

    def __init__(
        self,
        config: AgentConfigV2Snapshot,
        *,
        state_root: Path,
        workspace: Path,
        cache: Path,
        runner: DockerRunner | None = None,
        credential_store: CredentialStore | None = None,
        image_state_root: Path | None = None,
        prepared_image: PreparedImageSelection | None = None,
        image_contract: NativeImageContract | None = None,
        disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        required_paths = (state_root, workspace, cache) + ((image_state_root,) if image_state_root is not None else ())
        for path in required_paths:
            if not path.is_absolute():
                raise ValueError("AgentEnvironment paths must be absolute")
        self.config = config
        self.state_root = state_root
        self.workspace = workspace
        self.cache = cache
        self.runner = runner or DockerCLI(state_root / "docker-client")
        self.image_state_root = image_state_root or (state_root / "prepared-image")
        self._prepared_image_override = prepared_image
        self._image_contract = image_contract
        self._pinned_image: PreparedImageSelection | None = None
        declarations = config.model.agent_environment.credentials
        has_credentials = any((
            declarations.creation_environment_names,
            declarations.creation_file_names,
            declarations.exec_environment_names,
            declarations.exec_file_names,
        ))
        self.credential_store = credential_store or (
            InstallationCredentialStore(state_root) if has_credentials else EmptyCredentialStore()
        )
        self.disk_usage = disk_usage
        self.clock = clock
        self._mounts = self._admit_additional_mounts()
        self._credential_lock = threading.Lock()
        self._redaction_values: list[bytes] = []
        credentials = config.model.agent_environment.credentials
        self._credential_initialization_error: str | None = None
        try:
            self._pinned_creation_environment = {
                name: self._credential_value("environment", name)
                for name in credentials.creation_environment_names
            }
            self._pinned_creation_files = {
                name: self._credential_value("file", name)
                for name in credentials.creation_file_names
            }
            if prepared_image is not None:
                self._pinned_image = prepared_image
                self.spec = self._spec_revision(prepared_image)
            else:
                self.spec = self._spec_revision(None)
        except AgentEnvironmentError as error:
            self._pinned_creation_environment = {}
            self._pinned_creation_files = {}
            self._credential_initialization_error = error.code
            self.spec = base64.b32encode(hashlib.sha256(_canonical({
                "schema": 1, "config": config.semantic_revision,
                "credential_private_state": "unavailable",
            })).digest()).decode("ascii").lower().rstrip("=")
        self.registry_path = state_root / "registry.json"
        self.lock_path = state_root / "installation.lock"
        self._thread_lock = threading.RLock()

    @classmethod
    def stop_registered(
        cls, *, state_root: Path, runner: DockerRunner | None = None,
    ) -> str:
        """Stop the exact registered container without removing any persistent state.

        This deliberately does not need the current application configuration: an
        operator must still be able to stop a container after configuration has
        become invalid.  Registry custody, the pinned Docker endpoint, labels,
        generation, spec, and exact container identity are all rechecked under the
        installation lock before and after the stop.
        """
        registry_path = state_root / "registry.json"
        if not registry_path.exists():
            return "absent"
        environment = cls.__new__(cls)
        environment.state_root = state_root
        environment.registry_path = registry_path
        environment.lock_path = state_root / "installation.lock"
        environment.runner = runner or DockerCLI(state_root / "docker-client")
        environment._thread_lock = threading.RLock()
        with environment._locked(create=False):
            registry = environment._registry(create=False)
            if registry.get("selected_container_id") is None:
                return "absent"
            facts = environment._exact_current(registry, allow_stale=True)
            if facts.spec != registry.get("spec"):
                raise AgentEnvironmentError("environment_identity_conflict")
            if facts.state in {"running", "restarting", "paused"}:
                stopped = environment._docker(
                    "container", "stop", "--time", "10", facts.container_id,
                )
                if stopped.returncode != 0:
                    raise AgentEnvironmentError("lifecycle_action_failed")
            fresh = environment._exact_current(registry, allow_stale=True)
            if fresh.spec != registry.get("spec") or fresh.state in {
                "running", "restarting", "paused",
            }:
                raise AgentEnvironmentError("lifecycle_action_failed")
            environment._mark_container_processes_gone(registry, fresh.container_id)
            registry["state"] = "stopped"
            registry["reason_code"] = None
            _atomic_private(environment.registry_path, registry)
            return "stopped"

    @staticmethod
    def _paths_overlap(first: str, second: str) -> bool:
        left = Path(first).parts
        right = Path(second).parts
        return left == right[:len(left)] or right == left[:len(right)]

    def _admit_additional_mounts(self) -> tuple[MountCustody, ...]:
        admitted: list[MountCustody] = []
        home = str(Path.home().resolve())
        protected_sources = tuple(map(str, (self.state_root, self.workspace, self.cache)))
        for declaration in self.config.model.agent_environment.additional_mounts:
            source = Path(declaration.source)
            destination = declaration.destination
            if "," in declaration.source or "," in destination:
                raise AgentEnvironmentError("additional_mount_path_invalid")
            if any(self._paths_overlap(destination, reserved) for reserved in FORBIDDEN_MOUNT_DESTINATIONS):
                raise AgentEnvironmentError("additional_mount_destination_forbidden")
            if any(self._paths_overlap(destination, prior.destination) for prior in admitted):
                raise AgentEnvironmentError("additional_mount_destination_overlap")
            if any(self._paths_overlap(declaration.source, prior.source) for prior in admitted):
                raise AgentEnvironmentError("additional_mount_source_overlap")
            if self._paths_overlap(declaration.source, home) or any(
                self._paths_overlap(declaration.source, forbidden)
                for forbidden in FORBIDDEN_MOUNT_SOURCES
            ) or declaration.source in {"/run", "/var/run"} or any(
                marker in declaration.source for marker in FORBIDDEN_SOURCE_MARKERS
            ) or any(self._paths_overlap(declaration.source, protected) for protected in protected_sources):
                raise AgentEnvironmentError("additional_mount_source_forbidden")
            try:
                metadata = source.lstat()
                resolved = source.resolve(strict=True)
            except OSError as error:
                raise AgentEnvironmentError("additional_mount_custody_invalid") from error
            if (
                str(resolved) != declaration.source or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022
                or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
            ):
                raise AgentEnvironmentError("additional_mount_custody_invalid")
            admitted.append(MountCustody(
                declaration.source, destination, declaration.mode,
                metadata.st_dev, metadata.st_ino, metadata.st_uid, stat.S_IMODE(metadata.st_mode),
                "directory" if stat.S_ISDIR(metadata.st_mode) else "file",
            ))
        return tuple(admitted)

    def _mount_custody_valid(self) -> bool:
        for mount in self._mounts:
            try:
                metadata = Path(mount.source).lstat()
                resolved = Path(mount.source).resolve(strict=True)
            except OSError:
                return False
            if (
                str(resolved) != mount.source or metadata.st_dev != mount.device
                or metadata.st_ino != mount.inode or metadata.st_uid != mount.owner
                or stat.S_IMODE(metadata.st_mode) != mount.permissions or stat.S_ISLNK(metadata.st_mode)
                or ("directory" if stat.S_ISDIR(metadata.st_mode) else "file") != mount.file_type
            ):
                return False
        return True

    def _require_mount_custody(self) -> None:
        if not self._mount_custody_valid():
            raise AgentEnvironmentError("additional_mount_custody_changed")

    def _credential_value(self, kind: str, name: str) -> bytes:
        try:
            value = self.credential_store.resolve(kind, name)
        except CredentialStoreError as error:
            raise AgentEnvironmentError(str(error)) from None
        if not isinstance(value, bytes) or len(value) > 64 * 1024:
            raise AgentEnvironmentError("credential_value_invalid")
        if kind == "environment":
            try:
                value.decode("utf-8")
            except UnicodeError:
                raise AgentEnvironmentError("credential_value_invalid") from None
            if len(value) > 16 * 1024 or any(marker in value for marker in (b"\0", b"\n", b"\r")):
                raise AgentEnvironmentError("credential_value_invalid")
        elif kind != "file":
            raise AgentEnvironmentError("credential_kind_invalid")
        if value:
            with self._credential_lock:
                if value not in self._redaction_values:
                    self._redaction_values.append(value)
                    self._redaction_values = self._redaction_values[-128:]
        return value

    def _credential_fingerprint_bytes(self, kind: str, name: str, value: bytes) -> str:
        """Fingerprint the pinned snapshot, rejecting a store that changed during composition."""
        try:
            fingerprint = self.credential_store.fingerprint(kind, name)
            if self.credential_store.resolve(kind, name) != value:
                raise AgentEnvironmentError("credential_snapshot_changed")
            return fingerprint
        except CredentialStoreError as error:
            raise AgentEnvironmentError(str(error)) from None

    def _verify_prepared_image(self, image: PreparedImageSelection) -> None:
        """Freshly prove exact immutable image custody before container resolution."""

        result = self._docker("image", "inspect", image.image_id)
        if result.returncode != 0:
            raise AgentEnvironmentError("prepared_image_missing")
        try:
            documents = json.loads(result.stdout)
            raw = documents[0]
            config = raw["Config"]
            labels = config["Labels"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise AgentEnvironmentError("prepared_image_inspection_failed") from error
        expected_labels = {
            IMAGE_MANAGED_LABEL: "1",
            IMAGE_SCHEMA_LABEL: "1",
            IMAGE_CONTEXT_LABEL: image.context_revision,
        }
        if (
            raw.get("Id") != image.image_id
            or raw.get("Os") != "linux"
            or raw.get("Architecture") != image.architecture
            or image.platform != f"linux/{image.architecture}"
            or config.get("User") != "1000:1000"
            or config.get("Entrypoint") != ["/sbin/tini", "--"]
            or config.get("Cmd") != ["/usr/local/lib/voice-agent/agent-helper", "init-container"]
            or labels != expected_labels
            or not isinstance(raw.get("RootFS"), dict)
            or not raw["RootFS"].get("Layers")
        ):
            raise AgentEnvironmentError("prepared_image_mismatch")

    def _pin_prepared_image(self) -> PreparedImageSelection:
        try:
            if self._prepared_image_override is not None:
                selected = self._prepared_image_override
            else:
                contract = self._image_contract or load_runtime_image_contract(IMAGE_SOURCE_ROOT)
                selected = load_prepared_image(self.image_state_root, contract).selected
        except PreparedImageError as error:
            raise AgentEnvironmentError(error.code) from None
        if self._pinned_image is not None:
            if self._pinned_image != selected:
                raise AgentEnvironmentError("image_preparation_changed")
            self._verify_prepared_image(selected)
            return selected
        self._verify_prepared_image(selected)
        self._pinned_image = selected
        self.spec = self._spec_revision(selected)
        return selected

    def _spec_revision(self, image: PreparedImageSelection | None) -> str:
        model = self.config.model
        credentials = model.agent_environment.credentials
        creation_fingerprints = {
            "environment": {
                name: self._credential_fingerprint_bytes("environment", name, self._pinned_creation_environment[name])
                for name in credentials.creation_environment_names
            },
            "files": {
                name: self._credential_fingerprint_bytes("file", name, self._pinned_creation_files[name])
                for name in credentials.creation_file_names
            },
        }
        document = {
            "schema": 1,
            "image": {
                "configuration": model.agent_environment.image.model_dump(mode="json"),
                "context_revision": image.context_revision if image else "unavailable",
                "image_id": image.image_id if image else "unavailable",
                "platform": image.platform if image else "unavailable",
            },
            "workspace": str(self.workspace),
            "cache": str(self.cache),
            "mounts": {
                "managed": {"/workspace": "rw", "/cache": "rw"},
                "additional": [mount.spec_document() for mount in self._mounts],
            },
            "network": model.agent_environment.network.model_dump(mode="json"),
            "credentials": {
                "declaration": model.agent_environment.credentials.model_dump(mode="json"),
                "creation_fingerprints": creation_fingerprints,
            },
            "user": "1000:1000",
            "restart": "no",
            "cap_drop": ["ALL"],
            "cap_add": [],
            "no_new_privileges": True,
            "resources": model.agent_environment.resources.model_dump(mode="json"),
            "tmpfs": EXPECTED_TMPFS,
        }
        return base64.b32encode(hashlib.sha256(_canonical(document)).digest()).decode("ascii").lower().rstrip("=")

    @staticmethod
    def _verify_private_directory(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise AgentEnvironmentError("environment_state_unsafe") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AgentEnvironmentError("environment_state_unsafe")

    @contextmanager
    def _locked(self, *, create: bool) -> Iterator[None]:
        with self._thread_lock:
            if create and not self.state_root.exists():
                self.state_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            if not self.state_root.exists():
                raise AgentEnvironmentError("environment_absent")
            self._verify_private_directory(self.state_root)
            flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                descriptor = os.open(self.lock_path, flags, 0o600)
            except FileNotFoundError as error:
                raise AgentEnvironmentError("environment_absent") from error
            try:
                os.fchmod(descriptor, 0o600)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink != 1
                ):
                    raise AgentEnvironmentError("environment_state_unsafe")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _new_registry(self) -> dict[str, object]:
        installation = str(uuid.UUID(bytes=secrets.token_bytes(16)))
        return {
            "schema_version": REGISTRY_SCHEMA,
            "installation_uuid": installation,
            "owner_key": owner_key(installation),
            "endpoint_fingerprint": None,
            "selected_container_id": None,
            "generation": 0,
            "spec": self.spec,
            "retained": [],
            "calls": {},
            "processes": {},
            "logical_cwd": "/workspace",
            "state": "absent",
            "reason_code": None,
        }

    def _registry(self, *, create: bool) -> dict[str, object]:
        if not self.registry_path.exists():
            if not create:
                raise AgentEnvironmentError("environment_absent")
            registry = self._new_registry()
            _atomic_private(self.registry_path, registry)
            return registry
        try:
            metadata = self.registry_path.lstat()
            document = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise AgentEnvironmentError("environment_registry_invalid") from error
        required = {
            "schema_version", "installation_uuid", "owner_key", "endpoint_fingerprint",
            "selected_container_id", "generation", "spec", "retained", "calls", "processes",
            "logical_cwd", "state", "reason_code",
        }
        historical = required - {"processes"}
        if isinstance(document, dict) and set(document) == historical:
            # Safe in-place registry evolution: old state gains no process authority.
            document["processes"] = {}
            _atomic_private(self.registry_path, document)
        if (
            not isinstance(document, dict)
            or set(document) != required
            or document.get("schema_version") != REGISTRY_SCHEMA
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or document.get("owner_key") != owner_key(str(document.get("installation_uuid")))
            or not isinstance(document.get("retained"), list)
            or not isinstance(document.get("calls"), dict)
            or not isinstance(document.get("processes"), dict)
        ):
            raise AgentEnvironmentError("environment_registry_invalid")
        return document

    def _docker(self, *arguments: str, stdin: bytes = b"", timeout: float = DOCKER_TIMEOUT) -> DockerResult:
        result = self.runner.run(tuple(arguments), stdin=stdin, timeout=timeout)
        if len(result.stdout) > MAX_DOCKER_OUTPUT or len(result.stderr) > MAX_DOCKER_OUTPUT:
            raise AgentEnvironmentError("docker_response_out_of_bounds")
        return result

    def _endpoint(self, registry: dict[str, object], *, pin: bool) -> str:
        context = self._docker("context", "show")
        server = self._docker("version", "--format", "{{json .Server}}")
        if context.returncode != 0 or server.returncode != 0 or not context.stdout.strip() or not server.stdout.strip():
            raise AgentEnvironmentError("docker_runtime_unavailable")
        try:
            server_identity = json.loads(server.stdout)
            engine_id = server_identity["ID"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise AgentEnvironmentError("docker_runtime_unavailable") from error
        if not isinstance(engine_id, str) or not engine_id or len(engine_id.encode("utf-8")) > 256:
            raise AgentEnvironmentError("docker_runtime_unavailable")
        fingerprint = hashlib.sha256(
            context.stdout.strip() + b"\0" + engine_id.encode("utf-8")
        ).hexdigest()
        pinned = registry.get("endpoint_fingerprint")
        if pinned is None and pin:
            registry["endpoint_fingerprint"] = fingerprint
            _atomic_private(self.registry_path, registry)
        elif pinned != fingerprint:
            raise AgentEnvironmentError("docker_endpoint_changed")
        return fingerprint

    def _candidate_ids(self, registry: Mapping[str, object]) -> tuple[str, ...]:
        owner = str(registry["owner_key"])
        result = self._docker(
            "container", "ls", "-a", "--no-trunc",
            "--filter", f"label={MANAGED_LABEL}=1",
            "--filter", f"label={OWNER_LABEL}={owner}",
            "--format", "{{.ID}}",
        )
        if result.returncode != 0:
            raise AgentEnvironmentError("docker_runtime_unavailable")
        ids = tuple(line.strip() for line in result.stdout.decode("ascii", "strict").splitlines() if line.strip())
        if len(ids) != len(set(ids)) or any(not CONTAINER_ID.fullmatch(value) for value in ids):
            raise AgentEnvironmentError("docker_truth_uncertain")
        return ids

    def _inspect(self, container_id: str) -> ContainerFacts:
        if not CONTAINER_ID.fullmatch(container_id):
            raise AgentEnvironmentError("environment_identity_invalid")
        result = self._docker("container", "inspect", container_id)
        if result.returncode != 0:
            raise AgentEnvironmentError("docker_truth_uncertain")
        try:
            documents = json.loads(result.stdout)
            raw = documents[0]
            labels = raw["Config"]["Labels"]
            state = raw["State"]["Status"]
            exact_id = raw["Id"]
            generation = int(labels[GENERATION_LABEL])
            spec = labels[SPEC_LABEL]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AgentEnvironmentError("docker_truth_uncertain") from error
        if not isinstance(raw, dict) or not isinstance(labels, dict) or exact_id != container_id:
            raise AgentEnvironmentError("docker_truth_uncertain")
        return ContainerFacts(container_id, generation, str(spec), str(state), labels, raw)

    def _creation_environment(self) -> dict[str, str]:
        credentials = self.config.model.agent_environment.credentials
        result = {
            name: self._pinned_creation_environment[name].decode("utf-8")
            for name in credentials.creation_environment_names
        }
        for index, name in enumerate(credentials.creation_file_names):
            result[name] = f"/var/lib/voice-agent/credentials/{name}"
            result[f"VOICE_AGENT_CREATE_FILE_NAME_{index}"] = name
            result[f"VOICE_AGENT_CREATE_FILE_B64_{index}"] = base64.b64encode(
                self._pinned_creation_files[name]
            ).decode("ascii")
        if credentials.creation_file_names:
            result["VOICE_AGENT_CREATE_FILE_COUNT"] = str(len(credentials.creation_file_names))
        if credentials.creation_environment_names:
            result["VOICE_AGENT_CREATE_ENV_NAMES"] = ",".join(credentials.creation_environment_names)
        if credentials.creation_file_names:
            result["VOICE_AGENT_CREATE_FILE_NAMES"] = ",".join(credentials.creation_file_names)
        return result

    def _effective_mismatches(self, facts: ContainerFacts, registry: Mapping[str, object]) -> tuple[str, ...]:
        expected_labels = {
            MANAGED_LABEL: "1", SCHEMA_LABEL: "1", OWNER_LABEL: str(registry["owner_key"]),
            SPEC_LABEL: self.spec, GENERATION_LABEL: str(facts.generation),
        }
        mismatches: list[str] = []
        if any(facts.labels.get(key) != value for key, value in expected_labels.items()):
            mismatches.append("labels")
        raw = facts.raw
        host = raw.get("HostConfig", {}) if isinstance(raw, dict) else {}
        config = raw.get("Config", {}) if isinstance(raw, dict) else {}
        mounts = raw.get("Mounts", []) if isinstance(raw, dict) else []
        resource = self.config.model.agent_environment.resources
        network = self.config.model.agent_environment.network
        expected_mounts = {
            (str(self.workspace), "/workspace", True), (str(self.cache), "/cache", True),
            *((mount.source, mount.destination, mount.mode == "read_write") for mount in self._mounts),
        }
        observed_mounts = {
            (str(item.get("Source")), str(item.get("Destination")), bool(item.get("RW")))
            for item in mounts if isinstance(item, dict) and item.get("Type") == "bind"
        }
        expected_image = self._pinned_image.image_id if self._pinned_image is not None else None
        checks = {
            "image": expected_image is not None and config.get("Image") == expected_image and raw.get("Image") == expected_image,
            "platform": raw.get("Platform") == "linux",
            "user": config.get("User") == "1000:1000",
            "restart_policy": isinstance(host.get("RestartPolicy"), dict) and host["RestartPolicy"].get("Name") == "no",
            "resources": host.get("NanoCpus") == resource.cpus * 1_000_000_000
                and host.get("Memory") == resource.memory_mib * 1024 * 1024
                and host.get("PidsLimit") == resource.pids
                and host.get("ShmSize") == resource.shm_mib * 1024 * 1024,
            "security": host.get("CapDrop") == ["ALL"] and host.get("CapAdd") in (None, [])
                and "no-new-privileges" in (host.get("SecurityOpt") or [])
                and not host.get("Privileged") and host.get("PidMode", "") == ""
                and host.get("IpcMode") not in {"host"} and host.get("UTSMode", "") != "host"
                and not host.get("Devices"),
            "network": host.get("NetworkMode") == ("bridge" if network.enabled else "none"),
            "credentials": self._environment_contains(
                self._observed_environment(config.get("Env")), self._creation_environment()
            ),
            "tmpfs": host.get("Tmpfs") == EXPECTED_TMPFS,
            "mounts": observed_mounts == expected_mounts,
            "mount_custody": self._mount_custody_valid(),
            "ports": not config.get("ExposedPorts") and not host.get("PortBindings"),
        }
        mismatches.extend(name for name, passed in checks.items() if not passed)
        return tuple(sorted(mismatches))

    @staticmethod
    def _environment_contains(observed: Mapping[str, str], expected: Mapping[str, str]) -> bool:
        return "!invalid" not in observed and all(observed.get(name) == value for name, value in expected.items())

    @staticmethod
    def _observed_environment(value: object) -> dict[str, str]:
        if value in (None, []):
            return {}
        if not isinstance(value, list) or any(not isinstance(item, str) or "=" not in item for item in value):
            return {"!invalid": "1"}
        return dict(item.split("=", 1) for item in value)

    def _all_facts(self, registry: Mapping[str, object]) -> tuple[ContainerFacts, ...]:
        facts = tuple(self._inspect(value) for value in self._candidate_ids(registry))
        retained = {str(item["container_id"]) for item in registry["retained"] if isinstance(item, dict) and "container_id" in item}
        current = tuple(item for item in facts if item.container_id not in retained)
        compatible = tuple(item for item in current if item.spec == self.spec and not self._effective_mismatches(item, registry))
        if len(compatible) > 1 or len(current) > 1:
            raise AgentEnvironmentError("environment_identity_conflict")
        return facts

    def _resolve(self, registry: dict[str, object]) -> ContainerFacts | None:
        facts = self._all_facts(registry)
        observed = {item.container_id: item for item in facts}
        for record in registry["retained"]:
            if isinstance(record, dict) and str(record.get("container_id")) in observed:
                record["state"] = observed[str(record["container_id"])].state
        retained = {str(item["container_id"]) for item in registry["retained"] if isinstance(item, dict)}
        current = tuple(item for item in facts if item.container_id not in retained)
        selected = registry.get("selected_container_id")
        if selected is not None:
            matches = tuple(item for item in current if item.container_id == selected)
            if len(matches) != 1:
                raise AgentEnvironmentError("environment_selected_missing")
            facts_one = matches[0]
        elif not current:
            registry["state"] = "absent"
            registry["reason_code"] = None
            _atomic_private(self.registry_path, registry)
            return None
        else:
            facts_one = current[0]
        mismatches = self._effective_mismatches(facts_one, registry)
        if facts_one.spec != self.spec or mismatches:
            registry["state"] = "stale_spec"
            registry["reason_code"] = "spec_tampered_or_drifted" if mismatches else "configured_spec_changed"
            _atomic_private(self.registry_path, registry)
            raise AgentEnvironmentError("stale_spec", fields=mismatches or ("spec",))
        if selected is None:
            registry["selected_container_id"] = facts_one.container_id
            registry["generation"] = facts_one.generation
            registry["spec"] = self.spec
        preserve_resource_stop = (
            registry.get("state") == "resource_stopped"
            and facts_one.state in {"created", "exited"}
        )
        registry["state"] = "resource_stopped" if preserve_resource_stop else self._state(facts_one.state)
        if facts_one.state in {"created", "exited", "dead"}:
            self._mark_container_processes_gone(registry, facts_one.container_id)
        if not preserve_resource_stop:
            registry["reason_code"] = None
        _atomic_private(self.registry_path, registry)
        return facts_one

    @staticmethod
    def _mark_container_processes_gone(registry: dict[str, object], container_id: str) -> None:
        processes = registry.get("processes", {})
        if not isinstance(processes, dict):
            return
        for claim in processes.values():
            if isinstance(claim, dict) and claim.get("container_id") == container_id:
                claim["last_state"] = "gone"

    @staticmethod
    def _state(value: str) -> str:
        return {
            "created": "stopped", "exited": "exited", "running": "running",
            "restarting": "restarting", "paused": "unhealthy", "dead": "unhealthy",
            "removing": "retiring",
        }.get(value, "unavailable")

    def _prepare_bind_roots(self) -> None:
        self._require_mount_custody()
        for path in (self.workspace, self.cache):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise AgentEnvironmentError("managed_bind_unsafe")
            os.chmod(path, 0o700)

    def _create_arguments(
        self, registry: Mapping[str, object], generation: int, *, environment_file: Path | None = None
    ) -> tuple[str, ...]:
        resource = self.config.model.agent_environment.resources
        network = self.config.model.agent_environment.network
        owner = str(registry["owner_key"])
        name = f"voice-agent-v2-{owner[:12]}-g{generation}"
        labels = (
            f"{MANAGED_LABEL}=1", f"{SCHEMA_LABEL}=1", f"{OWNER_LABEL}={owner}",
            f"{SPEC_LABEL}={self.spec}", f"{GENERATION_LABEL}={generation}",
        )
        arguments: list[str] = ["container", "create", "--name", name]
        for label in labels:
            arguments.extend(("--label", label))
        creation_environment = self._creation_environment()
        private_environment_names = set(
            self.config.model.agent_environment.credentials.creation_environment_names
        ) | {name for name in creation_environment if name.startswith("VOICE_AGENT_CREATE_FILE_B64_")}
        for name, value in sorted(creation_environment.items()):
            if name not in private_environment_names:
                arguments.extend(("--env", f"{name}={value}"))
        if environment_file is not None:
            arguments.extend(("--env-file", str(environment_file)))
        arguments.extend((
            "--restart", "no", "--cpus", str(resource.cpus), "--memory", f"{resource.memory_mib}m",
            "--pids-limit", str(resource.pids), "--shm-size", f"{resource.shm_mib}m",
            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--network", "bridge" if network.enabled else "none",
            "--mount", f"type=bind,src={self.workspace},dst=/workspace,rw",
            "--mount", f"type=bind,src={self.cache},dst=/cache,rw",
        ))
        for mount in self._mounts:
            mode = "readonly" if mount.mode == "read_only" else "rw"
            arguments.extend((
                "--mount", f"type=bind,src={mount.source},dst={mount.destination},{mode}",
            ))
        arguments.extend((
            "--tmpfs", "/scratch:size=1024m,exec,nosuid,nodev",
            "--tmpfs", "/tmp:size=512m,exec,nosuid,nodev",
            "--tmpfs", "/var/tmp:size=256m,noexec,nosuid,nodev",
            "--tmpfs", "/run:size=64m,noexec,nosuid,nodev",
            self._pinned_image.image_id if self._pinned_image is not None else "image-preparation-unavailable",
        ))
        return tuple(arguments)

    def _readiness(self, facts: ContainerFacts) -> None:
        result = self._docker(
            "container", "exec", facts.container_id,
            "/usr/local/lib/voice-agent/agent-helper", "readiness",
        )
        if result.returncode != 0 or result.stdout.strip() != b'{"ready":true,"schema":1}':
            raise AgentEnvironmentError("environment_unhealthy")

    def _start(self, registry: dict[str, object], facts: ContainerFacts) -> ContainerFacts:
        self._mark_container_processes_gone(registry, facts.container_id)
        registry["state"] = "starting"
        _atomic_private(self.registry_path, registry)
        result = self._docker("container", "start", facts.container_id)
        if result.returncode != 0:
            registry["state"] = "unhealthy"
            registry["reason_code"] = "container_start_failed"
            _atomic_private(self.registry_path, registry)
            raise AgentEnvironmentError("environment_unhealthy")
        fresh = self._inspect(facts.container_id)
        if fresh.state != "running" or self._effective_mismatches(fresh, registry):
            raise AgentEnvironmentError("environment_unhealthy")
        self._readiness(fresh)
        registry["state"] = "running"
        registry["reason_code"] = None
        _atomic_private(self.registry_path, registry)
        return fresh

    def _create(self, registry: dict[str, object]) -> ContainerFacts:
        self._prepare_bind_roots()
        generation = int(registry["generation"]) + 1
        registry["state"] = "creating"
        registry["generation"] = generation
        _atomic_private(self.registry_path, registry)
        environment_file: Path | None = None
        try:
            names = self.config.model.agent_environment.credentials.creation_environment_names
            creation_environment = self._creation_environment()
            private_names = list(names) + sorted(
                name for name in creation_environment if name.startswith("VOICE_AGENT_CREATE_FILE_B64_")
            )
            if private_names:
                descriptor, file_name = tempfile.mkstemp(prefix=".create-environment.", dir=self.state_root)
                environment_file = Path(file_name)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    for name in private_names:
                        output.write(name.encode("ascii") + b"=" + creation_environment[name].encode("utf-8") + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
            result = self._docker(*self._create_arguments(registry, generation, environment_file=environment_file))
        finally:
            if environment_file is not None:
                environment_file.unlink(missing_ok=True)
        candidate = result.stdout.decode("ascii", "ignore").strip()
        if result.returncode != 0 or not CONTAINER_ID.fullmatch(candidate):
            registry["state"] = "unavailable"
            registry["reason_code"] = "container_create_failed"
            _atomic_private(self.registry_path, registry)
            code = "additional_mount_creation_rejected" if self._mounts else "environment_creation_failed"
            raise AgentEnvironmentError(code)
        facts = self._inspect(candidate)
        mismatch = self._effective_mismatches(facts, registry)
        if mismatch or facts.generation != generation:
            registry["state"] = "unavailable"
            registry["reason_code"] = "created_container_inspection_failed"
            _atomic_private(self.registry_path, registry)
            raise AgentEnvironmentError("environment_inspection_failed", fields=mismatch)
        started = self._start(registry, facts)
        registry["selected_container_id"] = candidate
        registry["spec"] = self.spec
        registry["state"] = "running"
        _atomic_private(self.registry_path, registry)
        return started

    def _create_unselected_rebuild(self, registry: dict[str, object], generation: int) -> ContainerFacts:
        """Create and validate a next generation without changing current selection."""
        self._prepare_bind_roots()
        environment_file: Path | None = None
        candidate: str | None = None
        try:
            names = self.config.model.agent_environment.credentials.creation_environment_names
            creation_environment = self._creation_environment()
            private_names = list(names) + sorted(
                name for name in creation_environment if name.startswith("VOICE_AGENT_CREATE_FILE_B64_")
            )
            if private_names:
                descriptor, file_name = tempfile.mkstemp(prefix=".rebuild-environment.", dir=self.state_root)
                environment_file = Path(file_name)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    for name in private_names:
                        output.write(name.encode("ascii") + b"=" + creation_environment[name].encode("utf-8") + b"\n")
                    output.flush(); os.fsync(output.fileno())
            result = self._docker(*self._create_arguments(registry, generation, environment_file=environment_file))
            candidate = result.stdout.decode("ascii", "ignore").strip()
            if result.returncode != 0 or not CONTAINER_ID.fullmatch(candidate):
                code = "additional_mount_creation_rejected" if self._mounts else "environment_creation_failed"
                raise AgentEnvironmentError(code)
            facts = self._inspect(candidate)
            mismatch = self._effective_mismatches(facts, registry)
            if mismatch or facts.generation != generation:
                raise AgentEnvironmentError("environment_inspection_failed", fields=mismatch)
            started = self._docker("container", "start", candidate)
            if started.returncode != 0:
                raise AgentEnvironmentError("environment_unhealthy")
            fresh = self._inspect(candidate)
            if fresh.state != "running" or fresh.generation != generation or self._effective_mismatches(fresh, registry):
                raise AgentEnvironmentError("environment_unhealthy")
            self._readiness(fresh)
            return fresh
        except AgentEnvironmentError:
            if candidate and CONTAINER_ID.fullmatch(candidate):
                cleaned = False
                recovery: ContainerFacts | None = None
                try:
                    fresh = self._inspect(candidate)
                    exact = (
                        fresh.labels.get(MANAGED_LABEL) == "1"
                        and fresh.labels.get(SCHEMA_LABEL) == "1"
                        and fresh.labels.get(OWNER_LABEL) == registry["owner_key"]
                        and fresh.labels.get(SPEC_LABEL) == self.spec
                        and fresh.generation == generation
                    )
                    if exact:
                        recovery = fresh
                        if fresh.state == "running":
                            stopped = self._docker("container", "stop", "--time", "5", candidate)
                            if stopped.returncode == 0:
                                recovery = self._inspect(candidate)
                        if recovery.state != "running":
                            cleaned = self._docker("container", "rm", candidate).returncode == 0
                except AgentEnvironmentError:
                    recovery = None
                if recovery is not None and not cleaned and not any(
                    isinstance(record, dict) and record.get("container_id") == candidate
                    for record in registry["retained"]
                ):
                    registry["retained"].append({
                        "container_id": candidate, "generation": generation, "spec": self.spec,
                        "state": recovery.state,
                    })
                    _atomic_private(self.registry_path, registry)
            raise
        finally:
            if environment_file is not None:
                environment_file.unlink(missing_ok=True)

    def ensure_running(self) -> ContainerFacts:
        if self._credential_initialization_error is not None:
            raise AgentEnvironmentError(self._credential_initialization_error)
        self._pin_prepared_image()
        with self._locked(create=True):
            registry = self._registry(create=True)
            self._endpoint(registry, pin=True)
            facts = self._resolve(registry)
            if facts is None:
                return self._create(registry)
            if facts.state in {"created", "exited"}:
                return self._start(registry, facts)
            if facts.state == "running":
                self._readiness(facts)
                return facts
            if facts.state == "restarting":
                raise AgentEnvironmentError("environment_restarting")
            raise AgentEnvironmentError("environment_unhealthy")

    def _free_reserve(self, facts: ContainerFacts) -> None:
        minimum = self.config.model.agent_environment.resources.host_free_reserve_mib * 1024 * 1024
        observed = min(self.disk_usage(path).free for path in (self.state_root, self.workspace, self.cache))
        if observed >= minimum:
            return
        with self._locked(create=True):
            registry = self._registry(create=False)
            self._endpoint(registry, pin=False)
            fresh = self._inspect(facts.container_id)
            if (
                fresh.container_id != registry.get("selected_container_id")
                or self._effective_mismatches(fresh, registry)
            ):
                raise AgentEnvironmentError("environment_identity_conflict")
            stopped = self._docker("container", "stop", "--time", "5", fresh.container_id)
            after = self._inspect(fresh.container_id)
            if (
                stopped.returncode != 0 or after.state == "running"
                or after.labels.get(OWNER_LABEL) != registry["owner_key"]
                or after.generation != registry["generation"]
            ):
                registry["state"] = "unhealthy"
                registry["reason_code"] = "resource_stop_outcome_unknown"
                _atomic_private(self.registry_path, registry)
                raise AgentEnvironmentError("resource_stop_outcome_unknown")
            self._mark_container_processes_gone(registry, fresh.container_id)
            registry["state"] = "resource_stopped"
            registry["reason_code"] = "host_free_reserve_breached"
            _atomic_private(self.registry_path, registry)
        raise AgentEnvironmentError("host_free_reserve_breached")

    def _exec_credential_arguments(self, call_id: str, facts: ContainerFacts) -> tuple[str, ...]:
        credentials = self.config.model.agent_environment.credentials
        values: dict[str, str] = {}
        for name in credentials.exec_environment_names:
            values[name] = self._credential_value("environment", name).decode("utf-8")
        for name in credentials.exec_file_names:
            content = self._credential_value("file", name)
            result = self._docker(
                "container", "exec", "-i", facts.container_id,
                "/usr/local/lib/voice-agent/agent-helper", "credential-file-put",
                "--scope", "exec", "--call-id", call_id, "--name", name,
                "--expected-bytes", str(len(content)),
                "--expected-sha256", hashlib.sha256(content).hexdigest(),
                stdin=content,
            )
            if result.returncode != 0:
                self._cleanup_exec_credentials(call_id, facts.container_id)
                raise AgentEnvironmentError("credential_injection_failed")
            values[name] = f"/run/voice-agent-credentials/{call_id}/{name}"
        if credentials.exec_environment_names:
            values["VOICE_AGENT_EXEC_ENV_NAMES"] = ",".join(credentials.exec_environment_names)
        if credentials.exec_file_names:
            values["VOICE_AGENT_EXEC_FILE_NAMES"] = ",".join(credentials.exec_file_names)
        arguments: list[str] = []
        for name, value in sorted(values.items()):
            arguments.extend(("-e", f"{name}={value}"))
        return tuple(arguments)

    def _cleanup_exec_credentials(self, call_id: str, container_id: str) -> bool:
        if not self.config.model.agent_environment.credentials.exec_file_names:
            return True
        try:
            result = self._docker(
                "container", "exec", container_id,
                "/usr/local/lib/voice-agent/agent-helper", "credential-file-clean",
                "--call-id", call_id,
            )
            return result.returncode == 0
        except AgentEnvironmentError:
            return False

    def _operation_payload(
        self, tool_id: str, arguments: Mapping[str, object], facts: ContainerFacts,
    ) -> tuple[bytes, str | None, str | None]:
        data = dict(arguments)
        if any(not isinstance(key, str) or key.startswith("_voice_agent_") for key in data):
            raise AgentEnvironmentError("operation_arguments_invalid")
        if tool_id in WEB_TOOLS:
            try:
                data = {"command": research_command(tool_id, data)}
            except ValueError as error:
                raise AgentEnvironmentError(str(error)) from None
        background = data.get("background", False)
        if type(background) is not bool:
            raise AgentEnvironmentError("background_mode_invalid")
        process_receipt: str | None = None
        process_action: str | None = None
        if background:
            if tool_id not in {"shell.exec", "execute_code"}:
                raise AgentEnvironmentError("background_operation_unsupported")
            process_receipt = uuid.uuid4().hex
            data["_voice_agent_process_receipt"] = process_receipt
            data["_voice_agent_owner"] = None  # filled from private custody below
            data["_voice_agent_spec"] = self.spec
            data["_voice_agent_generation"] = facts.generation
        elif tool_id in {"process", "receipt"}:
            process_action = str(data.get("action", "poll" if tool_id == "receipt" else ""))
            if process_action not in {"poll", "logs", "wait", "write", "kill"}:
                raise AgentEnvironmentError("process_action_invalid")
            process_receipt = data.get("receipt") if isinstance(data.get("receipt"), str) else None
            if process_receipt is None or not PROCESS_RECEIPT.fullmatch(process_receipt):
                raise AgentEnvironmentError("process_receipt_invalid")
            with self._locked(create=True):
                registry = self._registry(create=False)
                claim = registry["processes"].get(process_receipt)
                if not isinstance(claim, dict):
                    raise AgentEnvironmentError("process_receipt_unknown")
                if (
                    claim.get("container_id") != facts.container_id
                    or claim.get("generation") != facts.generation
                    or claim.get("spec") != self.spec
                    or claim.get("owner") != registry.get("owner_key")
                    or not isinstance(claim.get("identity"), dict)
                ):
                    raise AgentEnvironmentError("process_identity_unknown")
                data["_voice_agent_owner"] = registry["owner_key"]
                data["_voice_agent_spec"] = self.spec
                data["_voice_agent_generation"] = facts.generation
                data["_voice_agent_expected_identity"] = claim["identity"]
            wait_seconds = data.get("timeout_seconds", 0)
            if type(wait_seconds) not in {int, float} or not 0 <= float(wait_seconds) <= MAX_PROCESS_WAIT_SECONDS:
                raise AgentEnvironmentError("process_wait_out_of_bounds")
            if process_action == "write":
                encoded = data.get("data_base64", "")
                if not isinstance(encoded, str):
                    raise AgentEnvironmentError("process_write_invalid")
                try:
                    written = base64.b64decode(encoded, validate=True)
                except ValueError as error:
                    raise AgentEnvironmentError("process_write_invalid") from error
                if len(written) > MAX_PROCESS_WRITE_BYTES:
                    raise AgentEnvironmentError("process_write_out_of_bounds")
            if process_action == "logs":
                maximum = data.get("maximum_bytes", MAX_PROCESS_LOG_BYTES)
                if type(maximum) is not int or not 1 <= maximum <= MAX_PROCESS_LOG_BYTES:
                    raise AgentEnvironmentError("process_logs_out_of_bounds")
        try:
            payload = _canonical(data)
        except (TypeError, ValueError, RecursionError) as error:
            raise AgentEnvironmentError("operation_arguments_invalid") from error
        if len(payload) > 256 * 1024:
            raise AgentEnvironmentError("operation_arguments_out_of_bounds")
        return payload, process_receipt, process_action

    def execute(
        self,
        tool_id: str,
        arguments: Mapping[str, object],
        *,
        call_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> CallReceipt:
        helper = HELPERS.get(tool_id)
        if helper is None or not isinstance(arguments, Mapping):
            raise AgentEnvironmentError("operation_not_registered")
        identifier = call_id or uuid.uuid4().hex
        if not CALL_ID.fullmatch(identifier):
            raise AgentEnvironmentError("call_identity_invalid")
        facts = self.ensure_running()
        self._free_reserve(facts)
        payload, process_receipt, process_action = self._operation_payload(tool_id, arguments, facts)
        if process_receipt is not None and arguments.get("background") is True:
            # The helper checks this trusted owner value against its rootfs claim.
            decoded = json.loads(payload)
            with self._locked(create=True):
                owner = self._registry(create=False)["owner_key"]
            decoded["_voice_agent_owner"] = owner
            payload = _canonical(decoded)
        with self._locked(create=True):
            registry = self._registry(create=False)
            if registry.get("selected_container_id") != facts.container_id:
                raise AgentEnvironmentError("environment_identity_conflict")
            calls = registry["calls"]
            assert isinstance(calls, dict)
            prior = calls.get(identifier)
            if isinstance(prior, dict):
                if prior.get("state") != "completed" or not isinstance(prior.get("receipt"), dict):
                    raise AgentEnvironmentError("execution_outcome_unknown")
                saved = prior["receipt"]
                try:
                    return CallReceipt(
                        identifier, facts.container_id, facts.generation,
                        str(saved["status"]), saved.get("exit_code"),
                        base64.b64decode(str(saved["stdout_base64"]), validate=True),
                        base64.b64decode(str(saved["stderr_base64"]), validate=True),
                        str(saved["cwd"]), True, dict(saved.get("output", {})),
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise AgentEnvironmentError("execution_outcome_unknown") from error
            calls[identifier] = {"state": "dispatching"}
            if process_receipt is not None and arguments.get("background") is True:
                processes = registry["processes"]
                assert isinstance(processes, dict)
                if len(processes) >= MAX_BACKGROUND_PROCESSES:
                    calls.pop(identifier, None)
                    raise AgentEnvironmentError("background_process_limit_reached")
                processes[process_receipt] = {
                    "container_id": facts.container_id, "generation": facts.generation,
                    "spec": self.spec, "owner": registry["owner_key"],
                    "identity": None, "last_state": "dispatching",
                }
            if len(calls) > 512:
                removable = next((
                    key for key, value in calls.items()
                    if key != identifier and isinstance(value, dict)
                    and value.get("state") == "completed"
                ), None)
                if removable is not None:
                    calls.pop(removable)
            cwd = str(registry.get("logical_cwd", "/workspace"))
            _atomic_private(self.registry_path, registry)
        try:
            credential_arguments = self._exec_credential_arguments(identifier, facts)
        except AgentEnvironmentError:
            self._forget_undispatched_call(identifier, facts.container_id, process_receipt)
            raise
        command = (
            "container", "exec", "-i", *credential_arguments, facts.container_id,
            "/usr/local/lib/voice-agent/agent-helper", "claim-execute",
            "--call-id", identifier, "--tool", helper, "--cwd", cwd,
        )
        timeout = min(
            timeout_seconds or self.config.model.agent_environment.lifecycle.command_timeout_seconds,
            600,
        )
        try:
            result = self._docker(*command, stdin=payload, timeout=timeout)
        except AgentEnvironmentError:
            self._cleanup_exec_credentials(identifier, facts.container_id)
            self._mark_call_unknown(identifier, facts.container_id)
            raise AgentEnvironmentError("execution_outcome_unknown") from None
        if result.accepted is None and result.returncode not in {0, 125, 126, 127}:
            self._mark_call_unknown(identifier, facts.container_id)
            raise AgentEnvironmentError("execution_outcome_unknown")
        if result.returncode == 125 and result.accepted is False:
            # The only automatic redispatch: definite rejection before acceptance,
            # after same-ID inspection/start, with the identical call ID.
            restarted = self.ensure_running()
            if restarted.container_id != facts.container_id:
                raise AgentEnvironmentError("environment_identity_conflict")
            try:
                result = self._docker(*command, stdin=payload, timeout=timeout)
            except AgentEnvironmentError:
                self._cleanup_exec_credentials(identifier, facts.container_id)
                self._mark_call_unknown(identifier, facts.container_id)
                raise AgentEnvironmentError("execution_outcome_unknown") from None
        if not self._cleanup_exec_credentials(identifier, facts.container_id):
            self._mark_call_unknown(identifier, facts.container_id)
            raise AgentEnvironmentError("credential_cleanup_failed")
        if result.accepted is None and result.returncode != 0:
            self._mark_call_unknown(identifier, facts.container_id)
            raise AgentEnvironmentError("execution_outcome_unknown")
        maximum = self.config.model.agent_environment.resources.maximum_output_bytes
        if len(result.stdout) > maximum or len(result.stderr) > maximum:
            self.cancel_call(identifier, facts.container_id)
            raise AgentEnvironmentError("operation_output_out_of_bounds")
        try:
            document = json.loads(result.stdout)
            status = str(document["status"])
            exit_code = document.get("exit_code")
            output = base64.b64decode(document.get("stdout_base64", ""), validate=True)
            error_output = base64.b64decode(document.get("stderr_base64", ""), validate=True)
            next_cwd = str(document.get("cwd", "/workspace"))
            replayed = bool(document.get("replayed", False))
            metadata = _receipt_output_metadata(document, output, error_output)
            if tool_id in WEB_TOOLS:
                status, exit_code, output, error_output, metadata = postprocess_research_output(
                    tool_id, output
                )
        except (AgentEnvironmentError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._mark_call_unknown(identifier, facts.container_id)
            raise AgentEnvironmentError("execution_outcome_unknown") from error
        if status not in {"completed", "failed", "cancelled"} or not next_cwd.startswith("/"):
            raise AgentEnvironmentError("execution_receipt_invalid")
        with self._locked(create=True):
            current = self._registry(create=False)
            if current.get("selected_container_id") != facts.container_id:
                raise AgentEnvironmentError("environment_identity_conflict")
            current["logical_cwd"] = next_cwd if len(next_cwd.encode()) <= 4096 else "/workspace"
            current_calls = current["calls"]
            assert isinstance(current_calls, dict)
            details = metadata.get("details", {})
            if process_receipt is not None and isinstance(details, dict):
                internal_identity = details.pop("_voice_agent_identity", None)
                if arguments.get("background") is True:
                    processes = current["processes"]
                    assert isinstance(processes, dict)
                    if status != "completed":
                        # The fixed helper kills any child for which it cannot
                        # durably issue an identity receipt; no authority remains.
                        processes.pop(process_receipt, None)
                    elif (
                        not isinstance(internal_identity, dict)
                        or set(internal_identity) != {"pid", "pgid", "start_time", "executable", "init_start_time"}
                        or any(type(internal_identity[key]) is not int for key in ("pid", "pgid", "start_time", "init_start_time"))
                        or not isinstance(internal_identity["executable"], str)
                    ):
                        raise AgentEnvironmentError("process_receipt_invalid")
                    else:
                        processes[process_receipt] = {
                            "container_id": facts.container_id, "generation": facts.generation,
                            "spec": self.spec, "owner": current["owner_key"],
                            "identity": internal_identity, "last_state": "running",
                        }
                elif process_action is not None:
                    state = details.get("state")
                    if isinstance(state, str):
                        current["processes"][process_receipt]["last_state"] = state
            if len(output) <= 4096 and len(error_output) <= 4096:
                current_calls[identifier] = {
                    "state": "completed",
                    "receipt": {
                        "status": status,
                        "exit_code": exit_code,
                        "stdout_base64": base64.b64encode(output).decode("ascii"),
                        "stderr_base64": base64.b64encode(error_output).decode("ascii"),
                        "cwd": next_cwd,
                        "output": metadata,
                    },
                }
            else:
                current_calls[identifier] = {
                    "state": "completed_unrecoverable",
                    "stdout_sha256": hashlib.sha256(output).hexdigest(),
                    "stderr_sha256": hashlib.sha256(error_output).hexdigest(),
                }
            _atomic_private(self.registry_path, current)
        return CallReceipt(identifier, facts.container_id, facts.generation, status, exit_code, output, error_output, next_cwd, replayed, metadata)

    def redact_display(self, value: bytes) -> bytes:
        """Create a display/log copy only; callers retain the original bytes."""
        credentials = self.config.model.agent_environment.credentials
        with self._credential_lock:
            secrets_to_hide = list(self._redaction_values)
        secrets_to_hide += list(self._pinned_creation_environment.values()) + list(self._pinned_creation_files.values())
        for kind, names in (
            ("environment", credentials.exec_environment_names),
            ("file", credentials.exec_file_names),
        ):
            for name in names:
                try:
                    secrets_to_hide.append(self._credential_value(kind, name))
                except AgentEnvironmentError:
                    pass
        redacted = value
        for secret in sorted((item for item in secrets_to_hide if item), key=len, reverse=True):
            redacted = redacted.replace(secret, b"[REDACTED]")
        return redacted

    @staticmethod
    def _stream_location(root: str, relative_path: str) -> tuple[str, str]:
        if root not in STREAM_ROOTS or not isinstance(relative_path, str):
            raise AgentEnvironmentError("stream_destination_invalid")
        candidate = Path(relative_path)
        if (
            not relative_path or "\0" in relative_path or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or len(relative_path.encode("utf-8")) > 4096
        ):
            raise AgentEnvironmentError("stream_destination_invalid")
        return root, f"{STREAM_ROOTS[root]}/{relative_path}"

    def stream_inbound(
        self, data: bytes, *, root: str, relative_path: str, transfer_id: str | None = None
    ) -> ByteStreamReceipt:
        """Controller-only gateway admission; no host path or model mount is accepted."""
        if not isinstance(data, bytes):
            raise AgentEnvironmentError("stream_input_invalid")
        resource = self.config.model.agent_environment.resources
        if len(data) > resource.maximum_stream_bytes:
            raise AgentEnvironmentError("stream_input_out_of_bounds")
        root_name, container_path = self._stream_location(root, relative_path)
        identifier = transfer_id or uuid.uuid4().hex
        if not CALL_ID.fullmatch(identifier):
            raise AgentEnvironmentError("call_identity_invalid")
        facts = self.ensure_running()
        self._free_reserve(facts)
        digest = hashlib.sha256(data).hexdigest()
        try:
            result = self._docker(
                "container", "exec", "-i", facts.container_id,
                "/usr/local/lib/voice-agent/agent-helper", "stream-in",
                "--transfer-id", identifier, "--root", root_name, "--path", relative_path,
                "--expected-bytes", str(len(data)), "--expected-sha256", digest,
                stdin=data, timeout=resource.stream_timeout_seconds,
            )
        except AgentEnvironmentError:
            raise AgentEnvironmentError("stream_outcome_unknown") from None
        if result.returncode != 0:
            raise AgentEnvironmentError("stream_outcome_unknown")
        try:
            document = json.loads(result.stdout)
        except (UnicodeError, ValueError, TypeError) as error:
            raise AgentEnvironmentError("stream_outcome_unknown") from error
        if document != {
            "atomic": True, "bytes": len(data), "mode": "0600", "path": container_path,
            "sha256": digest, "status": "stored", "transfer_id": identifier,
        }:
            raise AgentEnvironmentError("stream_outcome_unknown")
        return ByteStreamReceipt(identifier, "inbound", container_path, len(data), digest, "stored")

    def stream_outbound(
        self, *, root: str, relative_path: str, transfer_id: str | None = None
    ) -> tuple[bytes, ByteStreamReceipt]:
        resource = self.config.model.agent_environment.resources
        root_name, container_path = self._stream_location(root, relative_path)
        identifier = transfer_id or uuid.uuid4().hex
        if not CALL_ID.fullmatch(identifier):
            raise AgentEnvironmentError("call_identity_invalid")
        facts = self.ensure_running()
        stat_result = self._docker(
            "container", "exec", facts.container_id,
            "/usr/local/lib/voice-agent/agent-helper", "stream-stat",
            "--root", root_name, "--path", relative_path,
            timeout=resource.stream_timeout_seconds,
        )
        try:
            metadata = json.loads(stat_result.stdout)
            byte_count = metadata["bytes"]
            digest = metadata["sha256"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AgentEnvironmentError("stream_read_failed") from error
        if (
            stat_result.returncode != 0 or type(byte_count) is not int or byte_count < 0
            or byte_count > resource.maximum_stream_bytes
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise AgentEnvironmentError("stream_output_out_of_bounds")
        try:
            result = self._docker(
                "container", "exec", facts.container_id,
                "/usr/local/lib/voice-agent/agent-helper", "stream-out",
                "--root", root_name, "--path", relative_path,
                "--expected-bytes", str(byte_count), "--expected-sha256", digest,
                timeout=resource.stream_timeout_seconds,
            )
        except AgentEnvironmentError:
            raise AgentEnvironmentError("stream_outcome_unknown") from None
        if (
            result.returncode != 0 or len(result.stdout) != byte_count
            or hashlib.sha256(result.stdout).hexdigest() != digest
        ):
            raise AgentEnvironmentError("stream_outcome_unknown")
        return result.stdout, ByteStreamReceipt(
            identifier, "outbound", container_path, byte_count, digest, "read"
        )

    def deliver_outbound(
        self,
        *,
        root: str,
        relative_path: str,
        target: str,
        transport: Callable[[bytes], TransportAcknowledgement],
        transfer_id: str | None = None,
    ) -> ByteStreamReceipt:
        """Send once.  Only exact target/bytes/hash acknowledgement becomes ``sent``."""
        payload, receipt = self.stream_outbound(
            root=root, relative_path=relative_path, transfer_id=transfer_id
        )
        try:
            acknowledgement = transport(payload)
        except Exception:
            acknowledgement = TransportAcknowledgement("unknown")
        exact = (
            acknowledgement.outcome == "acknowledged"
            and acknowledgement.target == target
            and acknowledgement.byte_count == receipt.byte_count
            and acknowledgement.sha256 == receipt.sha256
        )
        outcome = "sent" if exact else "rejected" if acknowledgement.outcome == "rejected" else "unknown"
        return ByteStreamReceipt(
            receipt.transfer_id, receipt.direction, receipt.container_path,
            receipt.byte_count, receipt.sha256, outcome, exact, target,
        )

    def _forget_undispatched_call(
        self, call_id: str, container_id: str, process_receipt: str | None = None,
    ) -> None:
        try:
            with self._locked(create=True):
                registry = self._registry(create=False)
                calls = registry.get("calls")
                if registry.get("selected_container_id") == container_id and isinstance(calls, dict):
                    if calls.get(call_id) == {"state": "dispatching"}:
                        calls.pop(call_id)
                        processes = registry.get("processes")
                        if process_receipt is not None and isinstance(processes, dict):
                            claim = processes.get(process_receipt)
                            if isinstance(claim, dict) and claim.get("identity") is None:
                                processes.pop(process_receipt, None)
                        _atomic_private(self.registry_path, registry)
        except AgentEnvironmentError:
            pass

    def _mark_call_unknown(self, call_id: str, container_id: str) -> None:
        try:
            with self._locked(create=True):
                registry = self._registry(create=False)
                if registry.get("selected_container_id") != container_id:
                    return
                calls = registry["calls"]
                if isinstance(calls, dict):
                    calls[call_id] = {"state": "unknown"}
                    _atomic_private(self.registry_path, registry)
        except AgentEnvironmentError:
            pass

    def cancel_call(self, call_id: str, container_id: str | None = None) -> bool:
        if not CALL_ID.fullmatch(call_id):
            return False
        with self._locked(create=True):
            registry = self._registry(create=False)
            self._endpoint(registry, pin=False)
            selected = str(registry.get("selected_container_id") or "")
            if container_id is not None and container_id != selected:
                return False
            facts = self._inspect(selected)
            if facts.state != "running" or self._effective_mismatches(facts, registry):
                return False
            result = self._docker(
                "container", "exec", facts.container_id,
                "/usr/local/lib/voice-agent/agent-helper", "cancel-call", "--call-id", call_id,
            )
            return result.returncode == 0 and result.stdout == b'{"outcome":"signalled"}'

    def status(self) -> dict[str, object]:
        if self._credential_initialization_error is not None:
            return self._status_document(
                None, state="unavailable", reason=self._credential_initialization_error
            )
        try:
            self._pin_prepared_image()
        except AgentEnvironmentError as error:
            return self._status_document(None, state="unavailable", reason=error.code)
        if not self.registry_path.exists():
            return self._status_document(None, state="absent", reason=None)
        try:
            with self._locked(create=False):
                registry = self._registry(create=False)
                self._endpoint(registry, pin=False)
                facts = self._resolve(registry)
                state = (
                    "absent" if facts is None
                    else "resource_stopped" if registry.get("state") == "resource_stopped"
                    else self._state(facts.state)
                )
                return self._status_document(registry, state=state, reason=registry.get("reason_code"))
        except AgentEnvironmentError as error:
            registry = None
            try:
                registry = self._registry(create=False)
            except AgentEnvironmentError:
                pass
            return self._status_document(registry, state="unavailable" if error.code not in {"stale_spec", "environment_identity_conflict"} else error.code, reason=error.code, mismatch=error.fields)

    def _status_document(
        self, registry: Mapping[str, object] | None, *, state: str, reason: object,
        mismatch: tuple[str, ...] = (),
    ) -> dict[str, object]:
        selected = registry.get("selected_container_id") if registry else None
        known_present = {
            "creating", "running", "stopped", "exited", "starting", "restarting",
            "stale_spec", "unhealthy", "resource_stopped", "retiring", "retired",
        }
        persistence: bool | None = (
            False if state == "absent" else True if state in known_present else None
        )
        credentials = self.config.model.agent_environment.credentials
        exposure = [
            {"name": name, "mode": mode}
            for mode, names in (
                ("create_environment", credentials.creation_environment_names),
                ("create_file", credentials.creation_file_names),
                ("exec_environment", credentials.exec_environment_names),
                ("exec_file", credentials.exec_file_names),
            )
            for name in names
        ]
        processes = registry.get("processes", {}) if registry else {}
        process_status = [
            {
                "receipt": receipt, "state": str(claim.get("last_state", "unknown")),
                "observation": "last_reconciled",
            }
            for receipt, claim in sorted(processes.items())
            if isinstance(receipt, str) and isinstance(claim, dict)
        ][:MAX_BACKGROUND_PROCESSES]
        retained = registry.get("retained", []) if registry else []
        retained_status = [
            {
                "container_id_prefix": str(record.get("container_id", ""))[:12],
                "generation": record.get("generation"),
                "spec_prefix": str(record.get("spec", ""))[:12],
                "selectable": False,
                "last_inspected_state": str(record.get("state", "unknown")),
            }
            for record in retained if isinstance(record, dict)
        ]
        return {
            "schema_version": STATUS_SCHEMA,
            "installation_prefix": str(registry.get("owner_key"))[:12] if registry else None,
            "config_revision_prefix": self.config.semantic_revision[:12],
            "state": state,
            "container_id_prefix": str(selected)[:12] if selected else None,
            "spec_prefix": self.spec[:12],
            "generation": registry.get("generation") if registry else 0,
            "reason_code": reason if isinstance(reason, str) else None,
            "mismatch_fields": list(mismatch),
            "bounds": self.config.model.agent_environment.resources.model_dump(mode="json"),
            "network_egress_enabled": self.config.model.agent_environment.network.enabled,
            "published_ports": [],
            "internal_listeners_may_conflict": True,
            "additional_mounts": [
                {"source": mount.source, "destination": mount.destination, "mode": mount.mode}
                for mount in self._mounts
            ],
            "additional_mount_authority_warning": MOUNT_AUTHORITY_WARNING,
            "unmounted_host_paths_sockets_devices_accessible": False,
            "docker_socket_mounted": False,
            "process_receipts": process_status,
            "background_process_limit": MAX_BACKGROUND_PROCESSES,
            "process_poll_required_for_fresh_running_truth": True,
            "retained_generations": retained_status,
            "lifecycle_selector_supported": False,
            "destructive_confirmation_required": True,
            "data_deletion_requires_separate_confirmation": True,
            "reset_recreates_lazily_under_same_spec": True,
            "rebuild_kills_old_processes": True,
            "rebuild_retains_old_generation_by_default": True,
            "remove_affects_only_current_container": True,
            "retire_affects_only_recorded_noncurrent_generation": True,
            "conflicts_are_not_automatically_resolved": True,
            "visible_conflict_kinds": [
                "process_state", "internal_port", "git_lock", "package_manager_lock",
                "concurrent_writer", "stale_receipt", "stopped", "unhealthy",
                "resource_stopped", "retained_generation",
            ],
            "credential_configuration_count": 1,
            "credential_exposure": exposure,
            "credential_authority_warning": CREDENTIAL_AUTHORITY_WARNING,
            "credential_values_or_fingerprints_exposed": False,
            "remote_effects_rolled_back_by_cancellation": False,
            "automatic_network_or_stream_retry": False,
            # These are lifecycle facts, not a claim that destroyed rootfs or
            # tmpfs/process state can be recovered. They contain no path,
            # command, file, package, or repository content.
            "rootfs_and_files_persist": persistence,
            "rootfs_persists_until_exact_container_destruction": persistence,
            "workspace_persists_after_reset_rebuild_remove_by_default": True,
            "cache_persists_after_reset_rebuild_remove_by_default": True,
            "tmpfs_persists_across_container_stop": False,
            "processes_survive_controller_events_only_while_container_running": True,
            "processes_survive_container_stop": False,
            "shell_local_state_persists_across_exec": False,
            "logical_cwd_persists_across_exec_and_controller_restart": persistence,
            "workspace_and_cache_preserved_by_default": True,
        }

    def _exact_current(self, registry: dict[str, object], *, allow_stale: bool = False) -> ContainerFacts:
        self._endpoint(registry, pin=False)
        if allow_stale:
            selected = str(registry.get("selected_container_id") or "")
            candidates = self._candidate_ids(registry)
            retained_ids = {
                str(record.get("container_id")) for record in registry.get("retained", [])
                if isinstance(record, dict)
            }
            if (
                not selected or candidates.count(selected) != 1
                or set(candidates) != retained_ids | {selected}
            ):
                raise AgentEnvironmentError("environment_identity_conflict")
            facts = self._inspect(selected)
        else:
            facts = self._resolve(registry)
            if facts is None or facts.container_id != registry.get("selected_container_id"):
                raise AgentEnvironmentError("environment_absent")
        # Immediate second inspection is the destructive-action reinspection.
        fresh = self._inspect(facts.container_id)
        if (
            fresh.labels.get(MANAGED_LABEL) != "1"
            or fresh.labels.get(SCHEMA_LABEL) != "1"
            or fresh.labels.get(OWNER_LABEL) != registry["owner_key"]
            or fresh.generation != registry["generation"]
            or fresh.container_id != registry.get("selected_container_id")
        ):
            raise AgentEnvironmentError("environment_identity_conflict")
        return fresh

    def lifecycle(self, action: str, *, confirmed: bool) -> dict[str, object]:
        if action == "status":
            return self.status()
        if self._credential_initialization_error is not None:
            raise AgentEnvironmentError(self._credential_initialization_error)
        if action not in {"reset", "rebuild", "remove", "retire"}:
            raise AgentEnvironmentError("lifecycle_action_invalid")
        if not confirmed:
            raise AgentEnvironmentError("confirmation_required")
        with self._locked(create=True):
            registry = self._registry(create=False)
            if action == "retire":
                retained = registry["retained"]
                if not retained:
                    raise AgentEnvironmentError("retained_generation_absent")
                record = retained[0]
                self._endpoint(registry, pin=False)
                recorded_id = str(record.get("container_id", ""))
                candidates = self._candidate_ids(registry)
                if candidates.count(recorded_id) != 1 or recorded_id == registry.get("selected_container_id"):
                    raise AgentEnvironmentError("environment_identity_conflict")
                facts = self._inspect(recorded_id)
                expected = (
                    facts.labels.get(MANAGED_LABEL) == "1"
                    and facts.labels.get(SCHEMA_LABEL) == "1"
                    and facts.labels.get(OWNER_LABEL) == registry["owner_key"]
                    and facts.generation == record.get("generation")
                    and facts.spec == record.get("spec")
                )
                if not expected:
                    raise AgentEnvironmentError("environment_identity_conflict")
                if facts.state == "running":
                    stopped = self._docker("container", "stop", "--time", "10", facts.container_id)
                    if stopped.returncode != 0:
                        raise AgentEnvironmentError("lifecycle_action_failed")
                    facts = self._inspect(facts.container_id)
                # Immediate fresh reinspection after any stop and before rm.
                facts = self._inspect(facts.container_id)
                if not (
                    facts.labels.get(MANAGED_LABEL) == "1"
                    and facts.labels.get(SCHEMA_LABEL) == "1"
                    and facts.labels.get(OWNER_LABEL) == registry["owner_key"]
                    and facts.generation == record.get("generation")
                    and facts.spec == record.get("spec")
                ):
                    raise AgentEnvironmentError("environment_identity_conflict")
                result = self._docker("container", "rm", facts.container_id)
                if result.returncode != 0:
                    raise AgentEnvironmentError("lifecycle_action_failed")
                registry["retained"] = retained[1:]
                _atomic_private(self.registry_path, registry)
                return self._status_document(
                    registry,
                    state=str(registry.get("state", "absent")),
                    reason=registry.get("reason_code"),
                )
            facts = self._exact_current(registry, allow_stale=True)
            if action == "rebuild":
                self._pin_prepared_image()
                if facts.state == "running":
                    stopped = self._docker("container", "stop", "--time", "10", facts.container_id)
                    if stopped.returncode != 0:
                        raise AgentEnvironmentError("lifecycle_action_failed")
                    facts = self._inspect(facts.container_id)
                self._mark_container_processes_gone(registry, facts.container_id)
                next_generation = max(
                    [int(registry["generation"]), *[
                        int(record.get("generation", 0)) for record in registry["retained"]
                        if isinstance(record, dict)
                    ]]
                ) + 1
                next_facts = self._create_unselected_rebuild(registry, next_generation)
                # One fsynced replacement is the only selection boundary. The
                # old stopped generation becomes nonselectable recovery material.
                registry["retained"].append({
                    "container_id": facts.container_id, "generation": facts.generation,
                    "spec": facts.spec, "state": facts.state,
                })
                registry["selected_container_id"] = next_facts.container_id
                registry["generation"] = next_generation
                registry["spec"] = self.spec
                registry["state"] = "running"
                registry["reason_code"] = None
                _atomic_private(self.registry_path, registry)
                return self._status_document(registry, state="running", reason=None)
            original_spec = facts.spec
            if facts.state == "running":
                stopped = self._docker("container", "stop", "--time", "10", facts.container_id)
                if stopped.returncode != 0:
                    raise AgentEnvironmentError("lifecycle_action_failed")
                facts = self._inspect(facts.container_id)
            # Immediate fresh reinspection after any stop and before rm.
            facts = self._inspect(facts.container_id)
            self._mark_container_processes_gone(registry, facts.container_id)
            if (
                facts.labels.get(MANAGED_LABEL) != "1"
                or facts.labels.get(SCHEMA_LABEL) != "1"
                or facts.labels.get(OWNER_LABEL) != registry["owner_key"]
                or facts.generation != registry["generation"]
                or facts.spec != original_spec
                or facts.container_id != registry.get("selected_container_id")
            ):
                raise AgentEnvironmentError("environment_identity_conflict")
            result = self._docker("container", "rm", facts.container_id)
            if result.returncode != 0:
                raise AgentEnvironmentError("lifecycle_action_failed")
            registry["selected_container_id"] = None
            registry["state"] = "absent"
            registry["reason_code"] = None
            _atomic_private(self.registry_path, registry)
            return self._status_document(registry, state="absent", reason=None)

"""Native local immutable image preparation for the sole AgentEnvironment.

Preparation is an explicit stand operation. Runtime consumes only the exact image
ID recorded in private installation state and never builds, pulls, publishes, or
trusts a mutable tag.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
from typing import Mapping, Protocol, Sequence

IMAGE_LOCK_SCHEMA = "voice-agent.agent-environment-image-lock.v2"
PREPARED_IMAGE_SCHEMA = "voice-agent.prepared-agent-environment-image.v1"
IMAGE_MANAGED_LABEL = "io.priney.voice-agent-v2.image-managed"
IMAGE_SCHEMA_LABEL = "io.priney.voice-agent-v2.image-schema"
IMAGE_CONTEXT_LABEL = "io.priney.voice-agent-v2.image-context"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTEXT_REVISION = re.compile(r"[0-9a-f]{64}\Z")
PLATFORM_ARCHITECTURES = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}


class PreparedImageError(RuntimeError):
    """Content-free local image preparation or custody failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ImageCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class ImageCommandRunner(Protocol):
    def run(self, arguments: Sequence[str], *, cwd: Path | None = None) -> ImageCommandResult: ...


@dataclass(frozen=True, slots=True)
class NativeImageContract:
    source_root: Path
    context_root: Path
    revision: str
    base_image: str
    native_platforms: tuple[str, ...]
    entrypoint: tuple[str, ...]
    command: tuple[str, ...]
    user: str
    required_labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PreparedImageSelection:
    image_id: str
    context_revision: str
    platform: str
    architecture: str

    def document(self) -> dict[str, str]:
        return {
            "image_id": self.image_id,
            "context_revision": self.context_revision,
            "platform": self.platform,
            "architecture": self.architecture,
        }


@dataclass(frozen=True, slots=True)
class PreparedImageRecord:
    selected: PreparedImageSelection
    retained: tuple[PreparedImageSelection, ...]

    def document(self) -> dict[str, object]:
        return {
            "schema_version": PREPARED_IMAGE_SCHEMA,
            "selected": self.selected.document(),
            "retained": [item.document() for item in self.retained],
        }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "//" in value:
        raise PreparedImageError("image_lock_invalid")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise PreparedImageError("image_lock_invalid")
    return value


def load_native_image_contract(source_root: Path) -> NativeImageContract:
    """Validate the exact repository lock and canonical build-context bytes."""

    lock_path = source_root / "agent-environment/image-lock.v2.json"
    try:
        raw = lock_path.read_bytes()
        document = json.loads(raw)
    except (OSError, ValueError) as error:
        raise PreparedImageError("image_lock_invalid") from error
    required = {
        "schema_version", "base_image", "native_platforms", "context_files",
        "runtime_pull", "registry_required", "entrypoint", "command", "user", "labels",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise PreparedImageError("image_lock_invalid")
    if (
        document.get("schema_version") != IMAGE_LOCK_SCHEMA
        or document.get("runtime_pull") is not False
        or document.get("registry_required") is not False
        or not isinstance(document.get("base_image"), str)
        or re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", document["base_image"]) is None
        or document.get("native_platforms") != ["linux/amd64", "linux/arm64"]
        or document.get("entrypoint") != ["/sbin/tini", "--"]
        or document.get("command") != ["/usr/local/lib/voice-agent/agent-helper", "init-container"]
        or document.get("user") != "1000:1000"
        or document.get("labels") != {
            IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1",
        }
    ):
        raise PreparedImageError("image_lock_invalid")
    files = document.get("context_files")
    if not isinstance(files, list) or not files:
        raise PreparedImageError("image_lock_invalid")
    observed: list[dict[str, object]] = []
    contents: dict[str, bytes] = {}
    names: set[str] = set()
    context_root = source_root / "agent-environment"
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "mode"}:
            raise PreparedImageError("image_lock_invalid")
        relative = _safe_relative(item["path"])
        if relative in names or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise PreparedImageError("image_lock_invalid")
        if not isinstance(item["mode"], str) or not re.fullmatch(r"0[0-7]{3}", item["mode"]):
            raise PreparedImageError("image_lock_invalid")
        names.add(relative)
        path = context_root / relative
        try:
            metadata = path.lstat()
            content = path.read_bytes()
        except OSError as error:
            raise PreparedImageError("image_context_invalid") from error
        if (
            not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
            or f"{stat.S_IMODE(metadata.st_mode):04o}" != item["mode"]
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise PreparedImageError("image_context_invalid")
        contents[relative] = content
        observed.append({"path": relative, "sha256": item["sha256"], "mode": item["mode"]})
    if names != {"Dockerfile", "helpers/agent-helper"}:
        raise PreparedImageError("image_lock_invalid")
    expected_base = f"ARG BASE_IMAGE={document['base_image']}".encode("utf-8")
    if expected_base not in contents["Dockerfile"]:
        raise PreparedImageError("image_lock_invalid")
    revision = hashlib.sha256(_canonical({
        "schema_version": IMAGE_LOCK_SCHEMA,
        "base_image": document["base_image"],
        "native_platforms": document["native_platforms"],
        "context_files": sorted(observed, key=lambda value: str(value["path"])),
        "entrypoint": document["entrypoint"],
        "command": document["command"],
        "user": document["user"],
        "labels": document["labels"],
    })).hexdigest()
    return NativeImageContract(
        source_root=source_root, context_root=context_root, revision=revision,
        base_image=document["base_image"], native_platforms=tuple(document["native_platforms"]),
        entrypoint=tuple(document["entrypoint"]), command=tuple(document["command"]),
        user=str(document["user"]), required_labels=dict(document["labels"]),
    )


def _private_directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreparedImageError("image_preparation_missing" if not create else "image_preparation_state_unsafe") from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PreparedImageError("image_preparation_state_unsafe")


def _selection(value: object) -> PreparedImageSelection:
    if not isinstance(value, dict) or set(value) != {"image_id", "context_revision", "platform", "architecture"}:
        raise PreparedImageError("image_preparation_invalid")
    image_id = value.get("image_id")
    revision = value.get("context_revision")
    target = value.get("platform")
    architecture = value.get("architecture")
    if (
        not isinstance(image_id, str) or IMAGE_ID.fullmatch(image_id) is None
        or not isinstance(revision, str) or CONTEXT_REVISION.fullmatch(revision) is None
        or target not in {"linux/amd64", "linux/arm64"}
        or architecture not in {"amd64", "arm64"}
        or target != f"linux/{architecture}"
    ):
        raise PreparedImageError("image_preparation_invalid")
    return PreparedImageSelection(image_id, revision, str(target), str(architecture))


def load_prepared_image(state_root: Path, contract: NativeImageContract) -> PreparedImageRecord:
    _private_directory(state_root, create=False)
    path = state_root / "prepared-image.json"
    try:
        metadata = path.lstat()
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PreparedImageError("image_preparation_missing") from error
    except (OSError, UnicodeError, ValueError) as error:
        raise PreparedImageError("image_preparation_invalid") from error
    if (
        not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
        or not isinstance(document, dict) or set(document) != {"schema_version", "selected", "retained"}
        or document.get("schema_version") != PREPARED_IMAGE_SCHEMA or not isinstance(document.get("retained"), list)
    ):
        raise PreparedImageError("image_preparation_invalid")
    selected = _selection(document["selected"])
    retained = tuple(_selection(item) for item in document["retained"])
    if selected.context_revision != contract.revision:
        raise PreparedImageError("image_preparation_stale")
    if len({item.image_id for item in retained}) != len(retained) or selected.image_id in {item.image_id for item in retained}:
        raise PreparedImageError("image_preparation_invalid")
    return PreparedImageRecord(selected, retained)


def _atomic_private(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(_canonical(document) + b"\n")
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(parent)
        finally: os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _server_platform(runner: ImageCommandRunner) -> tuple[str, str]:
    result = runner.run(("docker", "version", "--format", "{{json .Server}}"))
    security = runner.run(("docker", "info", "--format", "{{json .SecurityOptions}}"))
    if result.returncode != 0 or security.returncode != 0:
        raise PreparedImageError("docker_runtime_unavailable")
    try:
        server = json.loads(result.stdout)
        options = json.loads(security.stdout)
    except (TypeError, ValueError) as error:
        raise PreparedImageError("docker_runtime_unavailable") from error
    architecture = PLATFORM_ARCHITECTURES.get(str(server.get("Arch", platform.machine())).lower())
    if server.get("Os") != "linux" or architecture is None or "name=rootless" not in options:
        raise PreparedImageError("native_rootless_platform_unsupported")
    return f"linux/{architecture}", architecture


def _inspect_image(
    runner: ImageCommandRunner, reference: str, contract: NativeImageContract,
    *, target_platform: str, architecture: str,
) -> PreparedImageSelection:
    result = runner.run(("docker", "image", "inspect", reference))
    if result.returncode != 0:
        raise PreparedImageError("prepared_image_missing")
    try:
        documents = json.loads(result.stdout)
        raw = documents[0]
        config = raw["Config"]
        image_id = raw["Id"]
        labels = config["Labels"]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise PreparedImageError("prepared_image_inspection_failed") from error
    expected_labels = {**contract.required_labels, IMAGE_CONTEXT_LABEL: contract.revision}
    if (
        not isinstance(image_id, str) or IMAGE_ID.fullmatch(image_id) is None
        or raw.get("Os") != "linux" or raw.get("Architecture") != architecture
        or config.get("User") != contract.user
        or config.get("Entrypoint") != list(contract.entrypoint)
        or config.get("Cmd") != list(contract.command)
        or labels != expected_labels
        or not isinstance(raw.get("RootFS"), dict) or not raw["RootFS"].get("Layers")
    ):
        raise PreparedImageError("prepared_image_mismatch")
    return PreparedImageSelection(image_id, contract.revision, target_platform, architecture)


def prepare_native_image(
    *, state_root: Path, source_root: Path, runner: ImageCommandRunner,
) -> dict[str, object]:
    """Idempotently build and select one native exact image without deletion."""

    contract = load_native_image_contract(source_root)
    target_platform, architecture = _server_platform(runner)
    if target_platform not in contract.native_platforms:
        raise PreparedImageError("native_rootless_platform_unsupported")
    _private_directory(state_root, create=True)
    prior: PreparedImageRecord | None = None
    try:
        prior = load_prepared_image(state_root, contract)
        selected = _inspect_image(
            runner, prior.selected.image_id, contract,
            target_platform=target_platform, architecture=architecture,
        )
        if selected == prior.selected:
            return image_status_document(prior, contract, state="prepared", changed=False)
    except PreparedImageError as error:
        if error.code not in {"image_preparation_missing", "image_preparation_stale", "prepared_image_missing", "prepared_image_mismatch"}:
            raise
        try:
            path = state_root / "prepared-image.json"
            if path.exists():
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(document, dict) and document.get("schema_version") == PREPARED_IMAGE_SCHEMA:
                        prior = PreparedImageRecord(_selection(document.get("selected")), tuple(_selection(item) for item in document.get("retained", [])))
        except (OSError, ValueError, PreparedImageError):
            prior = None
    tag = f"voice-agent-v2-agent-environment:prepared-{contract.revision[:24]}-{architecture}"
    build = runner.run((
        "docker", "buildx", "build", "--load", "--platform", target_platform,
        "--file", str(contract.context_root / "Dockerfile"),
        "--build-arg", f"VOICE_AGENT_IMAGE_CONTEXT_REVISION={contract.revision}",
        "--tag", tag, str(contract.context_root),
    ))
    if build.returncode != 0:
        raise PreparedImageError("native_image_build_failed")
    selected = _inspect_image(
        runner, tag, contract, target_platform=target_platform, architecture=architecture,
    )
    retained = list(prior.retained if prior else ())
    if prior is not None and prior.selected.image_id != selected.image_id:
        retained.append(prior.selected)
    unique: dict[str, PreparedImageSelection] = {item.image_id: item for item in retained}
    unique.pop(selected.image_id, None)
    record = PreparedImageRecord(selected, tuple(unique.values()))
    _atomic_private(state_root / "prepared-image.json", record.document())
    return image_status_document(record, contract, state="prepared", changed=True)


def inspect_prepared_image(
    *, state_root: Path, source_root: Path, runner: ImageCommandRunner,
) -> dict[str, object]:
    contract = load_native_image_contract(source_root)
    try:
        target_platform, architecture = _server_platform(runner)
        record = load_prepared_image(state_root, contract)
        selected = _inspect_image(
            runner, record.selected.image_id, contract,
            target_platform=target_platform, architecture=architecture,
        )
        if selected != record.selected:
            raise PreparedImageError("prepared_image_mismatch")
        return image_status_document(record, contract, state="prepared", changed=False)
    except PreparedImageError as error:
        return {
            "schema_version": "voice-agent.prepared-agent-environment-image-status.v1",
            "state": "missing" if error.code == "image_preparation_missing" else "unavailable",
            "reason_code": error.code,
            "context_revision_prefix": contract.revision[:12],
            "image_id_prefix": None,
            "native_platform": None,
            "retained_image_count": 0,
            "runtime_build_pull_publish": False,
            "automatic_image_deletion": False,
            "changed": False,
        }


def image_status_document(
    record: PreparedImageRecord, contract: NativeImageContract, *, state: str, changed: bool,
) -> dict[str, object]:
    return {
        "schema_version": "voice-agent.prepared-agent-environment-image-status.v1",
        "state": state,
        "reason_code": None,
        "context_revision_prefix": contract.revision[:12],
        "image_id_prefix": record.selected.image_id[:19],
        "native_platform": record.selected.platform,
        "retained_image_count": len(record.retained),
        "runtime_build_pull_publish": False,
        "automatic_image_deletion": False,
        "changed": changed,
    }

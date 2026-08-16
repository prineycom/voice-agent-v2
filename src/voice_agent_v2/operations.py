"""Fail-closed single-host release, compatibility, and operations boundary.

The module deliberately owns no service daemon.  systemd owns the bounded outer
restart while the existing foreground runner owns its exact child processes.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import glob
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Callable, Iterator, Mapping
from urllib.parse import urlsplit

from .runtime_directory import SYSTEMD_RUNTIME_ROOT


OPERATIONS_SCHEMA = "voice-agent.operations.v1"
RELEASE_SCHEMA = "voice-agent.operational-release.v2"
DEFAULT_MANIFEST_RELATIVE = Path("config/operations-v1.json")
DEFAULT_STATE_ROOT = Path("~/.local/share/voice-agent-v2").expanduser()
CONFIGURATION_EXIT_STATUS = 2
RUNTIME_FAILURE_EXIT_STATUS = 1
MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
BUILD_ID = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID = re.compile(r"^[0-9a-f]{24}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_PROVIDER_IDENTITY = (
    "LiquidAI/LFM2.5-2.6B-GGUF@b421ad1d549afeda6a0fb2ad3a697cb5a7879adc#Q4_K_M"
)
EXPECTED_CONTRACTS = {
    "stt": "voice-agent.stt.v1",
    "llm_provider": "voice-agent.llm-provider.v1",
    "tts": "voice-agent.tts.v2",
    "realtime_control": "voice-agent.realtime-control.v2",
    "health_readiness": "voice-agent.health-readiness.v1",
    "observation": "voice-agent.observation.v1",
    "avatar_host": "voice-agent.avatar-host.v1",
    "avatar_control": "voice-agent.avatar-control.v1",
    "selected_avatar_module": "mvp-eye-svg-v1",
}
EXPECTED_ARTIFACT_NAMES = frozenset({
    "livekit-server", "silero-vad-v6", "whisper-large-v3-turbo-model",
    "whisper-large-v3-turbo-config", "whisper-large-v3-turbo-tokenizer",
    "whisper-large-v3-turbo-vocabulary", "whisper-large-v3-turbo-preprocessor",
    "llama-server-b10357", "lfm2.5-q4-k-m", "silero-v5-5-ru",
    "silero-python", "silero-torch-extension", "silero-libtorch-cpu",
})
EXPECTED_CACHE_BOUNDS = {
    "slice6-runtime": 2_147_483_648,
    "stt-model": 2_147_483_648,
    "stt-runtime": 4_294_967_296,
    "stt-service-state": 1_073_741_824,
    "local-lfm-runtime": 5_368_709_120,
    "silero-runtime": 2_147_483_648,
    "silero-state": 1_073_741_824,
}
EXPECTED_SUSTAINED_ACCEPTANCE = {
    "minimum_turns": 20,
    "minimum_turn_success_ratio": 1.0,
    "maximum_p95_total_turn_ms": 20_000,
    "maximum_p95_cancellation_latency_ms": 250,
    "maximum_process_rss_growth_mib": 512,
    "maximum_gpu_vram_growth_mib": 512,
    "minimum_avatar_healthy_frame_ratio": 0.98,
    "minimum_avatar_fps": 55,
}
ALLOWED_CONFIGURATION_NAMES = frozenset({
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_INTERNAL_URL",
    "LIVEKIT_PUBLIC_URL",
    "SLICE6_APP_PUBLIC_URL",
    "VOICE_AGENT_DIAGNOSTIC_CAPTURE",
    "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT",
    "VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS",
})
SECRET_CONFIGURATION_NAMES = frozenset({"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"})
REQUIRED_CONFIGURATION_NAMES = frozenset({
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_INTERNAL_URL",
})


class OperationalError(RuntimeError):
    """An actionable, content-free operational failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationReport:
    build_id: str | None
    release_id: str | None
    provider_mode: str
    artifact_count: int
    cache_bytes: dict[str, int]
    disk_available_bytes: int
    configuration_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "voice-agent.operational-validation.v1",
            "status": "compatible",
            "build_id": self.build_id,
            "release_id": self.release_id,
            "provider_mode": self.provider_mode,
            "external_provider_supervised": False,
            "automatic_fallback": False,
            "artifact_count": self.artifact_count,
            "cache_bytes": dict(sorted(self.cache_bytes.items())),
            "disk_available_bytes": self.disk_available_bytes,
            "configuration": "valid-secret-values-redacted",
            "configuration_fingerprint": self.configuration_fingerprint,
        }


def _json_object(path: Path, *, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise OperationalError(code, f"{path.name} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise OperationalError(code, f"{path.name} is not a JSON object")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise OperationalError("artifact_unavailable", f"artifact {path.name} is unreadable") from error
    return digest.hexdigest()


def _expanded_path(value: str, *, home: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise OperationalError("operations_manifest_invalid", "an operational path is invalid")
    expanded = value.replace("{home}", str(home))
    if "{" in expanded or "}" in expanded:
        raise OperationalError("operations_manifest_invalid", "an operational path token is invalid")
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        raise OperationalError("operations_manifest_invalid", "an operational path is not absolute")
    return path


def _require_exact_keys(
    value: Mapping[str, object], required: set[str], *, label: str,
    code: str = "operations_manifest_invalid",
) -> None:
    if set(value) != required:
        raise OperationalError(code, f"{label} has missing or unknown fields")


def load_operations_manifest(path: Path) -> dict[str, object]:
    manifest = _json_object(path, code="operations_manifest_invalid")
    _require_exact_keys(
        manifest,
        {
            "schema_version", "deployment", "lifecycle", "components", "contracts",
            "artifacts", "python_runtimes", "disk", "sustained_acceptance",
        },
        label="operations manifest",
    )
    if manifest.get("schema_version") != OPERATIONS_SCHEMA:
        raise OperationalError("operations_manifest_incompatible", "operations schema is incompatible")
    deployment = manifest.get("deployment")
    lifecycle = manifest.get("lifecycle")
    components = manifest.get("components")
    disk = manifest.get("disk")
    if not isinstance(deployment, dict) or not isinstance(lifecycle, dict):
        raise OperationalError("operations_manifest_invalid", "deployment lifecycle is invalid")
    if not isinstance(components, list) or not components or not isinstance(disk, dict):
        raise OperationalError("operations_manifest_invalid", "component or disk policy is invalid")
    if not (
        deployment.get("provider_mode") == "local"
        and deployment.get("provider_identity") == EXPECTED_PROVIDER_IDENTITY
        and deployment.get("automatic_fallback") is False
        and deployment.get("wake_enabled") is False
        and deployment.get("auth_boundary") == "loopback"
    ):
        raise OperationalError("operations_manifest_incompatible", "fixed provider/security policy changed")
    start_order = lifecycle.get("start_order")
    stop_order = lifecycle.get("stop_order")
    expected_start = [
        "configuration", "artifacts", "local-llm", "livekit",
        "gateway-controller-stt-tts-provider",
    ]
    expected_stop = [
        "gateway-controller-stt-tts-provider", "livekit", "local-llm",
    ]
    restart = lifecycle.get("restart_policy")
    if start_order != expected_start or stop_order != expected_stop or not isinstance(restart, dict):
        raise OperationalError("operations_manifest_incompatible", "service ordering is incompatible")
    if not (
        restart.get("mode") == "on-failure"
        and restart.get("restart_seconds") == 5
        and restart.get("start_limit_interval_seconds") == 600
        and restart.get("start_limit_burst") == 2
        and restart.get("automatic_recoveries_per_failure_window") == 1
        and restart.get("configuration_exit_status") == CONFIGURATION_EXIT_STATUS
        and lifecycle.get("startup_hard_seconds") == 300
        and lifecycle.get("graceful_drain_seconds") == 54
        and lifecycle.get("hard_stop_seconds") == 75
    ):
        raise OperationalError("operations_manifest_incompatible", "restart/drain policy is incompatible")
    by_name: dict[str, dict[str, object]] = {}
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("name"), str):
            raise OperationalError("operations_manifest_invalid", "component declaration is invalid")
        name = str(component["name"])
        if name in by_name:
            raise OperationalError("operations_manifest_invalid", "component declaration is duplicated")
        by_name[name] = component
    required_components = {
        "livekit", "web-gateway", "controller", "local-stt", "local-tts",
        "selected-local-llm", "provider-adapter", "avatar-host", "mvp-eye", "cloud-llm",
    }
    if set(by_name) != required_components:
        raise OperationalError("operations_manifest_incompatible", "operational component set is incompatible")
    cloud = by_name["cloud-llm"]
    if not (
        cloud.get("location") == "external"
        and cloud.get("supervision") == "external-readiness-only"
        and cloud.get("active") is False
        and cloud.get("admission_required") is False
    ):
        raise OperationalError("operations_manifest_incompatible", "cloud supervision boundary is invalid")
    contracts = manifest.get("contracts")
    artifacts = manifest.get("artifacts")
    runtimes = manifest.get("python_runtimes")
    sustained = manifest.get("sustained_acceptance")
    if contracts != EXPECTED_CONTRACTS:
        raise OperationalError("operations_manifest_incompatible", "contract/client compatibility changed")
    if not isinstance(artifacts, list):
        raise OperationalError("operations_manifest_invalid", "artifact declarations are invalid")
    artifact_names = {
        artifact.get("name") for artifact in artifacts if isinstance(artifact, dict)
    }
    if artifact_names != EXPECTED_ARTIFACT_NAMES or len(artifacts) != len(artifact_names):
        raise OperationalError("operations_manifest_incompatible", "selected artifact set changed")
    if any(
        not isinstance(artifact.get("sha256"), str)
        or not SHA256.fullmatch(str(artifact["sha256"]))
        for artifact in artifacts if isinstance(artifact, dict)
    ):
        raise OperationalError("operations_manifest_invalid", "artifact checksum declaration is invalid")
    if not isinstance(runtimes, list) or {
        runtime.get("name") for runtime in runtimes if isinstance(runtime, dict)
    } != {"slice6", "stt"}:
        raise OperationalError("operations_manifest_incompatible", "selected Python runtime set changed")
    cache_roots = disk.get("cache_roots")
    if not isinstance(cache_roots, list):
        raise OperationalError("operations_manifest_invalid", "cache bounds are invalid")
    observed_cache_bounds = {
        cache.get("name"): cache.get("maximum_bytes")
        for cache in cache_roots if isinstance(cache, dict)
    }
    if not (
        disk.get("cleanup_policy") == "refuse-without-deleting"
        and disk.get("minimum_free_bytes") == 8_589_934_592
        and disk.get("release_maximum_count") == 3
        and disk.get("release_maximum_bytes") == 1_073_741_824
        and observed_cache_bounds == EXPECTED_CACHE_BOUNDS
        and sustained == EXPECTED_SUSTAINED_ACCEPTANCE
    ):
        raise OperationalError("operations_manifest_incompatible", "disk/cache/sustained policy changed")
    return manifest


def parse_server_configuration(path: Path) -> dict[str, str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise OperationalError("configuration_unavailable", "server configuration is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise OperationalError("configuration_unavailable", "server configuration is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise OperationalError("configuration_unavailable", "server configuration is not a regular file")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        os.close(descriptor)
        raise OperationalError("configuration_permissions", "server configuration ownership or mode is unsafe")
    values: dict[str, str] = {}
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            content = source.read(MAX_COMMAND_OUTPUT_BYTES + 1)
    except (OSError, UnicodeError) as error:
        raise OperationalError("configuration_unavailable", "server configuration is unreadable") from error
    if len(content.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
        raise OperationalError("configuration_invalid", "server configuration exceeds its size bound")
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise OperationalError("configuration_invalid", "server configuration syntax is invalid")
        name, value = stripped.split("=", 1)
        if not ENV_NAME.fullmatch(name) or name not in ALLOWED_CONFIGURATION_NAMES:
            raise OperationalError("configuration_invalid", "server configuration contains an unsupported name")
        if name in values:
            raise OperationalError("configuration_invalid", "server configuration contains a duplicate name")
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or value != value.strip() or any(ord(character) < 32 for character in value):
            raise OperationalError("configuration_invalid", "server configuration contains an invalid value")
        values[name] = value
    if not REQUIRED_CONFIGURATION_NAMES.issubset(values):
        raise OperationalError("configuration_invalid", "required server configuration is missing")
    return values


def _validated_url(
    value: str, *, schemes: set[str], loopback: bool = False,
) -> object:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise OperationalError("configuration_invalid", "configured URL is invalid") from error
    if (
        parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment or port is None
    ):
        raise OperationalError("configuration_invalid", "configured URL is invalid")
    if loopback and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise OperationalError("configuration_invalid", "internal URL is not loopback-only")
    return parsed


def validate_server_configuration(values: Mapping[str, str]) -> dict[str, object]:
    api_key = values["LIVEKIT_API_KEY"]
    api_secret = values["LIVEKIT_API_SECRET"]
    if len(api_key) > 128 or not 16 <= len(api_secret) <= 256:
        raise OperationalError("configuration_invalid", "LiveKit signing material is outside bounds")
    internal = _validated_url(
        values["LIVEKIT_INTERNAL_URL"], schemes={"ws"}, loopback=True,
    )
    public = _validated_url(
        values.get("LIVEKIT_PUBLIC_URL", "ws://127.0.0.1:7880"),
        schemes={"ws", "wss"},
    )
    app = _validated_url(
        values.get("SLICE6_APP_PUBLIC_URL", "http://127.0.0.1:8000"),
        schemes={"http", "https"},
    )
    if internal.hostname not in {"127.0.0.1", "localhost", "::1"} or internal.port != 7880:
        raise OperationalError("configuration_invalid", "LiveKit internal endpoint changed")
    for parsed, secure_scheme in ((public, "wss"), (app, "https")):
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = parsed.hostname == "localhost"
        if not is_loopback and parsed.scheme != secure_scheme:
            raise OperationalError(
                "configuration_invalid", "non-loopback public URL must use TLS",
            )
    capture = values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE", "0")
    capture_root = values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT")
    if capture not in {"0", "1"} or (capture == "0" and capture_root is not None):
        raise OperationalError("configuration_invalid", "diagnostic capture opt-in is invalid")
    if capture == "1":
        runtime_root = SYSTEMD_RUNTIME_ROOT
        configured_capture_root = (
            Path(capture_root).resolve(strict=False) if capture_root else None
        )
        if (
            configured_capture_root is None or not configured_capture_root.is_absolute()
            or configured_capture_root == runtime_root
            or not configured_capture_root.is_relative_to(runtime_root)
        ):
            raise OperationalError("configuration_invalid", "diagnostic capture root is invalid")
        ttl = _bounded_integer(
            values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS", "900"),
            minimum=60, maximum=3600, label="diagnostic capture TTL",
        )
    else:
        if "VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS" in values:
            raise OperationalError("configuration_invalid", "diagnostic capture TTL requires opt-in")
        ttl = None
    effective_values = dict(values)
    effective_values.setdefault("LIVEKIT_PUBLIC_URL", "ws://127.0.0.1:7880")
    effective_values.setdefault("SLICE6_APP_PUBLIC_URL", "http://127.0.0.1:8000")
    public_values = {
        name: value for name, value in effective_values.items()
        if name not in SECRET_CONFIGURATION_NAMES
    }
    return {
        "public_values": public_values,
        "public_fingerprint": _sha256_bytes(_canonical_json(public_values)),
        "diagnostic_capture_ttl_seconds": ttl,
        "secret_configuration": "present-and-redacted",
    }


def _bounded_integer(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise OperationalError("configuration_invalid", f"{label} is invalid") from error
    if not minimum <= parsed <= maximum or str(parsed) != value:
        raise OperationalError("configuration_invalid", f"{label} is outside bounds")
    return parsed


def verify_tracked_manifest_alignment(
    *, source_root: Path, operations_manifest: Mapping[str, object],
) -> None:
    local_lfm = _json_object(
        source_root / "config/local-lfm-v1.json", code="operations_manifest_incompatible",
    )
    silero = _json_object(
        source_root / "config/silero-kseniya-tts-v1.json",
        code="operations_manifest_incompatible",
    )
    artifacts = operations_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise OperationalError("operations_manifest_invalid", "artifact declarations are invalid")
    by_name = {
        str(artifact["name"]): artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and isinstance(artifact.get("name"), str)
    }
    lfm_model = local_lfm.get("model")
    lfm_runtime = local_lfm.get("runtime")
    silero_model = silero.get("model")
    silero_runtime = silero.get("runtime")
    if not all(isinstance(value, dict) for value in (
        lfm_model, lfm_runtime, silero_model, silero_runtime,
    )):
        raise OperationalError("operations_manifest_incompatible", "selected runtime manifest is invalid")
    expected = {
        "lfm2.5-q4-k-m": (lfm_model.get("sha256"), lfm_model.get("size_bytes")),
        "llama-server-b10357": (lfm_runtime.get("binary_sha256"), None),
        "silero-v5-5-ru": (silero_model.get("sha256"), silero_model.get("size_bytes")),
        "silero-python": (silero_runtime.get("python_executable_sha256"), None),
        "silero-torch-extension": (silero_runtime.get("torch_c_sha256"), None),
        "silero-libtorch-cpu": (silero_runtime.get("libtorch_cpu_sha256"), None),
    }
    if not (
        local_lfm.get("provider_identity") == EXPECTED_PROVIDER_IDENTITY
        and lfm_runtime.get("automatic_fallback") is False
        and silero_runtime.get("automatic_retry") is False
        and silero_runtime.get("automatic_fallback") is False
        and silero_runtime.get("workers") == 2
    ):
        raise OperationalError("operations_manifest_incompatible", "selected runtime manifest policy changed")
    for name, (expected_hash, expected_size) in expected.items():
        artifact = by_name.get(name, {})
        if artifact.get("sha256") != expected_hash or (
            expected_size is not None and artifact.get("size_bytes") != expected_size
        ):
            raise OperationalError("operations_manifest_incompatible", "selected artifact manifests disagree")


def verify_artifacts(manifest: Mapping[str, object], *, home: Path) -> int:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise OperationalError("operations_manifest_invalid", "artifact manifest is empty")
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("name"), str):
            raise OperationalError("operations_manifest_invalid", "artifact declaration is invalid")
        name = str(artifact["name"])
        if name in seen:
            raise OperationalError("operations_manifest_invalid", "artifact declaration is duplicated")
        seen.add(name)
        paths: list[Path]
        if "path" in artifact:
            paths = [_expanded_path(str(artifact["path"]), home=home)]
        elif "path_glob" in artifact:
            pattern = str(artifact["path_glob"]).replace("{home}", str(home))
            paths = [Path(value) for value in sorted(glob.glob(pattern))]
            if len(paths) != artifact.get("matches"):
                raise OperationalError("artifact_incompatible", f"artifact {name} match count is incompatible")
        else:
            raise OperationalError("operations_manifest_invalid", "artifact path is missing")
        for path in paths:
            if artifact.get("resolve_symlink") is True:
                path = path.resolve()
            try:
                metadata = path.stat()
            except OSError as error:
                raise OperationalError("artifact_unavailable", f"artifact {name} is unavailable") from error
            expected_size = artifact.get("size_bytes")
            if not stat.S_ISREG(metadata.st_mode) or (
                isinstance(expected_size, int) and metadata.st_size != expected_size
            ):
                raise OperationalError("artifact_incompatible", f"artifact {name} size/type is incompatible")
            if artifact.get("executable") is True and not os.access(path, os.X_OK):
                raise OperationalError("artifact_incompatible", f"artifact {name} is not executable")
            expected_hash = artifact.get("sha256")
            if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
                raise OperationalError("artifact_incompatible", f"artifact {name} checksum is incompatible")
    return len(seen)


def verify_python_runtimes(manifest: Mapping[str, object], *, home: Path) -> None:
    runtimes = manifest.get("python_runtimes")
    if not isinstance(runtimes, list):
        raise OperationalError("operations_manifest_invalid", "Python runtime declarations are invalid")
    script = (
        "import importlib.metadata as m,json,sys;"
        "wanted=json.loads(sys.argv[1]);"
        "print(json.dumps({n:m.version(n) for n in wanted},sort_keys=True))"
    )
    for runtime in runtimes:
        if not isinstance(runtime, dict) or not isinstance(runtime.get("packages"), dict):
            raise OperationalError("operations_manifest_invalid", "Python runtime declaration is invalid")
        python = _expanded_path(str(runtime.get("python", "")), home=home)
        expected = runtime["packages"]
        if not python.is_file() or not os.access(python, os.X_OK):
            raise OperationalError("runtime_unavailable", "a pinned Python runtime is unavailable")
        try:
            result = subprocess.run(
                [str(python), "-I", "-c", script, json.dumps(sorted(expected))],
                capture_output=True, text=True, timeout=15, check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OperationalError("runtime_unavailable", "a pinned Python runtime did not respond") from error
        if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
            raise OperationalError("runtime_incompatible", "a pinned Python runtime is incompatible")
        try:
            observed = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise OperationalError("runtime_incompatible", "a pinned Python runtime report is invalid") from error
        if observed != expected:
            raise OperationalError("runtime_incompatible", "a pinned Python package set is incompatible")


def directory_size(path: Path) -> int:
    """Measure without following symlinks or deleting anything."""
    total = 0
    if not path.exists():
        return 0
    try:
        for root, directories, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
            for name in files:
                item = root_path / name
                try:
                    total += item.lstat().st_size
                except OSError as error:
                    raise OperationalError("cache_unavailable", "an operational cache cannot be measured") from error
    except OSError as error:
        raise OperationalError("cache_unavailable", "an operational cache cannot be measured") from error
    return total


def verify_disk_policy(
    manifest: Mapping[str, object], *, home: Path, filesystem_path: Path,
) -> tuple[int, dict[str, int]]:
    disk = manifest.get("disk")
    if not isinstance(disk, dict) or not isinstance(disk.get("cache_roots"), list):
        raise OperationalError("operations_manifest_invalid", "disk policy is invalid")
    try:
        usage = shutil.disk_usage(filesystem_path)
    except OSError as error:
        raise OperationalError("disk_preflight_failed", "deployment filesystem is unavailable") from error
    minimum_free = disk.get("minimum_free_bytes")
    if not isinstance(minimum_free, int) or usage.free < minimum_free:
        raise OperationalError("disk_pressure", "free disk is below the admission preflight bound")
    measured: dict[str, int] = {}
    for cache in disk["cache_roots"]:
        if not isinstance(cache, dict) or not isinstance(cache.get("name"), str):
            raise OperationalError("operations_manifest_invalid", "cache bound is invalid")
        path = _expanded_path(str(cache.get("path", "")), home=home)
        size = directory_size(path)
        maximum = cache.get("maximum_bytes")
        if not isinstance(maximum, int) or size > maximum:
            raise OperationalError("cache_pressure", f"cache {cache['name']} exceeds its non-destructive bound")
        measured[str(cache["name"])] = size
    return usage.free, measured


def validate_host(
    *, source_root: Path, config_path: Path, state_root: Path = DEFAULT_STATE_ROOT,
    verify_artifact_state: bool = True,
    configuration_values: Mapping[str, str] | None = None,
) -> ValidationReport:
    manifest = load_operations_manifest(source_root / DEFAULT_MANIFEST_RELATIVE)
    verify_tracked_manifest_alignment(
        source_root=source_root, operations_manifest=manifest,
    )
    values = (
        parse_server_configuration(config_path)
        if configuration_values is None else dict(configuration_values)
    )
    configuration = validate_server_configuration(values)
    artifact_count = 0
    if verify_artifact_state:
        artifact_count = verify_artifacts(manifest, home=Path.home())
        verify_python_runtimes(manifest, home=Path.home())
    state_root_parent = state_root.expanduser().resolve().parent
    filesystem_path = state_root_parent
    while not filesystem_path.exists() and filesystem_path != filesystem_path.parent:
        filesystem_path = filesystem_path.parent
    available, caches = verify_disk_policy(
        manifest, home=Path.home(), filesystem_path=filesystem_path,
    )
    return ValidationReport(
        build_id=None,
        release_id=None,
        provider_mode="local",
        artifact_count=artifact_count,
        cache_bytes=caches,
        disk_available_bytes=available,
        configuration_fingerprint=str(configuration["public_fingerprint"]),
    )


def _operational_release_id(
    *, commit: object, tree: object, manifest: object, configuration: object,
    configuration_locator: object, release_tree: object,
) -> str:
    return _sha256_bytes(_canonical_json({
        "commit": commit,
        "tree": tree,
        "manifest": manifest,
        "configuration": configuration,
        "configuration_locator": configuration_locator,
        "release_tree": release_tree,
    }))[:24]


def release_tree_digest(root: Path) -> str:
    """Hash every payload path, type, mode, target, size, and file content."""
    entries: list[bytes] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if relative == Path("release.json"):
            continue
        status = path.lstat()
        mode = stat.S_IMODE(status.st_mode)
        if path.is_symlink():
            value = (
                f"L\0{relative.as_posix()}\0{mode:o}\0{os.readlink(path)}"
            ).encode("utf-8")
        elif stat.S_ISDIR(status.st_mode):
            value = f"D\0{relative.as_posix()}\0{mode:o}".encode("utf-8")
        elif stat.S_ISREG(status.st_mode):
            value = (
                f"F\0{relative.as_posix()}\0{mode:o}\0{status.st_size}\0{sha256_file(path)}"
            ).encode("utf-8")
        else:
            raise OperationalError("release_incompatible", "release contains an unsupported file type")
        entries.append(value)
    return _sha256_bytes(b"\n".join(entries))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(path)
        elif stat.S_ISREG(metadata.st_mode):
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=True, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_symlink(path: Path, target: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _remove_symlink(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise OperationalError(
            "release_state_invalid", "release link is unavailable",
        ) from error
    if not stat.S_ISLNK(metadata.st_mode):
        raise OperationalError(
            "release_state_invalid", f"{path.name} is not a release link",
        )
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        raise OperationalError(
            "release_state_invalid", "release link could not be removed",
        ) from error


def _run_git(source_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *arguments], capture_output=True, text=True,
        timeout=30, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
        raise OperationalError("source_unavailable", "committed source identity is unavailable")
    return result.stdout.strip()


class ReleaseStore:
    def __init__(self, state_root: Path = DEFAULT_STATE_ROOT) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.releases = self.state_root / "releases"
        self.current_link = self.state_root / "current"
        self.previous_link = self.state_root / "previous"
        self.transaction_path = self.state_root / "link-transaction.json"
        self.lock_path = self.state_root / "operations.lock"
        self._lock_descriptor: int | None = None
        self._lock_depth = 0

    def _initialize(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        self.releases.mkdir(parents=True, exist_ok=True, mode=0o700)

    def locked(self) -> Iterator[None]:
        self._initialize()
        store = self

        class _Lock:
            def __enter__(inner_self) -> None:
                if store._lock_depth:
                    store._lock_depth += 1
                    return
                descriptor = os.open(
                    store.lock_path, os.O_RDWR | os.O_CREAT, 0o600,
                )
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    os.close(descriptor)
                    raise OperationalError(
                        "operations_busy",
                        "another release or service operation is already in progress",
                    ) from error
                store._lock_descriptor = descriptor
                store._lock_depth = 1
                try:
                    store._recover_link_transaction()
                except BaseException:
                    store._lock_descriptor = None
                    store._lock_depth = 0
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
                    raise

            def __exit__(inner_self, *_arguments: object) -> None:
                store._lock_depth -= 1
                if store._lock_depth:
                    return
                descriptor = store._lock_descriptor
                store._lock_descriptor = None
                if descriptor is None:
                    return
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

        return _Lock()  # type: ignore[return-value]

    def _recover_link_transaction(self) -> None:
        try:
            metadata = self.transaction_path.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise OperationalError(
                "release_state_invalid", "release link transaction is unavailable",
            ) from error
        if not (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == 0o600
        ):
            raise OperationalError(
                "release_state_invalid", "release link transaction custody changed",
            )
        transaction = _json_object(
            self.transaction_path, code="release_state_invalid",
        )
        _require_exact_keys(
            transaction,
            {"schema_version", "current", "previous"},
            label="release link transaction",
            code="release_state_invalid",
        )
        if transaction.get("schema_version") != "voice-agent.release-links.v1":
            raise OperationalError(
                "release_state_invalid", "release link transaction is incompatible",
            )
        updates: list[tuple[Path, str | None]] = []
        for name, link in (
            ("previous", self.previous_link),
            ("current", self.current_link),
        ):
            target = transaction.get(name)
            if target is None and name == "current":
                raise OperationalError(
                    "release_state_invalid", "release link transaction is invalid",
                )
            if target is not None:
                if not isinstance(target, str):
                    raise OperationalError(
                        "release_state_invalid", "release link transaction is invalid",
                    )
                target_path = (self.state_root / target).resolve()
                if (
                    target != f"releases/{target_path.name}"
                    or target_path.parent != self.releases.resolve()
                    or not RELEASE_ID.fullmatch(target_path.name)
                    or not target_path.is_dir()
                ):
                    raise OperationalError(
                        "release_state_invalid", "release link transaction target is invalid",
                    )
            updates.append((link, target))
        for link, target in updates:
            if target is None:
                _remove_symlink(link)
            else:
                _atomic_symlink(link, target)
        self.transaction_path.unlink()
        _fsync_directory(self.state_root)

    def _commit_links(
        self, *, current: str, previous: str | None = None,
    ) -> None:
        _atomic_json(self.transaction_path, {
            "schema_version": "voice-agent.release-links.v1",
            "current": current,
            "previous": previous,
        })
        self._recover_link_transaction()

    def _linked_release(self, link: Path) -> Path | None:
        if not link.is_symlink():
            if link.exists():
                raise OperationalError("release_state_invalid", f"{link.name} is not a release link")
            return None
        target = (link.parent / os.readlink(link)).resolve()
        releases_root = self.releases.resolve()
        if target.parent != releases_root or not RELEASE_ID.fullmatch(target.name):
            raise OperationalError("release_state_invalid", f"{link.name} points outside releases")
        return target

    def current(self) -> Path | None:
        return self._linked_release(self.current_link)

    def previous(self) -> Path | None:
        return self._linked_release(self.previous_link)

    def _release_limit(self, manifest: Mapping[str, object]) -> tuple[int, int]:
        disk = manifest.get("disk")
        if not isinstance(disk, dict):
            raise OperationalError("operations_manifest_invalid", "release disk policy is invalid")
        count = disk.get("release_maximum_count")
        size = disk.get("release_maximum_bytes")
        if not isinstance(count, int) or not isinstance(size, int):
            raise OperationalError("operations_manifest_invalid", "release bounds are invalid")
        return count, size

    def deploy(self, *, source_root: Path, config_path: Path) -> dict[str, object]:
        source_root = source_root.resolve()
        config_path = Path(os.path.abspath(config_path.expanduser()))
        if self.state_root.is_relative_to(source_root):
            raise OperationalError("release_state_invalid", "release state must remain outside source")
        status = _run_git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
        if status:
            raise OperationalError("source_dirty", "deployment requires an exact clean committed source")
        commit = _run_git(source_root, "rev-parse", "HEAD")
        tree = _run_git(source_root, "rev-parse", f"{commit}^{{tree}}")
        if not BUILD_ID.fullmatch(commit) or not BUILD_ID.fullmatch(tree):
            raise OperationalError("source_unavailable", "committed source identity is invalid")
        try:
            configuration_relative = config_path.relative_to(source_root).as_posix()
        except ValueError:
            configuration_relative = None
        if configuration_relative is not None and _run_git(
            source_root, "ls-tree", "-r", "--name-only", commit, "--",
            configuration_relative,
        ):
            raise OperationalError(
                "configuration_tracked",
                "selected private server configuration is tracked by the release commit",
            )
        host_report = validate_host(
            source_root=source_root, config_path=config_path, state_root=self.state_root,
        )
        manifest = load_operations_manifest(source_root / DEFAULT_MANIFEST_RELATIVE)
        manifest_digest = sha256_file(source_root / DEFAULT_MANIFEST_RELATIVE)
        locator_digest = _sha256_bytes(str(config_path).encode("utf-8"))
        with self.locked():
            current = self.current()
            current_release: dict[str, object] | None = None
            if current is not None:
                try:
                    current_release = validate_release(
                        current, state_root=self.state_root, verify_host_state=True,
                    )
                except OperationalError:
                    current_release = None
                if current_release is not None and all((
                    current_release.get("build_id") == commit,
                    current_release.get("source_tree") == tree,
                    current_release.get("operations_manifest_sha256") == manifest_digest,
                    current_release.get("configuration_path") == str(config_path),
                    current_release.get("configuration_locator_sha256") == locator_digest,
                    current_release.get("configuration_fingerprint")
                    == host_report.configuration_fingerprint,
                )):
                    return {
                        "schema_version": "voice-agent.deployment-result.v1",
                        "status": "no-op",
                        "changed": False,
                        "release_id": current.name,
                        "build_id": current_release["build_id"],
                    }

            existing = [path for path in self.releases.iterdir() if path.is_dir()]
            existing_bytes = sum(directory_size(path) for path in existing)
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.releases))
            try:
                archive = subprocess.run(
                    ["git", "-C", str(source_root), "archive", "--format=tar", commit],
                    capture_output=True, timeout=60, check=False,
                    env={
                        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                    },
                )
                if archive.returncode != 0:
                    raise OperationalError(
                        "release_build_failed", "committed source archive failed",
                    )
                with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
                    bundle.extractall(stage, filter="data")
                host_report = validate_host(
                    source_root=stage, config_path=config_path, state_root=self.state_root,
                )
                manifest = load_operations_manifest(stage / DEFAULT_MANIFEST_RELATIVE)
                manifest_digest = sha256_file(stage / DEFAULT_MANIFEST_RELATIVE)
                maximum_count, maximum_bytes = self._release_limit(manifest)
                web_root = stage / "web"
                web_dist = web_root / "dist"
                if web_dist.exists():
                    shutil.rmtree(web_dist)
                dependencies = source_root / "web" / "node_modules"
                dependency_link = web_root / "node_modules"
                if (
                    not dependencies.is_dir()
                    or dependency_link.exists()
                    or dependency_link.is_symlink()
                ):
                    raise OperationalError(
                        "release_build_failed", "versioned client dependencies are unavailable",
                    )
                environment = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": str(Path.home()),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "VITE_APP_VERSION": commit,
                }
                os.symlink(dependencies, dependency_link, target_is_directory=True)
                try:
                    build = subprocess.run(
                        [
                            "npm", "run", "build", "--", "--outDir", str(web_dist),
                            "--emptyOutDir",
                        ],
                        cwd=web_root, env=environment,
                        capture_output=True, text=True, timeout=180, check=False,
                    )
                finally:
                    dependency_link.unlink(missing_ok=True)
                if build.returncode != 0:
                    raise OperationalError(
                        "release_build_failed", "versioned client build failed",
                    )
                build_seen = False
                for asset in (web_dist / "assets").glob("*.js"):
                    try:
                        if commit.encode("ascii") in asset.read_bytes():
                            build_seen = True
                            break
                    except OSError as error:
                        raise OperationalError(
                            "release_build_failed", "versioned client build is unreadable",
                        ) from error
                if not build_seen:
                    raise OperationalError(
                        "release_build_failed",
                        "client build does not report the exact commit",
                    )
                tree_digest = release_tree_digest(stage)
                release_id = _operational_release_id(
                    commit=commit,
                    tree=tree,
                    manifest=manifest_digest,
                    configuration=host_report.configuration_fingerprint,
                    configuration_locator=locator_digest,
                    release_tree=tree_digest,
                )
                target = self.releases / release_id
                release_document = {
                    "schema_version": RELEASE_SCHEMA,
                    "release_id": release_id,
                    "build_id": commit,
                    "source_tree": tree,
                    "operations_schema": OPERATIONS_SCHEMA,
                    "operations_manifest_sha256": manifest_digest,
                    "configuration_path": str(config_path),
                    "configuration_locator_sha256": locator_digest,
                    "configuration_fingerprint": host_report.configuration_fingerprint,
                    "provider_mode": "local",
                    "external_provider_supervised": False,
                    "automatic_fallback": False,
                    "release_tree_sha256": tree_digest,
                }
                _atomic_json(stage / "release.json", release_document)
                _fsync_tree(stage)
                stage_bytes = directory_size(stage)
                if target.exists():
                    shutil.rmtree(stage)
                else:
                    if len(existing) >= maximum_count:
                        raise OperationalError(
                            "release_cache_pressure",
                            "release count bound reached; no release was deleted",
                        )
                    if existing_bytes + stage_bytes > maximum_bytes:
                        raise OperationalError(
                            "release_cache_pressure",
                            "release byte bound reached; no existing release was deleted",
                        )
                    os.rename(stage, target)
                    _fsync_directory(self.releases)
            except BaseException:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            validate_release(target, state_root=self.state_root, verify_host_state=True)
            retained_previous: str | None = None
            previous_target: str | None = None
            if current is not None and current != target and current_release is not None:
                previous_target = f"releases/{current.name}"
                retained_previous = current.name
            self._commit_links(
                current=f"releases/{target.name}", previous=previous_target,
            )
            return {
                "schema_version": "voice-agent.deployment-result.v1",
                "status": "activated",
                "changed": current != target,
                "release_id": release_id,
                "build_id": commit,
                "previous_release_id": retained_previous,
            }

    def rollback(self, *, required_system_unit: Path | None = None) -> dict[str, object]:
        with self.locked():
            current = self.current()
            previous = self.previous()
            if current is None or previous is None:
                raise OperationalError("rollback_unavailable", "no verified prior release is available")
            previous_document = validate_release(
                previous, state_root=self.state_root, verify_host_state=True,
            )
            if required_system_unit is not None:
                target_unit = previous / "ops/systemd/voice-agent-v2.service"
                try:
                    unit_matches = (
                        target_unit.is_file()
                        and required_system_unit.is_file()
                        and target_unit.stat().st_size == required_system_unit.stat().st_size
                        and sha256_file(target_unit) == sha256_file(required_system_unit)
                    )
                except (OSError, OperationalError):
                    unit_matches = False
                if not unit_matches:
                    raise OperationalError(
                        "rollback_unit_incompatible",
                        "prior release systemd unit differs from the installed unit",
                    )
            try:
                current_document = validate_release(
                    current, state_root=self.state_root, verify_host_state=False,
                )
            except OperationalError:
                current_document = None
            self._commit_links(
                current=f"releases/{previous.name}",
                previous=f"releases/{current.name}",
            )
            return {
                "schema_version": "voice-agent.rollback-result.v1",
                "status": "rolled-back",
                "release_id": previous.name,
                "build_id": previous_document["build_id"],
                "replaced_release_id": current.name,
                "replaced_build_id": (
                    current_document["build_id"] if current_document is not None else None
                ),
                "replaced_release_compatible": current_document is not None,
            }


def _validate_release_snapshot(
    release_root: Path, *, state_root: Path, verify_host_state: bool,
) -> tuple[dict[str, object], dict[str, str]]:
    release_root = release_root.resolve()
    releases_root = (state_root.expanduser().resolve() / "releases").resolve()
    if release_root.parent != releases_root or not RELEASE_ID.fullmatch(release_root.name):
        raise OperationalError("release_incompatible", "release is outside the bounded store")
    manifest_path = release_root / "release.json"
    try:
        root_status = release_root.lstat()
        manifest_status = manifest_path.lstat()
    except OSError as error:
        raise OperationalError("release_incompatible", "release custody is unavailable") from error
    if not (
        stat.S_ISDIR(root_status.st_mode)
        and root_status.st_uid == os.geteuid()
        and stat.S_IMODE(root_status.st_mode) == 0o700
        and stat.S_ISREG(manifest_status.st_mode)
        and manifest_status.st_uid == os.geteuid()
        and stat.S_IMODE(manifest_status.st_mode) == 0o600
    ):
        raise OperationalError("release_incompatible", "release custody or mode changed")
    document = _json_object(manifest_path, code="release_incompatible")
    required = {
        "schema_version", "release_id", "build_id", "source_tree", "operations_schema",
        "operations_manifest_sha256", "configuration_path", "configuration_locator_sha256",
        "configuration_fingerprint", "provider_mode",
        "external_provider_supervised", "automatic_fallback", "release_tree_sha256",
    }
    _require_exact_keys(
        document, required, label="release manifest", code="release_incompatible",
    )
    if not (
        document.get("schema_version") == RELEASE_SCHEMA
        and document.get("release_id") == release_root.name
        and isinstance(document.get("build_id"), str)
        and BUILD_ID.fullmatch(str(document["build_id"]))
        and isinstance(document.get("source_tree"), str)
        and BUILD_ID.fullmatch(str(document["source_tree"]))
        and isinstance(document.get("operations_manifest_sha256"), str)
        and SHA256.fullmatch(str(document["operations_manifest_sha256"]))
        and isinstance(document.get("configuration_locator_sha256"), str)
        and SHA256.fullmatch(str(document["configuration_locator_sha256"]))
        and isinstance(document.get("configuration_fingerprint"), str)
        and SHA256.fullmatch(str(document["configuration_fingerprint"]))
        and isinstance(document.get("release_tree_sha256"), str)
        and SHA256.fullmatch(str(document["release_tree_sha256"]))
        and document.get("operations_schema") == OPERATIONS_SCHEMA
        and document.get("provider_mode") == "local"
        and document.get("external_provider_supervised") is False
        and document.get("automatic_fallback") is False
    ):
        raise OperationalError("release_incompatible", "release identity or provider policy is incompatible")
    expected_release_id = _operational_release_id(
        commit=document["build_id"],
        tree=document["source_tree"],
        manifest=document["operations_manifest_sha256"],
        configuration=document["configuration_fingerprint"],
        configuration_locator=document["configuration_locator_sha256"],
        release_tree=document["release_tree_sha256"],
    )
    if expected_release_id != release_root.name:
        raise OperationalError("release_incompatible", "release metadata is not bound to its identity")
    operations_path = release_root / DEFAULT_MANIFEST_RELATIVE
    load_operations_manifest(operations_path)
    if sha256_file(operations_path) != document.get("operations_manifest_sha256"):
        raise OperationalError("release_incompatible", "release operations manifest changed")
    if release_tree_digest(release_root) != document.get("release_tree_sha256"):
        raise OperationalError("release_incompatible", "release file inventory changed")
    config_path = Path(str(document["configuration_path"]))
    if _sha256_bytes(str(config_path).encode("utf-8")) != document.get("configuration_locator_sha256"):
        raise OperationalError("release_incompatible", "release configuration locator changed")
    values = parse_server_configuration(config_path)
    configuration = validate_server_configuration(values)
    if configuration["public_fingerprint"] != document.get("configuration_fingerprint"):
        raise OperationalError("release_incompatible", "prior configuration is no longer compatible")
    web_dist = release_root / "web" / "dist"
    if not (web_dist / "index.html").is_file():
        raise OperationalError("release_incompatible", "versioned client build is missing")
    build_id = str(document["build_id"])
    if not any(
        build_id.encode("ascii") in asset.read_bytes()
        for asset in (web_dist / "assets").glob("*.js") if asset.is_file()
    ):
        raise OperationalError("release_incompatible", "client build identity is missing")
    if verify_host_state:
        validate_host(
            source_root=release_root,
            config_path=config_path,
            state_root=state_root,
            configuration_values=values,
        )
    return document, values


def validate_release(
    release_root: Path, *, state_root: Path, verify_host_state: bool,
) -> dict[str, object]:
    document, _values = _validate_release_snapshot(
        release_root, state_root=state_root, verify_host_state=verify_host_state,
    )
    return document


def _prepare_mutable_runtime_directory(path: Path) -> Path:
    for directory in (path.parent, path):
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
            status = directory.lstat()
        except OSError as error:
            raise OperationalError("runtime_unavailable", "mutable runtime state is unavailable") from error
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_mode & 0o077
        ):
            raise OperationalError("runtime_incompatible", "mutable runtime state is not private")
    return path


def execute_release(state_root: Path = DEFAULT_STATE_ROOT) -> None:
    store = ReleaseStore(state_root)
    release_root = store.current()
    if release_root is None:
        raise OperationalError("deployment_unavailable", "no active operational release exists")
    document, values = _validate_release_snapshot(
        release_root, state_root=store.state_root, verify_host_state=True,
    )
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("LITELLM_") and name not in ALLOWED_CONFIGURATION_NAMES
    }
    environment.update(values)
    pycache = _prepare_mutable_runtime_directory(
        SYSTEMD_RUNTIME_ROOT / "pycache"
    )
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["PYTHONPYCACHEPREFIX"] = str(pycache)
    environment["VOICE_AGENT_BUILD_ID"] = str(document["build_id"])
    environment["VOICE_AGENT_RELEASE_ID"] = str(document["release_id"])
    environment["SLICE6_WEB_DIST"] = str(release_root / "web" / "dist")
    environment["PYTHONPATH"] = str(release_root / "src")
    python = Path.home() / ".cache/voice-agent-v2/slice-6/runtime/venv/bin/python"
    script = release_root / "scripts/run_slice6.py"
    os.chdir(release_root)
    os.execve(str(python), [str(python), "-B", str(script)], environment)


def _finite_measurement(
    value: object, *, minimum: float = 0.0, maximum: float | None = None,
) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        numeric = float(value)
    except OverflowError:
        return False
    return (
        math.isfinite(numeric)
        and numeric >= minimum
        and (maximum is None or numeric <= maximum)
    )


def evaluate_sustained_run(
    manifest: Mapping[str, object], *, turns: list[Mapping[str, object]],
    avatar: Mapping[str, object],
) -> dict[str, object]:
    thresholds = manifest.get("sustained_acceptance")
    if not isinstance(thresholds, dict):
        raise OperationalError("operations_manifest_invalid", "sustained thresholds are invalid")
    minimum_turns = thresholds.get("minimum_turns")
    if not isinstance(minimum_turns, int) or len(turns) < minimum_turns:
        raise OperationalError("sustained_sample_too_small", "sustained run has too few turns")
    if any(
        not isinstance(turn, Mapping)
        or turn.get("outcome") not in {"completed", "failed", "interrupted"}
        or not _finite_measurement(turn.get("total_turn_ms"))
        or not _finite_measurement(turn.get("process_rss_mib"))
        or not _finite_measurement(turn.get("gpu_vram_used_mib"))
        or (
            turn.get("cancellation_latency_ms") is not None
            and not _finite_measurement(turn.get("cancellation_latency_ms"))
        )
        for turn in turns
    ) or not (
        _finite_measurement(avatar.get("healthy_frame_ratio"), maximum=1.0)
        and _finite_measurement(avatar.get("fps"))
    ):
        raise OperationalError(
            "sustained_report_invalid",
            "sustained measurements must be finite non-negative bounded scalars",
        )
    completed = sum(turn.get("outcome") == "completed" for turn in turns)
    success_ratio = completed / len(turns)
    totals = [float(turn["total_turn_ms"]) for turn in turns]
    cancellations = [
        float(turn["cancellation_latency_ms"])
        for turn in turns if turn.get("cancellation_latency_ms") is not None
    ]
    rss = [float(turn["process_rss_mib"]) for turn in turns]
    vram = [float(turn["gpu_vram_used_mib"]) for turn in turns]

    def p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        rank = max(1, (95 * len(ordered) + 99) // 100)
        return ordered[rank - 1]

    result = {
        "schema_version": "voice-agent.sustained-run-report.v1",
        "turn_count": len(turns),
        "completed_turns": completed,
        "turn_success_ratio": success_ratio,
        "p95_total_turn_ms": p95(totals),
        "p95_cancellation_latency_ms": p95(cancellations),
        "process_rss_growth_mib": max(rss) - min(rss),
        "gpu_vram_growth_mib": max(vram) - min(vram),
        "avatar_healthy_frame_ratio": avatar.get("healthy_frame_ratio"),
        "avatar_fps": avatar.get("fps"),
    }
    failures = []
    checks = (
        (success_ratio >= float(thresholds["minimum_turn_success_ratio"]), "turn_success"),
        (float(result["p95_total_turn_ms"]) <= float(thresholds["maximum_p95_total_turn_ms"]), "turn_latency"),
        (not cancellations or float(result["p95_cancellation_latency_ms"]) <= float(thresholds["maximum_p95_cancellation_latency_ms"]), "cancellation"),
        (float(result["process_rss_growth_mib"]) <= float(thresholds["maximum_process_rss_growth_mib"]), "rss_growth"),
        (float(result["gpu_vram_growth_mib"]) <= float(thresholds["maximum_gpu_vram_growth_mib"]), "vram_growth"),
        (isinstance(avatar.get("healthy_frame_ratio"), (int, float)) and float(avatar["healthy_frame_ratio"]) >= float(thresholds["minimum_avatar_healthy_frame_ratio"]), "avatar_health"),
        (isinstance(avatar.get("fps"), (int, float)) and float(avatar["fps"]) >= float(thresholds["minimum_avatar_fps"]), "avatar_fps"),
    )
    failures.extend(label for passed, label in checks if not passed)
    result["status"] = "pass" if not failures else "fail"
    result["failed_thresholds"] = failures
    if failures:
        raise OperationalError("sustained_threshold_failed", "sustained run failed: " + ",".join(failures))
    return result

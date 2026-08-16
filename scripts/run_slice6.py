#!/usr/bin/env python3
"""Start the pinned local LiveKit server, gateway/controller, and foreground tailnet HTTPS."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.silero_tts import verify_silero_runtime
from voice_agent_v2.slice6_config import (
    Slice6ConfigurationError,
    Slice6Settings,
    livekit_server_config,
    supervised_process_identity,
)

LIVEKIT_VERSION = "1.13.5"
SIGNAL_PORT = 7880
RTC_UDP_PORT = 7882
GATEWAY_PORT = 8000
LLAMA_PORT = 18080
LFM_CACHE = Path("/home/priney/.cache/voice-agent-v2/llama-cpp-gguf-q4")
LLAMA_BINARY = LFM_CACHE / "runtime" / "llama-b10357-cuda13-build" / "bin" / "llama-server"
LLAMA_BIN_DIRECTORY = LLAMA_BINARY.parent
CUDA_OVERLAY = LFM_CACHE / "runtime" / "cuda-13.3-overlay" / "lib"
LFM_MODEL = LFM_CACHE / "model" / "LFM2.5-2.6B-Q4_K_M.gguf"
LFM_MODEL_SIZE = 1_674_454_848
LFM_MODEL_SHA256 = "79fdf00351b46cf26f020aead28d01889886be87c55fa0eb907e6f9b00bfee14"
LLAMA_BINARY_SHA256 = "08625d7c6f380ce14a1fd6085e6468b13a7d169083928ab46706edb62979ac11"
LOCAL_LFM_ALIAS = "lfm2.5-2.6b-q4-k-m"
SERVER_SECRET_NAMES = frozenset({
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_KEYS",
})
FORBIDDEN_CLOUD_NAMES = frozenset({"LITELLM_BASE_URL", "LITELLM_TOKEN_FILE"})
SERVE_READINESS_TIMEOUT_SECONDS = 5.0
SERVE_STATUS_LIMIT_BYTES = 64 * 1024
OPERATIONAL_STATUS_LIMIT_BYTES = 64 * 1024
OPERATIONAL_UNREADY_GRACE_SECONDS = 2.0
RUNTIME_LISTENER_REQUIREMENTS = (
    ("local-llm", "tcp", LLAMA_PORT),
    ("livekit", "tcp", SIGNAL_PORT),
    ("livekit", "udp", RTC_UDP_PORT),
    ("gateway-controller-stt-tts-provider", "tcp", GATEWAY_PORT),
)


SHUTDOWN_ORDER = (
    "tailnet-app-route",
    "gateway-controller-stt-tts-provider",
    "tailnet-signal-route",
    "livekit",
    "local-llm",
)
STOP_TIMEOUTS = {
    "tailnet-app-route": 5.0,
    "gateway-controller-stt-tts-provider": 60.0,
    "tailnet-signal-route": 5.0,
    "livekit": 10.0,
    "local-llm": 10.0,
}


class ServiceProcessFailure(RuntimeError):
    pass


class ProcessSupervisor:
    """Own child processes and stop them in the declared safe drain order."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self._roles: dict[int, str] = {}

    def start(
        self, command: list[str], *, role: str | None = None, **kwargs: object,
    ) -> subprocess.Popen:
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as error:
            raise ServiceProcessFailure("supervised component could not start") from error
        self.processes.append(process)
        if role is not None:
            if role in self._roles.values():
                stop(process)
                raise RuntimeError(f"duplicate supervised role: {role}")
            self._roles[id(process)] = role
        return process

    def role(self, process: subprocess.Popen) -> str:
        return self._roles.get(id(process), "unclassified-child")

    def close(self, order: tuple[str, ...] | None = None) -> None:
        first_error: BaseException | None = None
        ordered: list[subprocess.Popen] = []
        if order is not None:
            for role in order:
                ordered.extend(
                    process for process in self.processes
                    if self._roles.get(id(process)) == role and process not in ordered
                )
        ordered.extend(process for process in reversed(self.processes) if process not in ordered)
        for process in ordered:
            try:
                timeout = STOP_TIMEOUTS.get(self.role(process), 5.0)
                stop(process, timeout=timeout)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


def required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value or value != value.strip():
        raise Slice6ConfigurationError(f"required server configuration is missing or invalid: {name}")
    return value


def without_server_secrets(environment: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name not in SERVER_SECRET_NAMES}


def validate_tailnet_identity(document: object, *, node_ip: str, hostname: str) -> None:
    if not isinstance(document, dict):
        raise ServiceProcessFailure("Tailscale self status is unavailable")
    if document.get("BackendState") == "NeedsLogin":
        raise Slice6ConfigurationError("Tailscale authentication is incompatible")
    self_status = document.get("Self")
    if not isinstance(self_status, dict) or self_status.get("Online") is not True:
        raise ServiceProcessFailure("Tailscale self is not online")
    dns_name = self_status.get("DNSName")
    addresses = self_status.get("TailscaleIPs")
    if (
        not isinstance(dns_name, str)
        or not isinstance(addresses, list)
        or dns_name.removesuffix(".") != hostname
        or node_ip not in addresses
    ):
        raise Slice6ConfigurationError(
            "configured Slice 6 URLs/node IP do not match this online Tailscale host"
        )


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local_lfm_artifacts() -> None:
    if not LLAMA_BINARY.is_file() or not os.access(LLAMA_BINARY, os.X_OK):
        raise RuntimeError("pinned local llama.cpp server is unavailable")
    if not LFM_MODEL.is_file() or LFM_MODEL.stat().st_size != LFM_MODEL_SIZE:
        raise RuntimeError("pinned local LFM model is unavailable or has the wrong size")
    if sha256_file(LLAMA_BINARY) != LLAMA_BINARY_SHA256:
        raise RuntimeError("pinned llama.cpp server checksum mismatch")
    if sha256_file(LFM_MODEL) != LFM_MODEL_SHA256:
        raise RuntimeError("pinned local LFM model checksum mismatch")


def llama_command() -> list[str]:
    return [
        str(LLAMA_BINARY),
        "--model", str(LFM_MODEL),
        "--alias", LOCAL_LFM_ALIAS,
        "--host", "127.0.0.1",
        "--port", str(LLAMA_PORT),
        "--ctx-size", "65536",
        "--parallel", "2",
        "--threads", "6",
        "--threads-batch", "6",
        "--batch-size", "2048",
        "--ubatch-size", "512",
        "--split-mode", "none",
        "--main-gpu", "0",
        "--n-gpu-layers", "99",
        "--flash-attn", "on",
        "--jinja",
        "--reasoning", "on",
        "--reasoning-format", "deepseek",
        "--reasoning-budget", "384",
        "--no-cache-prompt",
        "--cache-ram", "0",
        "--no-cache-idle-slots",
        "--metrics",
        "--slots",
        "--no-webui",
        "--verbosity", "1",
    ]


def local_lfm_health_ready(port: int, timeout: float) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/health", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(4_097)
        if response.status != 200 or len(body) > 4_096:
            return False
        document = json.loads(body)
        return isinstance(document, dict) and document.get("status") == "ok"
    except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


def systemd_notify_ready() -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        notifier.settimeout(1.0)
        notifier.connect(address)
        notifier.sendall(b"READY=1\nSTATUS=Voice Agent exact release ready\n")
    except OSError as error:
        raise ServiceProcessFailure("systemd readiness notification failed") from error
    finally:
        notifier.close()


def gateway_operational_ready(
    timeout: float = 0.2, *, expected_build_id: str | None = None,
    expected_release_id: str | None = None,
) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", GATEWAY_PORT, timeout=timeout)
    try:
        connection.request("GET", "/api/status", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(OPERATIONAL_STATUS_LIMIT_BYTES + 1)
        if response.status != 200 or len(body) > OPERATIONAL_STATUS_LIMIT_BYTES:
            return False
        document = json.loads(body)
        health = document.get("health") if isinstance(document, dict) else None
        return bool(
            isinstance(health, dict)
            and health.get("overall_readiness") == "ready"
            and document.get("provider_mode") == "local"
            and document.get("external_provider_supervised") is False
            and document.get("automatic_fallback") is False
            and (
                expected_build_id is None
                or document.get("build_id") == expected_build_id
            )
            and (
                expected_release_id is None
                or document.get("release_id") == expected_release_id
            )
        )
    except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


def require_runtime_ports_free(proc_root: Path = Path("/proc")) -> None:
    occupied: set[int] = set()
    for name, listening_state in (
        ("tcp", "0A"), ("tcp6", "0A"), ("udp", None), ("udp6", None),
    ):
        try:
            lines = (proc_root / "net" / name).read_text(encoding="ascii").splitlines()
        except OSError as error:
            raise ServiceProcessFailure("runtime port custody is unavailable") from error
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4 or (
                listening_state is not None and fields[3] != listening_state
            ):
                continue
            try:
                occupied.add(int(fields[1].rsplit(":", 1)[1], 16))
            except (IndexError, ValueError) as error:
                raise ServiceProcessFailure("runtime port custody is invalid") from error
    for port in (LLAMA_PORT, SIGNAL_PORT, GATEWAY_PORT, RTC_UDP_PORT):
        if port in occupied:
            raise ServiceProcessFailure(f"required runtime port is already owned: {port}")


def _listener_inodes(
    protocol: str, port: int, *, proc_root: Path,
) -> set[str]:
    names = ("tcp", "tcp6") if protocol == "tcp" else ("udp", "udp6")
    inodes: set[str] = set()
    for name in names:
        try:
            lines = (proc_root / "net" / name).read_text(encoding="ascii").splitlines()
        except OSError as error:
            raise ServiceProcessFailure("runtime listener custody is unavailable") from error
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError) as error:
                raise ServiceProcessFailure("runtime listener custody is invalid") from error
            if local_port != port or (protocol == "tcp" and fields[3] != "0A"):
                continue
            if not fields[9].isdigit() or fields[9] == "0":
                raise ServiceProcessFailure("runtime listener custody is invalid")
            inodes.add(fields[9])
    return inodes


def _process_tree_socket_inodes(pid: int, *, proc_root: Path) -> set[str]:
    pending = [pid]
    observed: set[int] = set()
    inodes: set[str] = set()
    while pending:
        current = pending.pop()
        if current in observed:
            continue
        observed.add(current)
        process_root = proc_root / str(current)
        try:
            children = (
                process_root / "task" / str(current) / "children"
            ).read_text(encoding="ascii").split()
            descriptors = tuple((process_root / "fd").iterdir())
        except OSError as error:
            raise ServiceProcessFailure("supervised listener owner is unavailable") from error
        for child in children:
            if not child.isdigit() or int(child) <= 0:
                raise ServiceProcessFailure("supervised listener owner is invalid")
            pending.append(int(child))
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ServiceProcessFailure("supervised listener custody is unavailable") from error
            if target.startswith("socket:[") and target.endswith("]"):
                inode = target[8:-1]
                if inode.isdigit() and inode != "0":
                    inodes.add(inode)
    return inodes


def require_runtime_listener_custody(
    supervisor: ProcessSupervisor,
    *,
    proc_root: Path = Path("/proc"),
    requirements: tuple[tuple[str, str, int], ...] = RUNTIME_LISTENER_REQUIREMENTS,
) -> None:
    processes_by_role = {
        role: [process for process in supervisor.processes if supervisor.role(process) == role]
        for role, _protocol, _port in requirements
    }
    owned_by_role: dict[str, set[str]] = {}
    for role, processes in processes_by_role.items():
        if len(processes) != 1:
            raise ServiceProcessFailure(f"supervised listener owner is ambiguous: {role}")
        process = processes[0]
        pid = getattr(process, "pid", None)
        if type(pid) is not int or pid <= 0 or process.poll() is not None:
            raise ServiceProcessFailure(f"supervised listener owner is unavailable: {role}")
        owned_by_role[role] = _process_tree_socket_inodes(pid, proc_root=proc_root)
    for role, protocol, port in requirements:
        listeners = _listener_inodes(protocol, port, proc_root=proc_root)
        if not listeners or not listeners.issubset(owned_by_role[role]):
            raise ServiceProcessFailure(
                f"runtime listener is not owned by the supervised component: {protocol}/{port}"
            )


def require_supervised_children_alive(
    supervisor: ProcessSupervisor, *, phase: str,
) -> None:
    for process in supervisor.processes:
        if process.poll() is not None:
            raise ServiceProcessFailure(
                f"supervised component exited {phase}: {supervisor.role(process)}"
            )


def publish_systemd_readiness(
    supervisor: ProcessSupervisor, *, build_id: str, release_id: str,
) -> None:
    require_supervised_children_alive(supervisor, phase="before readiness")
    require_runtime_listener_custody(supervisor)
    if not gateway_operational_ready(
        timeout=1.0,
        expected_build_id=build_id,
        expected_release_id=release_id,
    ):
        raise ServiceProcessFailure(
            "gateway did not expose exact operational readiness after route startup"
        )
    require_supervised_children_alive(supervisor, phase="before readiness")
    require_runtime_listener_custody(supervisor)
    systemd_notify_ready()


def wait_for_port(process: subprocess.Popen, port: int, name: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    require_lfm_health = port == LLAMA_PORT and name == "local LFM"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ServiceProcessFailure(f"{name} exited before readiness")
        remaining = deadline - time.monotonic()
        if require_lfm_health:
            if local_lfm_health_ready(port, min(0.2, remaining)):
                return
        else:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=min(0.2, remaining)):
                    return
            except OSError:
                pass
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    readiness = "become healthy" if require_lfm_health else "listen"
    raise ServiceProcessFailure(f"{name} did not {readiness} within {timeout:.0f}s")


def stop(process: subprocess.Popen, *, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_tailscale_serve_status(
    environment: dict[str, str], *, timeout: float = 5.0,
) -> dict[str, object]:
    try:
        status = subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise ServiceProcessFailure("Tailscale Serve status is unavailable") from error
    if status.returncode != 0 or len(status.stdout.encode("utf-8")) > SERVE_STATUS_LIMIT_BYTES:
        raise ServiceProcessFailure("Tailscale Serve status is unavailable")
    try:
        document = json.loads(status.stdout)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ServiceProcessFailure("Tailscale Serve status is unavailable") from error
    if not isinstance(document, dict):
        raise ServiceProcessFailure("Tailscale Serve status is unavailable")
    return document


def serve_route_state(
    document: dict[str, object], *, hostname: str, https_port: int, target: str,
) -> str:
    configurations: list[dict[str, object]] = [document]
    foreground = document.get("Foreground", {})
    if not isinstance(foreground, dict):
        raise Slice6ConfigurationError("Tailscale Serve status is unavailable")
    for configuration in foreground.values():
        if not isinstance(configuration, dict):
            raise Slice6ConfigurationError("Tailscale Serve status is unavailable")
        configurations.append(configuration)

    port_key = str(https_port)
    web_key = f"{hostname}:{https_port}"
    port_suffix = f":{https_port}"
    exact_found = False
    conflicting_use_found = False
    for configuration in configurations:
        tcp = configuration.get("TCP", {})
        web = configuration.get("Web", {})
        if not isinstance(tcp, dict) or not isinstance(web, dict):
            raise Slice6ConfigurationError("Tailscale Serve status is unavailable")
        tcp_entry = tcp.get(port_key)
        web_entry = web.get(web_key)
        handlers = web_entry.get("Handlers") if isinstance(web_entry, dict) else None
        root_handler = handlers.get("/") if isinstance(handlers, dict) else None
        exact = (
            isinstance(tcp_entry, dict)
            and tcp_entry.get("HTTPS") is True
            and isinstance(root_handler, dict)
            and root_handler.get("Proxy") == target
        )
        web_uses_port = any(
            isinstance(key, str) and (key == port_key or key.endswith(port_suffix))
            for key in web
        )
        uses_port = tcp_entry is not None or web_uses_port
        exact_found = exact_found or exact
        conflicting_use_found = conflicting_use_found or (uses_port and not exact)

    if exact_found and not conflicting_use_found:
        return "preexisting"
    if not exact_found and not conflicting_use_found:
        return "absent"
    return "conflict"


def wait_for_serve_route(
    process: subprocess.Popen,
    *,
    environment: dict[str, str],
    hostname: str,
    https_port: int,
    target: str,
    timeout: float = SERVE_READINESS_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise ServiceProcessFailure(
                f"Tailscale Serve HTTPS/{https_port} exited before readiness"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceProcessFailure(
                f"Tailscale Serve HTTPS/{https_port} did not register within {timeout:.0f}s"
            )
        try:
            document = read_tailscale_serve_status(
                environment, timeout=min(1.0, remaining),
            )
        except ServiceProcessFailure:
            document = {}
        if document and serve_route_state(
            document, hostname=hostname, https_port=https_port, target=target,
        ) == "preexisting":
            if process.poll() is not None:
                raise ServiceProcessFailure(
                    f"Tailscale Serve HTTPS/{https_port} exited before readiness"
                )
            return
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def reconcile_serve_routes(
    *,
    supervisor: ProcessSupervisor,
    environment: dict[str, str],
    hostname: str,
    routes: tuple[tuple[int, str], ...],
    timeout: float = SERVE_READINESS_TIMEOUT_SECONDS,
) -> dict[int, str]:
    route_ports = [https_port for https_port, _target in routes]
    if len(route_ports) != len(set(route_ports)):
        raise Slice6ConfigurationError("application and signaling HTTPS ports must differ")
    document = read_tailscale_serve_status(environment)
    states = {
        https_port: serve_route_state(
            document, hostname=hostname, https_port=https_port, target=target,
        )
        for https_port, target in routes
    }
    conflicts = [str(port) for port, state in states.items() if state == "conflict"]
    if conflicts:
        raise Slice6ConfigurationError(
            "conflicting Tailscale Serve mapping on HTTPS/" + ", HTTPS/".join(conflicts)
        )

    for https_port, target in routes:
        if states[https_port] == "preexisting":
            continue
        role = (
            "tailnet-app-route" if target.endswith(f":{GATEWAY_PORT}")
            else "tailnet-signal-route"
        )
        process = supervisor.start([
            "tailscale", "serve", "--yes", f"--https={https_port}", target,
        ], role=role, env=environment)
        wait_for_serve_route(
            process,
            environment=environment,
            hostname=hostname,
            https_port=https_port,
            target=target,
            timeout=timeout,
        )
        states[https_port] = "owned"
    return states


def main() -> int:
    if any(name in os.environ for name in FORBIDDEN_CLOUD_NAMES):
        raise Slice6ConfigurationError("LiteLLM configuration is forbidden in the local-LFM runtime")
    settings = Slice6Settings.from_environment(project_root=ROOT)
    node_ip = required("SLICE6_LIVEKIT_NODE_IP")
    app_https_port = int(required("SLICE6_APP_HTTPS_PORT"))
    signal_https_port = int(required("SLICE6_SIGNAL_HTTPS_PORT"))
    app_public_url = settings.app_public_url
    app_public = urlsplit(app_public_url)
    signal_public = urlsplit(settings.livekit_public_url)
    for port in (app_https_port, signal_https_port):
        if not 1 <= port <= 65535:
            raise Slice6ConfigurationError("tailnet HTTPS port is outside bounds")
    if app_https_port == signal_https_port:
        raise Slice6ConfigurationError("application and signaling HTTPS ports must differ")
    if (
        app_public.scheme != "https" or not app_public.hostname
        or app_public.username is not None or app_public.password is not None
        or app_public.path not in {"", "/"} or app_public.query or app_public.fragment
        or app_public.port != app_https_port
    ):
        raise Slice6ConfigurationError("SLICE6_APP_PUBLIC_URL must be exact HTTPS with the configured port")
    if signal_public.port != signal_https_port:
        raise Slice6ConfigurationError("LIVEKIT_PUBLIC_URL must use SLICE6_SIGNAL_HTTPS_PORT")
    if os.environ.get("SLICE6_ENABLE_TAILSCALE_SERVE") != "1":
        raise Slice6ConfigurationError("SLICE6_ENABLE_TAILSCALE_SERVE must be exactly 1")
    if shutil.which("tailscale") is None:
        raise RuntimeError("the required tailscale CLI is unavailable")
    tailscale_environment = without_server_secrets(dict(os.environ))
    try:
        status = subprocess.run(
            ["tailscale", "status", "--json"],
            env=tailscale_environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise ServiceProcessFailure("Tailscale self status is unavailable") from error
    if len(status.stdout.encode("utf-8")) > SERVE_STATUS_LIMIT_BYTES:
        raise ServiceProcessFailure("Tailscale self status is unavailable")
    try:
        status_document = json.loads(status.stdout)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ServiceProcessFailure("Tailscale self status is unavailable") from error
    if isinstance(status_document, dict) and status_document.get("BackendState") == "NeedsLogin":
        raise Slice6ConfigurationError("Tailscale authentication is incompatible")
    if status.returncode != 0:
        raise ServiceProcessFailure("Tailscale self status is unavailable")
    validate_tailnet_identity(
        status_document,
        node_ip=node_ip,
        hostname=str(app_public.hostname),
    )

    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-agent-v2" / "slice-6"
    binary = cache / "tooling" / f"livekit-server-v{LIVEKIT_VERSION}"
    python = cache / "runtime" / "venv" / "bin" / "python"
    if not binary.is_file() or not os.access(binary, os.X_OK) or not python.is_file():
        raise RuntimeError("Slice 6 tooling is missing; run ./setup-slice6")
    if not settings.web_dist.is_dir():
        raise RuntimeError("Slice 6 web build is missing; run ./setup-slice6")
    verify_local_lfm_artifacts()
    silero_metadata = verify_silero_runtime()
    require_runtime_ports_free()

    gateway_environment = dict(os.environ)
    gateway_environment["PYTHONPATH"] = str(ROOT / "src")
    gateway_environment.pop("LIVEKIT_CONFIG", None)
    gateway_environment.pop("LIVEKIT_KEYS", None)
    livekit_environment = without_server_secrets(gateway_environment)
    livekit_environment["LIVEKIT_CONFIG"] = livekit_server_config(node_ip)
    livekit_environment["LIVEKIT_KEYS"] = (
        f"{settings.livekit_api_key}: {settings.livekit_api_secret}"
    )
    tailscale_environment = without_server_secrets(gateway_environment)
    llama_environment = without_server_secrets(gateway_environment)
    for name in tuple(llama_environment):
        if name.startswith("LITELLM_"):
            llama_environment.pop(name)
    llama_environment["HOME"] = str(LFM_CACHE / "runtime" / "home")
    llama_environment["XDG_CACHE_HOME"] = str(LFM_CACHE / "runtime" / "home" / ".cache")
    llama_environment["LD_LIBRARY_PATH"] = f"{CUDA_OVERLAY}:{LLAMA_BIN_DIRECTORY}"
    supervisor = ProcessSupervisor()
    stopping = False

    def request_stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        llama_log = LFM_CACHE / "logs" / "slice6-local-lfm.log"
        llama_log.parent.mkdir(parents=True, exist_ok=True)
        llama_output = llama_log.open("ab", buffering=0)
        local_lfm = supervisor.start(
            llama_command(), role="local-llm", cwd=LFM_CACHE, env=llama_environment,
            stdout=llama_output, stderr=subprocess.STDOUT,
        )
        wait_for_port(local_lfm, LLAMA_PORT, "local LFM", timeout=30)

        livekit = supervisor.start(
            [str(binary)], role="livekit", cwd=ROOT, env=livekit_environment,
        )
        wait_for_port(livekit, SIGNAL_PORT, "LiveKit")

        gateway_environment["VOICE_AGENT_SUPERVISED_LFM_PROCESS"] = (
            supervised_process_identity(local_lfm.pid)
        )
        gateway_environment["VOICE_AGENT_SUPERVISED_LIVEKIT_PROCESS"] = (
            supervised_process_identity(livekit.pid)
        )
        gateway = supervisor.start(
            [
                str(python), "-B", "-m", "uvicorn", "voice_agent_v2.slice6_gateway:app",
                "--host", "127.0.0.1", "--port", str(GATEWAY_PORT),
                "--no-access-log", "--log-level", "info",
            ],
            role="gateway-controller-stt-tts-provider",
            cwd=ROOT,
            env=gateway_environment,
        )
        wait_for_port(gateway, GATEWAY_PORT, "Slice 6 gateway", timeout=20)

        serve_states = reconcile_serve_routes(
            supervisor=supervisor,
            environment=tailscale_environment,
            hostname=str(app_public.hostname),
            routes=(
                (app_https_port, f"http://127.0.0.1:{GATEWAY_PORT}"),
                (signal_https_port, f"http://127.0.0.1:{SIGNAL_PORT}"),
            ),
        )
        publish_systemd_readiness(
            supervisor,
            build_id=settings.build_id,
            release_id=settings.release_id,
        )
        print("Voice Agent v2 Slice 6 local-LFM development app started")
        print(f"loopback: http://127.0.0.1:{GATEWAY_PORT}")
        print(f"tailnet: {app_public_url}")
        print(
            f"LiveKit paths: signaling HTTPS/{signal_https_port}, "
            f"WebRTC UDP/{RTC_UDP_PORT} on tailscale0; ICE/TCP and TURN disabled"
        )
        print(
            f"local LFM: {LOCAL_LFM_ALIAS}, loopback-only HTTP/{LLAMA_PORT}, "
            "2 slots x 32768 tokens; no cloud provider or fallback"
        )
        print(
            "TTS: Silero v5_5_ru / kseniya, native mono pcm_s16le/48000, "
            f"{silero_metadata['workers']} isolated workers; private noncommercial only"
        )
        print(
            "Tailscale Serve ownership: "
            + ", ".join(
                f"HTTPS/{port}={serve_states[port]}"
                for port in (app_https_port, signal_https_port)
            )
        )
        print("Press Ctrl+C to stop this development run.")

        operational_unready_since: float | None = None
        while not stopping:
            for process in supervisor.processes:
                if process.poll() is not None:
                    raise ServiceProcessFailure(
                        f"supervised component exited unexpectedly: {supervisor.role(process)}"
                    )
            if gateway_operational_ready(
                expected_build_id=settings.build_id,
                expected_release_id=settings.release_id,
            ):
                operational_unready_since = None
            elif operational_unready_since is None:
                operational_unready_since = time.monotonic()
            elif time.monotonic() - operational_unready_since >= OPERATIONAL_UNREADY_GRACE_SECONDS:
                raise ServiceProcessFailure(
                    "gateway-owned capability remained unready beyond the recovery grace"
                )
            time.sleep(0.25)
    finally:
        active_failure = sys.exc_info()[0] is not None
        try:
            supervisor.close(SHUTDOWN_ORDER)
        except BaseException as cleanup_error:
            if not active_failure:
                raise ServiceProcessFailure("supervised component cleanup failed") from cleanup_error
            print("Voice Agent cleanup also failed within the systemd hard-stop bound", file=sys.stderr)
        finally:
            llama_output_object = locals().get("llama_output")
            if llama_output_object is not None:
                try:
                    llama_output_object.close()
                except OSError as cleanup_error:
                    if not active_failure:
                        raise ServiceProcessFailure("local LFM log cleanup failed") from cleanup_error
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ServiceProcessFailure as error:
        print(f"Voice Agent service failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (Slice6ConfigurationError, RuntimeError, OSError, ValueError) as error:
        print(f"Slice 6 startup failed: {error}", file=sys.stderr)
        raise SystemExit(2)

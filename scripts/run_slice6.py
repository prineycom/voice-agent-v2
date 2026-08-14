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


class ProcessSupervisor:
    """Own and stop only child processes started by this runner."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []

    def start(self, command: list[str], **kwargs: object) -> subprocess.Popen:
        process = subprocess.Popen(command, **kwargs)
        self.processes.append(process)
        return process

    def close(self) -> None:
        first_error: BaseException | None = None
        for process in reversed(self.processes):
            try:
                stop(process)
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
    if not isinstance(document, dict) or not isinstance(document.get("Self"), dict):
        raise Slice6ConfigurationError("Tailscale self status is unavailable")
    self_status = document["Self"]
    dns_name = self_status.get("DNSName")
    addresses = self_status.get("TailscaleIPs")
    online = self_status.get("Online")
    if not isinstance(dns_name, str) or not isinstance(addresses, list):
        raise Slice6ConfigurationError("Tailscale self status is unavailable")
    if not online or dns_name.removesuffix(".") != hostname or node_ip not in addresses:
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


def wait_for_port(process: subprocess.Popen, port: int, name: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    require_lfm_health = port == LLAMA_PORT and name == "local LFM"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited before readiness")
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
    raise RuntimeError(f"{name} did not {readiness} within {timeout:.0f}s")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
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
    except subprocess.TimeoutExpired as error:
        raise Slice6ConfigurationError("Tailscale Serve status is unavailable") from error
    if status.returncode != 0 or len(status.stdout.encode("utf-8")) > SERVE_STATUS_LIMIT_BYTES:
        raise Slice6ConfigurationError("Tailscale Serve status is unavailable")
    try:
        document = json.loads(status.stdout)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise Slice6ConfigurationError("Tailscale Serve status is unavailable") from error
    if not isinstance(document, dict):
        raise Slice6ConfigurationError("Tailscale Serve status is unavailable")
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
            raise RuntimeError(f"Tailscale Serve HTTPS/{https_port} exited before readiness")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Tailscale Serve HTTPS/{https_port} did not register within {timeout:.0f}s"
            )
        try:
            document = read_tailscale_serve_status(
                environment, timeout=min(1.0, remaining),
            )
        except Slice6ConfigurationError:
            document = {}
        if document and serve_route_state(
            document, hostname=hostname, https_port=https_port, target=target,
        ) == "preexisting":
            if process.poll() is not None:
                raise RuntimeError(f"Tailscale Serve HTTPS/{https_port} exited before readiness")
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
        process = supervisor.start([
            "tailscale", "serve", "--yes", f"--https={https_port}", target,
        ], env=environment)
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
    status = subprocess.run(
        ["tailscale", "status", "--json"],
        env=tailscale_environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if status.returncode != 0:
        raise Slice6ConfigurationError("Tailscale self status is unavailable")
    try:
        status_document = json.loads(status.stdout)
    except json.JSONDecodeError as error:
        raise Slice6ConfigurationError("Tailscale self status is unavailable") from error
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
            llama_command(), cwd=LFM_CACHE, env=llama_environment,
            stdout=llama_output, stderr=subprocess.STDOUT,
        )
        wait_for_port(local_lfm, LLAMA_PORT, "local LFM", timeout=30)

        livekit = supervisor.start([str(binary)], cwd=ROOT, env=livekit_environment)
        wait_for_port(livekit, SIGNAL_PORT, "LiveKit")

        gateway = supervisor.start(
            [
                str(python), "-m", "uvicorn", "voice_agent_v2.slice6_gateway:app",
                "--host", "127.0.0.1", "--port", str(GATEWAY_PORT),
                "--no-access-log", "--log-level", "info",
            ],
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

        while not stopping:
            for process in supervisor.processes:
                if process.poll() is not None:
                    raise RuntimeError("a Slice 6 development process exited unexpectedly")
            time.sleep(0.25)
    finally:
        try:
            supervisor.close()
        finally:
            llama_output_object = locals().get("llama_output")
            if llama_output_object is not None:
                llama_output_object.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Slice6ConfigurationError, RuntimeError, OSError, ValueError) as error:
        print(f"Slice 6 startup failed: {error}", file=sys.stderr)
        raise SystemExit(2)

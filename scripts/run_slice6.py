#!/usr/bin/env python3
"""Start the pinned local LiveKit server, gateway/controller, and foreground tailnet HTTPS."""

from __future__ import annotations

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

from voice_agent_v2.slice6_config import (
    Slice6ConfigurationError,
    Slice6Settings,
    livekit_server_config,
)

LIVEKIT_VERSION = "1.13.5"
SIGNAL_PORT = 7880
RTC_UDP_PORT = 7882
GATEWAY_PORT = 8000
SERVER_SECRET_NAMES = frozenset({
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "LIVEKIT_KEYS",
    "LITELLM_BASE_URL",
    "LITELLM_TOKEN_FILE",
})


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


def wait_for_port(process: subprocess.Popen, port: int, name: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{name} did not listen within {timeout:.0f}s")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
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
    processes: list[subprocess.Popen] = []
    stopping = False

    def request_stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        livekit = subprocess.Popen([str(binary)], cwd=ROOT, env=livekit_environment)
        processes.append(livekit)
        wait_for_port(livekit, SIGNAL_PORT, "LiveKit")

        gateway = subprocess.Popen(
            [
                str(python), "-m", "uvicorn", "voice_agent_v2.slice6_gateway:app",
                "--host", "127.0.0.1", "--port", str(GATEWAY_PORT),
                "--no-access-log", "--log-level", "info",
            ],
            cwd=ROOT,
            env=gateway_environment,
        )
        processes.append(gateway)
        wait_for_port(gateway, GATEWAY_PORT, "Slice 6 gateway", timeout=20)

        app_serve = subprocess.Popen([
            "tailscale", "serve", "--yes", f"--https={app_https_port}",
            f"http://127.0.0.1:{GATEWAY_PORT}",
        ], env=tailscale_environment)
        signal_serve = subprocess.Popen([
            "tailscale", "serve", "--yes", f"--https={signal_https_port}",
            f"http://127.0.0.1:{SIGNAL_PORT}",
        ], env=tailscale_environment)
        processes.extend([app_serve, signal_serve])
        print("Voice Agent v2 Slice 6 development app started")
        print(f"loopback: http://127.0.0.1:{GATEWAY_PORT}")
        print(f"tailnet: {app_public_url}")
        print(
            f"LiveKit paths: signaling HTTPS/{signal_https_port}, "
            f"WebRTC UDP/{RTC_UDP_PORT} on tailscale0; ICE/TCP and TURN disabled"
        )
        print("Press Ctrl+C to stop this development run.")

        while not stopping:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError("a Slice 6 development process exited unexpectedly")
            time.sleep(0.25)
    finally:
        for process in reversed(processes):
            stop(process)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Slice6ConfigurationError, RuntimeError, OSError, ValueError) as error:
        print(f"Slice 6 startup failed: {error}", file=sys.stderr)
        raise SystemExit(2)

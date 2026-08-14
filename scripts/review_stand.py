#!/usr/bin/env python3
"""Serve the built review fixture on loopback plus one owned foreground tailnet route."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 4177
TAILNET_HTTPS_PORT = 8447
TARGET = f"http://{LOOPBACK_HOST}:{LOOPBACK_PORT}"


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def tailscale_status() -> dict[str, object]:
    result = subprocess.run(
        ["tailscale", "serve", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Tailscale Serve status is unavailable")
    document = json.loads(result.stdout)
    if not isinstance(document, dict):
        raise RuntimeError("Tailscale Serve status is invalid")
    return document


def port_is_used(document: dict[str, object]) -> bool:
    configurations: list[dict[str, object]] = [document]
    foreground = document.get("Foreground", {})
    if isinstance(foreground, dict):
        configurations.extend(value for value in foreground.values() if isinstance(value, dict))
    port = str(TAILNET_HTTPS_PORT)
    suffix = f":{port}"
    for configuration in configurations:
        tcp = configuration.get("TCP", {})
        web = configuration.get("Web", {})
        if isinstance(tcp, dict) and port in tcp:
            return True
        if isinstance(web, dict) and any(
            isinstance(key, str) and (key == port or key.endswith(suffix)) for key in web
        ):
            return True
    return False


def run() -> int:
    if not (DIST / "index.html").is_file():
        raise RuntimeError("review build is missing; run ./run-review-stand start")
    if port_is_used(tailscale_status()):
        raise RuntimeError(f"tailnet HTTPS/{TAILNET_HTTPS_PORT} is already in use")

    server = ThreadingHTTPServer((LOOPBACK_HOST, LOOPBACK_PORT), ReviewHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    route = subprocess.Popen([
        "tailscale", "serve", "--yes", f"--https={TAILNET_HTTPS_PORT}", TARGET,
    ])
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        while not stopping:
            if route.poll() is not None:
                raise RuntimeError("owned Tailscale Serve route exited")
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
        route.terminate()
        try:
            route.wait(timeout=5)
        except subprocess.TimeoutExpired:
            route.kill()
            route.wait(timeout=2)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve",))
    args = parser.parse_args()
    return run() if args.command == "serve" else 2


if __name__ == "__main__":
    raise SystemExit(main())

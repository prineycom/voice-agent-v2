#!/usr/bin/env python3
"""Offline installed-SDK compatibility and scoped-capability checks for Slice 6."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class NetworkDenied(RuntimeError):
    pass


def deny_network(event: str, _args: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise NetworkDenied(f"network denied: {event}")


sys.addaudithook(deny_network)

from livekit import api

from voice_agent_v2.livekit_runtime import LiveKitRoomController
from voice_agent_v2.slice6_config import Slice6Settings

EXPECTED = {
    "livekit": "1.1.14",
    "livekit-api": "1.2.0",
    "fastapi": "0.141.1",
    "uvicorn": "0.52.1",
}


def main() -> int:
    try:
        socket.socket()
    except NetworkDenied:
        pass
    else:
        raise AssertionError("network denial audit hook is inactive")
    observed = {name: version(name) for name in EXPECTED}
    if observed != EXPECTED:
        raise AssertionError(f"installed Slice 6 SDK versions differ: {observed}")
    settings = Slice6Settings.from_environment({
        "LIVEKIT_API_KEY": "slice6-test-key",
        "LIVEKIT_API_SECRET": "s" * 32,
        "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_PUBLIC_URL": "wss://voice.test.ts.net:7443",
        "SLICE6_APP_PUBLIC_URL": "https://voice.test.ts.net:8443",
        "LITELLM_BASE_URL": "https://llm.example.test",
        "LITELLM_TOKEN_FILE": "/untracked/test-token",
    }, project_root=ROOT)
    controller = object.__new__(LiveKitRoomController)
    controller.settings = settings
    controller.room_name = "voice-session-test"
    controller.browser_identity = "browser-session-test"
    token = controller.browser_token()
    claims = api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(token)
    grants = claims.video
    if grants is None:
        raise AssertionError("room grants are absent")
    if not (
        grants.room_join
        and grants.room == controller.room_name
        and grants.can_publish
        and grants.can_subscribe
        and grants.can_publish_data
        and grants.can_publish_sources == ["microphone"]
    ):
        raise AssertionError(f"room capability is not narrow enough: {grants}")
    if any((grants.room_admin, grants.room_create, grants.room_list, grants.room_record)):
        raise AssertionError("browser capability contains a management grant")
    print("Slice 6 installed-runtime contract: PASS")
    print("SDK pins: " + ", ".join(f"{name}={value}" for name, value in observed.items()))
    print("capability: one room, microphone publish, agent subscribe/data; no management grants")
    print("network: no sockets opened; no model, provider, microphone, or physical browser used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

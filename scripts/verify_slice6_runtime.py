#!/usr/bin/env python3
"""Offline installed-SDK compatibility and scoped-capability checks for Slice 6."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class NetworkDenied(RuntimeError):
    pass


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.__new__" and len(args) > 1 and args[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise NetworkDenied(f"network denied: {event}")


sys.addaudithook(deny_network)

from livekit import api

from voice_agent_v2.livekit_runtime import LiveKitRoomController, SessionRegistry
from voice_agent_v2.slice6_config import Slice6Settings

EXPECTED = {
    "livekit": "1.1.14",
    "livekit-api": "1.2.0",
    "fastapi": "0.141.1",
    "uvicorn": "0.52.1",
}


class AsyncCloser:
    def __init__(self) -> None:
        self.closed = False

    async def disconnect(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


class SessionStub:
    def __init__(self) -> None:
        self.closed = False

    async def disconnect(self) -> None:
        self.closed = True


class RunnerStub:
    def __init__(self) -> None:
        self.closed_sessions: list[str] = []

    def close(self, session_id: str) -> None:
        self.closed_sessions.append(session_id)


async def verify_room_lifecycle_bounds(settings: Slice6Settings) -> None:
    bounded = replace(settings, browser_join_timeout_seconds=0.01)
    registry = SessionRegistry(bounded)
    controller = object.__new__(LiveKitRoomController)
    controller.settings = bounded
    controller.session_id = "session-unclaimed"
    controller._closed = False
    controller._browser_ready = False
    controller._browser_join_task = None
    controller._audio_task = None
    controller.session = SessionStub()
    controller.room = AsyncCloser()
    controller.audio_source = AsyncCloser()
    controller.runner = RunnerStub()
    controller.on_closed = registry.remove
    registry._controllers[controller.session_id] = controller
    controller.arm_browser_join_timeout()
    deadline = asyncio.get_running_loop().time() + 1
    while registry.active_count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    if registry.active_count != 0 or not controller._closed:
        raise AssertionError("unclaimed room did not release its registry slot")
    if not controller.session.closed or not controller.room.closed or not controller.audio_source.closed:
        raise AssertionError("unclaimed room did not close all owned resources")

    created = []

    class BlockingController:
        def __init__(self, **values) -> None:
            self.session_id = values["session_id"]
            self.started = asyncio.Event()
            self.closed = False
            created.append(self)

        async def start(self) -> None:
            self.started.set()
            await asyncio.Event().wait()

        def arm_browser_join_timeout(self) -> None:
            raise AssertionError("cancelled request must not arm a join timer")

        async def close(self, *, notify: bool = True) -> None:
            del notify
            self.closed = True

    cancelled_registry = SessionRegistry(settings)
    with patch(
        "voice_agent_v2.livekit_runtime.LiveKitRoomController", BlockingController
    ):
        request = asyncio.create_task(cancelled_registry.create())
        while not created:
            await asyncio.sleep(0)
        await created[0].started.wait()
        request.cancel()
        try:
            await request
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled registry request unexpectedly completed")
    if cancelled_registry.active_count != 0 or not created[0].closed:
        raise AssertionError("cancelled registry request leaked its controller")


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
    asyncio.run(verify_room_lifecycle_bounds(settings))
    print("Slice 6 installed-runtime contract: PASS")
    print("SDK pins: " + ", ".join(f"{name}={value}" for name, value in observed.items()))
    print("capability: one room, microphone publish, agent subscribe/data; no management grants")
    print("room lifecycle: unclaimed and request-cancelled sessions release all resources")
    print("network: no sockets opened; no model, provider, microphone, or physical browser used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

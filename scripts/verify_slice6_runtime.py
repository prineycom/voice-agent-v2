#!/usr/bin/env python3
"""Offline installed-SDK compatibility and scoped-capability checks for Slice 6."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
import socket
import sys
import threading
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

from voice_agent_v2.livekit_runtime import (
    LiveKitRoomController,
    SessionCapacityError,
    SessionRegistry,
)
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

    def clear_queue(self) -> None:
        pass


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


class BlockingStartupRunner(RunnerStub):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.started = False

    def start(self) -> None:
        self.entered.set()
        self.release.wait(2)
        self.started = True


class FailingSession(SessionStub):
    async def disconnect(self) -> None:
        raise RuntimeError("injected session cleanup failure")


def controller_stub(
    settings: Slice6Settings,
    session_id: str,
    *,
    session: SessionStub | None = None,
    runner: RunnerStub | None = None,
) -> LiveKitRoomController:
    controller = object.__new__(LiveKitRoomController)
    controller.settings = settings
    controller.session_id = session_id
    controller.browser_identity = f"browser-{session_id}"
    controller._closed = False
    controller._cleanup_complete = False
    controller._close_notified = False
    controller._transport_failed = False
    controller._close_lock = asyncio.Lock()
    controller._runner_start_task = None
    controller._browser_ready = False
    controller._browser_join_task = None
    controller._audio_task = None
    controller._control_task = None
    controller._control_queue = asyncio.Queue()
    controller.session = session or SessionStub()
    controller.room = AsyncCloser()
    controller.audio_source = AsyncCloser()
    controller.runner = runner or RunnerStub()
    controller.on_closed = lambda _session_id: asyncio.sleep(0)
    return controller


async def verify_room_lifecycle_bounds(settings: Slice6Settings) -> None:
    bounded = replace(settings, browser_join_timeout_seconds=0.01)
    registry = SessionRegistry(bounded)
    controller = controller_stub(bounded, "session-unclaimed")
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

    startup_runner = BlockingStartupRunner()
    startup_controller = controller_stub(
        settings, "session-startup", runner=startup_runner
    )

    def refuse_join_timer() -> None:
        raise AssertionError("cancelled request must not arm a join timer")

    startup_controller.arm_browser_join_timeout = refuse_join_timer

    cancelled_registry = SessionRegistry(settings)
    with patch(
        "voice_agent_v2.livekit_runtime.LiveKitRoomController",
        return_value=startup_controller,
    ):
        request = asyncio.create_task(cancelled_registry.create())
        if not await asyncio.to_thread(startup_runner.entered.wait, 1):
            raise AssertionError("runner startup worker did not begin")
        request.cancel()
        await asyncio.sleep(0.02)
        if startup_runner.closed_sessions:
            raise AssertionError("runner closed before its startup worker finished")
        startup_runner.release.set()
        try:
            await request
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled registry request unexpectedly completed")
    if (
        cancelled_registry.active_count != 0
        or not startup_runner.started
        or startup_runner.closed_sessions != [startup_controller.session_id]
    ):
        raise AssertionError("cancelled registry request leaked startup resources")

    failed_controller = controller_stub(
        settings,
        "session-cleanup-failure",
        session=FailingSession(),
    )
    failed_controller._browser_ready = True
    failed_registry = SessionRegistry(settings)
    failed_controller.on_closed = failed_registry.remove
    failed_registry._controllers[failed_controller.session_id] = failed_controller
    try:
        await failed_controller.close()
    except ExceptionGroup:
        pass
    else:
        raise AssertionError("cleanup failure unexpectedly released the controller")
    if failed_registry.active_count != 1:
        raise AssertionError("cleanup failure released session capacity")
    try:
        await failed_registry.create()
    except SessionCapacityError:
        pass
    else:
        raise AssertionError("cleanup failure admitted a replacement session")


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
    print("room lifecycle: startup cancellation drains; incomplete cleanup retains capacity")
    print("network: no sockets opened; no model, provider, microphone, or physical browser used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

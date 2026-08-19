#!/usr/bin/env python3
"""Offline installed-SDK compatibility and scoped-capability checks for Slice 6."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from importlib.metadata import version
import json
import os
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

from voice_agent_v2.agent_runtime import AgentRuntime
from voice_agent_v2.livekit_runtime import (
    LiveKitRoomController,
    LiveTurnRunner,
    SessionCapacityError,
    SessionRegistry,
)
from voice_agent_v2.local_lfm import LLAMA_ENDPOINT, MODEL_ALIAS, PROVIDER_IDENTITY
from voice_agent_v2.silero_tts import (
    MANIFEST_PATH as SILERO_MANIFEST,
    MODEL_IDENTITY as SILERO_IDENTITY,
    MODEL_SHA256 as SILERO_SHA256,
    MODEL_SIZE as SILERO_SIZE,
    SileroKseniyaTTS,
)
from voice_agent_v2.local_vad import SILERO_MODEL_SIZE
from voice_agent_v2.slice6_config import Slice6Settings

DISPOSABLE_AGENT_RUNTIME = AgentRuntime(
    config=None,
    status="degraded",
    reason_code="config_missing",
)

EXPECTED = {
    "livekit": "1.1.14",
    "livekit-api": "1.2.0",
    "fastapi": "0.141.1",
    "uvicorn": "0.52.1",
    "pydantic": "2.13.4",
    "PyYAML": "6.0.3",
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


class AudioSinkStub:
    def __init__(self, source: AsyncCloser) -> None:
        self.source = source
        self.closed = False
        self.transport_is_disconnected = False

    async def transport_disconnected(self) -> None:
        self.transport_is_disconnected = True

    async def close(self) -> None:
        if self.closed:
            return
        await self.source.aclose()
        self.closed = True

    async def wait_for_cleanup(self) -> None:
        return None


class SessionStub:
    def __init__(self) -> None:
        self.closed = False

    async def disconnect(self) -> None:
        self.closed = True


class RunnerStub:
    def __init__(self) -> None:
        self.closed_sessions: list[str] = []

    def cancel(self) -> None:
        return None

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

    def cancel_startup(self) -> None:
        return None


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
    controller._room_disconnected = False
    controller._close_retry_task = None
    controller._close_lock = asyncio.Lock()
    controller._runner_start_task = None
    controller._browser_ready = False
    controller._browser_join_task = None
    controller._audio_task = None
    controller._microphone_resume_task = None
    controller._microphone_track = None
    controller._microphone_publication_id = None
    controller._microphone_generation = 0
    controller._vad_model = object()
    controller._microphone_muted = False
    controller._capture_invalidated = False
    controller._control_task = None
    controller._control_queue = asyncio.Queue()
    controller.session = session or SessionStub()
    controller.room = AsyncCloser()
    controller.audio_source = AsyncCloser()
    controller.audio_sink = AudioSinkStub(controller.audio_source)
    controller.runner = runner or RunnerStub()
    controller.on_closed = lambda _session_id: asyncio.sleep(0)
    return controller


async def verify_room_lifecycle_bounds(settings: Slice6Settings) -> None:
    bounded = replace(settings, browser_join_timeout_seconds=0.01)
    registry = SessionRegistry(
        bounded, agent_runtime=DISPOSABLE_AGENT_RUNTIME
    )
    controller = controller_stub(bounded, "session-unclaimed")
    controller.on_closed = registry.remove
    registry._controllers[controller.session_id] = controller
    controller.arm_browser_join_timeout()
    deadline = asyncio.get_running_loop().time() + 1
    while registry.active_count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    if registry.active_count != 0 or not controller._closed:
        raise AssertionError("unclaimed room did not release its registry slot")
    if (
        not controller.session.closed
        or not controller.room.closed
        or not controller.audio_source.closed
        or not controller.audio_sink.closed
    ):
        raise AssertionError("unclaimed room did not close all owned resources")

    startup_runner = BlockingStartupRunner()
    startup_controller = controller_stub(
        settings, "session-startup", runner=startup_runner
    )

    def refuse_join_timer() -> None:
        raise AssertionError("cancelled request must not arm a join timer")

    startup_controller.arm_browser_join_timeout = refuse_join_timer

    cancelled_registry = SessionRegistry(
        settings, agent_runtime=DISPOSABLE_AGENT_RUNTIME
    )
    cancelled_registry._accepting = True
    cancelled_registry.operational_health = lambda: {"overall_readiness": "ready"}
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
    failed_registry = SessionRegistry(
        settings, agent_runtime=DISPOSABLE_AGENT_RUNTIME
    )
    failed_registry._accepting = True
    failed_controller.on_closed = failed_registry.remove
    failed_registry._controllers[failed_controller.session_id] = failed_controller
    try:
        await failed_controller.close()
    except Exception:
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


def verify_vad_contract() -> bool:
    model = Path(
        os.environ.get(
            "VOICE_AGENT_SLICE6_CACHE",
            str(Path.home() / ".cache/voice-agent-v2/slice-6"),
        )
    ) / "models/silero-vad-v6.onnx"
    if not model.is_file():
        return False
    if model.stat().st_size != SILERO_MODEL_SIZE:
        raise AssertionError("pinned Silero VAD artifact size differs")
    return True


def verify_local_lfm_contract(settings: Slice6Settings) -> None:
    manifest = json.loads((ROOT / "config" / "local-lfm-v1.json").read_text())
    if not (
        manifest["provider_mode"] == "local"
        and manifest["provider_identity"] == PROVIDER_IDENTITY
        and manifest["runtime"]["endpoint"] == LLAMA_ENDPOINT
        and manifest["runtime"]["model_alias"] == MODEL_ALIAS
        and manifest["runtime"]["parallel_slots"] == 2
        and manifest["runtime"]["total_context_tokens"] == 65_536
        and manifest["runtime"]["context_tokens_per_slot"] == 32_768
        and manifest["runtime"]["gpu_layers"] == 99
        and manifest["runtime"]["flash_attention"] is True
        and manifest["runtime"]["automatic_fallback"] is False
    ):
        raise AssertionError("tracked local LFM runtime manifest differs")
    runner = LiveTurnRunner(settings)
    if runner.llm.provider_mode != "local" or runner.llm.provider_identity != PROVIDER_IDENTITY:
        raise AssertionError("Slice 6 runner is not wired to the fixed local LFM provider")
    if not isinstance(runner.tts, SileroKseniyaTTS):
        raise AssertionError("Slice 6 composition root is not fixed directly to Silero/Kseniya")


def verify_silero_tts_contract() -> None:
    manifest = json.loads(SILERO_MANIFEST.read_text())
    if not (
        manifest["backend"] == "silero"
        and manifest["model_identity"] == SILERO_IDENTITY
        and manifest["model"]["speaker"] == "kseniya"
        and manifest["model"]["sha256"] == SILERO_SHA256
        and manifest["model"]["size_bytes"] == SILERO_SIZE
        and manifest["model"]["native_sample_rates_hz"] == [8_000, 24_000, 48_000]
        and manifest["runtime"]["workers"] == 2
        and manifest["runtime"]["automatic_retry"] is False
        and manifest["runtime"]["automatic_fallback"] is False
        and manifest["runtime"]["download_allowed"] is False
        and manifest["output_audio"]["sample_rate_hz"] == 48_000
        and manifest["input_audio"]["sample_rate_hz"] == 16_000
        and manifest["license"]["spdx_expression"] == "CC-BY-NC-SA-4.0"
        and manifest["license"]["commercial_use_authorized"] is False
    ):
        raise AssertionError("tracked Silero/Kseniya runtime manifest differs")
    adapter = SileroKseniyaTTS()
    if (
        adapter.version != "voice-agent.tts.v2"
        or adapter.speaker != "kseniya"
        or adapter.output_format.sample_rate_hz != 48_000
        or adapter.capabilities["max_parallel_requests"] != 2
        or adapter.capabilities["cooperative_cancel"] is not False
    ):
        raise AssertionError("composition-root Silero adapter contract differs")


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
        "LIVEKIT_PUBLIC_URL": "ws://127.0.0.1:7880",
        "SLICE6_APP_PUBLIC_URL": "http://127.0.0.1:8000",
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
    vad_present = verify_vad_contract()
    verify_local_lfm_contract(settings)
    verify_silero_tts_contract()
    asyncio.run(verify_room_lifecycle_bounds(settings))
    print("Slice 6 installed-runtime contract: PASS")
    print("SDK pins: " + ", ".join(f"{name}={value}" for name, value in observed.items()))
    print("capability: one room, microphone publish, agent subscribe/data; no management grants")
    print("room lifecycle: startup cancellation drains; incomplete cleanup retains capacity")
    print("local VAD: pinned Silero v6 CPU artifact shape=" + ("verified" if vad_present else "not-present"))
    print(
        "Silero TTS: v2 manifest/composition fixed to kseniya/native48/two workers; "
        "exact-cache identity belongs to ./verify-silero-kseniya; "
        "CC-BY-NC-SA-4.0 private noncommercial evaluation only; no fallback"
    )
    print(
        "local LFM: manifest/provider wiring verified; exact-cache identity belongs to "
        "./verify-local-lfm; loopback endpoint, 2 x 32768-token slots, no fallback"
    )
    print("network: no sockets opened; no model inference, microphone, or physical browser used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

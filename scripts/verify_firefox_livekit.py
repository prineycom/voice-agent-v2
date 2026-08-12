#!/usr/bin/env python3
"""Real Firefox, React VoiceClient, and official LiveKit deterministic regression."""

from __future__ import annotations

import asyncio
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

from livekit import api, rtc
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service

from voice_agent_v2.contracts import EventEnvelope
from voice_agent_v2.livekit_runtime import LiveKitAudioSink, LiveKitEventSink
from voice_agent_v2.realtime import CLIENT_CONTROL_TOPIC, MAX_CONTROL_BYTES, RealtimeSession
from voice_agent_v2.tracer import TraceResult

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-agent-v2" / "slice-6"
EVIDENCE_ROOT = Path(os.environ.get("NO_MISTAKES_EVIDENCE_DIR", str(CACHE)))
LIVEKIT = CACHE / "tooling/livekit-server-v1.13.5"
FIREFOX = shutil.which("firefox")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_port(port: int, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("integration process exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise RuntimeError("integration listener readiness timed out")


class DeterministicRunner:
    def run_turn(
        self,
        *,
        session_id,
        turn_id,
        input_pcm,
        cancellation,
        event_observer,
        audio_observer=None,
        trace_observer=None,
        retain_output=True,
    ) -> TraceResult:
        del input_pcm, trace_observer, retain_output
        events = []

        def emit(event_type, payload, terminal=False):
            event = EventEnvelope(
                session_id=session_id,
                turn_id=turn_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
                terminal=terminal,
            ).as_dict()
            events.append(event)
            event_observer(event)

        emit("turn.transcribing", {"stage": "stt"})
        emit("stt.final", {"transcript": "Детерминированная речь."})
        emit("turn.thinking", {"stage": "llm_provider"})
        if turn_id.endswith("2"):
            emit("llm.visible", {"response": "Видимый префикс перед ошибкой."})
            emit(
                "turn.failed",
                {
                    "outcome": "failed",
                    "stage": "llm_provider",
                    "code": "deterministic_provider_failure",
                },
                True,
            )
            return TraceResult(tuple(events), b"", b"")
        emit("llm.visible", {"response": "Детерминированный видимый ответ."})
        emit("turn.speaking", {"stage": "tts"})
        if audio_observer is None:
            raise AssertionError("realtime controller did not request streaming audio")
        pcm = b"\0\0" * 3_200
        audio_observer(0, pcm)
        if cancellation.cancelled:
            emit("turn.interrupted", {"outcome": "interrupted"}, True)
            return TraceResult(tuple(events), b"", b"")
        emit("tts.audio", {"chunk_index": 0, "byte_count": len(pcm)})
        emit("llm.final", {"response": "Детерминированный видимый ответ."})
        emit(
            "turn.completed",
            {"outcome": "completed", "output_bytes": len(pcm)},
            True,
        )
        return TraceResult(tuple(events), b"", b"")

    def cancel(self) -> None:
        return None

    def discard_turn(self, _session_id: str, _turn_id: str) -> None:
        return None


class RecordingEventSink(LiveKitEventSink):
    def __init__(self, room: rtc.Room, browser_identity: str) -> None:
        super().__init__(room, browser_identity)
        self.events: list[dict[str, object]] = []

    async def send(self, event: dict[str, object]) -> None:
        self.events.append(event)
        await super().send(event)


def handler_for(root: Path, capability: dict[str, object]):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def do_POST(self) -> None:
            if self.path != "/api/session":
                self.send_error(404)
                return
            payload = json.dumps(capability, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            requested = root / self.path.lstrip("/")
            if self.path != "/" and not requested.is_file():
                self.path = "/index.html"
            super().do_GET()

        def log_message(self, _format: str, *_args) -> None:
            return None

    return Handler


async def wait_for(predicate, message: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def main() -> int:
    dist = ROOT / "web/dist"
    missing = [
        name for name, present in (
            ("Firefox", FIREFOX is not None),
            ("pinned LiveKit", LIVEKIT.is_file()),
            ("built React app", (dist / "index.html").is_file()),
        ) if not present
    ]
    if missing:
        raise RuntimeError("required Firefox regression tooling is absent: " + ", ".join(missing))

    api_key, api_secret = "firefox-test-key", "s" * 32
    livekit_port, web_port = free_port(), free_port()
    session_id = "session-firefox-test"
    room_name = "firefox-deterministic-room"
    browser_identity = f"browser-{session_id}"
    agent_identity = f"agent-{session_id}"
    config = json.dumps({
        "port": livekit_port,
        "bind_addresses": ["127.0.0.1"],
        "rtc": {"tcp_port": 0, "udp_port": 0, "use_external_ip": False},
        "logging": {"level": "error"},
    })
    environment = dict(os.environ, LIVEKIT_CONFIG=config, LIVEKIT_KEYS=f"{api_key}: {api_secret}")
    livekit = subprocess.Popen(
        [str(LIVEKIT)], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    room = rtc.Room()
    audio_source: rtc.AudioSource | None = None
    audio_sink: LiveKitAudioSink | None = None
    session: RealtimeSession | None = None
    driver = None
    server: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    microphone_subscribed = asyncio.Event()
    browser_controls: list[dict[str, object]] = []
    try:
        await wait_port(livekit_port, livekit)
        agent_token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(agent_identity)
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            ))
            .to_jwt()
        )
        browser_token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(browser_identity)
            .with_grants(api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_publish_sources=["microphone"],
            ))
            .to_jwt()
        )
        await room.connect(f"ws://127.0.0.1:{livekit_port}", agent_token)
        audio_source = rtc.AudioSource(16_000, 1, queue_size_ms=100)
        audio_sink = LiveKitAudioSink(
            room,
            audio_source,
            lambda source: None,
        )
        event_sink = RecordingEventSink(room, browser_identity)
        failures: list[tuple[str, str]] = []
        session = RealtimeSession(
            session_id=session_id,
            runner=DeterministicRunner(),
            event_sink=event_sink,
            audio_sink=audio_sink,
            failure_handler=lambda stage, code: failures.append((stage, code)),
        )
        await audio_sink.start()
        persistent_publication_id = audio_sink.current_publication_id()

        @room.on("track_subscribed")
        def track_subscribed(track, publication, participant) -> None:
            if (
                participant.identity == browser_identity
                and track.kind == rtc.TrackKind.KIND_AUDIO
                and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            ):
                microphone_subscribed.set()

        @room.on("data_received")
        def data_received(packet) -> None:
            if (
                packet.participant is not None
                and packet.participant.identity == browser_identity
                and packet.topic == CLIENT_CONTROL_TOPIC
                and 0 < len(packet.data) <= MAX_CONTROL_BYTES
            ):
                browser_controls.append(json.loads(bytes(packet.data)))
                asyncio.create_task(session.handle_client_control(bytes(packet.data)))

        capability = {
            "session_id": session_id,
            "stream_epoch": 1,
            "livekit_url": f"ws://127.0.0.1:{livekit_port}",
            "token": browser_token,
            "expires_in_seconds": 60,
            "admission_timeout_ms": 30_000,
            "control_version": "voice-agent.realtime-control.v1",
        }
        server = ThreadingHTTPServer(
            ("127.0.0.1", web_port), handler_for(dist, capability)
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        options = Options()
        options.add_argument("-headless")
        options.set_preference("media.navigator.streams.fake", True)
        options.set_preference("media.navigator.permission.disabled", True)
        options.set_preference("media.autoplay.default", 0)
        download_root = CACHE / "firefox-downloads"
        download_root.mkdir(parents=True, exist_ok=True)
        for old in download_root.glob("voice-agent-diagnostic-*.jsonl"):
            old.unlink()
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", str(download_root))
        options.set_preference("browser.helperApps.neverAsk.saveToDisk", "application/x-ndjson")
        geckodriver = Path.home() / ".cache/selenium/geckodriver/linux64/0.37.1/geckodriver"
        service = Service(executable_path=str(geckodriver)) if geckodriver.is_file() else Service()
        driver = webdriver.Firefox(options=options, service=service)
        await asyncio.to_thread(driver.set_window_size, 1440, 1000)
        await asyncio.to_thread(driver.get, f"http://127.0.0.1:{web_port}/")
        connect = driver.find_element("xpath", "//button[contains(., 'Подключить микрофон')]")
        await asyncio.to_thread(connect.click)
        await asyncio.wait_for(microphone_subscribed.wait(), 15)
        await session.ready()

        first_turn = await session.submit_utterance(b"\0\0" * 320)
        first_context = session._active
        assert first_context is not None and first_context.task is not None
        await asyncio.wait_for(first_context.task, 15)
        if session._closed:
            browser_state = await asyncio.to_thread(
                driver.execute_script,
                "return {body:document.body.innerText,localKeys:Object.keys(localStorage),sessionKeys:Object.keys(sessionStorage)}",
            )
            raise AssertionError(
                "first full-stack turn closed the session: "
                f"failures={failures}, events={[event['type'] for event in event_sink.events]}, "
                f"browser={browser_state}"
            )
        second_turn = await session.submit_utterance(b"\0\0" * 320)
        second_context = session._active
        assert second_context is not None and second_context.task is not None
        await asyncio.wait_for(second_context.task, 15)

        await wait_for(
            lambda: (
                "Видимый префикс перед ошибкой." in driver.find_element("tag name", "body").text
                and "Ошибка" in driver.find_element("tag name", "body").text
            ),
            "React app did not retain and label the deterministic failed answer",
        )
        body = await asyncio.to_thread(lambda: driver.find_element("tag name", "body").text)
        storage_state = await asyncio.to_thread(
            driver.execute_script,
            "return {localKeys:Object.keys(localStorage),sessionKeys:Object.keys(sessionStorage)}",
        )
        if storage_state != {"localKeys": [], "sessionKeys": []}:
            raise AssertionError(f"browser persisted session data: {storage_state}")
        for visible_text in (
            "Детерминированный видимый ответ.",
            "Видимый префикс перед ошибкой.",
            "Завершено",
            "Ошибка",
            "Endpoint → текст",
            "Endpoint → server PCM",
        ):
            if visible_text not in body:
                raise AssertionError(f"React history omitted {visible_text!r}")
        if audio_sink.current_publication_id() != persistent_publication_id:
            raise AssertionError("LiveKit publication rotated between turns")
        if browser_controls:
            raise AssertionError(f"browser sent unexpected media correctness controls: {browser_controls}")
        download = driver.find_element("xpath", "//button[contains(., 'Скачать диагностику')]")
        await asyncio.to_thread(download.click)
        await wait_for(
            lambda: any(download_root.glob("voice-agent-diagnostic-*.jsonl")),
            "Firefox did not download the diagnostic timeline",
        )
        diagnostic_path = next(download_root.glob("voice-agent-diagnostic-*.jsonl"))
        diagnostic_output = diagnostic_path.read_text()
        records = [json.loads(line) for line in diagnostic_output.splitlines()]
        for private_content in (
            "Детерминированная речь.",
            "Детерминированный видимый ответ.",
            "Видимый префикс перед ошибкой.",
        ):
            if private_content in diagnostic_output:
                raise AssertionError("downloaded diagnostics retained conversation content")
        expected_failure = {
            "stage": "llm_provider",
            "event": "turn.failed",
            "sessionId": session_id,
            "turnId": second_turn,
            "failureCode": "deterministic_provider_failure",
        }
        if not any(all(record.get(key) == value for key, value in expected_failure.items()) for record in records):
            raise AssertionError("downloaded Firefox diagnostics lost the correlated server cause")
        if not any(
            record.get("serverControlType") == "turn.failed"
            and record.get("failureStage") == "llm_provider"
            and record.get("failureCode") == "deterministic_provider_failure"
            for record in records
        ):
            raise AssertionError("downloaded Firefox diagnostics lost normalized server failure fields")
        event_types = [event["type"] for event in event_sink.events]
        if "turn.completed" not in event_types or "turn.failed" not in event_types:
            raise AssertionError(f"full-stack deterministic lifecycle is incomplete: {event_types}")
        if not any(
            event["type"] == "turn.completed" and event["turn_id"] == first_turn
            for event in event_sink.events
        ):
            raise AssertionError("streamed publication did not reach its correlated completion")
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        screenshot_path = EVIDENCE_ROOT / "checkpoint-ab-firefox.png"
        result_path = EVIDENCE_ROOT / "checkpoint-ab-firefox.json"
        await asyncio.to_thread(driver.execute_script, "window.scrollTo(0, 0)")
        await asyncio.to_thread(driver.save_screenshot, str(screenshot_path))
        result_path.write_text(json.dumps({
            "browser_surface": body,
            "server_event_types": event_types,
            "persistent_publication_id": persistent_publication_id,
            "browser_media_controls": browser_controls,
            "browser_storage": storage_state,
            "downloaded_diagnostic": str(diagnostic_path),
            "audibility_claimed": False,
        }, ensure_ascii=False, indent=2) + "\n")
        print("Firefox/React/official LiveKit deterministic full-stack regression: PASS")
        print(f"Screenshot: {screenshot_path}")
        print(f"Result: {result_path}")
        print("Evidence: fake inference data, publication lifecycle, and downloadable normalized error; audibility not claimed")
    finally:
        if driver is not None:
            await asyncio.to_thread(driver.quit)
        if session is not None:
            try:
                await session.disconnect(notify_client=False)
            except Exception:
                pass
        if audio_sink is not None:
            try:
                await audio_sink.close()
            except Exception:
                pass
        elif audio_source is not None:
            await audio_source.aclose()
        await room.disconnect()
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=3)
        livekit.terminate()
        try:
            livekit.wait(timeout=3)
        except subprocess.TimeoutExpired:
            livekit.kill()
            livekit.wait(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

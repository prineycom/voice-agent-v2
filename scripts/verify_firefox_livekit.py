#!/usr/bin/env python3
"""Short production-entry Firefox smoke over an actual owned local LiveKit."""

from __future__ import annotations

import asyncio
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import signal
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
from voice_agent_v2.realtime import RealtimeSession
from voice_agent_v2.silero_tts import SileroVoiceProfile
from voice_agent_v2.tracer import TraceResult

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(
    os.environ.get(
        "VOICE_AGENT_SLICE6_CACHE",
        str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-agent-v2" / "slice-6"),
    )
)
LIVEKIT = CACHE / "tooling/livekit-server-v1.13.5"
GECKODRIVER = Path(
    os.environ.get(
        "VOICE_AGENT_GECKODRIVER",
        str(CACHE / "tooling/geckodriver-v0.37.1"),
    )
)
FIREFOX = shutil.which("firefox")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_for(predicate, message: str, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.03)
    raise AssertionError(message)


async def wait_port(port: int, process: subprocess.Popen[bytes]) -> None:
    await wait_for(
        lambda: process.poll() is None and _port_accepts(port),
        "owned LiveKit did not become ready",
    )


def _port_accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


class DeterministicRunner:
    def __init__(self) -> None:
        self.tts_profile = SileroVoiceProfile()
        self.audio_release = threading.Event()

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
        events: list[dict[str, object]] = []

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
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
        emit("llm.visible", {"response": "Детерминированный видимый ответ."})
        emit("turn.speaking", {"stage": "tts"})
        if audio_observer is None:
            raise AssertionError("realtime controller did not request streaming audio")
        if not self.audio_release.wait(3):
            raise AssertionError("browser did not arm current-generation PCM capture")
        pcm = b"".join(
            round(12_000 * math.sin(2 * math.pi * 440 * sample / 48_000)).to_bytes(
                2, "little", signed=True
            )
            for sample in range(36_000)
        )
        audio_observer(0, pcm)
        if cancellation.cancelled:
            emit("turn.interrupted", {"outcome": "interrupted"}, True)
        else:
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

    def turn_delivered(self, _session_id: str, _turn_id: str) -> None:
        return None


class RecordingEventSink(LiveKitEventSink):
    def __init__(self, room: rtc.Room, browser_identity: str) -> None:
        super().__init__(room, browser_identity)
        self.events: list[dict[str, object]] = []

    async def send(self, event: dict[str, object]) -> None:
        self.events.append(event)
        await super().send(event)


def handler_for(
    root: Path,
    capability: dict[str, object],
    session_requests: list[str],
):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *arguments, **keywords):
            super().__init__(*arguments, directory=str(root), **keywords)

        def do_POST(self) -> None:
            session_requests.append(self.path)
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

        def log_message(self, _format: str, *_arguments) -> None:
            return None

    return Handler


def production_surface(driver) -> dict[str, object]:
    return driver.execute_script(
        """
        return {
          connectActions: [...document.querySelectorAll('button')]
            .filter((button) => button.textContent.trim() === 'CONNECT').length,
          historyItems: document.querySelectorAll('.history-item').length,
          microphoneState: document.querySelector('.microphone-status-label')?.textContent ?? null,
          reviewLabel: document.querySelector('.connection-indicator__build')?.textContent ?? '',
        };
        """
    )


def assert_production_entry(surface: dict[str, object]) -> None:
    if surface != {
        "connectActions": 1,
        "historyItems": 0,
        "microphoneState": "MIC DISCONNECTED",
        "reviewLabel": "",
    }:
        raise AssertionError(
            f"production URL served ReviewStand or leaked fixture/session state: {surface}"
        )


def assert_current_pcm(capture: dict[str, object]) -> None:
    if (
        capture.get("ready") is not True
        or capture.get("decodedFrames", 0) < 1_000
        or capture.get("decodedRms", 0) <= 0.01
        or capture.get("decodedPeak", 0) <= 0.05
    ):
        raise AssertionError(
            "current-generation PCM was absent at the production browser playout boundary: "
            f"{capture}"
        )


def start_capture(driver) -> dict[str, object]:
    return driver.execute_script(
        """
        const audio = document.querySelector('audio[data-voice-agent-audio="agent-response"]');
        if (!(audio instanceof HTMLAudioElement)) return {ready:false, reason:'missing audio'};
        const stream = audio.srcObject;
        if (!(stream instanceof MediaStream)) return {ready:false, reason:'missing stream'};
        const mimeType = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus']
          .find((candidate) => MediaRecorder.isTypeSupported(candidate));
        if (mimeType === undefined) return {ready:false, reason:'missing recorder codec'};
        const capture = new MediaStream(stream.getAudioTracks());
        const recorder = new MediaRecorder(capture, {mimeType});
        const chunks = [];
        recorder.addEventListener('dataavailable', (event) => {
          if (event.data.size > 0) chunks.push(event.data);
        });
        recorder.start(40);
        window.__voiceAgentPrCapture = {recorder, chunks, mimeType};
        return {
          ready: recorder.state === 'recording',
          trackCount: capture.getAudioTracks().length,
          trackState: capture.getAudioTracks()[0]?.readyState ?? null,
          paused: audio.paused,
        };
        """
    )


async def stop_capture(driver) -> dict[str, object]:
    return await asyncio.to_thread(
        driver.execute_async_script,
        """
        const done = arguments[0];
        const capture = window.__voiceAgentPrCapture;
        if (capture?.recorder === undefined) { done({ready:false, reason:'missing recorder'}); return; }
        const decode = async () => {
          try {
            const encoded = await new Blob(capture.chunks, {type:capture.mimeType}).arrayBuffer();
            const context = new OfflineAudioContext(1, 1, 48000);
            const decoded = await context.decodeAudioData(encoded.slice(0));
            let squared = 0, peak = 0, count = 0;
            for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
              for (const sample of decoded.getChannelData(channel)) {
                squared += sample * sample; peak = Math.max(peak, Math.abs(sample)); count += 1;
              }
            }
            done({ready:true, decodedFrames:decoded.length,
              decodedRms:count === 0 ? 0 : Math.sqrt(squared / count), decodedPeak:peak});
          } catch (error) { done({ready:false, reason:String(error)}); }
        };
        capture.recorder.addEventListener('stop', decode, {once:true});
        capture.recorder.stop();
        """,
    )


def process_tree(root_pid: int) -> list[int]:
    found: list[int] = []
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children_path = Path(f"/proc/{parent}/task/{parent}/children")
        try:
            children = [int(value) for value in children_path.read_text().split()]
        except (FileNotFoundError, ProcessLookupError, OSError, ValueError):
            children = []
        for child in children:
            if child not in found:
                found.append(child)
                pending.append(child)
    return found


def browser_group_processes(process_group: int) -> list[int]:
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
            group = int(stat[2])
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except (FileNotFoundError, ProcessLookupError, OSError, ValueError, IndexError):
            continue
        if group == process_group and (b"/firefox" in command or b"geckodriver" in command):
            pids.append(int(entry.name))
    return pids


def firefox_profiles(pids: list[int]) -> list[Path]:
    profiles: list[Path] = []
    for pid in pids:
        try:
            arguments = (Path(f"/proc/{pid}/cmdline").read_bytes()).split(b"\0")
        except (FileNotFoundError, ProcessLookupError, OSError):
            continue
        for index, argument in enumerate(arguments[:-1]):
            if argument == b"-profile":
                profiles.append(Path(os.fsdecode(arguments[index + 1])).resolve())
    return list(dict.fromkeys(profiles))


def stop_owned_browser(driver) -> None:
    service_process = driver.service.process
    service_pid = service_process.pid if service_process is not None else None
    descendants = process_tree(service_pid) if service_pid is not None else []
    # Firefox can take more than ten seconds to acknowledge WebDriver quit
    # after WebRTC. This smoke owns the complete captured tree, so apply the
    # repository TERM/KILL policy directly instead of leaking gate time.
    owned = list(dict.fromkeys([
        *reversed(descendants),
        *browser_group_processes(os.getpgrp()),
        *([service_pid] if service_pid is not None else []),
    ]))
    profiles = firefox_profiles(owned)
    for pid in owned:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not any(Path(f"/proc/{pid}").exists() for pid in owned):
            break
        time.sleep(0.02)
    for pid in owned:
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if service_process is not None:
        try:
            service_process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    reap_deadline = time.monotonic() + 1.0
    while time.monotonic() < reap_deadline:
        live = []
        for pid in owned:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
            except (FileNotFoundError, ProcessLookupError, OSError, IndexError):
                continue
            if state != "Z":
                live.append(pid)
        if not live:
            break
        time.sleep(0.02)
    if live:
        raise RuntimeError(f"owned Firefox processes survived cleanup: {live}")
    allowed_parents = {Path("/tmp").resolve(), Path(os.environ.get("TMPDIR", "/tmp")).resolve()}
    for profile in profiles:
        if (
            profile.parent not in allowed_parents
            or not profile.name.startswith("rust_mozprofile")
            or profile.is_symlink()
            or (profile.exists() and profile.stat().st_uid != os.getuid())
        ):
            raise RuntimeError(f"refusing to remove unexpected Firefox profile: {profile}")
        if profile.exists():
            shutil.rmtree(profile, ignore_errors=False)
        if profile.exists():
            raise RuntimeError(f"owned Firefox profile survived cleanup: {profile}")


def create_server(port: int, root: Path, capability: dict[str, object], requests: list[str]):
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), handler_for(root, capability, requests)
    )
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    return server, thread


async def main() -> int:
    started = time.monotonic()
    functional_elapsed: float | None = None
    temporary_root = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    temporary_baseline = set(temporary_root.iterdir()) if temporary_root.is_dir() else set()
    dist = ROOT / "web/dist"
    review_dist = ROOT / "web/review/dist"
    missing = [
        name
        for name, present in (
            ("Firefox", FIREFOX is not None),
            ("pinned LiveKit", LIVEKIT.is_file()),
            ("pinned geckodriver", GECKODRIVER.is_file()),
            ("production build", (dist / "index.html").is_file()),
            ("review probe build", (review_dist / "index.html").is_file()),
        )
        if not present
    ]
    if missing:
        raise RuntimeError("required PR browser tooling is absent: " + ", ".join(missing))

    api_key, api_secret = "firefox-test-key", "s" * 32
    livekit_port, web_port, swapped_port = free_port(), free_port(), free_port()
    session_id = "session-firefox-pr"
    room_name = "firefox-pr-room"
    browser_identity = f"browser-{session_id}"
    agent_identity = f"agent-{session_id}"
    config = json.dumps(
        {
            "port": livekit_port,
            "bind_addresses": ["127.0.0.1"],
            "rtc": {"tcp_port": 0, "udp_port": 0, "use_external_ip": False},
            "logging": {"level": "error"},
        }
    )
    livekit = subprocess.Popen(
        [str(LIVEKIT)],
        env={**os.environ, "LIVEKIT_CONFIG": config, "LIVEKIT_KEYS": f"{api_key}: {api_secret}"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    room = rtc.Room()
    audio_source: rtc.AudioSource | None = None
    audio_sink: LiveKitAudioSink | None = None
    session: RealtimeSession | None = None
    driver = None
    servers: list[tuple[ThreadingHTTPServer, threading.Thread]] = []
    try:
        await wait_port(livekit_port, livekit)
        agent_token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(agent_identity)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
        browser_token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(browser_identity)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                    can_publish_sources=["microphone"],
                )
            )
            .to_jwt()
        )
        await room.connect(f"ws://127.0.0.1:{livekit_port}", agent_token)
        audio_source = rtc.AudioSource(48_000, 1, queue_size_ms=100)
        audio_sink = LiveKitAudioSink(room, audio_source, lambda _source: None)
        await audio_sink.start()
        event_sink = RecordingEventSink(room, browser_identity)
        runner = DeterministicRunner()
        session = RealtimeSession(
            session_id=session_id,
            runner=runner,
            event_sink=event_sink,
            audio_sink=audio_sink,
        )

        # Controlled stale-generation injection must be refused before LiveKit capture.
        await audio_sink.prepare("turn-generation-current", 99)
        try:
            await audio_sink.write(
                "turn-generation-stale", b"\0\0" * 960, lambda: False, media_generation=98
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("stale media generation reached the LiveKit audio source")
        await audio_sink.abandon("turn-generation-current")

        capability: dict[str, object] = {
            "session_id": session_id,
            "stream_epoch": 1,
            "livekit_url": f"ws://127.0.0.1:{livekit_port}",
            "token": browser_token,
            "expires_in_seconds": 60,
            "admission_timeout_ms": 4_000,
            "control_version": "voice-agent.realtime-control.v2",
            "llm_profile": {
                "provider_mode": "local",
                "model_identity": "LiquidAI/LFM2.5-2.6B-GGUF@b421ad1d549afeda6a0fb2ad3a697cb5a7879adc#Q4_K_M",
            },
            "tts_profile": {
                "profile": "silero-kseniya",
                "backend": "silero",
                "speaker": "kseniya",
                "output_sample_rate_hz": 48_000,
                "native_sample_rate_hz": 48_000,
                "license": "CC-BY-NC-SA-4.0",
                "private_noncommercial_only": True,
            },
        }
        production_requests: list[str] = []
        swapped_requests: list[str] = []
        servers.append(create_server(web_port, dist, capability, production_requests))
        servers.append(create_server(swapped_port, review_dist, capability, swapped_requests))

        options = Options()
        options.add_argument("-headless")
        options.set_preference("media.navigator.streams.fake", True)
        options.set_preference("media.navigator.permission.disabled", True)
        options.set_preference("media.autoplay.default", 0)
        options.set_preference("media.autoplay.block-webaudio", False)
        service = Service(executable_path=str(GECKODRIVER))
        driver = webdriver.Firefox(options=options, service=service)
        driver.set_script_timeout(5)
        driver.set_window_size(1280, 900)

        # Regression probe: the production-surface assertion must reject ReviewStand.
        driver.get(f"http://127.0.0.1:{swapped_port}/")
        await wait_for(lambda: production_surface(driver)["historyItems"] == 2, "ReviewStand probe did not load")
        try:
            assert_production_entry(production_surface(driver))
        except AssertionError:
            pass
        else:
            raise AssertionError("browser smoke would accept ReviewStand at the production URL")
        if swapped_requests:
            raise AssertionError("ReviewStand probe performed real admission")

        driver.get(f"http://127.0.0.1:{web_port}/")
        await wait_for(lambda: production_surface(driver)["connectActions"] == 1, "production entry did not load")
        assert_production_entry(production_surface(driver))
        driver.find_element("xpath", "//button[normalize-space()='CONNECT']").click()
        await wait_for(lambda: production_requests == ["/api/session"], "CONNECT did not request capability")
        await wait_for(
            lambda: driver.find_element("css selector", ".microphone-status-label").text == "MIC LIVE",
            "production browser did not publish its microphone",
        )
        await wait_for(lambda: browser_identity in room.remote_participants, "browser did not join actual LiveKit")

        try:
            assert_current_pcm({"ready": True, "decodedFrames": 0, "decodedRms": 0, "decodedPeak": 0})
        except AssertionError:
            pass
        else:
            raise AssertionError("browser smoke would accept absent response PCM")

        turn_id = await session.submit_utterance(b"\0\0" * 320)
        await wait_for(
            lambda: bool(driver.find_elements("css selector", "audio[data-voice-agent-audio='agent-response']")),
            "current correlated media publication did not attach to the browser",
        )
        capture_setup = start_capture(driver)
        if capture_setup != {"ready": True, "trackCount": 1, "trackState": "live", "paused": False}:
            raise AssertionError(f"production response stream was not attached: {capture_setup}")
        runner.audio_release.set()
        context = session._active
        assert context is not None and context.task is not None
        await asyncio.wait_for(context.task, 4)
        await asyncio.sleep(0.25)
        captured = await stop_capture(driver)
        assert_current_pcm(captured)
        body = driver.find_element("tag name", "body").text
        if "Детерминированный видимый ответ." not in body:
            raise AssertionError("correlated visible response did not reach the production UI")
        terminals = [event for event in event_sink.events if event.get("terminal") is True]
        if len(terminals) != 1 or terminals[0].get("turn_id") != turn_id:
            raise AssertionError(f"turn terminal correlation changed: {terminals}")
        terminal = terminals[0]
        if terminal.get("request_id") != "request-00000001" or terminal.get("media_generation") != 1:
            raise AssertionError(f"request/media generation changed: {terminal}")

        visible = next(event for event in event_sink.events if event["type"] == "llm.visible")
        forged = {
            **visible,
            "turn_id": "turn-stale-generation",
            "turn_generation": 99,
            "request_id": "request-stale-generation",
            "media_generation": 99,
            "sequence": int(terminal["sequence"]) + 1,
            "payload": {"response": "STALE GENERATION MUST DROP"},
        }
        await LiveKitEventSink.send(event_sink, forged)
        await asyncio.sleep(0.15)
        if "STALE GENERATION MUST DROP" in driver.find_element("tag name", "body").text:
            raise AssertionError("stale request/media generation mutated the production UI")

        # Actual production entry must reject a malformed current capability before joining.
        driver.get("about:blank")
        await wait_for(lambda: browser_identity not in room.remote_participants, "browser did not leave valid room")
        capability["llm_profile"] = {
            "provider_mode": "local",
            "model_identity": "invalid-capability-model",
        }
        driver.get(f"http://127.0.0.1:{web_port}/")
        driver.find_element("xpath", "//button[normalize-space()='CONNECT']").click()
        await wait_for(lambda: production_requests == ["/api/session", "/api/session"], "invalid capability was not fetched")
        await wait_for(
            lambda: "UNAVAILABLE" in driver.find_element("tag name", "body").text,
            "invalid capability did not fail visibly",
        )
        if browser_identity in room.remote_participants:
            raise AssertionError("invalid capability joined LiveKit")

        functional_elapsed = time.monotonic() - started
    finally:
        cleanup_started = time.monotonic()
        cleanup_marks: list[str] = []
        if driver is not None:
            await asyncio.to_thread(stop_owned_browser, driver)
        cleanup_marks.append(f"firefox={time.monotonic() - cleanup_started:.3f}")
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
        try:
            await room.disconnect()
        except Exception:
            pass
        cleanup_marks.append(f"rtc={time.monotonic() - cleanup_started:.3f}")
        for server, thread in reversed(servers):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        livekit.terminate()
        try:
            livekit.wait(timeout=2)
        except subprocess.TimeoutExpired:
            livekit.kill()
            livekit.wait(timeout=1)
        cleanup_marks.append(f"all={time.monotonic() - cleanup_started:.3f}")
        if temporary_root.is_dir():
            for artifact in set(temporary_root.iterdir()) - temporary_baseline:
                try:
                    status = artifact.lstat()
                except FileNotFoundError:
                    continue
                if status.st_uid != os.getuid():
                    raise RuntimeError(f"owned browser temp artifact changed owner: {artifact}")
                if artifact.is_symlink() or artifact.is_file():
                    artifact.unlink()
                elif artifact.is_dir():
                    shutil.rmtree(artifact)
                else:
                    raise RuntimeError(f"unexpected owned browser temp artifact: {artifact}")
        print("browser_smoke_cleanup: " + " ".join(cleanup_marks))
    elapsed = time.monotonic() - started
    if functional_elapsed is None:
        raise AssertionError("short Firefox/LiveKit smoke did not reach its acceptance boundary")
    if elapsed > 15:
        raise AssertionError(f"short Firefox/LiveKit smoke exceeded 15 seconds: {elapsed:.3f}s")
    print(
        "Firefox/production/actual-LiveKit smoke: PASS "
        f"seconds={elapsed:.3f} review_swap=reject capability=reject "
        "current_pcm=present stale_generation=drop cleanup=verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

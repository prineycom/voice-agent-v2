#!/usr/bin/env python3
"""Real Firefox, React VoiceClient, and official LiveKit deterministic regression."""

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
from voice_agent_v2.livekit_runtime import (
    LiveKitAudioSink,
    LiveKitEventSink,
    LiveKitRoomController,
)
from voice_agent_v2.realtime import RealtimeSession
from voice_agent_v2.silero_tts import SileroVoiceProfile
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
    def __init__(self) -> None:
        self.run_count = 0
        self.tts_profile = SileroVoiceProfile()

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
        self.run_count += 1
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
        if self.run_count == 3:
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
        pcm = b"".join(
            round(12_000 * math.sin(2 * math.pi * 440 * sample / 48_000)).to_bytes(
                2, "little", signed=True
            )
            for sample in range(36_000)
        )
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


class DeterministicVadModel:
    def __init__(self) -> None:
        self.speech = True
        self.infer_calls = 0
        self.reset_calls = 0

    def infer(self, _pcm_s16le: bytes) -> float:
        self.infer_calls += 1
        return 1.0 if self.speech else 0.0

    def reset(self) -> None:
        self.reset_calls += 1


class ControllerRecordingSession(RealtimeSession):
    def __init__(self, *, browser_controls: list[dict[str, object]], **kwargs) -> None:
        super().__init__(**kwargs)
        self.browser_controls = browser_controls
        self.started_utterances: list[str] = []
        self.abandoned_utterances: list[str] = []
        self.finished_utterances: list[str] = []

    async def start_utterance(self, *, announce: bool = True) -> str:
        turn_id = await super().start_utterance(announce=announce)
        self.started_utterances.append(turn_id)
        return turn_id

    async def abandon_unannounced_utterance(self, turn_id: str) -> bool:
        abandoned = await super().abandon_unannounced_utterance(turn_id)
        if abandoned:
            self.abandoned_utterances.append(turn_id)
        return abandoned

    async def finish_utterance(
        self, pcm: bytes, *, endpoint_monotonic: float | None = None
    ) -> str:
        turn_id = await super().finish_utterance(
            pcm, endpoint_monotonic=endpoint_monotonic
        )
        self.finished_utterances.append(turn_id)
        return turn_id

    async def handle_client_control(self, payload: bytes) -> bool:
        self.browser_controls.append(json.loads(payload))
        return await super().handle_client_control(payload)


def handler_for(
    root: Path,
    capability: dict[str, object],
    session_requests: list[str],
):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

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
    review_dist = ROOT / "web/review/dist"
    missing = [
        name for name, present in (
            ("Firefox", FIREFOX is not None),
            ("pinned LiveKit", LIVEKIT.is_file()),
            ("built React app", (dist / "index.html").is_file()),
            ("built review fixture", (review_dist / "index.html").is_file()),
        ) if not present
    ]
    if missing:
        raise RuntimeError("required Firefox regression tooling is absent: " + ", ".join(missing))

    api_key, api_secret = "firefox-test-key", "s" * 32
    livekit_port, web_port, review_port = free_port(), free_port(), free_port()
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
    session: ControllerRecordingSession | None = None
    controller: LiveKitRoomController | None = None
    driver = None
    server: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    review_server: ThreadingHTTPServer | None = None
    review_server_thread: threading.Thread | None = None
    microphone_subscribed = asyncio.Event()
    microphone_muted = asyncio.Event()
    microphone_unmuted = asyncio.Event()
    agent_reconnecting = asyncio.Event()
    agent_reconnected = asyncio.Event()
    microphone_publication_ids: set[str] = set()
    browser_controls: list[dict[str, object]] = []
    session_requests: list[str] = []
    review_session_requests: list[str] = []
    vad_model = DeterministicVadModel()
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
        audio_source = rtc.AudioSource(48_000, 1, queue_size_ms=100)
        audio_sink = LiveKitAudioSink(
            room,
            audio_source,
            lambda source: None,
        )
        event_sink = RecordingEventSink(room, browser_identity)
        failures: list[tuple[str, str]] = []
        controller = LiveKitRoomController.__new__(LiveKitRoomController)
        controller.room = room
        controller.session_id = session_id
        controller.browser_identity = browser_identity
        controller.audio_sink = audio_sink
        controller.trace = None
        controller._audio_task = None
        controller._microphone_resume_task = None
        controller._microphone_track = None
        controller._microphone_publication_id = None
        controller._microphone_generation = 0
        controller._microphone_muted = False
        controller._capture_invalidated = False
        controller._vad_model = vad_model
        controller._browser_join_task = None
        controller._runner_start_task = None
        controller._control_queue = asyncio.Queue(maxsize=32)
        controller._control_task = None
        controller._close_retry_task = None
        controller._close_lock = asyncio.Lock()
        controller._closed = False
        controller._cleanup_complete = True
        controller._close_notified = True
        controller._transport_failed = False
        controller._room_disconnected = False
        controller._browser_ready = False
        session = ControllerRecordingSession(
            session_id=session_id,
            runner=DeterministicRunner(),
            event_sink=event_sink,
            audio_sink=audio_sink,
            failure_handler=lambda stage, code: failures.append((stage, code)),
            reconnect_reset_handler=controller._invalidate_microphone_for_reconnect,
            browser_controls=browser_controls,
        )
        controller.session = session
        controller._register_handlers()
        await audio_sink.start()
        persistent_publication_id = audio_sink.current_publication_id()

        @room.on("track_subscribed")
        def track_subscribed(track, publication, participant) -> None:
            if (
                participant.identity == browser_identity
                and track.kind == rtc.TrackKind.KIND_AUDIO
                and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            ):
                microphone_publication_ids.add(publication.sid)
                microphone_subscribed.set()

        @room.on("track_muted")
        def track_muted(participant, publication) -> None:
            if (
                participant.identity == browser_identity
                and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            ):
                microphone_muted.set()

        @room.on("track_unmuted")
        def track_unmuted(participant, publication) -> None:
            if (
                participant.identity == browser_identity
                and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            ):
                microphone_unmuted.set()

        @room.on("reconnecting")
        def reconnecting() -> None:
            agent_reconnecting.set()

        @room.on("reconnected")
        def reconnected() -> None:
            agent_reconnected.set()

        capability = {
            "session_id": session_id,
            "stream_epoch": 1,
            "livekit_url": f"ws://127.0.0.1:{livekit_port}",
            "token": browser_token,
            "expires_in_seconds": 60,
            "admission_timeout_ms": 30_000,
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
        server = ThreadingHTTPServer(
            ("127.0.0.1", web_port), handler_for(dist, capability, session_requests)
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        review_server = ThreadingHTTPServer(
            ("127.0.0.1", review_port),
            handler_for(review_dist, capability, review_session_requests),
        )
        review_server_thread = threading.Thread(
            target=review_server.serve_forever, daemon=True
        )
        review_server_thread.start()

        options = Options()
        options.add_argument("-headless")
        options.set_preference("media.navigator.streams.fake", True)
        options.set_preference("media.navigator.permission.disabled", True)
        options.set_preference("media.autoplay.default", 0)
        options.set_preference("media.autoplay.block-webaudio", False)
        download_root = EVIDENCE_ROOT / "firefox-downloads"
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
        connect = driver.find_element("xpath", "//button[normalize-space()='CONNECT']")
        initial_surface = driver.execute_script("""
            const overlayMicrophone = document.querySelector(
                '.connection-overlay__microphone-status'
            );
            return {
                historyItems: document.querySelectorAll('.history-item').length,
                microphoneState: document.querySelector('.microphone-status-label')?.textContent,
                overlayMicrophoneState: overlayMicrophone?.textContent,
                overlayMicrophoneVisible: overlayMicrophone instanceof HTMLElement
                    && overlayMicrophone.getClientRects().length > 0,
            };
        """)
        if session_requests or microphone_subscribed.is_set():
            raise AssertionError("real browser path started a session before explicit CONNECT")
        if initial_surface != {
            "historyItems": 0,
            "microphoneState": "MIC DISCONNECTED",
            "overlayMicrophoneState": "MIC DISCONNECTED",
            "overlayMicrophoneVisible": True,
        }:
            raise AssertionError(
                f"real browser path hid microphone lifecycle or leaked fixture/session state: {initial_surface}"
            )
        await asyncio.to_thread(connect.click)
        await wait_for(
            lambda: session_requests == ["/api/session"],
            "CONNECT did not request the same-origin session capability",
        )
        await asyncio.wait_for(microphone_subscribed.wait(), 15)
        await wait_for(
            lambda: driver.find_elements("css selector", ".connection-overlay--ready"),
            "Slice 7 startup overlay did not report READY",
        )
        await wait_for(
            lambda: not driver.find_elements("css selector", ".connection-overlay"),
            "Slice 7 READY startup overlay did not clear",
        )
        await wait_for(
            lambda: driver.find_element("css selector", ".microphone-status-label").text == "MIC LIVE",
            "published microphone did not expose an unambiguous live state",
        )

        def settled_microphone_controls(enabled: bool):
            pressed = str(enabled).lower()
            return [
                control
                for control in driver.find_elements(
                    "css selector", f".microphone-button[aria-pressed='{pressed}']"
                )
                if control.is_displayed()
                and control.is_enabled()
                and control.get_attribute("aria-busy") == "false"
            ]

        async def wait_for_microphone_control(
            enabled: bool, message: str, timeout: float = 15
        ):
            await wait_for(
                lambda: (
                    len(settled_microphone_controls(enabled)) == 1
                    and not driver.find_elements("css selector", ".connection-overlay")
                ),
                message,
                timeout,
            )
            controls = settled_microphone_controls(enabled)
            if len(controls) != 1:
                raise AssertionError(message)
            return controls[0]

        await wait_for(
            lambda: len(session.started_utterances) == 1,
            "official LiveKit microphone track did not reach the controller VAD boundary",
        )

        def microphone_publications():
            participant = room.remote_participants.get(browser_identity)
            if participant is None:
                return []
            return [
                publication
                for publication in participant.track_publications.values()
                if publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            ]

        initial_publications = microphone_publications()
        if len(initial_publications) != 1:
            raise AssertionError(
                f"expected one browser microphone publication, got {len(initial_publications)}"
            )
        browser_microphone_publication_id = initial_publications[0].sid
        stale_candidate_turn = session.started_utterances[0]
        microphone_events_before_off = len(event_sink.events)
        microphone_toggle = await wait_for_microphone_control(
            True, "React microphone control did not settle enabled before mute"
        )
        await asyncio.to_thread(microphone_toggle.click)
        await asyncio.wait_for(microphone_muted.wait(), 15)
        await wait_for_microphone_control(
            False, "React microphone control did not report effective muted state"
        )
        await wait_for(
            lambda: driver.find_element("css selector", ".microphone-status-label").text == "MIC MUTED",
            "React microphone status did not expose muted publication state",
        )
        await wait_for(
            lambda: session.abandoned_utterances == [stale_candidate_turn],
            "controller did not discard the pre-mute VAD candidate",
        )
        await asyncio.sleep(0.2)
        muted_infer_calls = vad_model.infer_calls
        await asyncio.sleep(0.5)
        if vad_model.infer_calls != muted_infer_calls:
            raise AssertionError("browser microphone frames continued through VAD after track_muted")
        if session.finished_utterances or len(event_sink.events) != microphone_events_before_off:
            raise AssertionError("microphone off completed or published the stale user turn")
        muted_publications = microphone_publications()
        if (
            len(muted_publications) != 1
            or muted_publications[0].sid != browser_microphone_publication_id
            or not muted_publications[0].muted
        ):
            raise AssertionError("microphone off replaced or failed to mute its publication")

        microphone_unmuted.clear()
        microphone_toggle = await wait_for_microphone_control(
            False, "React microphone control did not settle muted before unmute"
        )
        await asyncio.to_thread(microphone_toggle.click)
        await asyncio.wait_for(microphone_unmuted.wait(), 15)
        await wait_for_microphone_control(
            True, "React microphone control did not report effective unmuted state"
        )
        await wait_for(
            lambda: driver.find_element("css selector", ".microphone-status-label").text == "MIC LIVE",
            "React microphone status did not expose resumed live publication",
        )
        await wait_for(
            lambda: len(session.started_utterances) == 2,
            "fresh unmuted microphone frames did not reach a new VAD generation",
        )
        vad_model.speech = False
        await wait_for(
            lambda: len(session.finished_utterances) == 1,
            "fresh post-unmute speech did not produce its intended turn",
        )
        resumed_turn = session.finished_utterances[0]
        resumed_context = session._active
        assert resumed_context is not None and resumed_context.task is not None
        await asyncio.wait_for(resumed_context.task, 15)
        if resumed_turn == stale_candidate_turn:
            raise AssertionError("post-unmute speech reused the stale VAD candidate")
        resumed_publications = microphone_publications()
        if (
            len(resumed_publications) != 1
            or resumed_publications[0].sid != browser_microphone_publication_id
            or resumed_publications[0].muted
        ):
            raise AssertionError("microphone on replaced or failed to resume its publication")

        capture_setup = driver.execute_script("""
            const audio = document.querySelector('audio[data-voice-agent-audio="agent-response"]');
            if (!(audio instanceof HTMLAudioElement)) return {ready: false, reason: 'missing audio'};
            const attachedStream = audio.srcObject;
            if (!(attachedStream instanceof MediaStream)) {
                return {ready: false, reason: 'attached response stream missing'};
            }
            const capture = new MediaStream(attachedStream.getAudioTracks());
            const mimeType = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus']
                .find((candidate) => MediaRecorder.isTypeSupported(candidate));
            if (mimeType === undefined) {
                return {ready: false, reason: 'no supported recorder format'};
            }
            const recorder = new MediaRecorder(capture, {mimeType});
            const chunks = [];
            recorder.addEventListener('dataavailable', (event) => {
                if (event.data.size > 0) chunks.push(event.data);
            });
            recorder.start(50);
            window.__voiceAgentAttachedStreamCapture = {recorder, chunks, mimeType};
            const track = capture.getAudioTracks()[0];
            return {
                ready: recorder.state === 'recording',
                recorderState: recorder.state,
                mimeType,
                paused: audio.paused,
                ended: audio.ended,
                readyState: audio.readyState,
                currentTime: audio.currentTime,
                audioTrackCount: capture.getAudioTracks().length,
                audioTrackReadyState: track?.readyState ?? null,
            };
        """)
        if (
            capture_setup.get("ready") is not True
            or capture_setup.get("paused") is not False
            or capture_setup.get("ended") is not False
            or capture_setup.get("readyState", 0) < 2
            or capture_setup.get("audioTrackCount") != 1
            or capture_setup.get("audioTrackReadyState") != "live"
        ):
            raise AssertionError(f"attached browser response stream capture did not start: {capture_setup}")

        first_turn = await session.submit_utterance(b"\0\0" * 320)
        first_context = session._active
        assert first_context is not None and first_context.task is not None
        await asyncio.wait_for(first_context.task, 15)
        await asyncio.sleep(1)
        captured_playout = await asyncio.to_thread(
            driver.execute_async_script,
            """
                const done = arguments[0];
                const capture = window.__voiceAgentAttachedStreamCapture;
                if (capture?.recorder === undefined) {
                    done({ready: false, reason: 'recorder missing'});
                    return;
                }
                const decodeCapture = async () => {
                    try {
                        const blob = new Blob(capture.chunks, {type: capture.mimeType});
                        const encoded = await blob.arrayBuffer();
                        const decoder = new OfflineAudioContext(1, 1, 48_000);
                        const decoded = await decoder.decodeAudioData(encoded.slice(0));
                        let squared = 0;
                        let peak = 0;
                        let sampleCount = 0;
                        for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
                            const samples = decoded.getChannelData(channel);
                            sampleCount += samples.length;
                            for (const sample of samples) {
                                squared += sample * sample;
                                peak = Math.max(peak, Math.abs(sample));
                            }
                        }
                        done({
                            ready: true,
                            encodedBytes: encoded.byteLength,
                            decodedFrames: decoded.length,
                            decodedSampleRate: decoded.sampleRate,
                            decodedRms: sampleCount === 0 ? 0 : Math.sqrt(squared / sampleCount),
                            decodedPeak: peak,
                        });
                    } catch (error) {
                        done({ready: false, reason: String(error)});
                    }
                };
                if (capture.recorder.state === 'recording') {
                    capture.recorder.addEventListener('stop', decodeCapture, {once: true});
                    capture.recorder.stop();
                } else {
                    void decodeCapture();
                }
            """,
        )
        if (
            captured_playout.get("ready") is not True
            or captured_playout.get("decodedFrames", 0) < 1_000
            or captured_playout.get("decodedRms", 0) <= 0.01
            or captured_playout.get("decodedPeak", 0) <= 0.05
        ):
            raise AssertionError(
                "current-generation synthetic response PCM was absent from the stream "
                f"attached at the browser playout boundary: {captured_playout}"
            )
        response_playout = {**capture_setup, "attachedStreamCapture": captured_playout}
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
        microphone_muted.clear()
        microphone_toggle = await wait_for_microphone_control(
            True, "React microphone control did not settle enabled before reconnect mute"
        )
        await asyncio.to_thread(microphone_toggle.click)
        await asyncio.wait_for(microphone_muted.wait(), 15)
        await asyncio.sleep(0.2)
        reconnect_muted_infer_calls = vad_model.infer_calls
        reconnect_vad_counts = (
            len(session.started_utterances),
            len(session.finished_utterances),
        )
        livekit.send_signal(signal.SIGSTOP)
        try:
            await asyncio.wait_for(agent_reconnecting.wait(), 30)
        finally:
            livekit.send_signal(signal.SIGCONT)
        await asyncio.wait_for(agent_reconnected.wait(), 30)
        await wait_for_microphone_control(
            False,
            "browser UI did not settle muted after the transient LiveKit reconnect",
            timeout=30,
        )
        await wait_for(
            lambda: driver.find_element("css selector", ".microphone-status-label").text == "MIC MUTED",
            "muted reconnect lost the explicit microphone status",
            timeout=30,
        )
        await asyncio.sleep(0.5)
        reconnect_publications = microphone_publications()
        if (
            vad_model.infer_calls != reconnect_muted_infer_calls
            or len(reconnect_publications) != 1
            or reconnect_publications[0].sid != browser_microphone_publication_id
            or not reconnect_publications[0].muted
        ):
            raise AssertionError("transient LiveKit reconnect did not preserve microphone off")
        if reconnect_vad_counts != (
            len(session.started_utterances),
            len(session.finished_utterances),
        ):
            raise AssertionError("muted reconnect invented a microphone turn")
        if microphone_publication_ids != {browser_microphone_publication_id}:
            raise AssertionError("microphone toggle or reconnect rotated the browser publication")

        microphone_unmuted.clear()
        microphone_toggle = await wait_for_microphone_control(
            False, "React microphone control did not settle muted before reconnect resume"
        )
        await asyncio.to_thread(microphone_toggle.click)
        await asyncio.wait_for(microphone_unmuted.wait(), 15)
        await wait_for(
            lambda: vad_model.infer_calls > reconnect_muted_infer_calls,
            "browser microphone did not cleanly resume after muted reconnect",
        )

        second_turn = await session.submit_utterance(b"\0\0" * 320)
        second_context = session._active
        assert second_context is not None and second_context.task is not None
        await asyncio.wait_for(second_context.task, 15)

        menu = driver.find_element("xpath", "//button[@aria-label='Open menu']")
        await asyncio.to_thread(menu.click)
        history = driver.find_element("xpath", "//button[contains(., 'HISTORY')]")
        await asyncio.to_thread(history.click)
        await wait_for(
            lambda: (
                "Видимый префикс перед ошибкой." in driver.find_element("tag name", "body").text
                and "FAILED" in driver.find_element("tag name", "body").text
            ),
            "React history panel did not retain and label the deterministic failed answer",
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
            "COMPLETED",
            "FAILED",
            "USER",
            "AGENT",
        ):
            if visible_text not in body:
                raise AssertionError(f"React history omitted {visible_text!r}")
        if audio_sink.current_publication_id() != persistent_publication_id:
            raise AssertionError("LiveKit publication rotated between turns")
        if any(control.get("type") != "client.reconnected" for control in browser_controls):
            raise AssertionError(f"browser sent unexpected microphone controls: {browser_controls}")
        menu = driver.find_element("xpath", "//button[@aria-label='Open menu']")
        await asyncio.to_thread(menu.click)
        status = driver.find_element("xpath", "//button[contains(., 'STATUS')]")
        await asyncio.to_thread(status.click)
        await wait_for(
            lambda: "SILERO / kseniya / 48 KHZ" in driver.find_element("tag name", "body").text,
            "React status system tab omitted the active TTS configuration",
        )
        await wait_for(
            lambda: driver.execute_script("""
                const shell = document.querySelector('.voice-shell');
                const panel = document.querySelector('.status-panel.slide-panel--open');
                if (!(shell instanceof HTMLElement) || !(panel instanceof HTMLElement)) return false;
                const rect = panel.getBoundingClientRect();
                return shell.scrollLeft === 0
                    && Math.abs(rect.right - window.innerWidth) < 1
                    && document.querySelectorAll('.slide-panel--open').length === 1;
            """),
            "React status panel did not settle at the right edge without shifting the shell",
        )
        status_panel_layout = driver.execute_script("""
            const shell = document.querySelector('.voice-shell');
            const panel = document.querySelector('.status-panel').getBoundingClientRect();
            return {
                shell_scroll_left: shell.scrollLeft,
                panel_right: panel.right,
                viewport_width: window.innerWidth,
                open_panel_count: document.querySelectorAll('.slide-panel--open').length,
            };
        """)
        timeline = driver.find_element("xpath", "//button[normalize-space()='TIMELINE']")
        await asyncio.to_thread(timeline.click)
        await wait_for(
            lambda: "FIRST VISIBLE RESPONSE" in driver.find_element("tag name", "body").text,
            "React status timeline tab did not render",
        )
        download = driver.find_element("xpath", "//button[contains(., 'DOWNLOAD DIAGNOSTICS')]")
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
        if not any(
            record.get("stage") == "playback"
            and record.get("event") == "first_programmatic_signal"
            and record.get("turnId") == first_turn
            and record.get("requestId") is not None
            and record.get("mediaGeneration") is not None
            for record in records
        ):
            raise AssertionError("downloaded Firefox diagnostics lost the first programmatic browser signal")
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

        await asyncio.to_thread(driver.get, f"http://127.0.0.1:{review_port}/")

        def generated_review_surface():
            return driver.execute_script("""
                return {
                    connectActions: [...document.querySelectorAll('button')]
                        .filter((button) => button.textContent.trim() === 'CONNECT').length,
                    historyItems: document.querySelectorAll('.history-item').length,
                    microphoneState: document.querySelector('.microphone-status-label')?.textContent,
                    reviewLabel: document.querySelector('.connection-indicator__build')?.textContent ?? '',
                };
            """)

        await wait_for(
            lambda: (
                (surface := generated_review_surface())["historyItems"] == 2
                and surface["microphoneState"] == "MIC LIVE"
                and surface["reviewLabel"].startswith("REVIEW ")
            ),
            "dedicated review entry did not execute its isolated fixture",
        )
        review_surface = generated_review_surface()
        if review_surface["connectActions"] != 0:
            raise AssertionError(
                f"generated review entry exposed real admission: {review_surface}"
            )
        if review_session_requests or session_requests != ["/api/session"]:
            raise AssertionError("review entry performed real session admission")

        result_path.write_text(json.dumps({
            "browser_surface": body,
            "server_event_types": event_types,
            "persistent_publication_id": persistent_publication_id,
            "browser_reconnect_controls": browser_controls,
            "browser_microphone_publication_id": browser_microphone_publication_id,
            "browser_microphone_publication_count": len(reconnect_publications),
            "microphone_frames_stopped_while_muted": True,
            "stale_vad_candidate_discarded": session.abandoned_utterances == [stale_candidate_turn],
            "fresh_vad_turn_after_unmute": resumed_turn,
            "resident_vad_reset_count": vad_model.reset_calls,
            "microphone_off_preserved_across_reconnect": True,
            "response_pcm_browser_playout": response_playout,
            "generated_review_entry": review_surface,
            "status_panel_layout": status_panel_layout,
            "browser_storage": storage_state,
            "downloaded_diagnostic": str(diagnostic_path),
            "audibility_claimed": False,
        }, ensure_ascii=False, indent=2) + "\n")
        print("Firefox/React/official LiveKit deterministic full-stack regression: PASS")
        print(f"Screenshot: {screenshot_path}")
        print(f"Result: {result_path}")
        print("Evidence: synthetic PCM reached the browser playout boundary; audibility not claimed")
    finally:
        controller_tasks: list[asyncio.Task[None]] = []
        if controller is not None:
            controller._closed = True
            controller._microphone_generation += 1
            for task in (
                controller._microphone_resume_task,
                controller._audio_task,
                controller._control_task,
            ):
                if task is not None and not task.done():
                    task.cancel()
                    controller_tasks.append(task)
        if controller_tasks:
            await asyncio.gather(*controller_tasks, return_exceptions=True)
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
        if review_server is not None:
            review_server.shutdown()
            review_server.server_close()
        if review_server_thread is not None:
            review_server_thread.join(timeout=3)
        livekit.terminate()
        try:
            livekit.wait(timeout=3)
        except subprocess.TimeoutExpired:
            livekit.kill()
            livekit.wait(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python3
"""Real Firefox + official LiveKit deterministic lifecycle regression (not audibility)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-agent-v2" / "slice-6"
LIVEKIT = CACHE / "tooling/livekit-server-v1.13.5"
FIREFOX = shutil.which("firefox")

HARNESS_HTML = """<!doctype html><meta charset=utf-8><title>Slice 6 Firefox LiveKit probe</title>
<script src=/livekit.umd.js></script><pre id=result>starting</pre><script src=/probe.js></script>"""
HARNESS_JS = r"""
window.addEventListener('error', event => { document.querySelector('#result').textContent=JSON.stringify({ok:false,failure:event.message,events:[]}); document.title='FAIL'; });
(async () => {
const { Room, RoomEvent } = LivekitClient;
const params = new URLSearchParams(location.search);
const room = new Room({adaptiveStream:false,dynacast:false});
const events=[]; const record=(event,fields={})=>events.push({event,...fields});
let agentTrack=null; let failure=null;
room.on(RoomEvent.DataReceived,(payload,participant,_kind,topic)=>{
  if(topic==='voice-agent.control.v1'){const value=JSON.parse(new TextDecoder().decode(payload));record('control',{type:value.type,turn:value.turn_id});}
});
room.on(RoomEvent.TrackSubscribed,(track,pub,participant)=>{agentTrack=track;record('track-subscribed',{sid:pub.trackSid,identity:participant.identity});});
room.on(RoomEvent.TrackUnsubscribed,(_track,pub)=>record('track-unsubscribed',{sid:pub.trackSid}));
try {
  await room.connect(params.get('url'),params.get('token'));
  record('connected');
  await new Promise(r=>setTimeout(r,250));
  if(!agentTrack) throw new Error('missing correlated agent track');
  await new Promise(r=>setTimeout(r,350));
} catch(error){failure=error instanceof Error?error.message:String(error);}
finally {await room.disconnect();}
const result={ok:failure===null,failure,events};
document.querySelector('#result').textContent=JSON.stringify(result);
document.title=result.ok?'PASS':'FAIL';
})().catch(error => { document.querySelector('#result').textContent=JSON.stringify({ok:false,failure:error instanceof Error?error.message:String(error),events:[]}); document.title='FAIL'; });
"""


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


async def main() -> int:
    if FIREFOX is None or not LIVEKIT.is_file():
        print("Firefox/LiveKit deterministic regression: SKIP (host tooling absent)")
        return 0
    try:
        from livekit import api, rtc
    except ImportError:
        print("Firefox/LiveKit deterministic regression: SKIP (Python SDK absent)")
        return 0
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service

    api_key, api_secret = "firefox-test-key", "s" * 32
    livekit_port, web_port = free_port(), free_port()
    room_name = "firefox-deterministic-room"
    config = json.dumps({
        "port": livekit_port, "bind_addresses": ["127.0.0.1"],
        "rtc": {"tcp_port": 0, "udp_port": 0, "use_external_ip": False},
        "logging": {"level": "error"},
    })
    environment = dict(os.environ, LIVEKIT_CONFIG=config, LIVEKIT_KEYS=f"{api_key}: {api_secret}")
    with tempfile.TemporaryDirectory(prefix="slice6-firefox-") as directory:
        root = Path(directory)
        (root / "index.html").write_text(HARNESS_HTML)
        (root / "probe.js").write_text(HARNESS_JS)
        livekit_js = ROOT / "web/node_modules/livekit-client/dist/livekit-client.umd.js"
        if not livekit_js.is_file():
            print("Firefox/LiveKit deterministic regression: SKIP (npm runtime absent)")
            return 0
        shutil.copy2(livekit_js, root / "livekit.umd.js")
        livekit = subprocess.Popen([str(LIVEKIT)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        web = subprocess.Popen(
            [shutil.which("python3") or "python3", "-m", "http.server", str(web_port), "--bind", "127.0.0.1"],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        room = rtc.Room()
        source = None
        driver = None
        try:
            await wait_port(livekit_port, livekit)
            await wait_port(web_port, web)
            agent_token = (
                api.AccessToken(api_key, api_secret).with_identity("agent-firefox-test")
                .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_publish_data=True))
                .to_jwt()
            )
            browser_token = (
                api.AccessToken(api_key, api_secret).with_identity("browser-firefox-test")
                .with_grants(api.VideoGrants(room_join=True, room=room_name, can_subscribe=True))
                .to_jwt()
            )
            await room.connect(f"ws://127.0.0.1:{livekit_port}", agent_token)
            source = rtc.AudioSource(16_000, 1, queue_size_ms=100)
            track = rtc.LocalAudioTrack.create_audio_track("agent-response", source)
            publication = await room.local_participant.publish_track(track)
            options = Options()
            options.add_argument("-headless")
            options.set_preference("media.navigator.streams.fake", True)
            geckodriver = Path.home() / ".cache/selenium/geckodriver/linux64/0.37.1/geckodriver"
            service = Service(executable_path=str(geckodriver)) if geckodriver.is_file() else Service()
            driver = webdriver.Firefox(options=options, service=service)
            url = f"http://127.0.0.1:{web_port}/?url=ws://127.0.0.1:{livekit_port}&token={browser_token}"
            driver.get(url)
            await asyncio.sleep(0.5)
            control = {
                "schema_version":"voice-agent.realtime-control.v1","session_id":"session-firefox-test",
                "turn_id":"turn-firefox-test","stream_epoch":1,"sequence":1,"type":"turn.failed",
                "terminal":True,"payload":{"stage":"injected","code":"deterministic_failure"},
            }
            await room.local_participant.publish_data(
                json.dumps(control).encode(), reliable=True, topic="voice-agent.control.v1"
            )
            for _ in range(10):
                await source.capture_frame(rtc.AudioFrame(data=b"\0\0" * 320, sample_rate=16_000, num_channels=1, samples_per_channel=320))
            await room.local_participant.unpublish_track(publication.sid)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and driver.title not in {"PASS", "FAIL"}:
                await asyncio.sleep(0.1)
            if driver.title not in {"PASS", "FAIL"}:
                raise AssertionError(
                    "Firefox harness timed out: "
                    + str(driver.execute_script(
                        "return {title:document.title,body:document.body.innerText,ready:document.readyState,"
                        "livekit:typeof window.LivekitClient,entries:performance.getEntriesByType('resource').map(x=>x.name)}"
                    ))
                    + " console=" + str(driver.get_log("browser") if hasattr(driver, "get_log") else [])
                )
            result = json.loads(driver.find_element("id", "result").text)
            if not result.get("ok"):
                raise AssertionError(f"Firefox LiveKit lifecycle failed: {result.get('failure')}")
            observed = [entry["event"] for entry in result["events"]]
            if not {"connected", "control", "track-subscribed", "track-unsubscribed"}.issubset(observed):
                raise AssertionError(f"Firefox missed official LiveKit lifecycle: {observed}")
            controls = [entry for entry in result["events"] if entry["event"] == "control"]
            if [entry.get("type") for entry in controls] != ["turn.failed"]:
                raise AssertionError(f"Firefox missed deterministic failure control: {controls}")
            print("Firefox/official LiveKit deterministic lifecycle: PASS")
            print("Evidence: data/track subscription lifecycle only; microphone, speaker and audibility not claimed")
        finally:
            if driver is not None:
                driver.quit()
            if source is not None:
                await source.aclose()
            await room.disconnect()
            for process in (web, livekit):
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

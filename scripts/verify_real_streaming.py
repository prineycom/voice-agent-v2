#!/usr/bin/env python3
"""Focused resident Qwen + real local LFM streaming timing check, content-free output."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.local_lfm import LocalLFMProvider
from voice_agent_v2.local_tts import Qwen3TTS
from voice_agent_v2.tracer import CancellationToken

sys.path.insert(0, str(ROOT / "scripts"))
import run_slice6


def start_local_lfm() -> tuple[subprocess.Popen | None, object | None]:
    try:
        with socket.create_connection(("127.0.0.1", run_slice6.LLAMA_PORT), timeout=0.2):
            return None, None
    except OSError:
        pass
    run_slice6.verify_local_lfm_artifacts()
    environment = run_slice6.without_server_secrets(dict(os.environ))
    environment["HOME"] = str(run_slice6.LFM_CACHE / "runtime" / "home")
    environment["XDG_CACHE_HOME"] = str(run_slice6.LFM_CACHE / "runtime" / "home" / ".cache")
    environment["LD_LIBRARY_PATH"] = f"{run_slice6.CUDA_OVERLAY}:{run_slice6.LLAMA_BIN_DIRECTORY}"
    log_path = run_slice6.LFM_CACHE / "logs" / "verify-real-streaming-lfm.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        run_slice6.llama_command(), cwd=run_slice6.LFM_CACHE, env=environment,
        stdout=output, stderr=subprocess.STDOUT,
    )
    run_slice6.wait_for_port(process, run_slice6.LLAMA_PORT, "local LFM", timeout=30)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            from urllib.request import urlopen
            with urlopen(f"http://127.0.0.1:{run_slice6.LLAMA_PORT}/health", timeout=1) as response:
                if response.status == 200:
                    return process, output
        except OSError:
            time.sleep(0.1)
    run_slice6.stop(process)
    output.close()
    raise RuntimeError("local LFM health did not become ready")


def main() -> int:
    local_lfm, local_lfm_output = start_local_lfm()
    provider = LocalLFMProvider()
    tts = Qwen3TTS()
    token = CancellationToken()
    pcm_first = threading.Event()
    callbacks: list[tuple[float, int]] = []
    started = time.monotonic()
    try:
        provider.readiness(token)
        tts.start(token)
        budget = tts.create_turn_budget()

        def synthesize(sentence: str) -> None:
            for chunk in tts.stream_synthesize(
                session_id="session-real-stream",
                turn_id="turn-real-stream",
                text=sentence,
                cancellation=token,
                turn_budget=budget,
            ):
                callbacks.append((time.monotonic(), len(chunk)))
                pcm_first.set()

        provider.respond_with_handoff(
            session_id="session-real-stream",
            turn_id="turn-real-stream",
            transcript="Ответь двумя короткими законченными предложениями о спокойном утре.",
            on_sentence=synthesize,
            cancellation=token,
        )
        completed = time.monotonic()
        if not pcm_first.is_set() or not callbacks or callbacks[0][0] >= completed:
            raise AssertionError("first Qwen PCM did not precede total LFM/TTS completion")
        print("Focused real LFM -> resident Qwen PCM streaming: PASS")
        print(f"first_pcm_ms={(callbacks[0][0] - started) * 1000:.3f}")
        print(f"total_completion_ms={(completed - started) * 1000:.3f}")
        print(f"pcm_chunks={len(callbacks)} content_logged=false")
    finally:
        token.cancel()
        try:
            tts.close()
        finally:
            provider.reset_session("session-real-stream")
            if local_lfm is not None:
                run_slice6.stop(local_lfm)
            if local_lfm_output is not None:
                local_lfm_output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

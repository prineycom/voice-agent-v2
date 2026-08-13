#!/usr/bin/env python3
"""Isolated full resident Parakeet + local LFM + Qwen overlap measurement."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_slice6
import voice_agent_v2.local_tts as local_tts
from voice_agent_v2.local_lfm import LocalLFMProvider
from voice_agent_v2.local_tts import Qwen3TTS
from voice_agent_v2.parakeet_stt import ParakeetSTT
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.tracer import CancellationToken

CACHE = Path("/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt")
RESULT = CACHE / "results" / "parakeet-full-resident-overlap.json"
FIXTURE = CACHE / "fixtures" / "ruls-v1" / "ruls-test-0000.wav"
RESERVE_MIB = 1_536


def gpu_memory() -> tuple[float, float]:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    used, total = (float(item.strip()) for item in completed.stdout.splitlines()[0].split(","))
    return used, total


def process_vram() -> dict[int, float]:
    completed = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    result: dict[int, float] = {}
    for line in completed.stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) == 2 and fields[0].isdigit():
            result[int(fields[0])] = result.get(int(fields[0]), 0.0) + float(fields[1])
    return result


def available_ram_mib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    raise AssertionError("MemAvailable is absent")


def start_lfm() -> tuple[subprocess.Popen[bytes], object]:
    try:
        with socket.create_connection(("127.0.0.1", run_slice6.LLAMA_PORT), timeout=0.2):
            raise RuntimeError("refusing to reuse or disturb an existing local LFM service")
    except ConnectionRefusedError:
        pass
    except OSError:
        pass
    run_slice6.verify_local_lfm_artifacts()
    environment = run_slice6.without_server_secrets(dict(os.environ))
    environment["HOME"] = str(CACHE / "lfm-home")
    environment["XDG_CACHE_HOME"] = str(CACHE / "lfm-xdg")
    environment["LD_LIBRARY_PATH"] = f"{run_slice6.CUDA_OVERLAY}:{run_slice6.LLAMA_BIN_DIRECTORY}"
    log_path = CACHE / "logs" / "full-overlap-lfm.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        run_slice6.llama_command(), cwd=run_slice6.LFM_CACHE, env=environment,
        stdout=output, stderr=subprocess.STDOUT,
    )
    try:
        run_slice6.wait_for_port(process, run_slice6.LLAMA_PORT, "local LFM", timeout=30)
    except BaseException:
        run_slice6.stop(process)
        output.close()
        raise
    return process, output


def main() -> int:
    if os.environ.get("VOICE_AGENT_GPU_BAKEOFF_LOCK_HELD") != "1":
        raise RuntimeError("full overlap requires the shared GPU bakeoff lock")
    local_tts.CACHE = CACHE
    local_tts.LOGS = CACHE / "logs"
    local_lfm = None
    lfm_output = None
    stt = ParakeetSTT()
    tts = Qwen3TTS()
    provider = LocalLFMProvider()
    token = CancellationToken()
    peak_used, total_vram = gpu_memory()
    minimum_available_ram = available_ram_mib()
    stop_sampling = threading.Event()

    def sample() -> None:
        nonlocal peak_used, minimum_available_ram
        while not stop_sampling.wait(0.05):
            used, _total = gpu_memory()
            peak_used = max(peak_used, used)
            minimum_available_ram = min(minimum_available_ram, available_ram_mib())

    sampler = threading.Thread(target=sample, name="full-overlap-resource-sampler")
    try:
        local_lfm, lfm_output = start_lfm()
        provider.readiness(token)
        stt.start(token)
        stt_warmup = stt.warmup(token)
        tts_warmup = tts.warmup(token)
        identities_before = {
            "lfm": local_lfm.pid,
            "stt": stt.process_id,
            "tts": tts.process_id,
        }
        resident_used, _ = gpu_memory()
        resident_process_vram = process_vram()
        sampler.start()
        with wave.open(str(FIXTURE), "rb") as source:
            pcm = source.readframes(source.getnframes())
        started = time.monotonic()
        result = RealTurnController(stt, provider, tts).run_turn(
            session_id="session-full-overlap",
            turn_id="turn-full-overlap",
            input_pcm=pcm,
            cancellation=token,
            retain_output=False,
        )
        elapsed_ms = (time.monotonic() - started) * 1_000
        terminal_type = result.terminal_event["type"]
        event_count = len(result.events)
        output_bytes = result.terminal_event.get("payload", {}).get("output_bytes", 0)
        del result
        identities_after = {
            "lfm": local_lfm.pid if local_lfm.poll() is None else None,
            "stt": stt.process_id,
            "tts": tts.process_id,
        }
        stop_sampling.set()
        sampler.join()
        reserve = total_vram - peak_used
        document = {
            "schema_version": "voice-agent.parakeet-full-resident-overlap.v1",
            "isolated": True,
            "gpu_lock_held_for_process_lifetime": True,
            "models": {
                "stt": stt.identity,
                "llm": provider.provider_identity,
                "tts": tts.identity,
            },
            "warmup": {
                "stt_discarded": stt_warmup.get("discarded") is True,
                "tts_discarded": tts_warmup.get("discarded") is True,
            },
            "process_identity": {
                "before": identities_before,
                "after": identities_after,
                "stable": identities_before == identities_after,
            },
            "resources": {
                "resident_total_gpu_used_mib": resident_used,
                "peak_total_gpu_used_mib": peak_used,
                "total_gpu_vram_mib": total_vram,
                "minimum_reserve_mib": reserve,
                "required_reserve_mib": RESERVE_MIB,
                "reserve_passed": reserve >= RESERVE_MIB,
                "minimum_host_ram_available_mib": minimum_available_ram,
                "resident_compute_process_vram_mib": {
                    "lfm": resident_process_vram.get(identities_before["lfm"], 0.0),
                    "stt": resident_process_vram.get(identities_before["stt"], 0.0),
                    "tts": resident_process_vram.get(identities_before["tts"], 0.0),
                },
            },
            "turn": {
                "terminal_type": terminal_type,
                "event_count": event_count,
                "output_bytes": output_bytes,
                "total_latency_ms": elapsed_ms,
                "content_retained": False,
                "physical_audibility_claimed": False,
            },
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "result": str(RESULT),
            "terminal": terminal_type,
            "process_identity_stable": document["process_identity"]["stable"],
            "peak_total_gpu_used_mib": peak_used,
            "reserve_mib": reserve,
            "reserve_passed": document["resources"]["reserve_passed"],
        }, indent=2))
        if terminal_type != "turn.completed" or not document["process_identity"]["stable"]:
            raise AssertionError("full resident overlap turn failed")
        if reserve < RESERVE_MIB:
            raise AssertionError("full resident overlap violated the 1.5 GiB reserve")
    finally:
        stop_sampling.set()
        if sampler.is_alive():
            sampler.join()
        token.cancel()
        try:
            stt.close()
        finally:
            try:
                tts.close()
            finally:
                provider.reset_session("session-full-overlap")
                if local_lfm is not None:
                    run_slice6.stop(local_lfm)
                if lfm_output is not None:
                    lfm_output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

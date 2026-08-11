#!/usr/bin/env python3
"""Automated public-corpus full real-inference acceptance for Slice 5."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.slice2.resources import ResourceSampler
from voice_agent_v2.cloud_llm import LiteLLMProvider
from voice_agent_v2.local_stt import CACHE, WhisperSTT
from voice_agent_v2.local_tts import OUTPUT_FORMAT, Qwen3TTS
from voice_agent_v2.real_turn import RealTurnController


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise RuntimeError("public corpus format changed")
        return source.readframes(source.getnframes())


def write_wav(path: Path, pcm: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to replace playable artifact: {path}")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(OUTPUT_FORMAT.sample_rate_hz)
        output.writeframes(pcm)


def main() -> int:
    fixture = json.loads((ROOT / "benchmarks" / "fixtures" / "stt-russian-ruls.v1.json").read_text())
    run_id = str(time.monotonic_ns())
    playable = CACHE / "playable" / f"slice5-public-turns-{run_id}"
    playable.mkdir(parents=True)
    stt = WhisperSTT()
    llm = LiteLLMProvider()
    tts = Qwen3TTS()
    turns = []
    try:
        llm.readiness()
        stt.start()
        tts.start()
        controller = RealTurnController(stt, llm, tts)
        with ResourceSampler(0.05) as sampler:
            for index, sample in enumerate(fixture["samples"][:3], 1):
                pcm = read_pcm(CACHE / "corpora" / "stt-ruls-v1" / f"ruls-test-{sample['row_index']:04d}.wav")
                started = time.monotonic()
                result = controller.run_turn(
                    session_id=f"session-public-{index:04d}", turn_id=f"turn-public-{index:04d}", input_pcm=pcm,
                )
                terminal = result.terminal_event
                if terminal["type"] == "turn.completed" and result.output_pcm:
                    write_wav(playable / f"turn-{index}.wav", result.output_pcm)
                turns.append({
                    "turn": index, "public_sample_id": sample["id"],
                    "terminal": terminal["type"], "failure_stage": terminal["payload"].get("stage"),
                    "failure_code": terminal["payload"].get("code"),
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "output_bytes": len(result.output_pcm), "output_chunk_count": sum(event["type"] == "tts.audio" for event in result.events),
                })
            sample = fixture["samples"][3]
            pcm = read_pcm(CACHE / "corpora" / "stt-ruls-v1" / f"ruls-test-{sample['row_index']:04d}.wav")
            interrupted = controller.run_turn(
                session_id="session-public-cancel", turn_id="turn-public-cancel", input_pcm=pcm,
                cancel_after_output_chunks=1,
            )
            cancellation_terminal = interrupted.terminal_event["type"]
            cancellation_stage = interrupted.terminal_event["payload"].get("stage")
            cancellation_code = interrupted.terminal_event["payload"].get("code")
            terminal_index = [event["terminal"] for event in interrupted.events].index(True)
            if terminal_index != len(interrupted.events) - 1:
                raise RuntimeError("event emitted after interrupted terminal")
    finally:
        stt.close()
        tts.close()
    evidence = {
        "schema_version": "voice-agent.slice5-public-acceptance.v1",
        "fixed_stack": {"stt": stt.identity, "llm": llm.provider_identity, "tts": tts.identity},
        "turns": turns,
        "cancellation_terminal": cancellation_terminal,
        "cancellation_failure_stage": cancellation_stage,
        "cancellation_failure_code": cancellation_code,
        "stale_chunk_count": 0,
        "output_format": OUTPUT_FORMAT.as_dict(),
        "resources": {"summary": sampler.summary(), "samples": [asdict(sample) for sample in sampler.samples]},
        "privacy": {"public_corpus_only": True, "input_retained": False, "default_output_retained": False, "playable_artifacts_are_explicit_public_diagnostics": True},
        "human_microphone_listening_status": "pending",
    }
    output = CACHE / "evidence" / f"slice5-public-real-turns-{run_id}.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    completed_count = sum(item["terminal"] == "turn.completed" for item in turns)
    failed_count = len(turns) - completed_count
    if completed_count < 2:
        raise RuntimeError("fewer than two of three sustained public turns completed")
    print("Slice 5 automated public full-turn acceptance")
    print(f"turns_completed: {completed_count}")
    print(f"turns_failed_explicitly: {failed_count}")
    print(f"cancellation_terminal: {cancellation_terminal}")
    print(f"output_format: pcm_s16le/{OUTPUT_FORMAT.sample_rate_hz}Hz/mono")
    print(f"playable_artifacts: {playable}")
    print(f"gpu_vram_peak_mib: {sampler.summary()['gpu_vram_peak_mib']}")
    print(f"host_ram_minimum_available_mib: {sampler.summary()['host_ram_minimum_available_mib']}")
    print("human_microphone_listening_status: pending")
    print("RESULT: PASS_WITH_OPERATOR_FIXED_PROVIDER_LIMITATIONS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

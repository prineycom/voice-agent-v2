#!/usr/bin/env python3
"""One host-local real voice turn for final microphone/listening acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.cloud_llm import LiteLLMProvider
from voice_agent_v2.local_stt import CACHE, WhisperSTT
from voice_agent_v2.local_tts import OUTPUT_FORMAT, Qwen3TTS
from voice_agent_v2.real_turn import RealTurnController


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real Whisper → LiteLLM → Qwen3 voice turn")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--microphone", action="store_true", help="capture the default PipeWire microphone")
    source.add_argument("--input-wav", type=Path, help="use a 16 kHz mono PCM WAV")
    parser.add_argument("--duration", type=float, default=8.0, help="microphone capture seconds (default 8)")
    parser.add_argument("--play", action="store_true", help="play Qwen3 PCM through the default PipeWire sink")
    return parser.parse_args()


def wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise SystemExit("input WAV must be pcm_s16le/16000Hz/mono")
        return source.readframes(source.getnframes())


def microphone_pcm(duration: float) -> bytes:
    if not 1.0 <= duration <= 30.0:
        raise SystemExit("microphone duration must be between 1 and 30 seconds")
    temporary = CACHE / "runtime" / "turn-temp" / f"microphone-{time.monotonic_ns()}.pcm"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    print(f"Speak now ({duration:.1f} seconds)...", flush=True)
    try:
        subprocess.run([
            "pw-record", "--rate", "16000", "--channels", "1", "--format", "s16",
            "--raw", "--sample-count", str(int(duration * 16_000)), str(temporary),
        ], check=True)
        return temporary.read_bytes()
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = arguments()
    pcm = microphone_pcm(args.duration) if args.microphone else wav_pcm(args.input_wav.resolve())
    stt = WhisperSTT()
    llm = LiteLLMProvider()
    tts = Qwen3TTS()
    session_id = f"session-local-{time.monotonic_ns()}"
    turn_id = f"turn-local-{time.monotonic_ns()}"
    try:
        llm.readiness()
        stt.start()
        tts.start()
        result = RealTurnController(stt, llm, tts).run_turn(
            session_id=session_id, turn_id=turn_id, input_pcm=pcm,
        )
    finally:
        stt.close()
        tts.close()
    transcript = next((event["payload"]["transcript"] for event in result.events if event["type"] == "stt.final"), None)
    response = next((event["payload"]["response"] for event in result.events if event["type"] == "llm.final"), None)
    print(f"Transcript: {transcript or '<none>'}")
    print(f"Response: {response or '<none>'}")
    print(f"Terminal: {result.terminal_event['type']}")
    print(f"Output: pcm_s16le/{OUTPUT_FORMAT.sample_rate_hz}Hz/mono, {len(result.output_pcm)} bytes")
    if args.play and result.output_pcm:
        subprocess.run([
            "pw-play", "--rate", str(OUTPUT_FORMAT.sample_rate_hz), "--channels", "1",
            "--format", "s16", "--raw", "-",
        ], input=result.output_pcm, check=True)
    evidence = {
        "schema_version": "voice-agent.slice5-human-command-run.v1",
        "terminal": result.terminal_event["type"],
        "stt_identity": stt.identity,
        "provider_identity": llm.provider_identity,
        "tts_identity": tts.identity,
        "output_format": OUTPUT_FORMAT.as_dict(),
        "input_bytes": len(pcm), "output_bytes": len(result.output_pcm),
        "input_retained": False, "output_retained": False,
        "provider_observation": llm.observations[-1] if llm.observations else None,
        "tts_observation_count": len(tts.observations),
    }
    output = CACHE / "evidence" / f"slice5-human-run-{time.monotonic_ns()}.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("Retention: microphone=false, synthesized_audio=false, conversation_content=false")
    print("Human acceptance: judge microphone transcript and Qwen3 intelligibility/naturalness now; not auto-claimed.")
    return 0 if result.terminal_event["type"] == "turn.completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

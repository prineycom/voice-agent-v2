#!/usr/bin/env python3
"""Canonical-host real STT verification with the pinned public corpus."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.audio import DEFAULT_AUDIO_FORMAT
from voice_agent_v2.contracts import LLM_VERSION
from voice_agent_v2.local_stt import CACHE, TEMP, WhisperSTT
from voice_agent_v2.tracer import DeterministicTTS, FIXED_RESPONSE, SessionController


class FixedDownstreamLLM:
    version = LLM_VERSION
    provider_mode = "deterministic-slice3-downstream"
    provider_identity = "fixed-response-after-real-stt"

    def respond(self, *, session_id: str, turn_id: str, transcript: str) -> str:
        del session_id, turn_id
        if not transcript.strip():
            raise AssertionError("real STT transcript is empty")
        return FIXED_RESPONSE


def main() -> int:
    fixture = json.loads((ROOT / "benchmarks" / "fixtures" / "stt-russian-ruls.v1.json").read_text())
    sample = fixture["samples"][0]
    path = CACHE / "corpora" / "stt-ruls-v1" / f"ruls-test-{sample['row_index']:04d}.wav"
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise SystemExit("public corpus audio format mismatch")
        pcm = source.readframes(source.getnframes())
    stt = WhisperSTT()
    try:
        controller = SessionController(stt, FixedDownstreamLLM(), DeterministicTTS())
        trace = controller.run_turn(input_pcm=pcm)
    finally:
        stt.close()
    if trace.terminal_event["type"] != "turn.completed":
        raise SystemExit("real STT tracer did not complete")
    transcript = next(event["payload"]["transcript"] for event in trace.events if event["type"] == "stt.final")
    retained = list(TEMP.glob("*.wav")) if TEMP.exists() else []
    if retained:
        raise SystemExit("temporary STT audio was retained")
    evidence = {
        "schema_version": "voice-agent.slice3-acceptance.v1",
        "stt_identity": stt.identity,
        "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(),
        "public_sample_id": sample["id"],
        "terminal": "turn.completed",
        "temporary_audio_retained": False,
        "observation": stt.observations[-1],
    }
    output = CACHE / "evidence" / "slice3-public-turn.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("Real STT canonical-host public-corpus verification")
    print(f"stt_identity: {stt.identity}")
    print(f"public_sample: {sample['id']}")
    print(f"transcript: {transcript}")
    print("terminal: turn.completed")
    print("temporary_audio_retained: false")
    print("physical_microphone_acceptance: not_claimed_by_this_command")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

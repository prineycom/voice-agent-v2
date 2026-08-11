#!/usr/bin/env python3
"""Automated real-STT → selected LiteLLM → deterministic-audio Slice 4 acceptance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.audio import DEFAULT_AUDIO_FORMAT, generated_output_pcm
from voice_agent_v2.cloud_llm import LiteLLMProvider
from voice_agent_v2.contracts import StageFailure, TTS_VERSION
from voice_agent_v2.local_stt import CACHE, WhisperSTT
from voice_agent_v2.tracer import SessionController


class AnyTextToneTTS:
    version = TTS_VERSION

    def synthesize(self, *, session_id: str, turn_id: str, text: str, audio_format):
        del session_id, turn_id
        if not text.strip() or audio_format != DEFAULT_AUDIO_FORMAT:
            raise AssertionError("selected provider produced invalid text")
        pcm = generated_output_pcm()
        return tuple(pcm[offset:offset + 1600] for offset in range(0, len(pcm), 1600))


def main() -> int:
    fixture = json.loads((ROOT / "benchmarks" / "fixtures" / "stt-russian-ruls.v1.json").read_text())
    sample = fixture["samples"][0]
    path = CACHE / "corpora" / "stt-ruls-v1" / f"ruls-test-{sample['row_index']:04d}.wav"
    with wave.open(str(path), "rb") as source:
        pcm = source.readframes(source.getnframes())
    stt = WhisperSTT()
    provider = LiteLLMProvider()
    try:
        readiness = provider.readiness()
        trace = SessionController(stt, provider, AnyTextToneTTS()).run_turn(input_pcm=pcm)
    finally:
        stt.close()
    if trace.terminal_event["type"] != "turn.completed":
        raise SystemExit("selected-provider tracer did not complete")
    transcript = next(event["payload"]["transcript"] for event in trace.events if event["type"] == "stt.final")
    response = next(event["payload"]["response"] for event in trace.events if event["type"] == "llm.final")
    observation = provider.observations[-1]
    forbidden = {"text", "transcript", "response", "messages", "authorization"}
    if forbidden.intersection(observation):
        raise SystemExit("provider observation contains forbidden content field")
    cancellation_provider = LiteLLMProvider()
    cancellation_code = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            cancellation_provider.respond,
            session_id="session-public-cancel", turn_id="turn-public-cancel",
            transcript=("Публичная синтетическая проверка отмены. " * 40)[:4000],
        )
        time.sleep(0.25)
        cancellation_provider.cancel()
        try:
            future.result(timeout=45)
        except StageFailure as error:
            cancellation_code = error.code
    if cancellation_code != "selected_provider_cancelled":
        raise SystemExit("selected-provider cancellation was not explicit")

    evidence = {
        "schema_version": "voice-agent.slice4-acceptance.v1",
        "provider_mode": provider.provider_mode,
        "provider_identity": provider.provider_identity,
        "selected_alias": readiness["selected_alias"],
        "endpoint": readiness["endpoint"],
        "external_transfer": True,
        "automatic_fallback": False,
        "redirects_followed": readiness["redirects_followed"],
        "authenticated_alias_capability": readiness["authenticated_alias_capability"],
        "runtime_network_proof_enforced": readiness["runtime_network_proof_enforced"],
        "transport_security": readiness["transport_security"],
        "public_sample_id": sample["id"],
        "terminal": "turn.completed",
        "safe_observation": observation,
        "content_persisted": False,
        "cancellation_code": cancellation_code,
    }
    output = CACHE / "evidence" / "slice4-selected-provider-turn.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("Slice 4 selected-provider automated acceptance")
    print(f"provider: {provider.provider_identity}")
    print(f"endpoint: {readiness['endpoint']} (temporary operator-accepted HTTP)")
    print(f"public_transcript: {transcript}")
    print(f"response: {response}")
    print("automatic_fallback: false")
    print("runtime_network_proof_enforced: false")
    print(f"cancellation: {cancellation_code}")
    print("content_persisted: false")
    print("terminal: turn.completed")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

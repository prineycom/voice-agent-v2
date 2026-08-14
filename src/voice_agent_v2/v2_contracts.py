"""Backend-neutral contracts owned by Voice Agent v2."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Protocol

from .contracts import (
    AudioFormat,
    LLM_VERSION,
    STT_VERSION,
    TURN_LIFECYCLE_VERSION,
)

EVENT_ENVELOPE_V2_VERSION = "voice-agent.event-envelope.v2"
TTS_V2_VERSION = "voice-agent.tts.v2"

CONTRACT_VERSIONS_V2 = {
    "turn_lifecycle": TURN_LIFECYCLE_VERSION,
    "stt": STT_VERSION,
    "llm_provider": LLM_VERSION,
    "tts": TTS_V2_VERSION,
}


def validate_tts_v2_document(document: object) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != TTS_V2_VERSION:
        raise ValueError("invalid TTS v2 document")
    request = document.get("request")
    result = document.get("result")
    if not isinstance(request, dict) or not isinstance(result, dict):
        raise ValueError("invalid TTS v2 document")
    request_audio = request.get("audio")
    result_audio = result.get("audio")
    expected_audio = {
        "encoding": "pcm_s16le",
        "sample_rate_hz": 48_000,
        "channels": 1,
        "sample_width_bytes": 2,
    }
    if request_audio != expected_audio or result_audio != expected_audio:
        raise ValueError("invalid TTS v2 audio format")
    chunks = result.get("chunks")
    chunk_count = result.get("chunk_count")
    audio_bytes = result.get("audio_bytes")
    samples = result.get("samples")
    duration_ms = result.get("duration_ms")
    if (
        not isinstance(chunks, list)
        or not 1 <= len(chunks) <= 64
        or not isinstance(chunk_count, int)
        or isinstance(chunk_count, bool)
        or chunk_count != len(chunks)
        or not isinstance(audio_bytes, int)
        or isinstance(audio_bytes, bool)
        or not isinstance(samples, int)
        or isinstance(samples, bool)
        or not 2 <= audio_bytes <= 1_440_000
        or not 1 <= samples <= 720_000
        or not isinstance(duration_ms, (int, float))
        or isinstance(duration_ms, bool)
        or not math.isfinite(duration_ms)
        or not 0 < duration_ms <= 15_000
        or result.get("status") != "success"
        or result.get("terminal_count") != 1
    ):
        raise ValueError("invalid TTS v2 result totals")
    total_bytes = 0
    for sequence, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError("invalid TTS v2 chunk")
        chunk_sequence = chunk.get("sequence")
        byte_count = chunk.get("byte_count")
        if (
            not isinstance(chunk_sequence, int)
            or isinstance(chunk_sequence, bool)
            or chunk_sequence != sequence
            or not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= 65_536
            or byte_count % expected_audio["sample_width_bytes"]
        ):
            raise ValueError("invalid TTS v2 chunk")
        total_bytes += byte_count
    expected_samples = total_bytes // (
        expected_audio["channels"] * expected_audio["sample_width_bytes"]
    )
    expected_duration_ms = (
        expected_samples / expected_audio["sample_rate_hz"] * 1_000
    )
    if (
        audio_bytes != total_bytes
        or samples != expected_samples
        or not math.isclose(
            float(duration_ms), expected_duration_ms, rel_tol=0.0, abs_tol=0.0005
        )
    ):
        raise ValueError("inconsistent TTS v2 result totals")


@dataclass(frozen=True)
class EventEnvelopeV2:
    session_id: str
    turn_id: str
    stream_epoch: int
    turn_generation: int
    request_id: str
    sequence: int
    event_type: str
    payload: dict[str, object]
    terminal: bool = False
    diagnostic_timestamp: str | None = None

    def as_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "schema_version": EVENT_ENVELOPE_V2_VERSION,
            "contract_versions": dict(CONTRACT_VERSIONS_V2),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "stream_epoch": self.stream_epoch,
            "turn_generation": self.turn_generation,
            "request_id": self.request_id,
            "sequence": self.sequence,
            "type": self.event_type,
            "terminal": self.terminal,
            "payload": self.payload,
        }
        if self.diagnostic_timestamp is not None:
            event["diagnostic_timestamp"] = self.diagnostic_timestamp
        return event


@dataclass(frozen=True)
class TTSRequestKey:
    session_id: str
    stream_epoch: int
    turn_id: str
    turn_generation: int
    request_id: str
    segment_index: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "session_id": self.session_id,
            "stream_epoch": self.stream_epoch,
            "turn_id": self.turn_id,
            "turn_generation": self.turn_generation,
            "request_id": self.request_id,
            "segment_index": self.segment_index,
        }


class TextToSpeechV2(Protocol):
    version: str
    identity: str
    speaker: str
    output_format: AudioFormat
    capabilities: dict[str, object]

    def start(self, cancellation: object | None = None) -> dict[str, object]: ...

    def ready_for_admission(self) -> bool: ...

    def stream_synthesize(
        self,
        *,
        session_id: str,
        stream_epoch: int,
        turn_id: str,
        turn_generation: int,
        request_id: str,
        segment_index: int,
        text: str,
        audio_format: AudioFormat,
        cancellation: object | None = None,
        turn_budget: object | None = None,
    ) -> Iterable[bytes]: ...

    def invalidate_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None: ...

    def close(self) -> None: ...

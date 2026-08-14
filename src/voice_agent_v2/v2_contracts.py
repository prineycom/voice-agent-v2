"""Backend-neutral contracts owned by Voice Agent v2."""

from __future__ import annotations

from dataclasses import dataclass
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

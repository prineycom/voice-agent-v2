"""Slice 1 contract types for the deterministic voice-turn tracer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

EVENT_ENVELOPE_VERSION = "voice-agent.event-envelope.v1"
TURN_LIFECYCLE_VERSION = "voice-agent.turn-lifecycle.v1"
STT_VERSION = "voice-agent.stt.v1"
LLM_VERSION = "voice-agent.llm-provider.v1"
TTS_VERSION = "voice-agent.tts.v1"

CONTRACT_VERSIONS = {
    "turn_lifecycle": TURN_LIFECYCLE_VERSION,
    "stt": STT_VERSION,
    "llm_provider": LLM_VERSION,
    "tts": TTS_VERSION,
}

TERMINAL_TYPES = frozenset({"turn.completed", "turn.interrupted", "turn.failed"})


class StageFailure(RuntimeError):
    """A deterministic hard failure at one inference seam."""

    def __init__(self, stage: str, code: str) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code


@dataclass(frozen=True)
class AudioFormat:
    encoding: str = "pcm_s16le"
    sample_rate_hz: int = 16_000
    channels: int = 1
    sample_width_bytes: int = 2

    def as_dict(self) -> dict[str, int | str]:
        return {
            "encoding": self.encoding,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "sample_width_bytes": self.sample_width_bytes,
        }


@dataclass(frozen=True)
class EventEnvelope:
    session_id: str
    turn_id: str
    sequence: int
    event_type: str
    payload: dict[str, object]
    terminal: bool = False
    diagnostic_timestamp: str | None = None

    def as_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "schema_version": EVENT_ENVELOPE_VERSION,
            "contract_versions": dict(CONTRACT_VERSIONS),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "type": self.event_type,
            "terminal": self.terminal,
            "payload": self.payload,
        }
        if self.diagnostic_timestamp is not None:
            event["diagnostic_timestamp"] = self.diagnostic_timestamp
        return event


class SpeechToText(Protocol):
    version: str

    def transcribe(
        self, *, session_id: str, turn_id: str, pcm: bytes, audio_format: AudioFormat
    ) -> str: ...


class LLMProvider(Protocol):
    version: str
    provider_mode: str
    provider_identity: str

    def respond(self, *, session_id: str, turn_id: str, transcript: str) -> str: ...


class TextToSpeech(Protocol):
    version: str

    def synthesize(
        self, *, session_id: str, turn_id: str, text: str, audio_format: AudioFormat
    ) -> Sequence[bytes]: ...

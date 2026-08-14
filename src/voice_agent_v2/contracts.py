"""Slice 1 contract types for the deterministic voice-turn tracer."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Protocol, Sequence

EVENT_ENVELOPE_VERSION = "voice-agent.event-envelope.v1"
EVENT_ENVELOPE_V2_VERSION = "voice-agent.event-envelope.v2"
TURN_LIFECYCLE_VERSION = "voice-agent.turn-lifecycle.v1"
STT_VERSION = "voice-agent.stt.v1"
LLM_VERSION = "voice-agent.llm-provider.v1"
TTS_VERSION = "voice-agent.tts.v1"
TTS_V2_VERSION = "voice-agent.tts.v2"

CONTRACT_VERSIONS = {
    "turn_lifecycle": TURN_LIFECYCLE_VERSION,
    "stt": STT_VERSION,
    "llm_provider": LLM_VERSION,
    "tts": TTS_VERSION,
}

CONTRACT_VERSIONS_V2 = {
    "turn_lifecycle": TURN_LIFECYCLE_VERSION,
    "stt": STT_VERSION,
    "llm_provider": LLM_VERSION,
    "tts": TTS_V2_VERSION,
}

TERMINAL_TYPES = frozenset({"turn.completed", "turn.interrupted", "turn.failed"})
CORRELATION_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


def valid_correlation_id(value: str) -> bool:
    return isinstance(value, str) and CORRELATION_ID_PATTERN.fullmatch(value) is not None


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


@dataclass(frozen=True)
class EventEnvelopeV2:
    """Backend-neutral active event spine with request and turn freshness."""

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
    """Historical TTS v1 seam retained for immutable Slice 1–5 evidence."""

    version: str

    def synthesize(
        self, *, session_id: str, turn_id: str, text: str, audio_format: AudioFormat
    ) -> Sequence[bytes]: ...


class TextToSpeechV2(Protocol):
    """Model-neutral extension point; the active composition has one Silero adapter."""

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

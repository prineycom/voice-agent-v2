"""Dependency-free deterministic end-to-end synthetic voice-turn tracer."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Iterable

from .audio import DEFAULT_AUDIO_FORMAT, generated_input_pcm, generated_output_pcm
from .contracts import (
    AudioFormat,
    EventEnvelope,
    LLM_VERSION,
    STT_VERSION,
    TTS_VERSION,
    LLMProvider,
    SpeechToText,
    StageFailure,
    TextToSpeech,
)

FIXED_SESSION_ID = "session-synthetic-0001"
FIXED_TURN_ID = "turn-synthetic-0001"
FIXED_TRANSCRIPT = "Привет, это синтетический тест."
FIXED_RESPONSE = "Здравствуйте! Детерминированный голосовой ответ готов."
FAKE_PROVIDER_MODE = "deterministic-fake"
FAKE_PROVIDER_IDENTITY = "slice-1-fixed-provider"
AUDIO_CHUNK_BYTES = 1_600
SCENARIOS = (
    "success",
    "stt_failure",
    "llm_failure",
    "tts_failure",
    "cancel_after_first_audio",
)


@dataclass(frozen=True)
class TracePlan:
    failure_stage: str | None = None
    cancel_after_audio_chunks: int | None = None


@dataclass(frozen=True)
class TraceResult:
    events: tuple[dict[str, object], ...]
    input_pcm: bytes
    output_pcm: bytes

    @property
    def terminal_event(self) -> dict[str, object]:
        terminals = [event for event in self.events if event["terminal"]]
        if len(terminals) != 1:
            raise AssertionError(f"expected exactly one terminal event, got {len(terminals)}")
        return terminals[0]


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class DeterministicSTT:
    version = STT_VERSION

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def transcribe(
        self, *, session_id: str, turn_id: str, pcm: bytes, audio_format: AudioFormat
    ) -> str:
        del session_id, turn_id
        if self.fail:
            raise StageFailure("stt", "injected_stt_failure")
        if audio_format != DEFAULT_AUDIO_FORMAT or pcm != generated_input_pcm():
            raise StageFailure("stt", "invalid_synthetic_audio")
        return FIXED_TRANSCRIPT


class DeterministicLLMProvider:
    version = LLM_VERSION
    provider_mode = FAKE_PROVIDER_MODE
    provider_identity = FAKE_PROVIDER_IDENTITY

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def respond(self, *, session_id: str, turn_id: str, transcript: str) -> str:
        del session_id, turn_id
        if self.fail:
            raise StageFailure("llm_provider", "injected_selected_provider_failure")
        if transcript != FIXED_TRANSCRIPT:
            raise StageFailure("llm_provider", "invalid_synthetic_transcript")
        return FIXED_RESPONSE


class DeterministicTTS:
    version = TTS_VERSION

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def synthesize(
        self, *, session_id: str, turn_id: str, text: str, audio_format: AudioFormat
    ) -> tuple[bytes, ...]:
        del session_id, turn_id
        if self.fail:
            raise StageFailure("tts", "injected_tts_failure")
        if text != FIXED_RESPONSE or audio_format != DEFAULT_AUDIO_FORMAT:
            raise StageFailure("tts", "invalid_synthetic_tts_request")
        pcm = generated_output_pcm()
        return tuple(pcm[offset : offset + AUDIO_CHUNK_BYTES] for offset in range(0, len(pcm), AUDIO_CHUNK_BYTES))


class SessionController:
    """Owns ordered lifecycle emission, orchestration, correlation, and terminal state."""

    def __init__(self, stt: SpeechToText, llm: LLMProvider, tts: TextToSpeech) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._validate_contract_versions()

    def _validate_contract_versions(self) -> None:
        adapters = (
            ("stt", self.stt, STT_VERSION),
            ("llm_provider", self.llm, LLM_VERSION),
            ("tts", self.tts, TTS_VERSION),
        )
        for stage, adapter, expected_version in adapters:
            actual_version = getattr(adapter, "version", None)
            if actual_version != expected_version:
                raise ValueError(
                    f"incompatible {stage} contract version: "
                    f"expected {expected_version!r}, got {actual_version!r}"
                )

    def run_turn(
        self,
        *,
        input_pcm: bytes,
        plan: TracePlan = TracePlan(),
        cancellation: CancellationToken | None = None,
        diagnostic_clock: Callable[[], str] | None = None,
    ) -> TraceResult:
        self._validate_contract_versions()
        events: list[dict[str, object]] = []
        output_chunks: list[bytes] = []
        token = cancellation or CancellationToken()

        def emit(event_type: str, payload: dict[str, object], *, terminal: bool = False) -> None:
            if any(event["terminal"] for event in events):
                raise AssertionError("cannot emit after a terminal event")
            timestamp = diagnostic_clock() if diagnostic_clock is not None else None
            events.append(
                EventEnvelope(
                    session_id=FIXED_SESSION_ID,
                    turn_id=FIXED_TURN_ID,
                    sequence=len(events) + 1,
                    event_type=event_type,
                    payload=payload,
                    terminal=terminal,
                    diagnostic_timestamp=timestamp,
                ).as_dict()
            )

        def fail(error: StageFailure) -> TraceResult:
            emit(
                "turn.failed",
                {"outcome": "failed", "stage": error.stage, "code": error.code},
                terminal=True,
            )
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))

        emit(
            "turn.listening",
            {
                "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(),
                "input_bytes": len(input_pcm),
                "input_sha256": sha256(input_pcm).hexdigest(),
            },
        )
        emit("turn.transcribing", {"stage": "stt"})
        try:
            transcript = self.stt.transcribe(
                session_id=FIXED_SESSION_ID,
                turn_id=FIXED_TURN_ID,
                pcm=input_pcm,
                audio_format=DEFAULT_AUDIO_FORMAT,
            )
        except StageFailure as error:
            return fail(error)

        emit("stt.final", {"transcript": transcript})
        emit(
            "turn.thinking",
            {
                "stage": "llm_provider",
                "provider_mode": self.llm.provider_mode,
                "provider_identity": self.llm.provider_identity,
            },
        )
        try:
            response = self.llm.respond(
                session_id=FIXED_SESSION_ID,
                turn_id=FIXED_TURN_ID,
                transcript=transcript,
            )
        except StageFailure as error:
            return fail(error)

        emit(
            "llm.final",
            {
                "response": response,
                "provider_mode": self.llm.provider_mode,
                "provider_identity": self.llm.provider_identity,
            },
        )
        emit("turn.speaking", {"stage": "tts", "audio_format": DEFAULT_AUDIO_FORMAT.as_dict()})
        try:
            chunks = self.tts.synthesize(
                session_id=FIXED_SESSION_ID,
                turn_id=FIXED_TURN_ID,
                text=response,
                audio_format=DEFAULT_AUDIO_FORMAT,
            )
        except StageFailure as error:
            return fail(error)

        for chunk_index, chunk in enumerate(chunks):
            if token.cancelled:
                emit(
                    "turn.interrupted",
                    {"outcome": "interrupted", "audio_chunks_emitted": len(output_chunks)},
                    terminal=True,
                )
                return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))
            output_chunks.append(chunk)
            emit(
                "tts.audio",
                {
                    "chunk_index": chunk_index,
                    "byte_count": len(chunk),
                    "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(),
                },
            )
            if plan.cancel_after_audio_chunks == len(output_chunks):
                token.cancel()

        if token.cancelled:
            emit(
                "turn.interrupted",
                {"outcome": "interrupted", "audio_chunks_emitted": len(output_chunks)},
                terminal=True,
            )
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))

        output_pcm = b"".join(output_chunks)
        emit(
            "turn.completed",
            {
                "outcome": "completed",
                "audio_chunks_emitted": len(output_chunks),
                "output_bytes": len(output_pcm),
                "output_sha256": sha256(output_pcm).hexdigest(),
                "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(),
            },
            terminal=True,
        )
        return TraceResult(tuple(events), input_pcm, output_pcm)


def plan_for_scenario(scenario: str) -> TracePlan:
    plans = {
        "success": TracePlan(),
        "stt_failure": TracePlan(failure_stage="stt"),
        "llm_failure": TracePlan(failure_stage="llm_provider"),
        "tts_failure": TracePlan(failure_stage="tts"),
        "cancel_after_first_audio": TracePlan(cancel_after_audio_chunks=1),
    }
    try:
        return plans[scenario]
    except KeyError as error:
        raise ValueError(f"unknown scenario: {scenario}") from error


def run_scenario(scenario: str) -> TraceResult:
    plan = plan_for_scenario(scenario)
    controller = SessionController(
        DeterministicSTT(fail=plan.failure_stage == "stt"),
        DeterministicLLMProvider(fail=plan.failure_stage == "llm_provider"),
        DeterministicTTS(fail=plan.failure_stage == "tts"),
    )
    return controller.run_turn(input_pcm=generated_input_pcm(), plan=plan)


def normalize_events(events: Iterable[dict[str, object]]) -> bytes:
    """Remove explicitly diagnostic timestamps and serialize canonical JSON Lines."""
    lines: list[str] = []
    for event in events:
        normalized = {key: value for key, value in event.items() if key != "diagnostic_timestamp"}
        lines.append(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_trace_artifacts(directory: Path, result: TraceResult) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "input.pcm").write_bytes(result.input_pcm)
    (directory / "output.pcm").write_bytes(result.output_pcm)
    (directory / "trace.normalized.jsonl").write_bytes(normalize_events(result.events))

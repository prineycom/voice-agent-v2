"""Cumulative real STT → selected LLM → local streaming TTS turn controller."""

from __future__ import annotations

from typing import Callable

from .audio import DEFAULT_AUDIO_FORMAT
from .contracts import EventEnvelope, LLM_VERSION, STT_VERSION, TTS_VERSION, StageFailure
from .tracer import CancellationToken, TraceResult


class RealTurnController:
    def __init__(self, stt, llm, tts) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._validate_contract_versions()

    def _validate_contract_versions(self) -> None:
        for stage, adapter, expected in (
            ("stt", self.stt, STT_VERSION),
            ("llm_provider", self.llm, LLM_VERSION),
            ("tts", self.tts, TTS_VERSION),
        ):
            actual = getattr(adapter, "version", None)
            if actual != expected:
                raise ValueError(f"incompatible {stage} contract version: expected {expected!r}, got {actual!r}")

    def run_turn(
        self, *, session_id: str, turn_id: str, input_pcm: bytes,
        cancellation: CancellationToken | None = None,
        cancel_after_output_chunks: int | None = None,
        diagnostic_clock: Callable[[], str] | None = None,
    ) -> TraceResult:
        self._validate_contract_versions()
        events: list[dict[str, object]] = []
        output_chunks: list[bytes] = []
        token = cancellation or CancellationToken()

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            if any(event["terminal"] for event in events):
                raise AssertionError("cannot emit after terminal")
            events.append(EventEnvelope(
                session_id=session_id, turn_id=turn_id, sequence=len(events) + 1,
                event_type=event_type, payload=payload, terminal=terminal,
                diagnostic_timestamp=diagnostic_clock() if diagnostic_clock else None,
            ).as_dict())

        def fail(error: StageFailure) -> TraceResult:
            emit("turn.failed", {"outcome": "failed", "stage": error.stage, "code": error.code}, True)
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))

        emit("turn.listening", {
            "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(), "input_bytes": len(input_pcm),
            "audio_duration_ms": len(input_pcm) / 2 / 16_000 * 1000,
        })
        emit("turn.transcribing", {"stage": "stt"})
        try:
            transcript = self.stt.transcribe(
                session_id=session_id, turn_id=turn_id, pcm=input_pcm, audio_format=DEFAULT_AUDIO_FORMAT,
            )
        except StageFailure as error:
            return fail(error)
        emit("stt.final", {"transcript": transcript})
        emit("turn.thinking", {
            "stage": "llm_provider", "provider_mode": self.llm.provider_mode,
            "provider_identity": self.llm.provider_identity,
        })

        tts_error: StageFailure | None = None
        pending_chunks: list[bytes] = []

        def synthesize_sentence(sentence: str) -> None:
            nonlocal tts_error
            if tts_error is not None:
                return
            try:
                for chunk in self.tts.stream_synthesize(
                    session_id=session_id, turn_id=turn_id, text=sentence,
                    audio_format=self.tts.output_format,
                ):
                    if token.cancelled:
                        self.tts.cancel()
                        self.llm.cancel()
                        raise StageFailure("tts", "turn_cancelled")
                    pending_chunks.append(chunk)
            except StageFailure as error:
                tts_error = error

        try:
            if hasattr(self.llm, "respond_with_handoff"):
                response = self.llm.respond_with_handoff(
                    session_id=session_id, turn_id=turn_id, transcript=transcript,
                    on_sentence=synthesize_sentence,
                )
            else:
                response = self.llm.respond(session_id=session_id, turn_id=turn_id, transcript=transcript)
                synthesize_sentence(response)
        except StageFailure as error:
            return fail(error)

        emit("llm.final", {
            "response": response, "provider_mode": self.llm.provider_mode,
            "provider_identity": self.llm.provider_identity,
        })
        emit("turn.speaking", {"stage": "tts", "audio_format": self.tts.output_format.as_dict()})
        if tts_error is not None:
            return fail(tts_error)
        for index, chunk in enumerate(pending_chunks):
            if token.cancelled:
                self.tts.cancel()
                self.llm.cancel()
                emit("turn.interrupted", {"outcome": "interrupted", "audio_chunks_emitted": len(output_chunks)}, True)
                return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))
            output_chunks.append(chunk)
            emit("tts.audio", {
                "chunk_index": index, "byte_count": len(chunk),
                "audio_format": self.tts.output_format.as_dict(),
            })
            if cancel_after_output_chunks == len(output_chunks):
                token.cancel()
        if token.cancelled:
            self.tts.cancel()
            self.llm.cancel()
            emit("turn.interrupted", {"outcome": "interrupted", "audio_chunks_emitted": len(output_chunks)}, True)
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))
        output_pcm = b"".join(output_chunks)
        emit("turn.completed", {
            "outcome": "completed", "audio_chunks_emitted": len(output_chunks),
            "output_bytes": len(output_pcm),
            "audio_format": self.tts.output_format.as_dict(),
        }, True)
        return TraceResult(tuple(events), input_pcm, output_pcm)

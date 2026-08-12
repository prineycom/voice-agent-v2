"""Cumulative real STT → selected LLM → local streaming TTS turn controller."""

from __future__ import annotations

import threading
from typing import Callable

from .audio import DEFAULT_AUDIO_FORMAT
from .contracts import (
    EventEnvelope, LLM_VERSION, STT_VERSION, TTS_VERSION, StageFailure, valid_correlation_id,
)
from .tracer import CancellationToken, TraceResult


class _TurnInterrupted(Exception):
    pass


class RealTurnController:
    def __init__(self, stt, llm, tts) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._turn_lock = threading.Lock()
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
        event_observer: Callable[[dict[str, object]], None] | None = None,
    ) -> TraceResult:
        with self._turn_lock:
            return self._run_turn(
                session_id=session_id,
                turn_id=turn_id,
                input_pcm=input_pcm,
                cancellation=cancellation,
                cancel_after_output_chunks=cancel_after_output_chunks,
                diagnostic_clock=diagnostic_clock,
                event_observer=event_observer,
            )

    def _run_turn(
        self, *, session_id: str, turn_id: str, input_pcm: bytes,
        cancellation: CancellationToken | None,
        cancel_after_output_chunks: int | None,
        diagnostic_clock: Callable[[], str] | None,
        event_observer: Callable[[dict[str, object]], None] | None,
    ) -> TraceResult:
        self._validate_contract_versions()
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise ValueError("session_id and turn_id must satisfy the correlation-ID contract")
        events: list[dict[str, object]] = []
        output_chunks: list[bytes] = []
        token = cancellation or CancellationToken()

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            if any(event["terminal"] for event in events):
                raise AssertionError("cannot emit after terminal")
            event = EventEnvelope(
                session_id=session_id, turn_id=turn_id, sequence=len(events) + 1,
                event_type=event_type, payload=payload, terminal=terminal,
                diagnostic_timestamp=diagnostic_clock() if diagnostic_clock else None,
            ).as_dict()
            events.append(event)
            if event_observer is not None:
                event_observer(event)

        def fail(error: StageFailure) -> TraceResult:
            payload: dict[str, object] = {
                "outcome": "failed", "stage": error.stage, "code": error.code,
            }
            input_retained = getattr(error, "input_retained", None)
            if input_retained is not None:
                payload["input_retained"] = bool(input_retained)
            emit("turn.failed", payload, True)
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))

        def cancel_adapters(*adapters) -> None:
            for adapter in adapters:
                cancel = getattr(adapter, "cancel", None)
                if cancel is not None:
                    try:
                        cancel()
                    except Exception:
                        pass

        def interrupted() -> TraceResult:
            emit("turn.interrupted", {
                "outcome": "interrupted", "audio_chunks_emitted": len(output_chunks),
            }, True)
            return TraceResult(tuple(events), input_pcm, b"".join(output_chunks))

        def run_stage(operation, *adapters):
            unregister = token.register(lambda: cancel_adapters(*adapters))
            try:
                if token.cancelled:
                    raise _TurnInterrupted
                result = operation()
            except StageFailure:
                if token.cancelled:
                    raise _TurnInterrupted
                raise
            finally:
                unregister()
            if token.cancelled:
                cancel_adapters(*adapters)
                raise _TurnInterrupted
            return result

        emit("turn.listening", {
            "audio_format": DEFAULT_AUDIO_FORMAT.as_dict(), "input_bytes": len(input_pcm),
            "audio_duration_ms": len(input_pcm) / 2 / 16_000 * 1000,
        })
        emit("turn.transcribing", {"stage": "stt"})
        try:
            transcript = run_stage(
                lambda: self.stt.transcribe(
                    session_id=session_id, turn_id=turn_id, pcm=input_pcm,
                    audio_format=DEFAULT_AUDIO_FORMAT, cancellation=token,
                ),
                self.stt,
            )
        except _TurnInterrupted:
            return interrupted()
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
                    audio_format=self.tts.output_format, cancellation=token,
                ):
                    if token.cancelled:
                        raise _TurnInterrupted
                    pending_chunks.append(chunk)
            except StageFailure as error:
                tts_error = error

        def generate_response() -> str:
            if hasattr(self.llm, "respond_with_handoff"):
                return self.llm.respond_with_handoff(
                    session_id=session_id, turn_id=turn_id, transcript=transcript,
                    on_sentence=synthesize_sentence, cancellation=token,
                )
            response = self.llm.respond(
                session_id=session_id, turn_id=turn_id, transcript=transcript,
                cancellation=token,
            )
            synthesize_sentence(response)
            return response

        try:
            response = run_stage(generate_response, self.llm, self.tts)
        except _TurnInterrupted:
            return interrupted()
        except StageFailure as error:
            return fail(error)

        emit("llm.final", {
            "response": response, "provider_mode": self.llm.provider_mode,
            "provider_identity": self.llm.provider_identity,
        })
        emit("turn.speaking", {"stage": "tts", "audio_format": self.tts.output_format.as_dict()})
        if token.cancelled:
            cancel_adapters(self.llm, self.tts)
            return interrupted()
        if tts_error is not None:
            return fail(tts_error)
        if not pending_chunks:
            return fail(StageFailure("tts", "empty_tts_output"))
        for index, chunk in enumerate(pending_chunks):
            if token.cancelled:
                cancel_adapters(self.llm, self.tts)
                return interrupted()
            output_chunks.append(chunk)
            emit("tts.audio", {
                "chunk_index": index, "byte_count": len(chunk),
                "audio_format": self.tts.output_format.as_dict(),
            })
            if cancel_after_output_chunks == len(output_chunks):
                token.cancel()
        if token.cancelled:
            cancel_adapters(self.llm, self.tts)
            return interrupted()
        output_pcm = b"".join(output_chunks)
        emit("turn.completed", {
            "outcome": "completed", "audio_chunks_emitted": len(output_chunks),
            "output_bytes": len(output_pcm),
            "audio_format": self.tts.output_format.as_dict(),
        }, True)
        return TraceResult(tuple(events), input_pcm, output_pcm)

"""Cumulative real STT → selected LLM → local streaming TTS turn controller."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .audio import DEFAULT_AUDIO_FORMAT, OUTPUT_MEDIA_MAX_BYTES, OUTPUT_MEDIA_MAX_SECONDS
from .contracts import (
    EventEnvelope, LLM_VERSION, STT_VERSION, TTS_VERSION, StageFailure, valid_correlation_id,
)
from .tracer import CancellationToken, TraceResult


MAX_TURN_TTS_CHUNKS = 4096
MAX_TURN_TTS_OUTPUT_BYTES = OUTPUT_MEDIA_MAX_BYTES
MAX_TURN_TTS_SECONDS = float(OUTPUT_MEDIA_MAX_SECONDS)


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
        trace_observer: Callable[[str, str, dict[str, object]], None] | None = None,
        audio_observer: Callable[[int, bytes], None] | None = None,
        retain_output: bool = True,
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
                trace_observer=trace_observer,
                audio_observer=audio_observer,
                retain_output=retain_output,
            )

    def _run_turn(
        self, *, session_id: str, turn_id: str, input_pcm: bytes,
        cancellation: CancellationToken | None,
        cancel_after_output_chunks: int | None,
        diagnostic_clock: Callable[[], str] | None,
        event_observer: Callable[[dict[str, object]], None] | None,
        trace_observer: Callable[[str, str, dict[str, object]], None] | None,
        audio_observer: Callable[[int, bytes], None] | None,
        retain_output: bool,
    ) -> TraceResult:
        self._validate_contract_versions()
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise ValueError("session_id and turn_id must satisfy the correlation-ID contract")
        events: list[dict[str, object]] = []
        output_chunks: list[bytes] = []
        tts_chunks = 0
        token = cancellation or CancellationToken()

        def trace(stage: str, event: str, **fields: object) -> None:
            if trace_observer is not None:
                trace_observer(stage, event, fields)

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
            trace("controller", "event", event_type=event_type, terminal=terminal)

        def fail(error: StageFailure) -> TraceResult:
            payload: dict[str, object] = {
                "outcome": "failed", "stage": error.stage, "code": error.code,
            }
            input_retained = getattr(error, "input_retained", None)
            if input_retained is not None:
                payload["input_retained"] = bool(input_retained)
            emit("turn.failed", payload, True)
            return TraceResult(tuple(events), input_pcm, b"")

        def cancel_adapters(*adapters) -> None:
            for adapter in adapters:
                cancel = getattr(adapter, "cancel_request", None)
                if cancel is None:
                    cancel = getattr(adapter, "cancel", None)
                if cancel is not None:
                    try:
                        cancel()
                    except Exception:
                        pass

        def interrupted() -> TraceResult:
            emit("turn.interrupted", {
                "outcome": "interrupted", "audio_chunks_emitted": tts_chunks,
            }, True)
            return TraceResult(tuple(events), input_pcm, b"")

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
        tts_started = False
        visible_fragments: list[str] = []
        visible_chars = 0
        tts_bytes = 0
        tts_deadline = time.monotonic() + MAX_TURN_TTS_SECONDS
        create_turn_budget = getattr(self.tts, "create_turn_budget", None)
        turn_budget = create_turn_budget() if create_turn_budget is not None else None

        def publish_visible_sentence(sentence: str) -> None:
            nonlocal visible_chars
            if token.cancelled:
                raise _TurnInterrupted
            visible_fragments.append(sentence)
            visible_chars += len(sentence)
            emit("llm.visible", {
                "response": " ".join(visible_fragments),
                "provider_mode": self.llm.provider_mode,
                "provider_identity": self.llm.provider_identity,
            })
            trace("llm_provider", "visible_sentence", visible_chars=visible_chars)

        def synthesize_sentence(sentence: str) -> None:
            nonlocal tts_error, tts_started, tts_chunks, tts_bytes
            if token.cancelled:
                raise _TurnInterrupted
            if tts_error is not None:
                return
            try:
                if not tts_started:
                    tts_started = True
                    emit("turn.speaking", {
                        "stage": "tts", "audio_format": self.tts.output_format.as_dict()
                    })
                if time.monotonic() >= tts_deadline:
                    raise StageFailure("tts", "selected_tts_output_out_of_bounds")
                arguments = {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "text": sentence,
                    "audio_format": self.tts.output_format,
                    "cancellation": token,
                }
                if turn_budget is not None:
                    arguments["turn_budget"] = turn_budget
                trace("tts", "sentence_started", visible_chars=len(sentence))
                sentence_chunks = 0
                for chunk in self.tts.stream_synthesize(**arguments):
                    if token.cancelled:
                        continue
                    tts_chunks += 1
                    sentence_chunks += 1
                    tts_bytes += len(chunk)
                    if (
                        tts_chunks > MAX_TURN_TTS_CHUNKS
                        or tts_bytes > MAX_TURN_TTS_OUTPUT_BYTES
                        or time.monotonic() >= tts_deadline
                    ):
                        raise StageFailure("tts", "selected_tts_output_out_of_bounds")
                    if retain_output:
                        output_chunks.append(chunk)
                    if audio_observer is not None:
                        audio_observer(tts_chunks - 1, chunk)
                    emit("tts.audio", {
                        "chunk_index": tts_chunks - 1,
                        "byte_count": len(chunk),
                        "audio_format": self.tts.output_format.as_dict(),
                    })
                    if cancel_after_output_chunks == tts_chunks:
                        token.cancel()
                if token.cancelled:
                    raise _TurnInterrupted
                trace("tts", "sentence_completed", chunk_count=sentence_chunks)
            except _TurnInterrupted:
                raise
            except StageFailure as error:
                tts_error = error
                trace("tts", "failed", failure_code=error.code)

        def generate_response() -> str:
            if hasattr(self.llm, "respond_with_handoff"):
                arguments = {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript": transcript,
                    "on_sentence": synthesize_sentence,
                    "cancellation": token,
                }
                if getattr(self.llm, "supports_visible_handoff", False):
                    arguments["on_visible_sentence"] = publish_visible_sentence
                else:
                    def visible_then_synthesize(sentence: str) -> None:
                        publish_visible_sentence(sentence)
                        synthesize_sentence(sentence)
                    arguments["on_sentence"] = visible_then_synthesize
                if getattr(self.llm, "supports_handoff_abort", False):
                    def abort_handoff() -> None:
                        cancel = getattr(self.tts, "cancel_request", None)
                        if cancel is None:
                            cancel = getattr(self.tts, "cancel", None)
                        if cancel is not None:
                            cancel()

                    arguments["on_handoff_abort"] = abort_handoff
                return self.llm.respond_with_handoff(**arguments)
            response = self.llm.respond(
                session_id=session_id, turn_id=turn_id, transcript=transcript,
                cancellation=token,
            )
            publish_visible_sentence(response)
            synthesize_sentence(response)
            return response

        try:
            response = run_stage(generate_response, self.llm, self.tts)
        except _TurnInterrupted:
            return interrupted()
        except StageFailure as error:
            return fail(error)

        if token.cancelled:
            cancel_adapters(self.llm, self.tts)
            return interrupted()
        if not visible_fragments:
            emit("llm.visible", {
                "response": response,
                "provider_mode": self.llm.provider_mode,
                "provider_identity": self.llm.provider_identity,
            })
        emit("llm.final", {
            "response": response, "provider_mode": self.llm.provider_mode,
            "provider_identity": self.llm.provider_identity,
        })
        trace("llm_provider", "completed", visible_chars=len(response))
        if tts_error is not None:
            return fail(tts_error)
        if not tts_started:
            return fail(StageFailure("tts", "empty_tts_output"))
        if tts_chunks < 1 or tts_bytes < 1:
            return fail(StageFailure("tts", "empty_tts_output"))
        if token.cancelled:
            cancel_adapters(self.llm, self.tts)
            return interrupted()
        output_pcm = b"".join(output_chunks)
        emit("turn.completed", {
            "outcome": "completed", "audio_chunks_emitted": tts_chunks,
            "output_bytes": tts_bytes,
            "audio_format": self.tts.output_format.as_dict(),
        }, True)
        return TraceResult(tuple(events), input_pcm, output_pcm)

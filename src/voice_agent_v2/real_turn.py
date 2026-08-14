"""Cumulative real STT → selected LLM → local streaming TTS turn controller."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .audio import (
    INPUT_AUDIO_FORMAT,
    OUTPUT_MEDIA_MAX_BYTES,
    OUTPUT_MEDIA_MAX_SECONDS,
    TTS_V2_OUTPUT_MEDIA_MAX_BYTES,
    TTS_V2_OUTPUT_MEDIA_MAX_SECONDS,
)
from .contracts import (
    EventEnvelope,
    EventEnvelopeV2,
    LLM_VERSION,
    STT_VERSION,
    TTS_VERSION,
    TTS_V2_VERSION,
    StageFailure,
    valid_correlation_id,
)
from .tts_text import RussianTTSSegmenter
from .tracer import CancellationToken, TraceResult


MAX_TURN_TTS_CHUNKS = 4096
MAX_TURN_TTS_V2_OUTPUT_BYTES = TTS_V2_OUTPUT_MEDIA_MAX_BYTES
MAX_TURN_TTS_V2_SECONDS = float(TTS_V2_OUTPUT_MEDIA_MAX_SECONDS)


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
            ("stt", self.stt, (STT_VERSION,)),
            ("llm_provider", self.llm, (LLM_VERSION,)),
            ("tts", self.tts, (TTS_VERSION, TTS_V2_VERSION)),
        ):
            actual = getattr(adapter, "version", None)
            if actual not in expected:
                raise ValueError(
                    f"incompatible {stage} contract version: expected one of {expected!r}, got {actual!r}"
                )

    def run_turn(
        self, *, session_id: str, turn_id: str, input_pcm: bytes,
        cancellation: CancellationToken | None = None,
        cancel_after_output_chunks: int | None = None,
        diagnostic_clock: Callable[[], str] | None = None,
        event_observer: Callable[[dict[str, object]], None] | None = None,
        trace_observer: Callable[[str, str, dict[str, object]], None] | None = None,
        audio_observer: Callable[[int, bytes], None] | None = None,
        segment_started_observer: Callable[[int], None] | None = None,
        segment_audio_observer: Callable[[int, bytes | None], None] | None = None,
        retain_output: bool = True,
        stream_epoch: int = 1,
        turn_generation: int = 1,
        request_id: str | None = None,
    ) -> TraceResult:
        operation = lambda: self._run_turn(
            session_id=session_id,
            turn_id=turn_id,
            input_pcm=input_pcm,
            cancellation=cancellation,
            cancel_after_output_chunks=cancel_after_output_chunks,
            diagnostic_clock=diagnostic_clock,
            event_observer=event_observer,
            trace_observer=trace_observer,
            audio_observer=audio_observer,
            segment_started_observer=segment_started_observer,
            segment_audio_observer=segment_audio_observer,
            retain_output=retain_output,
            stream_epoch=stream_epoch,
            turn_generation=turn_generation,
            request_id=request_id or f"request-{turn_id.removeprefix('turn-')}",
        )
        # Historical v1/Qwen evidence retains its whole-turn serialization.  The
        # active v2 scheduler must allow an obsolete Silero call and its replacement
        # to occupy the two isolated workers concurrently.
        if getattr(self.tts, "version", None) == TTS_VERSION:
            with self._turn_lock:
                return operation()
        return operation()

    def _run_turn(
        self, *, session_id: str, turn_id: str, input_pcm: bytes,
        cancellation: CancellationToken | None,
        cancel_after_output_chunks: int | None,
        diagnostic_clock: Callable[[], str] | None,
        event_observer: Callable[[dict[str, object]], None] | None,
        trace_observer: Callable[[str, str, dict[str, object]], None] | None,
        audio_observer: Callable[[int, bytes], None] | None,
        segment_started_observer: Callable[[int], None] | None,
        segment_audio_observer: Callable[[int, bytes | None], None] | None,
        retain_output: bool,
        stream_epoch: int,
        turn_generation: int,
        request_id: str,
    ) -> TraceResult:
        self._validate_contract_versions()
        if not all(valid_correlation_id(value) for value in (session_id, turn_id, request_id)):
            raise ValueError("session, turn, and request IDs must satisfy the correlation-ID contract")
        if stream_epoch < 1 or turn_generation < 1:
            raise ValueError("stream epoch and turn generation must be positive")
        if (segment_started_observer is None) != (segment_audio_observer is None):
            raise ValueError("segment reservation observers must be supplied together")
        active_v2 = getattr(self.tts, "version", None) == TTS_V2_VERSION
        events: list[dict[str, object]] = []
        event_lock = threading.Lock()
        output_chunks: list[bytes] = []
        tts_chunks = 0
        token = cancellation or CancellationToken()

        def trace(stage: str, event: str, **fields: object) -> None:
            if trace_observer is not None:
                trace_observer(stage, event, fields)

        def emit(event_type: str, payload: dict[str, object], terminal: bool = False) -> None:
            with event_lock:
                if any(event["terminal"] for event in events):
                    raise AssertionError("cannot emit after terminal")
                envelope = (
                    EventEnvelopeV2(
                        session_id=session_id,
                        turn_id=turn_id,
                        stream_epoch=stream_epoch,
                        turn_generation=turn_generation,
                        request_id=request_id,
                        sequence=len(events) + 1,
                        event_type=event_type,
                        payload=payload,
                        terminal=terminal,
                        diagnostic_timestamp=diagnostic_clock() if diagnostic_clock else None,
                    )
                    if active_v2
                    else EventEnvelope(
                        session_id=session_id,
                        turn_id=turn_id,
                        sequence=len(events) + 1,
                        event_type=event_type,
                        payload=payload,
                        terminal=terminal,
                        diagnostic_timestamp=diagnostic_clock() if diagnostic_clock else None,
                    )
                )
                event = envelope.as_dict()
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
                invalidate = getattr(adapter, "invalidate_turn", None)
                if invalidate is not None:
                    try:
                        invalidate(session_id, stream_epoch, turn_id, turn_generation)
                    except Exception:
                        pass
                    continue
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
            "audio_format": INPUT_AUDIO_FORMAT.as_dict(), "input_bytes": len(input_pcm),
            "audio_duration_ms": len(input_pcm) / 2 / INPUT_AUDIO_FORMAT.sample_rate_hz * 1000,
        })
        emit("turn.transcribing", {"stage": "stt"})
        try:
            transcript = run_stage(
                lambda: self.stt.transcribe(
                    session_id=session_id, turn_id=turn_id, pcm=input_pcm,
                    audio_format=INPUT_AUDIO_FORMAT, cancellation=token,
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
        visible_text = ""
        visible_chars = 0
        tts_bytes = 0
        tts_deadline = time.monotonic() + (
            MAX_TURN_TTS_V2_SECONDS if active_v2 else float(OUTPUT_MEDIA_MAX_SECONDS)
        )
        tts_output_limit = (
            MAX_TURN_TTS_V2_OUTPUT_BYTES if active_v2 else OUTPUT_MEDIA_MAX_BYTES
        )
        segmenter = RussianTTSSegmenter() if active_v2 else None
        segment_index = 0
        create_turn_budget = getattr(self.tts, "create_turn_budget", None)
        turn_budget = create_turn_budget() if create_turn_budget is not None else None

        def publish_visible_text(update: str) -> None:
            nonlocal visible_chars, visible_text
            if token.cancelled:
                raise _TurnInterrupted
            if getattr(self.llm, "visible_handoff_is_cumulative", False):
                if not update.startswith(visible_text):
                    raise StageFailure("llm_provider", "selected_provider_protocol_error")
                visible_text = update
            else:
                visible_text += update
            visible_chars = len(visible_text)
            emit("llm.visible", {
                "response": visible_text,
                "provider_mode": self.llm.provider_mode,
                "provider_identity": self.llm.provider_identity,
            })
            trace("llm_provider", "visible_text", visible_chars=visible_chars)

        def synthesize_segment(segment: str) -> None:
            nonlocal tts_error, tts_started, tts_chunks, tts_bytes, segment_index
            if token.cancelled:
                raise _TurnInterrupted
            if tts_error is not None:
                return
            current_segment = segment_index
            segment_index += 1
            reservation_active = False
            try:
                if segment_started_observer is not None:
                    segment_started_observer(current_segment)
                    reservation_active = True
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
                    "text": segment,
                    "audio_format": self.tts.output_format,
                    "cancellation": token,
                }
                if active_v2:
                    arguments.update({
                        "stream_epoch": stream_epoch,
                        "turn_generation": turn_generation,
                        "request_id": request_id,
                        "segment_index": current_segment,
                    })
                if turn_budget is not None:
                    arguments["turn_budget"] = turn_budget
                trace(
                    "tts", "segment_started",
                    segment_index=current_segment,
                    visible_chars=len(segment),
                )
                sentence_chunks = 0
                sentence_output = bytearray()
                noncooperative = active_v2 and not bool(
                    getattr(self.tts, "capabilities", {}).get("cooperative_cancel", True)
                )
                if noncooperative:
                    trace("tts", "noncooperative_worker_started", segment_index=current_segment)
                try:
                    for chunk in self.tts.stream_synthesize(**arguments):
                        if token.cancelled:
                            continue
                        tts_chunks += 1
                        sentence_chunks += 1
                        tts_bytes += len(chunk)
                        if (
                            tts_chunks > MAX_TURN_TTS_CHUNKS
                            or tts_bytes > tts_output_limit
                            or time.monotonic() >= tts_deadline
                        ):
                            raise StageFailure("tts", "selected_tts_output_out_of_bounds")
                        if retain_output:
                            output_chunks.append(chunk)
                        if segment_audio_observer is not None:
                            sentence_output.extend(chunk)
                        elif audio_observer is not None:
                            audio_observer(tts_chunks - 1, chunk)
                        emit("tts.audio", {
                            "chunk_index": tts_chunks - 1,
                            "segment_index": current_segment,
                            "byte_count": len(chunk),
                            "audio_format": self.tts.output_format.as_dict(),
                        })
                        if cancel_after_output_chunks == tts_chunks:
                            token.cancel()
                finally:
                    if noncooperative:
                        trace(
                            "tts",
                            "noncooperative_worker_finished",
                            segment_index=current_segment,
                        )
                if token.cancelled:
                    raise _TurnInterrupted
                if segment_audio_observer is not None:
                    reservation_active = False
                    segment_audio_observer(current_segment, bytes(sentence_output))
                trace(
                    "tts", "segment_completed",
                    segment_index=current_segment,
                    chunk_count=sentence_chunks,
                )
            except _TurnInterrupted:
                raise
            except StageFailure as error:
                if token.cancelled:
                    raise _TurnInterrupted from error
                tts_error = error
                trace("tts", "failed", segment_index=current_segment, failure_code=error.code)
            finally:
                if reservation_active and segment_audio_observer is not None:
                    segment_audio_observer(current_segment, None)

        def synthesize_visible_piece(piece: str) -> None:
            nonlocal tts_error
            if token.cancelled:
                raise _TurnInterrupted
            if not active_v2:
                synthesize_segment(piece)
                return
            assert segmenter is not None
            try:
                segments = segmenter.feed(piece)
            except StageFailure as error:
                tts_error = error
                return
            for segment in segments:
                synthesize_segment(segment)

        def generate_response() -> str:
            if hasattr(self.llm, "respond_with_handoff"):
                arguments = {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript": transcript,
                    "on_sentence": synthesize_visible_piece,
                    "cancellation": token,
                }
                if getattr(self.llm, "supports_visible_handoff", False):
                    arguments["on_visible_sentence"] = publish_visible_text
                else:
                    def visible_then_synthesize(sentence: str) -> None:
                        publish_visible_text(sentence)
                        synthesize_visible_piece(sentence)
                    arguments["on_sentence"] = visible_then_synthesize
                if getattr(self.llm, "supports_handoff_abort", False):
                    def abort_handoff() -> bool:
                        cancel_adapters(self.tts)
                        return active_v2 and not bool(
                            getattr(self.tts, "capabilities", {}).get(
                                "cooperative_cancel", True
                            )
                        )

                    arguments["on_handoff_abort"] = abort_handoff
                return self.llm.respond_with_handoff(**arguments)
            response = self.llm.respond(
                session_id=session_id, turn_id=turn_id, transcript=transcript,
                cancellation=token,
            )
            publish_visible_text(response)
            synthesize_visible_piece(response)
            return response

        def generate_response_with_cleanup_signal() -> str:
            context_committed = False
            try:
                response = generate_response()
                context_committed = True
                return response
            finally:
                trace(
                    "llm_provider",
                    "cooperative_cleanup_complete",
                    context_committed=context_committed,
                )

        try:
            response = run_stage(generate_response_with_cleanup_signal, self.llm, self.tts)
        except _TurnInterrupted:
            return interrupted()
        except StageFailure as error:
            return fail(error)

        if token.cancelled:
            cancel_adapters(self.llm, self.tts)
            return interrupted()
        if active_v2 and segmenter is not None and tts_error is None:
            try:
                for final_segment in segmenter.finish():
                    synthesize_segment(final_segment)
            except _TurnInterrupted:
                return interrupted()
            except StageFailure as error:
                tts_error = error
        if visible_text != response:
            visible_text = response
            visible_chars = len(response)
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

"""Selected local Qwen3 CustomVoice TTS adapter with streaming PCM and no default retention."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Iterator

from .audio import OUTPUT_MEDIA_MAX_BYTES, OUTPUT_MEDIA_MAX_SECONDS
from .contracts import AudioFormat, StageFailure, TTS_VERSION, valid_correlation_id
from .process_adapter import AdapterProcess, AdapterProcessError
from .tracer import CancellationToken

CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
VENV = CACHE / "runtime" / "qwen-tts-venv"
RUNNER = Path(__file__).resolve().parents[2] / "benchmarks" / "slice2" / "runners" / "qwen3_tts_runner.py"
LOGS = CACHE / "raw" / "service-logs"
OUTPUT_FORMAT = AudioFormat()
TTS_REQUEST_TIMEOUT_SECONDS = float(OUTPUT_MEDIA_MAX_SECONDS)
MAX_TTS_CHUNKS = 4096
MAX_TTS_AUDIO_SECONDS = OUTPUT_MEDIA_MAX_SECONDS
MAX_TTS_OUTPUT_BYTES = OUTPUT_MEDIA_MAX_BYTES


@dataclass
class TurnTTSBudget:
    deadline: float
    chunks: int = 0
    output_bytes: int = 0

    @classmethod
    def create(cls) -> TurnTTSBudget:
        return cls(deadline=time.monotonic() + TTS_REQUEST_TIMEOUT_SECONDS)

    def remaining_seconds(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise StageFailure("tts", "selected_tts_output_out_of_bounds")
        return remaining

    def consume(self, byte_count: int) -> None:
        self.chunks += 1
        self.output_bytes += byte_count
        if self.chunks > MAX_TTS_CHUNKS or self.output_bytes > MAX_TTS_OUTPUT_BYTES:
            raise StageFailure("tts", "selected_tts_output_out_of_bounds")
        self.remaining_seconds()


def _environment() -> dict[str, str]:
    return {
        "HOME": str(CACHE / "home"), "XDG_CACHE_HOME": str(CACHE / "xdg"),
        "HF_HOME": str(CACHE / "huggingface"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }


class Qwen3TTS:
    version = TTS_VERSION
    identity = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice@0c0e3051f131929182e2c023b9537f8b1c68adfe"
    output_format = OUTPUT_FORMAT

    def __init__(self) -> None:
        self._process: AdapterProcess | None = None
        self.ready_metadata: dict | None = None
        self.observations: list[dict] = []

    def start(self, cancellation: CancellationToken | None = None) -> dict:
        if self._process is not None:
            if cancellation is not None and cancellation.cancelled:
                self.cancel()
                raise StageFailure("tts", "selected_tts_cancelled")
            return dict(self.ready_metadata or {})
        log = LOGS / f"qwen3-tts-{time.monotonic_ns()}.stderr.log"
        process = AdapterProcess([str(VENV / "bin" / "python"), str(RUNNER)], log, _environment())
        self._process = process
        try:
            if cancellation is not None and cancellation.cancelled:
                process.cancel()
            self.ready_metadata = process.start(180)
            if cancellation is not None and cancellation.cancelled:
                self.cancel()
                raise StageFailure("tts", "selected_tts_cancelled")
        except (AdapterProcessError, OSError) as error:
            try:
                process.close()
            finally:
                self._process = None
                self.ready_metadata = None
            raise StageFailure("tts", "selected_tts_unavailable") from error
        return dict(self.ready_metadata)

    def create_turn_budget(self) -> TurnTTSBudget:
        return TurnTTSBudget.create()

    def stream_synthesize(
        self, *, session_id: str, turn_id: str, text: str,
        audio_format: AudioFormat = OUTPUT_FORMAT,
        cancellation: CancellationToken | None = None,
        turn_budget: TurnTTSBudget | None = None,
    ) -> Iterator[bytes]:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("tts", "invalid_correlation_id")
        if audio_format != OUTPUT_FORMAT:
            raise StageFailure("tts", "unsupported_tts_audio_format")
        if not text.strip() or len(text) > 4096:
            raise StageFailure("tts", "tts_text_out_of_bounds")
        if cancellation is not None and cancellation.cancelled:
            raise StageFailure("tts", "selected_tts_cancelled")
        self.start(cancellation)
        process = self._process
        if process is None:
            raise StageFailure("tts", "selected_tts_unavailable")
        request_id = f"{session_id}-{turn_id}-{time.monotonic_ns()}"
        started = time.monotonic()
        budget = turn_budget or self.create_turn_budget()
        first = None
        chunks = 0
        total_bytes = 0
        final_seen = False
        try:
            for event in process.stream(
                {
                    "command": "synthesize", "request_id": request_id, "text": text,
                    "emit_pcm": True, "output_sample_rate_hz": OUTPUT_FORMAT.sample_rate_hz,
                }, budget.remaining_seconds()
            ):
                if cancellation is not None and cancellation.cancelled:
                    raise StageFailure("tts", "selected_tts_cancelled")
                budget.remaining_seconds()
                if event["event"] == "chunk":
                    data = base64.b64decode(event["pcm_base64"], validate=True)
                    if len(data) != event["bytes"] or len(data) % 2:
                        raise AdapterProcessError("invalid TTS PCM chunk")
                    chunks += 1
                    if event["sequence"] != chunks:
                        raise AdapterProcessError("out-of-order TTS chunk")
                    total_bytes += len(data)
                    if chunks > MAX_TTS_CHUNKS or total_bytes > MAX_TTS_OUTPUT_BYTES:
                        raise StageFailure("tts", "selected_tts_output_out_of_bounds")
                    budget.consume(len(data))
                    first = first or time.monotonic()
                    yield data
                elif event["event"] == "final":
                    if final_seen:
                        raise AdapterProcessError("duplicate TTS terminal event")
                    final_seen = True
                    if (
                        event["audio_bytes"] != total_bytes or event["chunk_count"] != chunks
                        or event["sample_rate_hz"] != OUTPUT_FORMAT.sample_rate_hz
                        or event["channels"] != OUTPUT_FORMAT.channels or event["encoding"] != OUTPUT_FORMAT.encoding
                    ):
                        raise AdapterProcessError("TTS terminal totals/format mismatch")
                else:
                    raise AdapterProcessError("unknown TTS event")
            if not final_seen or chunks == 0 or total_bytes == 0:
                raise AdapterProcessError("TTS produced no audio")
        except StageFailure:
            self.cancel()
            raise
        except (AdapterProcessError, OSError, ValueError, KeyError, TypeError) as error:
            self.cancel()
            raise StageFailure("tts", "selected_tts_unavailable") from error
        completed = time.monotonic()
        self.observations.append({
            "identity": self.identity, "speaker": "ryan", "language": "Russian",
            "chunk_count": chunks, "output_bytes": total_bytes,
            "first_signal_ms": ((first or completed) - started) * 1000,
            "completion_ms": (completed - started) * 1000,
            "output_format": OUTPUT_FORMAT.as_dict(), "retained_by_adapter": False,
        })

    def synthesize(
        self, *, session_id: str, turn_id: str, text: str, audio_format: AudioFormat
    ) -> tuple[bytes, ...]:
        return tuple(self.stream_synthesize(session_id=session_id, turn_id=turn_id, text=text, audio_format=audio_format))

    def cancel(self) -> float:
        process = self._process
        if process is None:
            return 0.0
        latency = process.cancel()
        if getattr(process, "process", None) is not None:
            process.close()
        self._process = None
        self.ready_metadata = None
        return latency

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None
            self.ready_metadata = None

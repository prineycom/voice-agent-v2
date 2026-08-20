"""Cache-local Silero VAD with bounded endpointing and content-free telemetry."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import hashlib
import math
import os
import sys
from typing import Callable, Protocol

from .runtime_config import load_production_runtime_config

SAMPLE_RATE_HZ = 16_000
SAMPLE_WIDTH_BYTES = 2
SILERO_WINDOW_SAMPLES = 512
SILERO_CONTEXT_SAMPLES = 64
SILERO_MODEL_SHA256 = "4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2"
SILERO_MODEL_SIZE = 1_245_151
_RUNTIME_CONFIG = load_production_runtime_config()
DEFAULT_MODEL_PATH = (
    _RUNTIME_CONFIG["models"]["vad"] / "silero_vad_v6.onnx"
    if _RUNTIME_CONFIG is not None
    else Path.home() / ".cache" / "voice-agent-v2" / "slice-6" / "models" / "silero-vad-v6.onnx"
)


class SpeechProbabilityModel(Protocol):
    def infer(self, pcm_s16le: bytes) -> float: ...

    def reset(self) -> None: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SileroOnnxModel:
    """One CPU-only recurrent Silero v6 stream; accepts exact 512-sample windows."""

    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH) -> None:
        path = model_path.resolve()
        try:
            valid_identity = (
                path.is_file()
                and path.stat().st_size == SILERO_MODEL_SIZE
                and _sha256_file(path) == SILERO_MODEL_SHA256
            )
        except OSError:
            valid_identity = False
        if not valid_identity:
            raise RuntimeError("pinned Silero VAD model identity is unavailable")

        import numpy as np
        import onnxruntime

        self._np = np
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._h = np.zeros((1, 1, 128), dtype="float32")
        self._c = np.zeros((1, 1, 128), dtype="float32")
        self._context = np.zeros((SILERO_CONTEXT_SAMPLES,), dtype="float32")

    def infer(self, pcm_s16le: bytes) -> float:
        if len(pcm_s16le) != SILERO_WINDOW_SAMPLES * SAMPLE_WIDTH_BYTES:
            raise ValueError("Silero VAD window must contain exactly 512 samples")
        np = self._np
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype("float32") / 32768.0
        input_values = np.concatenate((self._context, samples)).reshape(1, -1)
        probability, self._h, self._c = self._session.run(
            None, {"input": input_values, "h": self._h, "c": self._c}
        )
        self._context = samples[-SILERO_CONTEXT_SAMPLES:].copy()
        value = float(probability.reshape(-1)[0])
        if not math.isfinite(value):
            raise RuntimeError("Silero VAD returned a non-finite probability")
        return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class VADSignal:
    kind: str
    payload: bytes | None = None


class SileroSpeechEndpoint:
    """Speech-confirmed endpoint; noise candidates never become public turns."""

    def __init__(
        self,
        model: SpeechProbabilityModel,
        *,
        telemetry: Callable[[dict[str, object]], None] | None = None,
        input_frame_ms: int = 20,
        start_probability: float = 0.60,
        continue_probability: float = 0.35,
        start_ms: int = 96,
        end_silence_ms: int = 640,
        min_speech_ms: int = 192,
        pre_roll_ms: int = 256,
        post_speech_ms: int = 160,
        max_utterance_ms: int = 15_000,
    ) -> None:
        if input_frame_ms != 20:
            raise ValueError("Slice 6 microphone frames must remain 20 ms")
        if not 0 < continue_probability < start_probability < 1:
            raise ValueError("VAD probability thresholds are invalid")
        for duration in (
            start_ms, end_silence_ms, min_speech_ms, pre_roll_ms, post_speech_ms,
        ):
            if duration <= 0 or duration % 32:
                raise ValueError("VAD durations must be positive 32 ms window multiples")
        if max_utterance_ms <= 0:
            raise ValueError("maximum utterance duration must be positive")
        self.model = model
        self.telemetry = telemetry
        self.input_frame_bytes = SAMPLE_RATE_HZ * SAMPLE_WIDTH_BYTES * input_frame_ms // 1000
        self.window_bytes = SILERO_WINDOW_SAMPLES * SAMPLE_WIDTH_BYTES
        self.start_probability = start_probability
        self.continue_probability = continue_probability
        self.start_windows = start_ms // 32
        self.end_windows = end_silence_ms // 32
        self.min_speech_windows = min_speech_ms // 32
        self.admission_windows = max(self.start_windows, self.min_speech_windows)
        self.pre_roll_bytes = SAMPLE_RATE_HZ * SAMPLE_WIDTH_BYTES * pre_roll_ms // 1000
        self.post_speech_ms = post_speech_ms
        self.post_speech_bytes = (
            SAMPLE_RATE_HZ * SAMPLE_WIDTH_BYTES * post_speech_ms // 1000
        )
        self.max_utterance_bytes = (
            SAMPLE_RATE_HZ * SAMPLE_WIDTH_BYTES * max_utterance_ms // 1000
        )
        self._history = bytearray()
        self._pending = bytearray()
        self._candidate_pre_onset = b""
        self._candidate_windows: list[bytes] = []
        self._candidate_voiced = 0
        self._speaking = False
        self._utterance = bytearray()
        self._processed_utterance_bytes = 0
        self._last_speech_end_bytes = 0
        self._speech_windows = 0
        self._silence_windows = 0
        self._window_index = 0

    @staticmethod
    def _rms(pcm: bytes) -> int:
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        mean_square = sum(value * value for value in samples) // max(1, len(samples))
        return int(mean_square**0.5)

    @staticmethod
    def _bucket(value: float) -> float:
        return round(min(1.0, max(0.0, value)), 3)

    def _extend_history(self, pcm: bytes) -> None:
        self._history.extend(pcm)
        overflow = len(self._history) - self.pre_roll_bytes
        if overflow > 0:
            del self._history[:overflow]

    def _observe(self, decision: str, probability: float, pcm: bytes) -> None:
        if self.telemetry is None:
            return
        self.telemetry({
            "stage": "vad",
            "decision": decision,
            "window_index": self._window_index,
            "probability": self._bucket(probability),
            "start_threshold": self.start_probability,
            "continue_threshold": self.continue_probability,
            "rms": self._rms(pcm),
            "candidate_duration_ms": len(self._candidate_windows) * 32,
            "speech_duration_ms": self._speech_windows * 32,
            "silence_duration_ms": self._silence_windows * 32,
            "submitted_post_speech_ms": self.post_speech_ms,
        })

    def feed(self, pcm: bytes) -> list[VADSignal]:
        if len(pcm) != self.input_frame_bytes:
            raise ValueError("endpoint frame must be 20 ms of 16 kHz mono s16le PCM")
        if self._speaking:
            self._utterance.extend(pcm)
        self._pending.extend(pcm)
        signals: list[VADSignal] = []
        while len(self._pending) >= self.window_bytes:
            window = bytes(self._pending[: self.window_bytes])
            del self._pending[: self.window_bytes]
            self._window_index += 1
            probability = self.model.infer(window)
            if not self._speaking:
                if probability >= self.start_probability:
                    if not self._candidate_windows:
                        # Freeze the pre-onset samples before confirmation can
                        # rotate them out of the bounded custody window.
                        self._candidate_pre_onset = bytes(self._history)
                    self._candidate_windows.append(window)
                    self._candidate_voiced += 1
                    self._observe("candidate", probability, window)
                else:
                    if self._candidate_windows:
                        self._observe("candidate_rejected", probability, window)
                        self._extend_history(b"".join(self._candidate_windows))
                    self._candidate_pre_onset = b""
                    self._candidate_windows.clear()
                    self._candidate_voiced = 0
                    self._extend_history(window)
                    self._observe("noise", probability, window)
                if self._candidate_voiced >= self.admission_windows:
                    candidate = b"".join(self._candidate_windows)
                    self._speaking = True
                    self._utterance = bytearray(
                        self._candidate_pre_onset + candidate + bytes(self._pending)
                    )
                    self._processed_utterance_bytes = (
                        len(self._candidate_pre_onset) + len(candidate)
                    )
                    self._last_speech_end_bytes = self._processed_utterance_bytes
                    self._speech_windows = self._candidate_voiced
                    self._silence_windows = 0
                    self._candidate_pre_onset = b""
                    self._candidate_windows.clear()
                    self._candidate_voiced = 0
                    self._observe("speech_started", probability, window)
                    signals.append(VADSignal("speech_started"))
                continue

            self._processed_utterance_bytes += self.window_bytes
            if probability >= self.continue_probability:
                self._speech_windows += 1
                self._silence_windows = 0
                self._last_speech_end_bytes = self._processed_utterance_bytes
                self._observe("speech", probability, window)
            else:
                self._silence_windows += 1
                self._observe("silence", probability, window)
            if (
                self._silence_windows >= self.end_windows
                or len(self._utterance) >= self.max_utterance_bytes
            ):
                if self._speech_windows >= self.min_speech_windows:
                    payload_end = min(
                        len(self._utterance),
                        self.max_utterance_bytes,
                        self._last_speech_end_bytes + self.post_speech_bytes,
                    )
                    self._observe("utterance", probability, window)
                    signals.append(VADSignal("utterance", bytes(self._utterance[:payload_end])))
                else:
                    self._observe("speech_discarded", probability, window)
                    signals.append(VADSignal("speech_discarded"))
                self.reset()
                break
        return signals

    def flush(self) -> list[VADSignal]:
        if not self._speaking:
            self.reset()
            return []
        if self._speech_windows < self.min_speech_windows:
            self.reset()
            return [VADSignal("speech_discarded")]
        payload_end = min(
            len(self._utterance),
            self.max_utterance_bytes,
            self._last_speech_end_bytes + self.post_speech_bytes,
        )
        payload = bytes(self._utterance[:payload_end])
        self.reset()
        return [VADSignal("utterance", payload)]

    def reset(self) -> None:
        self.model.reset()
        self._history.clear()
        self._pending.clear()
        self._candidate_pre_onset = b""
        self._candidate_windows.clear()
        self._candidate_voiced = 0
        self._speaking = False
        self._utterance = bytearray()
        self._processed_utterance_bytes = 0
        self._last_speech_end_bytes = 0
        self._speech_windows = 0
        self._silence_windows = 0

"""Resident JSON-lines adapter for pinned local Parakeet via NeMo-Speech.cpp."""

from __future__ import annotations

from array import array
import argparse
import ctypes
import json
import math
from pathlib import Path
import sys
import time
import wave

MAX_AUDIO_SAMPLES = 30 * 16_000
MAX_TRANSCRIPT_CHARS = 4_096


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--runtime-revision", required=True)
    return parser.parse_args()


class BackendConfig(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("gpu", ctypes.c_int32)]


class ModelConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("path", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
    ]


class RecognizerConfig(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("backend", ctypes.POINTER(BackendConfig)),
        ("model", ctypes.POINTER(ModelConfig)),
        ("streaming", ctypes.c_void_p),
        ("decoder", ctypes.c_void_p),
        ("vad", ctypes.c_void_p),
        ("endpointing", ctypes.c_void_p),
        ("postproc", ctypes.c_void_p),
        ("diar", ctypes.c_void_p),
        ("batching", ctypes.c_void_p),
    ]


class RecognitionOptions(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("request_id", ctypes.c_char_p),
        ("language_code", ctypes.c_char_p),
        ("interim_results", ctypes.c_bool),
        ("enable_word_time_offsets", ctypes.c_bool),
        ("enable_automatic_punctuation", ctypes.c_bool),
        ("verbatim_transcripts", ctypes.c_bool),
        ("profanity_filter", ctypes.c_bool),
        ("stop_history_eou_ms", ctypes.c_int32),
        ("speech_contexts", ctypes.c_void_p),
        ("speech_context_count", ctypes.c_size_t),
        ("max_alternatives", ctypes.c_int32),
        ("enable_speaker_diarization", ctypes.c_bool),
        ("max_speaker_count", ctypes.c_int32),
    ]


class ParakeetRuntime:
    def __init__(self, library_path: Path, model_path: Path) -> None:
        self.library = ctypes.CDLL(str(library_path))
        self._bind()
        backend = BackendConfig(ctypes.sizeof(BackendConfig), -1)
        encoded_model = str(model_path).encode()
        model = ModelConfig(ctypes.sizeof(ModelConfig), encoded_model, b"parakeet-tdt-0.6b-v3")
        config = RecognizerConfig(
            ctypes.sizeof(RecognizerConfig), ctypes.pointer(backend), ctypes.pointer(model),
            None, None, None, None, None, None, None,
        )
        recognizer = ctypes.c_void_p()
        status = self.library.nemo_speech_asr_create(ctypes.byref(config), ctypes.byref(recognizer))
        if status != 0 or not recognizer.value:
            raise RuntimeError("recognizer_create_failed")
        self.recognizer = recognizer

    def _bind(self) -> None:
        lib = self.library
        lib.nemo_speech_asr_create.argtypes = [
            ctypes.POINTER(RecognizerConfig), ctypes.POINTER(ctypes.c_void_p)
        ]
        lib.nemo_speech_asr_create.restype = ctypes.c_int
        lib.nemo_speech_asr_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_recognition_options_default.argtypes = []
        lib.nemo_speech_asr_recognition_options_default.restype = RecognitionOptions
        lib.nemo_speech_asr_recognize_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(RecognitionOptions),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nemo_speech_asr_recognize_f32.restype = ctypes.c_int
        lib.nemo_speech_asr_result_alternative_count.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_result_alternative_count.restype = ctypes.c_size_t
        lib.nemo_speech_asr_result_transcript.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.nemo_speech_asr_result_transcript.restype = ctypes.c_char_p
        lib.nemo_speech_asr_result_confidence.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        lib.nemo_speech_asr_result_confidence.restype = ctypes.c_float
        lib.nemo_speech_asr_result_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_speech_asr_version.argtypes = []
        lib.nemo_speech_asr_version.restype = ctypes.c_char_p

    @property
    def version(self) -> str:
        value = self.library.nemo_speech_asr_version()
        return value.decode("ascii", errors="strict") if value else "unknown"

    def transcribe(self, samples: list[float], request_id: str) -> tuple[str, float]:
        values = (ctypes.c_float * len(samples))(*samples)
        options = self.library.nemo_speech_asr_recognition_options_default()
        encoded_request = request_id.encode("ascii", errors="strict")
        options.request_id = encoded_request
        options.language_code = b"ru"
        options.interim_results = False
        options.enable_word_time_offsets = False
        options.enable_automatic_punctuation = True
        options.verbatim_transcripts = False
        options.profanity_filter = False
        options.max_alternatives = 1
        result = ctypes.c_void_p()
        status = self.library.nemo_speech_asr_recognize_f32(
            self.recognizer, ctypes.byref(options), values, len(samples), 16_000,
            ctypes.byref(result),
        )
        if status != 0 or not result.value:
            raise RuntimeError("recognition_failed")
        try:
            if self.library.nemo_speech_asr_result_alternative_count(result) < 1:
                return "", 0.0
            raw = self.library.nemo_speech_asr_result_transcript(result, 0)
            hypothesis = raw.decode("utf-8", errors="strict") if raw else ""
            confidence = float(self.library.nemo_speech_asr_result_confidence(result, 0))
            if len(hypothesis) > MAX_TRANSCRIPT_CHARS or not math.isfinite(confidence):
                raise RuntimeError("recognition_result_out_of_bounds")
            return hypothesis, confidence
        finally:
            self.library.nemo_speech_asr_result_destroy(result)

    def close(self) -> None:
        recognizer = getattr(self, "recognizer", None)
        if recognizer is not None and recognizer.value:
            self.library.nemo_speech_asr_destroy(recognizer)
            recognizer.value = None


def read_pcm(path: Path) -> tuple[list[float], float]:
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != 16_000
            or audio.getcomptype() != "NONE"
        ):
            raise ValueError("unsupported_audio_format")
        frame_count = audio.getnframes()
        if frame_count < 1 or frame_count > MAX_AUDIO_SAMPLES:
            raise ValueError("invalid_audio_payload")
        payload = audio.readframes(frame_count)
    values = array("h")
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    return [sample / 32_768.0 for sample in values], frame_count / 16_000


def warmup_samples() -> list[float]:
    """Deterministic non-speech signal that executes inference and is discarded."""
    return [0.01 * math.sin(2 * math.pi * 440 * index / 16_000) for index in range(16_000)]


def main() -> int:
    args = parse_args()
    if not args.model.is_file() or not args.library.is_file():
        raise SystemExit("pinned Parakeet runtime artifacts are unavailable")
    load_started = time.perf_counter()
    runtime = ParakeetRuntime(args.library, args.model)
    emit({
        "event": "ready",
        "load_ms": (time.perf_counter() - load_started) * 1_000,
        "runtime": "nemo-speech.cpp",
        "runtime_version": runtime.version,
        "runtime_revision": args.runtime_revision,
        "device": "cpu",
        "compute_type": "q8_0",
        "language": "ru",
        "full_utterance_only": True,
        "warmed": False,
    })
    try:
        for line in sys.stdin:
            request: object = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request_not_object")
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or not request_id.isascii() or len(request_id) > 128:
                emit({"event": "error", "request_id": None, "error_class": "invalid_request"})
                continue
            operation = request.get("operation")
            if operation == "shutdown":
                emit({"event": "stopped", "request_id": request_id})
                return 0
            started = time.perf_counter()
            try:
                if operation == "warmup":
                    runtime.transcribe(warmup_samples(), request_id)
                    emit({
                        "event": "final", "request_id": request_id, "discarded": True,
                        "warmup_ms": (time.perf_counter() - started) * 1_000,
                    })
                    continue
                if operation != "transcribe" or not isinstance(request.get("audio_path"), str):
                    raise ValueError("unsupported_operation")
                samples, duration = read_pcm(Path(request["audio_path"]))
                hypothesis, confidence = runtime.transcribe(samples, request_id)
                elapsed = time.perf_counter() - started
                emit({
                    "event": "final",
                    "request_id": request_id,
                    "hypothesis": hypothesis,
                    "confidence": confidence,
                    "finalization_ms": elapsed * 1_000,
                    "audio_duration_seconds": duration,
                    "realtime_factor": elapsed / duration,
                })
            except Exception as error:
                emit({
                    "event": "error", "request_id": request_id,
                    "error_class": type(error).__name__[:128],
                })
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

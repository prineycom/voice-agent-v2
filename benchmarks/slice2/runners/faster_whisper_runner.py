"""JSON-lines faster-whisper adapter for cache-local Slice 2 measurement only."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import wave


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--compute-type", default="float16")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.model.is_dir():
        raise SystemExit(f"model directory not found: {args.model}")
    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    model = WhisperModel(
        str(args.model),
        device=args.device,
        compute_type=args.compute_type,
        local_files_only=True,
        cpu_threads=8,
        num_workers=1,
    )
    emit(
        {
            "event": "ready",
            "load_ms": (time.perf_counter() - load_started) * 1000,
            "runtime": "faster-whisper",
            "runtime_version": importlib.metadata.version("faster-whisper"),
            "engine_version": importlib.metadata.version("ctranslate2"),
            "device": args.device,
            "compute_type": args.compute_type,
        }
    )
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("operation") == "shutdown":
            emit({"event": "stopped"})
            return 0
        if request.get("operation") != "transcribe":
            emit({"event": "error", "request_id": request.get("request_id"), "code": "unsupported_operation"})
            continue
        audio_path = Path(request["audio_path"])
        with wave.open(str(audio_path), "rb") as audio:
            duration_seconds = audio.getnframes() / audio.getframerate()
        started = time.perf_counter()
        segments, info = model.transcribe(
            str(audio_path),
            language="ru",
            task="transcribe",
            beam_size=5,
            best_of=5,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
        )
        pieces: list[str] = []
        first_segment_ms: float | None = None
        for segment in segments:
            if first_segment_ms is None:
                first_segment_ms = (time.perf_counter() - started) * 1000
            pieces.append(segment.text.strip())
        elapsed = time.perf_counter() - started
        emit(
            {
                "event": "final",
                "request_id": request["request_id"],
                "hypothesis": " ".join(piece for piece in pieces if piece),
                "detected_language": info.language,
                "language_probability": info.language_probability,
                "first_segment_ms": first_segment_ms if first_segment_ms is not None else elapsed * 1000,
                "finalization_ms": elapsed * 1000,
                "audio_duration_seconds": duration_seconds,
                "realtime_factor": elapsed / duration_seconds,
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

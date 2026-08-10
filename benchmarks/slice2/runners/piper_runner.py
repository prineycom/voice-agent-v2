"""JSON-lines Piper adapter for cache-local Slice 2 timing and listening evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
from itertools import chain
import json
from pathlib import Path
import sys
import time
import wave

from benchmarks.slice2.safety import cache_path


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def _stream_to_wave(voice: object, text: str, destination: Path) -> tuple[float, float, int, int]:
    started = time.perf_counter()
    iterator = voice.synthesize(text)  # type: ignore[attr-defined]
    first = next(iter(iterator), None)
    if first is None:
        raise RuntimeError("Piper emitted no audio")
    first_audio_ms = (time.perf_counter() - started) * 1000
    chunks = chain((first,), iterator)
    sample_rate = int(first.sample_rate)
    sample_width = int(first.sample_width)
    sample_channels = int(first.sample_channels)
    frame_count = 0
    with wave.open(str(destination), "wb") as output:
        output.setframerate(sample_rate)
        output.setsampwidth(sample_width)
        output.setnchannels(sample_channels)
        for chunk in chunks:
            if (
                int(chunk.sample_rate) != sample_rate
                or int(chunk.sample_width) != sample_width
                or int(chunk.sample_channels) != sample_channels
            ):
                raise RuntimeError("Piper changed audio format within one utterance")
            data = chunk.audio_int16_bytes
            output.writeframes(data)
            frame_count += len(data) // (sample_width * sample_channels)
    elapsed = time.perf_counter() - started
    duration_seconds = frame_count / sample_rate
    return first_audio_ms, elapsed * 1000, frame_count, sample_rate


def main() -> int:
    args = parse_args()
    if not args.model.is_file() or not args.config.is_file():
        raise SystemExit("Piper model/config not found")
    from piper import PiperVoice

    load_started = time.perf_counter()
    voice = PiperVoice.load(str(args.model), config_path=str(args.config), use_cuda=False)
    emit(
        {
            "event": "ready",
            "load_ms": (time.perf_counter() - load_started) * 1000,
            "runtime": "piper-tts",
            "runtime_version": importlib.metadata.version("piper-tts"),
            "device": "cpu",
        }
    )
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("operation") == "shutdown":
            emit({"event": "stopped"})
            return 0
        if request.get("operation") != "synthesize":
            emit({"event": "error", "request_id": request.get("request_id"), "code": "unsupported_operation"})
            continue
        destination = cache_path(Path(request["wav_path"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RuntimeError(f"refusing to replace listening artifact: {destination}")
        first_audio_ms, completion_ms, frames, sample_rate = _stream_to_wave(
            voice, request["utterance"], destination
        )
        duration_seconds = frames / sample_rate
        emit(
            {
                "event": "final",
                "request_id": request["request_id"],
                "first_audio_ms": first_audio_ms,
                "completion_ms": completion_ms,
                "audio_duration_seconds": duration_seconds,
                "realtime_factor": (completion_ms / 1000) / duration_seconds,
                "sample_rate": sample_rate,
                "frame_count": frames,
                "wav_path": str(destination),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

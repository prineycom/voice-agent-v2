"""JSON-lines adapter for the preregistered Qwen3-TTS CustomVoice configuration."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import numpy as np
import soxr
import torch
from faster_qwen3_tts import FasterQwen3TTS

MODEL = Path("/home/priney/.cache/voice-agent-v2/slice-2/artifacts/tts-qwen3-12hz-17b-customvoice")
OUTPUT_ROOT = Path("/home/priney/.cache/voice-agent-v2/slice-2/generated/qwen3-tts").resolve()
SAMPLE_RATE = 24000


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def output_path(value: str) -> Path:
    path = Path(value).resolve()
    if OUTPUT_ROOT not in path.parents or path.suffix != ".pcm":
        raise ValueError("output path is outside the authorized Qwen3 TTS PCM cache")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("refusing to replace generated Qwen3 TTS PCM")
    return path


def pcm16(chunk, sample_rate: int) -> bytes:
    values = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if int(sample_rate) != SAMPLE_RATE:
        values = soxr.resample(values, int(sample_rate), SAMPLE_RATE, quality="HQ")
    return (np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def main() -> int:
    started = time.monotonic()
    model = FasterQwen3TTS.from_pretrained(
        str(MODEL), device="cuda", dtype=torch.bfloat16,
        attn_implementation="sdpa", max_seq_len=2048,
    )
    torch.cuda.synchronize()
    emit({
        "event": "ready", "runtime": "faster-qwen3-tts", "runtime_version": "0.2.6",
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "speaker": "ryan",
        "language": "Russian", "sample_rate_hz": SAMPLE_RATE,
        "load_ms": (time.monotonic() - started) * 1000,
    })
    for line in sys.stdin:
        if not line.strip():
            continue
        command = json.loads(line)
        if command.get("operation") == "shutdown":
            emit({"event": "stopped"})
            break
        request_id = str(command["request_id"])
        if command.get("command") != "synthesize":
            emit({"event": "error", "request_id": request_id, "error_class": "unsupported_command"})
            continue
        path = output_path(str(command["output_path"]))
        begin = time.monotonic()
        first = None
        chunks = 0
        total_bytes = 0
        try:
            with path.open("xb") as handle:
                stream = model.generate_custom_voice_streaming(
                    text=str(command["text"]), speaker="ryan", language="Russian", instruct=None,
                    max_new_tokens=2048, min_new_tokens=2, temperature=0.8, top_k=50,
                    top_p=0.9, do_sample=True, repetition_penalty=1.05, chunk_size=4,
                )
                for audio, sample_rate, _metadata in stream:
                    data = pcm16(audio, int(sample_rate))
                    if not data:
                        continue
                    first = first or time.monotonic()
                    handle.write(data)
                    handle.flush()
                    chunks += 1
                    total_bytes += len(data)
            torch.cuda.synchronize()
            end = time.monotonic()
            emit({
                "event": "final", "request_id": request_id,
                "first_audio_ms": ((first or end) - begin) * 1000,
                "total_ms": (end - begin) * 1000,
                "audio_bytes": total_bytes, "chunk_count": chunks,
                "audio_duration_seconds": total_bytes / (SAMPLE_RATE * 2),
                "sample_rate_hz": SAMPLE_RATE, "channels": 1, "encoding": "pcm_s16le",
            })
        except Exception as error:  # adapter boundary: class only, no content or environment
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            emit({"event": "error", "request_id": request_id, "error_class": type(error).__name__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

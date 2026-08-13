"""JSON-lines adapter for the preregistered Qwen3-TTS CustomVoice configuration."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import signal
import sys
import time

_PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr  # third-party status/progress output must not corrupt JSON-lines stdout

import numpy as np
import soxr
import torch
from faster_qwen3_tts import FasterQwen3TTS

MODEL = Path("/home/priney/.cache/voice-agent-v2/slice-2/artifacts/tts-qwen3-12hz-17b-customvoice")
OUTPUT_ROOT = Path("/home/priney/.cache/voice-agent-v2/slice-2/generated/qwen3-tts").resolve()
SAMPLE_RATE = 24000
_active_request_id: str | None = None
_cancelled_request_id: str | None = None


def request_cancel(_signum, _frame) -> None:
    global _cancelled_request_id
    if _active_request_id is not None:
        _cancelled_request_id = _active_request_id


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), file=_PROTOCOL_STDOUT, flush=True)


def output_path(value: str) -> Path:
    path = Path(value).resolve()
    if OUTPUT_ROOT not in path.parents or path.suffix != ".pcm":
        raise ValueError("output path is outside the authorized Qwen3 TTS PCM cache")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("refusing to replace generated Qwen3 TTS PCM")
    return path


def pcm16(chunk, sample_rate: int, output_sample_rate: int = SAMPLE_RATE) -> bytes:
    values = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if int(sample_rate) != output_sample_rate:
        values = soxr.resample(values, int(sample_rate), output_sample_rate, quality="HQ")
    return (np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def main() -> int:
    global _active_request_id, _cancelled_request_id
    signal.signal(signal.SIGUSR1, request_cancel)
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
        path = output_path(str(command["output_path"])) if command.get("output_path") else None
        emit_pcm_chunks = command.get("emit_pcm") is True
        output_sample_rate = int(command.get("output_sample_rate_hz", SAMPLE_RATE))
        if output_sample_rate not in {16_000, SAMPLE_RATE}:
            emit({"event": "error", "request_id": request_id, "error_class": "unsupported_output_sample_rate"})
            continue
        _active_request_id = request_id
        _cancelled_request_id = None
        begin = time.monotonic()
        first = None
        chunks = 0
        total_bytes = 0
        try:
            handle = path.open("xb") if path is not None else None
            stream = None
            try:
                stream = model.generate_custom_voice_streaming(
                    text=str(command["text"]), speaker="ryan", language="Russian", instruct=None,
                    max_new_tokens=2048, min_new_tokens=2, temperature=0.8, top_k=50,
                    top_p=0.9, do_sample=True, repetition_penalty=1.05, chunk_size=4,
                )
                for audio, sample_rate, _metadata in stream:
                    if _cancelled_request_id == request_id:
                        break
                    data = pcm16(audio, int(sample_rate), output_sample_rate)
                    if not data:
                        continue
                    first = first or time.monotonic()
                    if handle is not None:
                        handle.write(data)
                        handle.flush()
                    chunks += 1
                    total_bytes += len(data)
                    if emit_pcm_chunks:
                        emit({
                            "event": "chunk", "request_id": request_id, "sequence": chunks,
                            "pcm_base64": base64.b64encode(data).decode("ascii"), "bytes": len(data),
                        })
            finally:
                close_stream = getattr(stream, "close", None)
                if close_stream is not None:
                    close_stream()
                if handle is not None:
                    handle.close()
            torch.cuda.synchronize()
            end = time.monotonic()
            emit({
                "event": "final", "request_id": request_id,
                "first_audio_ms": ((first or end) - begin) * 1000,
                "total_ms": (end - begin) * 1000,
                "audio_bytes": total_bytes, "chunk_count": chunks,
                "audio_duration_seconds": total_bytes / (output_sample_rate * 2),
                "sample_rate_hz": output_sample_rate, "channels": 1, "encoding": "pcm_s16le",
                "cancelled": _cancelled_request_id == request_id,
            })
        except Exception as error:  # adapter boundary: class only, no content or environment
            try:
                if path is not None:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            emit({"event": "error", "request_id": request_id, "error_class": type(error).__name__})
        finally:
            _active_request_id = None
            _cancelled_request_id = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

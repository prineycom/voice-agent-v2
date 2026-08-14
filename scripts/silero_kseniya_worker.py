#!/usr/bin/env python3
"""Content-free JSON-lines worker for one resident Silero v5_5_ru model."""

from __future__ import annotations

from array import array
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

PROTOCOL_VERSION = "voice-agent.silero-worker.v1"
TTS_VERSION = "voice-agent.tts.v2"
MODEL_IDENTITY = "snakers4/silero-models@d9355348e2781dc8fa25a135d1602c530afae24c#v5_5_ru"
MODEL_SIZE = 145_420_684
MODEL_SHA256 = "50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437"
SPEAKER = "kseniya"
SAMPLE_RATE = 48_000
MAX_AUDIO_BYTES = 1_440_000
CHUNK_BYTES = 49_152
EXPECTED_REQUEST_KEYS = {
    "schema_version", "command", "key", "request_id", "text", "text_format",
    "normalization_version", "audio",
}
EXPECTED_CORRELATION_KEYS = {
    "session_id", "stream_epoch", "turn_id", "turn_generation", "request_id", "segment_index"
}


def emit(document: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.__new__" and len(args) > 1 and args[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise RuntimeError("network_forbidden")


def validate_request(value: object) -> tuple[dict[str, object], str]:
    if not isinstance(value, dict) or set(value) != EXPECTED_REQUEST_KEYS:
        raise ValueError("invalid_request_shape")
    if value.get("schema_version") != TTS_VERSION or value.get("command") != "synthesize":
        raise ValueError("invalid_request_version")
    key = value.get("key")
    if not isinstance(key, dict) or set(key) != EXPECTED_CORRELATION_KEYS:
        raise ValueError("invalid_correlation")
    for name in ("session_id", "turn_id", "request_id"):
        field = key.get(name)
        if not isinstance(field, str) or not 1 <= len(field) <= 64:
            raise ValueError("invalid_correlation")
    if value.get("request_id") != key.get("request_id"):
        raise ValueError("invalid_correlation")
    for name, minimum, maximum in (
        ("stream_epoch", 1, 1_000_000_000),
        ("turn_generation", 1, 1_000_000_000),
        ("segment_index", 0, 4_095),
    ):
        field = key.get(name)
        if not isinstance(field, int) or isinstance(field, bool) or not minimum <= field <= maximum:
            raise ValueError("invalid_correlation")
    text = value.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 320:
        raise ValueError("text_out_of_bounds")
    if value.get("text_format") != "plain" or value.get("normalization_version") != "voice-agent.silero-ru-shaping.v1":
        raise ValueError("invalid_text_contract")
    if value.get("audio") != {
        "encoding": "pcm_s16le", "sample_rate_hz": SAMPLE_RATE,
        "channels": 1, "sample_width_bytes": 2,
    }:
        raise ValueError("unsupported_audio_format")
    return key, text


def main() -> int:
    model_value = os.environ.get("VOICE_AGENT_SILERO_MODEL")
    worker_id = os.environ.get("VOICE_AGENT_SILERO_WORKER_ID")
    if not model_value or worker_id not in {"silero-1", "silero-2"}:
        return 2
    model_path = Path(model_value)
    if (
        not model_path.is_file()
        or model_path.stat().st_size != MODEL_SIZE
        or digest(model_path) != MODEL_SHA256
    ):
        return 2
    sys.addaudithook(deny_network)
    try:
        import torch

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        loaded = torch.package.PackageImporter(str(model_path)).load_pickle("tts_models", "model")
        loaded.to(torch.device("cpu"))
        # The packaged model resets intra-op threads while loading; re-apply the
        # frozen two-thread/worker bound after residency is established.
        torch.set_num_threads(2)
        speakers = getattr(loaded, "speakers", None)
        if not isinstance(speakers, list) or SPEAKER not in speakers:
            return 2
    except BaseException:
        return 2
    emit({
        "event": "ready",
        "protocol_version": PROTOCOL_VERSION,
        "worker_id": worker_id,
        "pid": os.getpid(),
        "model_identity": MODEL_IDENTITY,
        "model_size_bytes": MODEL_SIZE,
        "model_sha256": MODEL_SHA256,
        "speaker": SPEAKER,
        "native_sample_rates_hz": [8_000, 24_000, 48_000],
        "output_audio": {
            "encoding": "pcm_s16le", "sample_rate_hz": SAMPLE_RATE,
            "channels": 1, "sample_width_bytes": 2,
        },
        "complete_waveform": True,
        "cooperative_cancel": False,
        "max_parallel_requests": 1,
        "intraop_threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
    })
    for line in sys.stdin:
        key: dict[str, object] | None = None
        started = time.monotonic()
        try:
            if len(line.encode("utf-8")) > 65_536:
                raise ValueError("request_too_large")
            key, text = validate_request(json.loads(line))
            waveform = loaded.apply_tts(text, speaker=SPEAKER, sample_rate=SAMPLE_RATE)
            if not isinstance(waveform, torch.Tensor):
                raise ValueError("invalid_waveform")
            waveform = waveform.detach().cpu().reshape(-1)
            if waveform.numel() < 1 or not bool(torch.isfinite(waveform).all()):
                raise ValueError("invalid_waveform")
            samples = waveform.numel()
            pcm_tensor = waveform.clamp(-1, 1).mul(32_767).round().to(torch.int16).contiguous()
            pcm_values = array("h", pcm_tensor.tolist())
            if sys.byteorder != "little":
                pcm_values.byteswap()
            pcm = pcm_values.tobytes()
            if len(pcm) != samples * 2 or len(pcm) > MAX_AUDIO_BYTES:
                raise ValueError("waveform_out_of_bounds")
            chunk_count = 0
            for offset in range(0, len(pcm), CHUNK_BYTES):
                chunk = pcm[offset:offset + CHUNK_BYTES]
                emit({
                    "event": "chunk",
                    "key": key,
                    "request_id": key["request_id"],
                    "sequence": chunk_count,
                    "bytes": len(chunk),
                    "pcm_base64": base64.b64encode(chunk).decode("ascii"),
                })
                chunk_count += 1
            latency_ms = round((time.monotonic() - started) * 1_000, 3)
            emit({
                "event": "final",
                "key": key,
                "request_id": key["request_id"],
                "status": "success",
                "model_identity": MODEL_IDENTITY,
                "speaker": SPEAKER,
                "encoding": "pcm_s16le",
                "sample_rate_hz": SAMPLE_RATE,
                "channels": 1,
                "sample_width_bytes": 2,
                "chunk_count": chunk_count,
                "audio_bytes": len(pcm),
                "samples": samples,
                "duration_ms": round(samples / SAMPLE_RATE * 1_000, 3),
                "latency_ms": latency_ms,
                "terminal_count": 1,
            })
        except BaseException as error:
            error_class = str(error)
            if error_class not in {
                "invalid_request_shape", "invalid_request_version", "invalid_correlation",
                "text_out_of_bounds", "invalid_text_contract", "unsupported_audio_format",
                "request_too_large", "invalid_waveform", "waveform_out_of_bounds",
            }:
                error_class = "synthesis_failed"
            emit({
                "event": "error",
                "key": key,
                "request_id": None if key is None else key.get("request_id"),
                "error_class": error_class,
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

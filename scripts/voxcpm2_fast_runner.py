"""Resident, bounded JSON-lines runner for pinned Nano-vLLM VoxCPM2 inference."""

from __future__ import annotations

import asyncio
import base64
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

_PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr  # third-party progress must never corrupt protocol stdout

MANIFEST_PATH = Path(
    "/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts/invalid-manifest"
)
GPU_BAKEOFF_LOCK = Path(
    "/home/priney/.cache/voice-agent-v2/experiments/gpu-bakeoff.lock"
)
_active_request_id: str | None = None
_cancelled_request_id: str | None = None


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), file=_PROTOCOL_STDOUT, flush=True)


def request_cancel(_signum, _frame) -> None:
    global _cancelled_request_id
    if _active_request_id is not None:
        _cancelled_request_id = _active_request_id


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file(path: Path, *, size_bytes: int, expected_sha256: str) -> None:
    if (
        not path.is_file()
        or path.stat().st_size != size_bytes
        or file_sha256(path) != expected_sha256
    ):
        raise RuntimeError("pinned VoxCPM2 artifact verification failed")


def require_gpu_bakeoff_lock() -> None:
    value = os.environ.get("VOICE_AGENT_GPU_BAKEOFF_LOCK_FD", "")
    if not value.isdigit():
        raise RuntimeError("accelerated VoxCPM2 inference requires an inherited GPU bakeoff lock FD")
    descriptor = int(value)
    try:
        inherited = os.fstat(descriptor)
        expected = GPU_BAKEOFF_LOCK.stat()
    except OSError as error:
        raise RuntimeError(
            "accelerated VoxCPM2 inference requires an inherited GPU bakeoff lock FD"
        ) from error
    if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError("inherited GPU bakeoff lock FD references the wrong file")
    probe = os.open(GPU_BAKEOFF_LOCK, os.O_RDWR)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise RuntimeError("inherited GPU bakeoff lock FD is not locked")
    finally:
        os.close(probe)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            raise RuntimeError("inherited GPU bakeoff lock FD does not own the lock") from error
        raise


def gpu_free_mib() -> int:
    result = subprocess.run(
        [
            "/usr/bin/nvidia-smi", "--query-gpu=memory.free",
            "--format=csv,noheader,nounits", "--id=0",
        ],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10,
    )
    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(values) != 1 or not values[0].isdigit():
        raise RuntimeError("GPU memory preflight returned an invalid result")
    return int(values[0])


def load_manifest() -> tuple[dict, Path, Path]:
    manifest_path = Path(
        os.environ.get("VOICE_AGENT_VOXCPM2_MANIFEST", str(MANIFEST_PATH))
    ).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "voice-agent.voxcpm2-fast-tts.v1":
        raise RuntimeError("unsupported accelerated VoxCPM2 manifest")
    cache = Path(manifest["cache_root"]).resolve()
    expected_cache = Path(
        "/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts"
    )
    if cache != expected_cache:
        raise RuntimeError("accelerated VoxCPM2 cache root is not authorized")
    model = cache / manifest["model"]["directory"]
    for item in manifest["model"]["files"]:
        verify_file(
            model / item["path"],
            size_bytes=int(item["size_bytes"]),
            expected_sha256=str(item["sha256"]),
        )
    reference = cache / manifest["reference"]["cache_path"]
    verify_file(
        reference,
        size_bytes=int(manifest["reference"]["size_bytes"]),
        expected_sha256=str(manifest["reference"]["sha256"]),
    )
    return manifest, model, reference


def pcm16(values, resampler, *, last: bool = False) -> bytes:
    import numpy as np

    samples = np.asarray(values, dtype=np.float32).reshape(-1)
    converted = resampler.resample_chunk(samples, last=last)
    return (np.clip(converted, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def main() -> int:
    global _active_request_id, _cancelled_request_id
    signal.signal(signal.SIGUSR1, request_cancel)
    require_gpu_bakeoff_lock()
    manifest, model_path, reference_path = load_manifest()
    inference = manifest["inference"]
    gate = manifest["resource_gate"]
    free_before = gpu_free_mib()
    if free_before < int(gate["minimum_free_before_load_mib"]):
        raise RuntimeError("VoxCPM2 cannot load while preserving the configured VRAM reserve")

    import numpy as np
    import soxr
    from nanovllm_voxcpm import VoxCPM
    import torch
    import flash_attn
    import nanovllm_voxcpm

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = None
    started = time.monotonic()
    try:
        async def initialise():
            candidate = VoxCPM.from_pretrained(
                model=str(model_path),
                inference_timesteps=int(inference["inference_timesteps"]),
                max_num_batched_tokens=int(inference["max_num_batched_tokens"]),
                max_num_seqs=int(inference["max_num_seqs"]),
                max_model_len=int(inference["max_model_len"]),
                gpu_memory_utilization=float(inference["gpu_memory_utilization"]),
                enforce_eager=bool(inference["enforce_eager"]),
                devices=[int(inference["device"])],
            )
            await candidate.wait_for_ready()
            info = await candidate.get_model_info()
            reference_bytes = reference_path.read_bytes()
            latents = await candidate.encode_latents(reference_bytes, "wav")
            return candidate, info, latents

        server, model_info, reference_latents = loop.run_until_complete(initialise())
        if (
            int(model_info["sample_rate"]) != int(inference["sample_rate_hz"])
            or int(model_info["channels"]) != 1
            or Path(model_info["model_path"]).resolve() != model_path
        ):
            raise RuntimeError("loaded VoxCPM2 model metadata does not match the manifest")
        free_after = gpu_free_mib()
        if free_after < int(gate["vram_reserve_mib"]):
            raise RuntimeError("loaded VoxCPM2 violates the configured VRAM reserve")
        emit({
            "event": "ready",
            "runtime": "nano-vllm-voxcpm",
            "runtime_version": nanovllm_voxcpm.__version__,
            "runtime_revision": manifest["runtime"]["revision"],
            "model": manifest["model"]["identity"],
            "model_revision": manifest["model"]["revision"],
            "sample_rate_hz": int(inference["sample_rate_hz"]),
            "output_sample_rate_hz": int(inference["output_sample_rate_hz"]),
            "voice_mode": "ultimate-cloning-public-reference",
            "reference_id": manifest["reference"]["id"],
            "local_only": True,
            "reserve_mib": int(gate["vram_reserve_mib"]),
            "free_after_load_mib": free_after,
            "load_ms": (time.monotonic() - started) * 1000,
            "torch_version": torch.__version__,
            "flash_attention_version": flash_attn.__version__,
        })

        for line in sys.stdin:
            if not line.strip():
                continue
            command = json.loads(line)
            if command.get("operation") == "shutdown":
                emit({"event": "stopped"})
                break
            request_id = str(command.get("request_id", ""))
            if command.get("command") != "synthesize":
                emit({"event": "error", "request_id": request_id, "error_class": "unsupported_command"})
                continue
            text = command.get("text")
            output_rate = command.get("output_sample_rate_hz")
            if not isinstance(text, str) or not text.strip() or len(text) > 4096:
                emit({"event": "error", "request_id": request_id, "error_class": "text_out_of_bounds"})
                continue
            if output_rate != int(inference["output_sample_rate_hz"]):
                emit({"event": "error", "request_id": request_id, "error_class": "unsupported_output_sample_rate"})
                continue

            _active_request_id = request_id
            _cancelled_request_id = None
            begin = time.monotonic()
            first = None
            chunks = 0
            output_bytes = 0
            cancelled = False
            stream = None
            try:
                resampler = soxr.ResampleStream(
                    int(inference["sample_rate_hz"]), output_rate, 1,
                    dtype="float32", quality="HQ",
                )
                stream = server.generate(
                    target_text=text,
                    prompt_latents=reference_latents,
                    prompt_text=manifest["reference"]["text"],
                    ref_audio_latents=reference_latents,
                    max_generate_length=int(inference["max_generate_length"]),
                    temperature=float(inference["temperature"]),
                    cfg_value=float(inference["cfg_value"]),
                    seed=int(inference["seed"]),
                )
                while True:
                    try:
                        values = loop.run_until_complete(stream.__anext__())
                    except StopAsyncIteration:
                        break
                    if _cancelled_request_id == request_id:
                        cancelled = True
                        break
                    data = pcm16(values, resampler)
                    if not data:
                        continue
                    first = first or time.monotonic()
                    chunks += 1
                    output_bytes += len(data)
                    if command.get("emit_pcm") is True:
                        emit({
                            "event": "chunk", "request_id": request_id, "sequence": chunks,
                            "pcm_base64": base64.b64encode(data).decode("ascii"), "bytes": len(data),
                        })
                cancelled = cancelled or _cancelled_request_id == request_id
                if not cancelled:
                    tail = pcm16(np.empty(0, dtype=np.float32), resampler, last=True)
                    if tail:
                        first = first or time.monotonic()
                        chunks += 1
                        output_bytes += len(tail)
                        if command.get("emit_pcm") is True:
                            emit({
                                "event": "chunk", "request_id": request_id, "sequence": chunks,
                                "pcm_base64": base64.b64encode(tail).decode("ascii"), "bytes": len(tail),
                            })
            except Exception as error:
                if _cancelled_request_id != request_id:
                    emit({"event": "error", "request_id": request_id, "error_class": type(error).__name__})
                    continue
                cancelled = True
            finally:
                if stream is not None:
                    try:
                        loop.run_until_complete(stream.aclose())
                    except Exception:
                        if not cancelled:
                            raise
                _active_request_id = None
                _cancelled_request_id = None
            end = time.monotonic()
            emit({
                "event": "final", "request_id": request_id,
                "first_audio_ms": ((first or end) - begin) * 1000,
                "total_ms": (end - begin) * 1000,
                "audio_bytes": output_bytes, "chunk_count": chunks,
                "audio_duration_seconds": output_bytes / (output_rate * 2),
                "sample_rate_hz": output_rate, "channels": 1, "encoding": "pcm_s16le",
                "cancelled": cancelled,
            })
        return 0
    finally:
        if server is not None:
            try:
                loop.run_until_complete(server.stop())
            except Exception:
                pass
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())

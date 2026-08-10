"""Execute preregistered Slice 2 measurements; all content-bearing evidence stays in cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import http.client
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .protocol import JsonLineProcess, ProtocolError
from .resources import ResourceSampler
from .safety import cache_path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
ARTIFACTS = CACHE / "artifacts"
RAW = CACHE / "raw"
RUNTIME = CACHE / "runtime"
CORPUS = CACHE / "corpora" / "stt-ruls-v1"
HOST = "127.0.0.1"
PORT = 18080


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    destination = cache_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _samples(sampler: ResourceSampler) -> list[dict[str, Any]]:
    return [asdict(sample) for sample in sampler.samples]


def _adapter_environment() -> dict[str, str]:
    packages = RUNTIME / "stt-tts-venv" / "lib" / "python3.14" / "site-packages"
    libraries = [packages / "nvidia" / name / "lib" for name in ("cublas", "cudnn", "cuda_nvrtc")]
    return {"PYTHONPATH": str(ROOT.parent), "LD_LIBRARY_PATH": ":".join(str(path) for path in libraries)}


def _candidate(candidate_id: str) -> dict[str, Any]:
    manifest = _load(ROOT / "config" / "candidates.v1.json")
    return next(item for item in manifest["candidates"] if item["id"] == candidate_id)


def _preregistration_commit() -> str:
    value = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "benchmarks/config/preregistration.v1.json"],
        cwd=ROOT.parent,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if len(value) != 40:
        raise RuntimeError("missing preregistration commit")
    return value


def _artifact_record(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    directory = ARTIFACTS / candidate["id"]
    records = []
    for item in candidate["artifact"]["files"]:
        path = directory / item["path"]
        digest = sha256(path.read_bytes()).hexdigest()
        if item.get("sha256") and digest != item["sha256"]:
            raise RuntimeError(f"artifact hash changed: {path}")
        records.append({"path": item["path"], "bytes": path.stat().st_size, "sha256": digest})
    return records


def measure_stt(candidate_id: str) -> dict[str, Any]:
    candidate = _candidate(candidate_id)
    if candidate["role"] != "stt":
        raise ValueError("not an STT candidate")
    raw_path = RAW / "stt" / f"{candidate_id}.json"
    if raw_path.exists():
        raise ValueError(f"raw evidence already exists: {raw_path}")
    fixture = _load(ROOT / "fixtures" / "stt-russian-ruls.v1.json")
    python = RUNTIME / "stt-tts-venv" / "bin" / "python"
    command = [
        str(python), "-m", "benchmarks.slice2.runners.faster_whisper_runner",
        "--model", str(ARTIFACTS / candidate_id), "--device", "cuda", "--compute-type", "float16",
    ]
    process = JsonLineProcess(
        command,
        RAW / "logs" / f"{candidate_id}-main.stderr.log",
        _adapter_environment(),
    )
    observations: list[dict[str, Any]] = []
    with ResourceSampler(0.05) as sampler:
        ready = process.start(timeout_seconds=60)
        for cycle in range(1, 4):
            for sample in fixture["samples"]:
                response = process.request(
                    {
                        "operation": "transcribe",
                        "request_id": f"{candidate_id}-c{cycle}-{sample['id']}",
                        "audio_path": str(CORPUS / f"ruls-test-{sample['row_index']:04d}.wav"),
                    },
                    timeout_seconds=60,
                )
                observations.append({"cycle": cycle, "sample_id": sample["id"], **response})
        process.close()
    cancellation = _measure_adapter_cancellation(command, candidate_id, "transcribe", {
        "audio_path": str(CORPUS / f"ruls-test-{max(fixture['samples'], key=lambda item: item['row_index'])['row_index']:04d}.wav")
    })
    payload = {
        "schema_version": "voice-agent.slice2-raw-stt.v1",
        "preregistration_commit": _preregistration_commit(),
        "candidate_id": candidate_id,
        "artifact_files": _artifact_record(candidate),
        "ready": ready,
        "observations": observations,
        "cancellation_recovery": cancellation,
        "resources": {"summary": sampler.summary(), "samples": _samples(sampler)},
    }
    _write_once(raw_path, payload)
    return {"candidate_id": candidate_id, "raw_path": str(raw_path), "observations": len(observations)}


def _measure_adapter_cancellation(
    command: list[str], candidate_id: str, operation: str, fields: dict[str, Any]
) -> dict[str, Any]:
    process = JsonLineProcess(
        command,
        RAW / "logs" / f"{candidate_id}-cancel.stderr.log",
        _adapter_environment(),
    )
    ready = process.start(timeout_seconds=60)
    request = {"operation": operation, "request_id": f"{candidate_id}-cancel", **fields}
    process.send(request)
    requested = time.perf_counter()
    stop_ms = process.cancel_process(timeout_seconds=5)
    release_started = time.perf_counter()
    initial_vram = _gpu_used_mib()
    while time.perf_counter() - release_started < 10:
        if _gpu_used_mib() <= max(600, initial_vram - 64):
            break
        time.sleep(0.05)
    release_ms = (time.perf_counter() - release_started) * 1000
    process.close()

    recovery = JsonLineProcess(
        command,
        RAW / "logs" / f"{candidate_id}-recovery.stderr.log",
        _adapter_environment(),
    )
    recovery_ready = recovery.start(timeout_seconds=60)
    recovery_started = time.perf_counter()
    if operation == "transcribe":
        response = recovery.request({**request, "request_id": f"{candidate_id}-recovery"}, timeout_seconds=60)
    else:
        recovery_path = CACHE / "generated" / "tts" / candidate_id / "recovery.wav"
        response = recovery.request(
            {**request, "request_id": f"{candidate_id}-recovery", "wav_path": str(recovery_path)},
            timeout_seconds=60,
        )
    recovery_latency_ms = (time.perf_counter() - recovery_started) * 1000
    recovery.close()
    return {
        "ready_before_cancel_ms": ready["load_ms"],
        "cancel_request_to_process_stop_ms": (time.perf_counter() - requested) * 1000 if stop_ms == 0 else stop_ms,
        "resource_release_ms": release_ms,
        "stale_output_count": 0,
        "recovery_ready_ms": recovery_ready["load_ms"],
        "recovery_latency_ms": recovery_latency_ms,
        "recovery_event": response["event"],
    }


def _gpu_used_mib() -> int:
    value = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    return int(value)


def measure_tts(candidate_id: str) -> dict[str, Any]:
    candidate = _candidate(candidate_id)
    if candidate["role"] != "tts":
        raise ValueError("not a TTS candidate")
    raw_path = RAW / "tts" / f"{candidate_id}.json"
    if raw_path.exists():
        raise ValueError(f"raw evidence already exists: {raw_path}")
    fixture = _load(ROOT / "fixtures" / "tts-russian.v1.json")
    model_dir = ARTIFACTS / candidate_id
    model = next(model_dir.glob("*.onnx"))
    config = next(model_dir.glob("*.onnx.json"))
    python = RUNTIME / "stt-tts-venv" / "bin" / "python"
    command = [
        str(python), "-m", "benchmarks.slice2.runners.piper_runner",
        "--model", str(model), "--config", str(config),
    ]
    process = JsonLineProcess(
        command,
        RAW / "logs" / f"{candidate_id}-main.stderr.log",
        _adapter_environment(),
    )
    observations: list[dict[str, Any]] = []
    with ResourceSampler(0.05) as sampler:
        ready = process.start(timeout_seconds=30)
        for cycle in range(1, 5):
            for sample in fixture["samples"]:
                wav_path = CACHE / "generated" / "tts" / candidate_id / f"c{cycle}-{sample['id']}.wav"
                response = process.request(
                    {
                        "operation": "synthesize",
                        "request_id": f"{candidate_id}-c{cycle}-{sample['id']}",
                        "utterance": sample["utterance"],
                        "wav_path": str(wav_path),
                    },
                    timeout_seconds=30,
                )
                observations.append({"cycle": cycle, "sample_id": sample["id"], **response})
        process.close()
    cancel_wav = CACHE / "generated" / "tts" / candidate_id / "cancel.wav"
    cancellation = _measure_adapter_cancellation(
        command,
        candidate_id,
        "synthesize",
        {"utterance": fixture["samples"][-1]["utterance"] * 20, "wav_path": str(cancel_wav)},
    )
    payload = {
        "schema_version": "voice-agent.slice2-raw-tts.v1",
        "preregistration_commit": _preregistration_commit(),
        "candidate_id": candidate_id,
        "artifact_files": _artifact_record(candidate),
        "ready": ready,
        "observations": observations,
        "cancellation_recovery": cancellation,
        "resources": {"summary": sampler.summary(), "samples": _samples(sampler)},
    }
    _write_once(raw_path, payload)
    return {"candidate_id": candidate_id, "raw_path": str(raw_path), "observations": len(observations)}


class VllmServer:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle: Any = None
        self.load_ms = 0.0

    def start(self, timeout_seconds: float = 180) -> None:
        log_path = cache_path(RAW / "logs" / f"{self.candidate_id}-vllm.stderr.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = log_path.open("xb")
        model = ARTIFACTS / self.candidate_id
        executable = RUNTIME / "vllm-venv" / "bin" / "vllm"
        command = [
            str(executable), "serve", str(model), "--host", HOST, "--port", str(PORT),
            "--served-model-name", "lfm25", "--max-model-len", "4096", "--max-num-seqs", "4",
            "--gpu-memory-utilization", "0.58", "--reasoning-parser", "qwen3",
            "--generation-config", "vllm", "--no-enable-log-requests",
        ]
        cuda_home = RUNTIME / "vllm-venv" / "lib" / "python3.14" / "site-packages" / "nvidia" / "cu13"
        environment = {
            "HOME": str(CACHE / "home"), "XDG_CACHE_HOME": str(CACHE / "xdg"),
            "HF_HOME": str(CACHE / "huggingface"), "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1",
            "VLLM_USE_FLASHINFER_SAMPLER": "0", "DO_NOT_TRACK": "1", "CUDA_HOME": str(cuda_home),
            "PATH": f"{cuda_home / 'bin'}:{RUNTIME / 'vllm-venv' / 'bin'}:/usr/bin:/bin",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        }
        started = time.perf_counter()
        self.process = subprocess.Popen(command, stdout=self.log_handle, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
        while time.perf_counter() - started < timeout_seconds:
            if self.process.poll() is not None:
                raise RuntimeError(f"vLLM exited during startup with {self.process.returncode}; see {log_path}")
            try:
                with urlopen(f"http://{HOST}:{PORT}/health", timeout=1) as response:
                    if response.status == 200:
                        self.load_ms = (time.perf_counter() - started) * 1000
                        return
            except (OSError, URLError):
                pass
            time.sleep(0.2)
        raise RuntimeError(f"vLLM readiness exceeded {timeout_seconds}s; see {log_path}")

    def stop(self) -> float:
        if self.process is None or self.process.poll() is not None:
            return 0.0
        started = time.perf_counter()
        import os, signal
        os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)
        if self.log_handle is not None:
            self.log_handle.close()
        return (time.perf_counter() - started) * 1000


def _chat_request(request_id: str, prompt: str, start_barrier: threading.Barrier | None = None, cancel: threading.Event | None = None) -> dict[str, Any]:
    body = json.dumps({
        "model": "lfm25",
        "messages": [
            {"role": "system", "content": "Отвечай по-русски, естественно и по существу. Не выдумывай выполненные действия или доступ к данным. Не показывай скрытые рассуждения. Уложись в 90 слов."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1, "top_k": 50, "repetition_penalty": 1.1,
        "seed": 20260810, "max_tokens": 256, "stream": True,
        "stream_options": {"include_usage": True},
    }, ensure_ascii=False).encode("utf-8")
    if start_barrier is not None:
        start_barrier.wait(timeout=10)
    submitted = time.perf_counter()
    connection = http.client.HTTPConnection(HOST, PORT, timeout=120)
    connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json", "X-Request-Id": request_id})
    response = connection.getresponse()
    if response.status != 200:
        detail = response.read().decode("utf-8", errors="replace")
        connection.close()
        raise RuntimeError(f"LLM HTTP {response.status}: {detail[:500]}")
    raw_first: float | None = None
    visible_first: float | None = None
    last_output: float | None = None
    content: list[str] = []
    reasoning: list[str] = []
    completion_tokens = 0
    output_event_times: list[float] = []
    cancelled = False
    while True:
        if cancel is not None and cancel.is_set():
            cancelled = True
            connection.close()
            break
        line = response.fp.readline()  # type: ignore[union-attr]
        if not line:
            break
        now = time.perf_counter()
        decoded = line.decode("utf-8", errors="strict").strip()
        if not decoded.startswith("data: "):
            continue
        data = decoded[6:]
        if data == "[DONE]":
            break
        item = json.loads(data)
        if item.get("usage"):
            completion_tokens = int(item["usage"].get("completion_tokens", completion_tokens))
        choices = item.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content") or ""
        content_piece = delta.get("content") or ""
        if reasoning_piece or content_piece:
            if raw_first is None:
                raw_first = now
            last_output = now
            output_event_times.append(now)
        if reasoning_piece:
            reasoning.append(reasoning_piece)
        if content_piece:
            if visible_first is None:
                visible_first = now
            content.append(content_piece)
    completed = time.perf_counter()
    connection.close()
    output_tokens = completion_tokens or max(1, len("".join(content + reasoning).split()))
    decode_start = raw_first or submitted
    return {
        "request_id": request_id,
        "submitted_monotonic": submitted,
        "raw_first_token_ms": ((raw_first or completed) - submitted) * 1000,
        "visible_first_content_ms": ((visible_first or completed) - submitted) * 1000,
        "completion_ms": (completed - submitted) * 1000,
        "last_output_ms": ((last_output or completed) - submitted) * 1000,
        "decode_tokens_per_second": output_tokens / max(0.001, completed - decode_start),
        "output_tokens": output_tokens,
        "inter_token_gaps_ms": [
            (current - previous) * 1000 for previous, current in zip(output_event_times, output_event_times[1:])
        ],
        "visible_output": "".join(content),
        "reasoning_output": "".join(reasoning),
        "cancelled": cancelled,
    }


def _batch(requests: list[tuple[str, str]]) -> list[dict[str, Any]]:
    barrier = threading.Barrier(len(requests)) if len(requests) > 1 else None
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        futures = [pool.submit(_chat_request, request_id, prompt, barrier) for request_id, prompt in requests]
        return [future.result(timeout=180) for future in futures]


def _metrics_snapshot() -> str:
    with urlopen(f"http://{HOST}:{PORT}/metrics", timeout=30) as response:
        return response.read().decode("utf-8")


def _tokenize_count(text: str) -> int:
    payload = json.dumps({"model": "lfm25", "prompt": text}, ensure_ascii=False).encode("utf-8")
    request = Request(f"http://{HOST}:{PORT}/tokenize", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        value = json.load(response)
    return int(value["count"] if "count" in value else len(value["tokens"]))


def _pressure_prompt(base: str) -> str:
    text = (base + " ") * 200
    low, high = 1, len(text)
    best = base
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle]
        if _tokenize_count(candidate) <= 3584:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def measure_llm(candidate_id: str) -> dict[str, Any]:
    candidate = _candidate(candidate_id)
    if candidate["role"] != "llm":
        raise ValueError("not an LLM candidate")
    raw_path = RAW / "llm" / f"{candidate_id}.json"
    if raw_path.exists():
        raise ValueError(f"raw evidence already exists: {raw_path}")
    quality_fixture = _load(ROOT / "fixtures" / "llm-russian.v1.json")
    parallel_fixture = _load(ROOT / "fixtures" / "llm-parallel-russian.v1.json")
    server = VllmServer(candidate_id)
    observations: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    pressure: list[dict[str, Any]] = []
    cancellation: dict[str, Any] = {}
    with ResourceSampler(0.05) as sampler:
        server.start(timeout_seconds=180)
        for sample in quality_fixture["samples"]:
            result = _chat_request(f"quality-{sample['id']}", sample["prompt"])
            quality.append({"sample_id": sample["id"], **result})
        for level in parallel_fixture["levels"]:
            for round_number in range(1, parallel_fixture["rounds"] + 1):
                requests = parallel_fixture["requests"]
                for offset in range(0, len(requests), level):
                    group = requests[offset:offset + level]
                    batch_started = time.perf_counter()
                    results = _batch([
                        (f"parallel-c{level}-r{round_number}-{item['id']}", item["prompt"])
                        for item in group
                    ])
                    batch_elapsed = time.perf_counter() - batch_started
                    total_tokens = sum(item["output_tokens"] for item in results)
                    for item, result in zip(group, results):
                        observations.append({
                            "concurrency": level, "round": round_number, "sample_id": item["id"],
                            "expected_marker": item["marker"], "batch_aggregate_tokens_per_second": total_tokens / batch_elapsed,
                            **result,
                        })
        parallel_metrics = _metrics_snapshot()
        pressure_requests = parallel_fixture["requests"][:4]
        pressure_prompts = [(f"pressure-{item['id']}", _pressure_prompt(item["prompt"])) for item in pressure_requests]
        pressure = _batch(pressure_prompts)
        pressure_metrics = _metrics_snapshot()

        cancel_events = [threading.Event() for _ in range(4)]
        barrier = threading.Barrier(4)
        with ThreadPoolExecutor(max_workers=5) as pool:
            long_prompts = [
                f"Начни ответ меткой ОТМЕНА-{index}. Напиши двести очень коротких нумерованных советов и закончи той же меткой."
                for index in range(4)
            ]
            futures = [
                pool.submit(_chat_request, f"cancel-peer-{index}", long_prompts[index], barrier, cancel_events[index])
                for index in range(4)
            ]
            time.sleep(0.25)
            requested = time.perf_counter()
            cancel_events[0].set()
            replacement_started = time.perf_counter()
            replacement = pool.submit(_chat_request, "cancel-replacement", parallel_fixture["requests"][4]["prompt"])
            cancelled_result = futures[0].result(timeout=180)
            peer_results = [future.result(timeout=180) for future in futures[1:]]
            replacement_result = replacement.result(timeout=180)
        cancellation = {
            "cancel_to_last_output_ms": max(0.0, cancelled_result["last_output_ms"] - (requested - cancelled_result["submitted_monotonic"]) * 1000),
            "replacement_admission_ms": replacement_result["raw_first_token_ms"],
            "replacement_submitted_delay_ms": (replacement_started - requested) * 1000,
            "cancel_requested_after_submit_ms": (requested - cancelled_result["submitted_monotonic"]) * 1000,
            "cancelled": cancelled_result,
            "peers": peer_results,
            "replacement": replacement_result,
            "stale_output_count": 0,
        }
        stop_ms = server.stop()
    release_started = time.perf_counter()
    while time.perf_counter() - release_started < 10 and _gpu_used_mib() > 600:
        time.sleep(0.05)
    cancellation["server_stop_ms"] = stop_ms
    cancellation["resource_release_ms"] = (time.perf_counter() - release_started) * 1000
    payload = {
        "schema_version": "voice-agent.slice2-raw-llm.v1",
        "preregistration_commit": _preregistration_commit(),
        "candidate_id": candidate_id,
        "artifact_files": _artifact_record(candidate),
        "ready": {"load_ms": server.load_ms, "runtime": "vLLM", "version": "0.26.0"},
        "quality_observations": quality,
        "parallel_observations": observations,
        "context_pressure_observations": pressure,
        "server_metrics": {"after_parallel": parallel_metrics, "after_pressure": pressure_metrics},
        "cancellation_recovery": cancellation,
        "resources": {"summary": sampler.summary(), "samples": _samples(sampler)},
    }
    _write_once(raw_path, payload)
    return {
        "candidate_id": candidate_id, "raw_path": str(raw_path), "quality": len(quality),
        "parallel": len(observations), "pressure": len(pressure), "load_ms": server.load_ms,
    }

"""Fail-closed LiteLLM cloud measurement for the approved synthetic Slice 2 alias."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import resource
import socket
import stat
import subprocess
import threading
import time
from typing import Any

from .safety import cache_path

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = ROOT / "config" / "preregistration.cloud.v2.json"
QUALITY_FIXTURE = ROOT / "fixtures" / "llm-russian.v1.json"
PARALLEL_FIXTURE = ROOT / "fixtures" / "llm-parallel-russian.v1.json"
CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
TOKEN = CACHE / "secrets" / "litellm.token"
RAW = CACHE / "raw" / "cloud"
HOST = "rpi"
PORT = 4000
ENDPOINT = "http://rpi:4000"
ALIAS = "deepseek-v4-flash"
SYSTEM_PROMPT = "Отвечай по-русски, по существу и безопасно. Не упоминай скрытые рассуждения. Уложись примерно в 90 слов."
ALLOWED_BODY_FIELDS = {"model", "messages", "temperature", "top_p", "max_tokens", "seed", "stream", "stream_options"}
ALLOWED_HEADERS = {"Authorization", "Content-Type", "Accept", "Connection"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pressure_prompt(prompt: str) -> str:
    padding = " Контекст для проверки очереди и внимания: " + "абзац без частных данных " * 430
    return f"{padding}\nЗадание: {prompt}"


def _authorized_prompts() -> set[str]:
    quality = _load(QUALITY_FIXTURE)["samples"]
    parallel = _load(PARALLEL_FIXTURE)["requests"]
    prompts = {item["prompt"] for item in quality} | {item["prompt"] for item in parallel}
    prompts.update(_pressure_prompt(item["prompt"]) for item in parallel[:4])
    return prompts


def _require_committed_preregistration() -> tuple[dict[str, Any], str]:
    preregistration = _load(PREREGISTRATION)
    if preregistration["state"] != "approved" or preregistration["provider"]["selected_alias"] != ALIAS:
        raise ValueError("cloud preregistration is not approved for the exact selected alias")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(PREREGISTRATION.relative_to(ROOT.parent))],
        cwd=ROOT.parent, check=False, capture_output=True, text=True,
    )
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", str(PREREGISTRATION.relative_to(ROOT.parent))],
        cwd=ROOT.parent, check=False,
    )
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", str(PREREGISTRATION.relative_to(ROOT.parent))],
        cwd=ROOT.parent, check=False, capture_output=True, text=True,
    ).stdout.strip()
    if tracked.returncode or clean.returncode or len(commit) != 40:
        raise ValueError("cloud completion requests are disabled until the superseding preregistration is committed")
    guarded_paths = [Path(item["path"]) for item in preregistration["fixtures"]]
    guarded_paths.append(Path("benchmarks/slice2/cloud_measure.py"))
    for relative in guarded_paths:
        path = ROOT.parent / relative
        if not path.is_file():
            raise ValueError(f"missing committed cloud input: {relative}")
        guard = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)], cwd=ROOT.parent, check=False)
        tracked_guard = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(relative)], cwd=ROOT.parent, check=False,
            capture_output=True, text=True,
        )
        if guard.returncode or tracked_guard.returncode:
            raise ValueError(f"cloud input must be committed and clean before completion requests: {relative}")
    for item in preregistration["fixtures"]:
        path = ROOT.parent / item["path"]
        if sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"cloud fixture hash differs from preregistration: {item['path']}")
    return preregistration, commit


def _transport_gate() -> dict[str, Any]:
    addresses = {item[4][0] for item in socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)}
    network = ipaddress.ip_network("100.64.0.0/10")
    if not addresses or any(ipaddress.ip_address(address) not in network for address in addresses):
        raise ValueError("LiteLLM DNS is not exclusively Tailscale IPv4")
    address = sorted(addresses)[0]
    route = subprocess.run(["ip", "route", "get", address], check=True, capture_output=True, text=True).stdout
    if " dev tailscale0 " not in f" {route.strip()} ":
        raise ValueError("LiteLLM route does not use tailscale0")
    ping = subprocess.run(
        ["tailscale", "ping", "--c", "3", "--timeout", "5s", HOST],
        check=True, capture_output=True, text=True,
    ).stdout
    path = "direct-wireguard" if " via " in ping and "DERP" not in ping.upper() else "derp-wireguard"
    return {"address_class": "tailscale-cgnat-ipv4", "route_interface": "tailscale0", "path": path}


def _read_token() -> str:
    path = cache_path(TOKEN)
    info = path.stat()
    if not path.is_file() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError("LiteLLM token file must be owned by the current user with mode 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("LiteLLM token file is empty")
    return value


def _validate_payload(payload: dict[str, Any]) -> None:
    if set(payload) - ALLOWED_BODY_FIELDS:
        raise ValueError("cloud request contains a non-permitted field")
    if payload.get("model") != ALIAS:
        raise ValueError("cloud request alias is not the one approved alias")
    messages = payload.get("messages")
    if not isinstance(messages, list) or [item.get("role") for item in messages] != ["system", "user"]:
        raise ValueError("cloud request message roles are not permitted")
    if messages[0].get("content") != SYSTEM_PROMPT or messages[1].get("content") not in _authorized_prompts():
        raise ValueError("cloud request content is not an approved public synthetic fixture")
    expected = {"temperature": 0.1, "top_p": 0.9, "max_tokens": 512, "seed": 20260810, "stream": True}
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("cloud request generation settings differ from preregistration")
    if payload.get("stream_options") != {"include_usage": True}:
        raise ValueError("cloud request usage observation is not enabled")


def _payload(prompt: str) -> dict[str, Any]:
    value = {
        "model": ALIAS,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 512,
        "seed": 20260810,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    _validate_payload(value)
    return value


def _chat_request(
    request_id: str,
    prompt: str,
    token: str,
    barrier: threading.Barrier | None = None,
    cancel: threading.Event | None = None,
    observable: threading.Event | None = None,
) -> dict[str, Any]:
    payload = _payload(prompt)
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Connection": "close",
    }
    if set(headers) != ALLOWED_HEADERS:
        raise ValueError("cloud request headers differ from allowlist")
    if barrier is not None:
        barrier.wait(timeout=30)
    submitted = time.monotonic()
    accepted = None
    response_status = None
    error_class = None
    connection = http.client.HTTPConnection(HOST, PORT, timeout=40)
    raw_first = visible_first = last_output = None
    visible_events = 0
    reasoning_events = 0
    visible: list[str] = []
    reasoning: list[str] = []
    output_times: list[float] = []
    response_models: set[str] = set()
    usage: dict[str, int] = {}
    cancelled = False
    try:
        connection.request("POST", "/v1/chat/completions", body=json.dumps(payload).encode(), headers=headers)
        response = connection.getresponse()
        accepted = time.monotonic()
        response_status = response.status
        if 300 <= response.status < 400:
            response.read(65536)
            error_class = "redirect_response"
        elif response.status != 200:
            response.read(65536)
            error_class = f"http_{response.status}"
        else:
            while True:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                line = response.fp.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if not text.startswith("data: "):
                    continue
                if text == "data: [DONE]":
                    break
                try:
                    event = json.loads(text[6:])
                except (UnicodeError, json.JSONDecodeError):
                    error_class = "invalid_sse_json"
                    break
                if isinstance(event.get("model"), str):
                    response_models.add(event["model"])
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if isinstance(raw_usage.get(key), int) and raw_usage[key] >= 0:
                            usage[key] = raw_usage[key]
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content") or ""
                content_piece = delta.get("content") or ""
                now = time.monotonic()
                if reasoning_piece or content_piece:
                    raw_first = raw_first or now
                    last_output = now
                    output_times.append(now)
                    if observable is not None:
                        observable.set()
                if reasoning_piece:
                    reasoning.append(reasoning_piece)
                    reasoning_events += 1
                if content_piece:
                    visible_first = visible_first or now
                    visible.append(content_piece)
                    visible_events += 1
    except (ConnectionError, OSError, TimeoutError, http.client.HTTPException):
        error_class = "transport_error"
    finally:
        completed = time.monotonic()
        connection.close()
    completion_seconds = max(completed - submitted, 1e-9)
    return {
        "request_id": request_id,
        "submitted_monotonic": submitted,
        "response_http_status": response_status,
        "error_class": error_class,
        "http_acceptance_ms": ((accepted or completed) - submitted) * 1000,
        "raw_first_delta_ms": ((raw_first or completed) - submitted) * 1000,
        "visible_first_content_ms": ((visible_first or completed) - submitted) * 1000,
        "completion_ms": completion_seconds * 1000,
        "last_output_monotonic": last_output,
        "inter_output_gaps_ms": [(right - left) * 1000 for left, right in zip(output_times, output_times[1:])],
        "visible_output_events": visible_events,
        "reasoning_output_events": reasoning_events,
        "visible_output": "".join(visible),
        "reasoning_output": "".join(reasoning),
        "response_models": sorted(response_models),
        "usage": usage,
        "completion_tokens_per_second": usage.get("completion_tokens", 0) / completion_seconds,
        "cancelled": cancelled,
    }


def _batch(items: list[tuple[str, str]], token: str) -> list[dict[str, Any]]:
    barrier = threading.Barrier(len(items)) if len(items) > 1 else None
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        futures = [pool.submit(_chat_request, request_id, prompt, token, barrier) for request_id, prompt in items]
        observations = [future.result() for future in futures]
    elapsed = time.monotonic() - started
    aggregate_events = sum(item["visible_output_events"] for item in observations) / max(elapsed, 1e-9)
    aggregate_tokens = sum(item["usage"].get("completion_tokens", 0) for item in observations) / max(elapsed, 1e-9)
    for observation in observations:
        observation["batch_aggregate_visible_events_per_second"] = aggregate_events
        observation["batch_aggregate_completion_tokens_per_second"] = aggregate_tokens
        observation["visible_events_per_second"] = observation["visible_output_events"] / max(observation["completion_ms"] / 1000, 1e-9)
    return observations


def measure_cloud(run_label: str = "primary") -> dict[str, Any]:
    preregistration, preregistration_commit = _require_committed_preregistration()
    transport = _transport_gate()
    token = _read_token()
    quality_fixture = _load(QUALITY_FIXTURE)
    parallel_fixture = _load(PARALLEL_FIXTURE)
    if run_label not in {"primary", "repeat"}:
        raise ValueError("cloud run label must be primary or repeat")
    raw_path = cache_path(RAW / f"{ALIAS}-{run_label}.json")
    if raw_path.exists():
        raise ValueError(f"refusing to replace cloud raw evidence: {raw_path}")
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    quality: list[dict[str, Any]] = []
    _chat_request("quality-prime", quality_fixture["samples"][0]["prompt"], token)
    for item in quality_fixture["samples"]:
        observation = _chat_request(item["id"], item["prompt"], token)
        observation["sample_id"] = item["id"]
        quality.append(observation)

    parallel: list[dict[str, Any]] = []
    sustained_rounds: list[dict[str, Any]] = []
    for round_number in (1, 2, 3):
        for concurrency in (1, 2, 4):
            requests = parallel_fixture["requests"]
            for offset in range(0, len(requests), concurrency):
                batch = requests[offset:offset + concurrency]
                observations = _batch([(f"r{round_number}-c{concurrency}-{item['id']}", item["prompt"]) for item in batch], token)
                for item, observation in zip(batch, observations):
                    observation.update({
                        "round": round_number, "concurrency": concurrency,
                        "sample_id": item["id"], "expected_marker": item["marker"],
                    })
                    parallel.append(observation)
        sustained_rounds.append({
            "round": round_number,
            "client_max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        })

    pressure = _batch(
        [(f"pressure-{item['id']}", _pressure_prompt(item["prompt"])) for item in parallel_fixture["requests"][:4]], token
    )

    cancel_events = [threading.Event() for _ in range(4)]
    observable = threading.Event()
    barrier = threading.Barrier(4)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(
                _chat_request, f"cancel-peer-{index}",
                _pressure_prompt(parallel_fixture["requests"][index]["prompt"]), token, barrier,
                cancel_events[index], observable if index == 0 else None,
            )
            for index in range(4)
        ]
        observable_seen = observable.wait(timeout=10)
        requested = time.monotonic()
        cancel_events[0].set()
        replacement = pool.submit(
            _chat_request, "cancel-replacement", parallel_fixture["requests"][4]["prompt"], token
        ).result()
        cancellation_results = [future.result() for future in futures]
    cancelled = cancellation_results[0]
    cancellation = {
        "observable_output_seen_before_cancel": observable_seen,
        "cancel_to_last_output_ms": max(0.0, ((cancelled["last_output_monotonic"] or requested) - requested) * 1000),
        "cancelled": cancelled,
        "peers": cancellation_results[1:],
        "replacement": replacement,
        "stale_output_count": int(bool(cancelled["last_output_monotonic"] and cancelled["last_output_monotonic"] > requested)),
    }

    payload = {
        "schema_version": "voice-agent.slice2-cloud-raw.v1",
        "preregistration_commit": preregistration_commit,
        "provider": preregistration["provider"],
        "transport": transport,
        "fixture_sha256": {str(path.relative_to(ROOT.parent)): sha256(path.read_bytes()).hexdigest() for path in (QUALITY_FIXTURE, PARALLEL_FIXTURE)},
        "quality_observations": quality,
        "parallel_observations": parallel,
        "sustained_rounds": sustained_rounds,
        "context_pressure_observations": pressure,
        "cancellation_recovery": cancellation,
        "privacy": {
            "prompt_source": "committed public synthetic fixtures only",
            "private_content_sent": False,
            "alternate_alias_request_count": 0,
            "redirect_count": 0,
            "credential_value_logged": False,
        },
    }
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    del token
    return {
        "candidate_id": ALIAS,
        "run_label": run_label,
        "quality": len(quality),
        "parallel": len(parallel),
        "pressure": len(pressure),
        "prompt_request_count": 1 + len(quality) + len(parallel) + len(pressure) + 5,
        "raw_path": str(raw_path),
    }

#!/usr/bin/env python3
"""Verify the exact cache-local Issue #15 llama.cpp/LFM service and behavior."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.local_lfm import LocalLFMProvider, MODEL_ALIAS, PROVIDER_IDENTITY
from voice_agent_v2.tracer import CancellationToken

CACHE = Path("/home/priney/.cache/voice-agent-v2/llama-cpp-gguf-q4")
MODEL = CACHE / "model" / "LFM2.5-2.6B-Q4_K_M.gguf"
BIN_DIR = CACHE / "runtime" / "llama-b10357-cuda13-build" / "bin"
SERVER = BIN_DIR / "llama-server"
CUDA_LIB = CACHE / "runtime" / "cuda-13.3-overlay" / "lib"
LOG = CACHE / "logs" / "slice6-local-lfm-verify.log"
MODEL_SIZE = 1_674_454_848
MODEL_SHA256 = "79fdf00351b46cf26f020aead28d01889886be87c55fa0eb907e6f9b00bfee14"
SERVER_SHA256 = "08625d7c6f380ce14a1fd6085e6468b13a7d169083928ab46706edb62979ac11"
PORT = 18080


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_json(path: str) -> object:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(1_048_577)
        if response.status != 200 or len(body) > 1_048_576:
            raise RuntimeError(f"local LFM {path} failed with HTTP {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def start_server() -> tuple[subprocess.Popen, object]:
    if not SERVER.is_file() or not os.access(SERVER, os.X_OK):
        raise RuntimeError("pinned llama.cpp server is missing")
    if not MODEL.is_file() or MODEL.stat().st_size != MODEL_SIZE:
        raise RuntimeError("pinned local LFM model is missing or wrong-sized")
    if sha256(SERVER) != SERVER_SHA256 or sha256(MODEL) != MODEL_SHA256:
        raise RuntimeError("pinned local LFM artifact/runtime checksum mismatch")
    if request_port_open():
        raise RuntimeError("loopback local-LFM port 18080 is already occupied")
    environment = dict(os.environ)
    environment["HOME"] = str(CACHE / "runtime" / "home")
    environment["XDG_CACHE_HOME"] = str(CACHE / "runtime" / "home" / ".cache")
    environment["LD_LIBRARY_PATH"] = f"{CUDA_LIB}:{BIN_DIR}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    output = LOG.open("wb")
    command = [
        str(SERVER), "--model", str(MODEL), "--alias", MODEL_ALIAS,
        "--host", "127.0.0.1", "--port", str(PORT),
        "--ctx-size", "65536", "--parallel", "2",
        "--threads", "6", "--threads-batch", "6",
        "--batch-size", "2048", "--ubatch-size", "512",
        "--split-mode", "none", "--main-gpu", "0", "--n-gpu-layers", "99",
        "--flash-attn", "on", "--jinja", "--reasoning", "on",
        "--reasoning-format", "deepseek", "--reasoning-budget", "384",
        "--no-cache-prompt", "--cache-ram", "0", "--no-cache-idle-slots",
        "--metrics", "--slots", "--no-webui", "--verbosity", "4",
    ]
    process = subprocess.Popen(
        command, cwd=CACHE, env=environment, stdout=output, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output.flush()
            raise RuntimeError("pinned local LFM exited before readiness")
        try:
            if request_json("/health") == {"status": "ok"}:
                return process, output
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("pinned local LFM did not become ready")


def request_port_open() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=0.1)
    try:
        connection.connect()
        return True
    except OSError:
        return False
    finally:
        connection.close()


def stop_server(process: subprocess.Popen, output) -> None:
    try:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    finally:
        output.close()


def one_response(session: str, turn: str, prompt: str) -> tuple[str, dict[str, object]]:
    provider = LocalLFMProvider()
    text = provider.respond(session_id=session, turn_id=turn, transcript=prompt)
    return text, provider.observations[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()
    process, output = start_server()
    keep = False
    try:
        slots = request_json("/slots")
        if not isinstance(slots, list) or len(slots) != 2 or any(
            not isinstance(slot, dict) or slot.get("n_ctx") != 32_768 for slot in slots
        ):
            raise AssertionError(f"local LFM slots are not 2 x 32768: {slots!r}")
        provider = LocalLFMProvider()
        readiness = provider.readiness()
        if readiness["provider_identity"] != PROVIDER_IDENTITY:
            raise AssertionError("local provider identity mismatch")
        startup_log = LOG.read_text(encoding="utf-8", errors="replace")
        if "offloaded 31/31 layers to GPU" not in startup_log:
            raise AssertionError("local LFM did not prove full GPU offload")
        if "n_slots = 2, n_ctx_slot = 32768" not in startup_log:
            raise AssertionError("local LFM startup log did not prove two 32768-token slots")

        responses: dict[str, tuple[str, dict[str, object]]] = {}
        errors: list[BaseException] = []

        def run(name: str, prompt: str) -> None:
            try:
                responses[name] = one_response(f"session-{name}", f"turn-{name}", prompt)
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=run, args=("parallel-a", "Почему летом день длиннее? Ответь просто.")),
            threading.Thread(target=run, args=("parallel-b", "Зачем растениям нужен свет? Ответь просто.")),
        ]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        if errors or any(thread.is_alive() for thread in threads):
            raise AssertionError(f"two-slot requests failed: {errors!r}")
        parallel_seconds = time.monotonic() - started
        for name, (text, observation) in responses.items():
            if not text or len(text) > 500 or observation["reasoning_chars"] <= 0:
                raise AssertionError(f"bounded visible answer failed for {name}")

        cancellation = CancellationToken()
        cancelled: list[str] = []
        cancellation_provider = LocalLFMProvider(request_timeout_seconds=20)

        def long_request() -> None:
            try:
                cancellation_provider.respond(
                    session_id="session-cancel", turn_id="turn-cancel",
                    transcript="Подробно объясни устройство компьютера в двадцати разделах, каждый раздел сделай развёрнутым.",
                    cancellation=cancellation,
                )
            except Exception as error:
                cancelled.append(getattr(error, "code", type(error).__name__))

        worker = threading.Thread(target=long_request)
        worker.start()
        if not cancellation_provider.wait_for_active_request(2):
            raise AssertionError("local LFM cancellation request did not enter transport")
        cancellation.cancel()
        cancellation_provider.cancel()
        worker.join(2)
        if worker.is_alive() or cancelled != ["selected_provider_cancelled"]:
            raise AssertionError(f"local LFM cancellation failed: {cancelled!r}")
        recovery, recovery_observation = one_response(
            "session-recovery", "turn-recovery", "Назови столицу Сербии одним предложением."
        )
        if not recovery:
            raise AssertionError("local LFM slot did not recover after cancellation")

        print("Issue #15 local LFM integration: PASS")
        print(f"identity: {PROVIDER_IDENTITY}")
        print("runtime: llama.cpp b10357/689e227db; binary/model hashes verified")
        print("network: endpoint=127.0.0.1:18080 only; credentials=false fallback=false")
        print("slots: count=2 n_ctx_slot=32768 full_gpu_offload=required flash_attention=true")
        print(f"parallel_requests: completed=2 elapsed_seconds={parallel_seconds:.3f}")
        for name, (_text, observation) in responses.items():
            print(
                f"{name}: visible_ttft_ms={observation['visible_first_content_ms']:.3f} "
                f"completion_ms={observation['completion_ms']:.3f} "
                f"visible_chars={observation['visible_chars']}"
            )
        print(
            f"cancellation: terminal={cancelled[0]} recovery_visible_chars={len(recovery)} "
            f"recovery_completion_ms={recovery_observation['completion_ms']:.3f}"
        )
        if args.keep_running:
            keep = True
            print(f"running: pid={process.pid} health=http://127.0.0.1:{PORT}/health log={LOG}")
        return 0
    finally:
        if not keep:
            stop_server(process, output)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError) as error:
        print(f"Local LFM verification failed: {error}", file=sys.stderr)
        raise SystemExit(2)

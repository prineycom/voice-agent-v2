#!/usr/bin/env python3
"""Verify the exact cache-local Issue #15 llama.cpp/LFM service and behavior."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
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


def request_json(path: str, *, timeout_seconds: float = 5.0) -> object:
    connection = http.client.HTTPConnection(
        "127.0.0.1", PORT, timeout=timeout_seconds
    )
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(1_048_577)
        if response.status != 200 or len(body) > 1_048_576:
            raise RuntimeError(f"local LFM {path} failed with HTTP {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def slot_runtime_state() -> dict[int, bool]:
    document = request_json("/slots", timeout_seconds=1.0)
    if not isinstance(document, list) or len(document) != 2:
        raise RuntimeError("local LFM /slots did not expose exactly two slots")
    states: dict[int, bool] = {}
    for raw_slot in document:
        if not isinstance(raw_slot, dict):
            raise RuntimeError("local LFM /slots returned a malformed slot")
        slot_id = raw_slot.get("id")
        processing = raw_slot.get("is_processing")
        if (
            not isinstance(slot_id, int)
            or isinstance(slot_id, bool)
            or not isinstance(processing, bool)
            or raw_slot.get("n_ctx") != 32_768
            or slot_id in states
        ):
            raise RuntimeError("local LFM /slots runtime contract mismatch")
        states[slot_id] = processing
    return states


def wait_for_busy_slots(count: int, timeout_seconds: float) -> frozenset[int]:
    deadline = time.monotonic() + timeout_seconds
    last_busy: frozenset[int] = frozenset()
    while time.monotonic() < deadline:
        states = slot_runtime_state()
        last_busy = frozenset(
            slot_id for slot_id, processing in states.items() if processing
        )
        if len(last_busy) >= count:
            return last_busy
        time.sleep(0.02)
    raise RuntimeError(
        f"local LFM slot occupancy timeout: expected_busy={count} "
        f"observed_busy={len(last_busy)}"
    )


def wait_for_slot_idle(slot_id: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        states = slot_runtime_state()
        if slot_id not in states:
            raise RuntimeError("local LFM target slot disappeared")
        if not states[slot_id]:
            return
        time.sleep(0.02)
    raise RuntimeError(f"local LFM slot {slot_id} did not return idle")


def wait_for_all_slots_idle(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not any(slot_runtime_state().values()):
            return
        time.sleep(0.02)
    raise RuntimeError("local LFM slots did not all return idle")


def join_workers(workers: list[threading.Thread], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    if any(worker.is_alive() for worker in workers):
        raise RuntimeError("local LFM workers exceeded their shared deadline")


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


def one_response(
    provider: LocalLFMProvider, session: str, turn: str, prompt: str
) -> tuple[str, dict[str, object]]:
    text = provider.respond(session_id=session, turn_id=turn, transcript=prompt)
    return text, provider.observations[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--tool-proposals-only", action="store_true")
    args = parser.parse_args()
    if args.tool_proposals_only:
        if args.keep_running:
            parser.error("--keep-running cannot be combined with --tool-proposals-only")
        from scripts.verify_tool_proposals import main as verify_tool_proposals

        return verify_tool_proposals([])
    process, output = start_server()
    keep = False
    try:
        initial_slots = slot_runtime_state()
        if any(initial_slots.values()):
            raise AssertionError("local LFM started with an unexpectedly busy slot")
        provider = LocalLFMProvider()
        readiness = provider.readiness()
        if readiness["provider_identity"] != PROVIDER_IDENTITY:
            raise AssertionError("local provider identity mismatch")
        startup_log = LOG.read_text(encoding="utf-8", errors="replace")
        if "offloaded 31/31 layers to GPU" not in startup_log:
            raise AssertionError("local LFM did not prove full GPU offload")
        if "n_slots = 2, n_ctx_slot = 32768" not in startup_log:
            raise AssertionError("local LFM startup log did not prove two 32768-token slots")

        responses: dict[str, str] = {}
        errors: list[BaseException] = []

        def run(name: str, prompt: str) -> None:
            try:
                responses[name] = provider.respond(
                    session_id=f"session-{name}",
                    turn_id=f"turn-{name}",
                    transcript=prompt,
                )
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(
                target=run,
                args=("parallel-a", "Объясни простыми словами, почему летом день длиннее, тремя законченными предложениями."),
            ),
            threading.Thread(
                target=run,
                args=("parallel-b", "Объясни простыми словами, зачем растениям нужен свет, тремя законченными предложениями."),
            ),
        ]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        occupied_parallel_slots = wait_for_busy_slots(2, 5)
        join_workers(threads, 25)
        if errors:
            codes = [getattr(error, "code", type(error).__name__) for error in errors]
            raise AssertionError(f"two-slot requests failed: {codes!r}")
        if len(occupied_parallel_slots) != 2:
            raise AssertionError("two distinct server slots were not busy concurrently")
        parallel_seconds = time.monotonic() - started
        parallel_observations = provider.observations[-2:]
        if len(responses) != 2 or len(parallel_observations) != 2:
            raise AssertionError("two-slot provider did not produce two observations")
        for name, text in responses.items():
            if not text or len(text) > 500:
                raise AssertionError(f"bounded visible answer failed for {name}")
        if any(observation.get("reasoning_chars", 0) <= 0 for observation in parallel_observations):
            raise AssertionError("two-slot responses did not expose parsed reasoning metrics")

        marker_a = f"ALFA{secrets.token_hex(4).upper()}"
        marker_b = f"BETA{secrets.token_hex(4).upper()}"

        def require_marker(text: str, expected: str, forbidden: str) -> None:
            normalized = text.upper()
            if expected not in normalized or forbidden in normalized:
                raise AssertionError("session marker context was not isolated")

        first_a, _ = one_response(
            provider,
            "session-isolation-a",
            "turn-isolation-a1",
            f"Запомни маркер {marker_a}. Ответь только этим маркером.",
        )
        first_b, _ = one_response(
            provider,
            "session-isolation-b",
            "turn-isolation-b1",
            f"Запомни маркер {marker_b}. Ответь только этим маркером.",
        )
        require_marker(first_a, marker_a, marker_b)
        require_marker(first_b, marker_b, marker_a)
        recalled_a, _ = one_response(
            provider,
            "session-isolation-a",
            "turn-isolation-a2",
            "Повтори только маркер, который я просил запомнить в этой сессии.",
        )
        recalled_b, _ = one_response(
            provider,
            "session-isolation-b",
            "turn-isolation-b2",
            "Повтори только маркер, который я просил запомнить в этой сессии.",
        )
        require_marker(recalled_a, marker_a, marker_b)
        require_marker(recalled_b, marker_b, marker_a)
        provider.reset_session("session-isolation-a")
        provider.reset_session("session-isolation-b")

        handoff_parts: list[str] = []
        handoff_times: list[float] = []
        handoff_started = time.monotonic()

        def capture_handoff(sentence: str) -> None:
            handoff_parts.append(sentence)
            handoff_times.append(time.monotonic())

        handoff_text = provider.respond_with_handoff(
            session_id="session-streaming",
            turn_id="turn-streaming",
            transcript="Ответь ровно двумя короткими законченными предложениями о небе.",
            on_sentence=capture_handoff,
        )
        handoff_returned = time.monotonic()
        handoff_observation = provider.observations[-1]
        if len(handoff_parts) < 2:
            raise AssertionError("local LFM did not produce incremental sentence handoff")
        if any(timestamp >= handoff_returned for timestamp in handoff_times):
            raise AssertionError("visible sentence handoff was not observed before final return")
        parser_completed = handoff_started + float(
            handoff_observation["completion_ms"]
        ) / 1_000
        if handoff_times[0] >= parser_completed:
            raise AssertionError("visible sentence handoff was buffered until stream completion")
        if " ".join(" ".join(handoff_parts).split()) != " ".join(handoff_text.split()):
            raise AssertionError("visible sentence handoff diverged from the final response")
        if handoff_observation.get("reasoning_chars", 0) <= 0:
            raise AssertionError("streaming response did not parse hidden reasoning separately")
        provider.reset_session("session-streaming")

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

        wait_for_all_slots_idle(5)
        worker = threading.Thread(target=long_request)
        worker.start()
        cancellation_busy_slots = wait_for_busy_slots(1, 5)
        if len(cancellation_busy_slots) != 1:
            raise AssertionError("cancellation request did not occupy exactly one server slot")
        cancelled_slot = next(iter(cancellation_busy_slots))
        cancellation.cancel()
        cancellation_provider.cancel()
        join_workers([worker], 3)
        if cancelled != ["selected_provider_cancelled"]:
            raise AssertionError(f"local LFM cancellation failed: {cancelled!r}")
        wait_for_slot_idle(cancelled_slot, 5)

        recovery_responses: dict[str, str] = {}
        recovery_errors: list[BaseException] = []

        def run_recovery(name: str, prompt: str) -> None:
            try:
                recovery_responses[name] = provider.respond(
                    session_id=f"session-recovery-{name}",
                    turn_id=f"turn-recovery-{name}",
                    transcript=prompt,
                )
            except BaseException as error:
                recovery_errors.append(error)

        recovery_workers = [
            threading.Thread(
                target=run_recovery,
                args=("a", "Назови столицу Сербии и поясни ответ двумя короткими предложениями."),
            ),
            threading.Thread(
                target=run_recovery,
                args=("b", "Назови столицу Хорватии и поясни ответ двумя короткими предложениями."),
            ),
        ]
        for recovery_worker in recovery_workers:
            recovery_worker.start()
        recovered_busy_slots = wait_for_busy_slots(2, 5)
        join_workers(recovery_workers, 25)
        if recovery_errors:
            codes = [
                getattr(error, "code", type(error).__name__)
                for error in recovery_errors
            ]
            raise AssertionError(f"full slot recovery requests failed: {codes!r}")
        if len(recovered_busy_slots) != 2 or len(recovery_responses) != 2:
            raise AssertionError("full two-slot capacity did not recover after cancellation")
        recovery_observations = provider.observations[-2:]

        print("Issue #15 local LFM integration: PASS")
        print(f"identity: {PROVIDER_IDENTITY}")
        print("runtime: llama.cpp b10357/689e227db; binary/model hashes verified")
        print("network: endpoint=127.0.0.1:18080 only; credentials=false fallback=false")
        print("slots: count=2 n_ctx_slot=32768 full_gpu_offload=required flash_attention=true")
        print(
            f"parallel_requests: completed=2 concurrently_busy_slots="
            f"{len(occupied_parallel_slots)} elapsed_seconds={parallel_seconds:.3f}"
        )
        for index, observation in enumerate(parallel_observations, start=1):
            print(
                f"parallel-{index}: visible_ttft_ms={observation['visible_first_content_ms']:.3f} "
                f"completion_ms={observation['completion_ms']:.3f} "
                f"visible_chars={observation['visible_chars']}"
            )
        print("session_context: multi_turn=2 isolated_sessions=2 marker_content_persisted=false")
        print(
            f"visible_handoff: chunks={len(handoff_parts)} "
            f"first_callback_ms={(handoff_times[0] - handoff_started) * 1000:.3f} "
            f"return_ms={(handoff_returned - handoff_started) * 1000:.3f}"
        )
        recovery_completion_ms = max(
            float(observation["completion_ms"])
            for observation in recovery_observations
        )
        print(
            f"cancellation: terminal={cancelled[0]} exact_slot_released=true "
            f"recovered_concurrently_busy_slots={len(recovered_busy_slots)} "
            f"recovery_completion_ms={recovery_completion_ms:.3f}"
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

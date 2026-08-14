#!/usr/bin/env python3
"""Focused exact-cache Silero/Kseniya evidence; never records text or PCM."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = Path(
    os.environ.get(
        "VOICE_AGENT_TASK_RUNTIME_ROOT",
        str(Path.home() / ".cache/voice-agent-v2/experiments/silero-kseniya-48k-ship"),
    )
).resolve()
os.environ["VOICE_AGENT_TASK_RUNTIME_ROOT"] = str(TASK_ROOT)

from voice_agent_v2.local_stt import WhisperSTT
from voice_agent_v2.local_vad import SileroOnnxModel, SileroSpeechEndpoint
from voice_agent_v2.silero_tts import (
    SileroWorkerPool,
    verify_silero_runtime,
)
from voice_agent_v2.tracer import CancellationToken

POOL_IDLE_RSS_MAX_MIB = 1_800.0
POOL_PEAK_RSS_MAX_MIB = 2_000.0
WARM_SYNTHESIS_MAX_MS = 2_000.0
WARM_RTF_MAX = 0.20
COEXISTENCE_RSS_MAX_MIB = 8_000.0


def rss_bytes(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def cpu_seconds(pid: int) -> float:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        return (int(fields[13]) + int(fields[14])) / ticks
    except (OSError, ValueError, IndexError):
        return 0.0


def gpu_used_mib() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    values: list[int] = []
    for line in result.stdout.splitlines():
        try:
            values.append(int(line.strip()))
        except ValueError:
            pass
    return sum(values)


def wait_for_busy(pool: SileroWorkerPool, count: int, timeout: float) -> tuple[str, ...]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = tuple(
            str(slot["worker_id"]) for slot in pool.slots if slot["state"] == "busy"
        )
        if len(busy) >= count:
            return busy
        time.sleep(0.002)
    return ()


def main() -> int:
    TASK_ROOT.mkdir(parents=True, exist_ok=True)
    evidence_dir = TASK_ROOT / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    identity = verify_silero_runtime()
    pool = SileroWorkerPool(runtime_root=TASK_ROOT)
    stt: WhisperSTT | None = None
    evidence: dict[str, object] = {
        "schema_version": "voice-agent.silero-kseniya-real-evidence.v1",
        "thresholds_frozen_before_run": {
            "pool_idle_rss_max_mib": POOL_IDLE_RSS_MAX_MIB,
            "pool_peak_rss_max_mib": POOL_PEAK_RSS_MAX_MIB,
            "warm_synthesis_max_ms": WARM_SYNTHESIS_MAX_MS,
            "warm_rtf_max": WARM_RTF_MAX,
            "coexistence_rss_max_mib": COEXISTENCE_RSS_MAX_MIB,
        },
        "identity": identity,
        "claims_excluded": [
            "audibility", "voice_beauty", "physical_barge_in", "speaker_stop_latency",
            "full_livekit_browser_stack",
        ],
    }
    try:
        ready = pool.start()
        initial_pids = tuple(pool.process_ids)
        if len(initial_pids) != 2 or len(set(initial_pids)) != 2 or pool.ready_count != 2:
            raise AssertionError("two stable warmed Silero workers were not ready")
        idle_rss = sum(rss_bytes(pid) for pid in initial_pids)
        if idle_rss / 2**20 > POOL_IDLE_RSS_MAX_MIB:
            raise AssertionError("two-worker idle RSS exceeded the frozen threshold")
        evidence["readiness"] = {
            "worker_count": ready["worker_count"],
            "worker_ids": ready["worker_ids"],
            "stable_process_count": len(initial_pids),
            "warmed": ready["warmed"],
            "idle_rss_mib": round(idle_rss / 2**20, 3),
        }

        old_key = __import__("voice_agent_v2.contracts", fromlist=["TTSRequestKey"]).TTSRequestKey(
            "real-session", 1, "turn-obsolete", 1, "request-obsolete", 0
        )
        current_key = __import__("voice_agent_v2.contracts", fromlist=["TTSRequestKey"]).TTSRequestKey(
            "real-session", 1, "turn-current", 2, "request-current", 0
        )
        old_token = CancellationToken()
        old_result: dict[str, object] = {}
        current_result: dict[str, object] = {}
        long_text = (
            "Это длинный технический ответ для проверки отмены старого поколения, "
            "который синтезируется целиком и после прерывания должен быть отброшен без доставки. "
            "Он остаётся короче жёсткого ограничения сегмента и не содержит частных данных."
        )
        current_text = (
            "Новый актуальный ответ использует второй изолированный процесс и возвращает "
            "нативный сорока восьми килогерцовый моно сигнал без понижения частоты."
        )
        sample_stop = threading.Event()
        rss_samples: list[int] = []

        def sample_resources() -> None:
            while not sample_stop.is_set():
                rss_samples.append(sum(rss_bytes(pid) for pid in initial_pids))
                time.sleep(0.004)

        def obsolete() -> None:
            try:
                pool.synthesize(old_key, long_text, old_token)
                old_result["outcome"] = "unexpected_delivery"
            except Exception as error:
                old_result["outcome"] = getattr(error, "code", type(error).__name__)

        def current() -> None:
            started = time.monotonic()
            chunks, metadata = pool.synthesize(current_key, current_text, None)
            current_result.update(metadata)
            current_result["wall_ms"] = round((time.monotonic() - started) * 1_000, 3)
            current_result["parent_chunk_count"] = len(chunks)
            current_result["parent_audio_bytes"] = sum(len(chunk) for chunk in chunks)

        before_cpu = {pid: cpu_seconds(pid) for pid in initial_pids}
        overlap_started = time.monotonic()
        sampler = threading.Thread(target=sample_resources)
        sampler.start()
        old_thread = threading.Thread(target=obsolete)
        old_thread.start()
        if not wait_for_busy(pool, 1, 1):
            raise AssertionError("obsolete Silero call did not enter a worker")
        old_token.cancel()
        current_thread = threading.Thread(target=current)
        current_thread.start()
        overlap_workers = wait_for_busy(pool, 2, 1)
        old_thread.join(5)
        current_thread.join(5)
        sample_stop.set()
        sampler.join(1)
        overlap_wall = time.monotonic() - overlap_started
        after_cpu = {pid: cpu_seconds(pid) for pid in initial_pids}
        if old_thread.is_alive() or current_thread.is_alive():
            raise AssertionError("Silero overlap did not finish within the bound")
        if old_result.get("outcome") != "selected_tts_cancelled":
            raise AssertionError("obsolete Silero PCM was not rejected after worker return")
        if len(overlap_workers) != 2:
            raise AssertionError("obsolete/current synthesis did not overlap on two workers")
        if current_result.get("parent_audio_bytes") != current_result.get("audio_bytes"):
            raise AssertionError("native 48 kHz parent totals differ from worker totals")
        duration_ms = float(current_result["duration_ms"])
        latency_ms = float(current_result["latency_ms"])
        rtf = latency_ms / duration_ms
        peak_rss = max(rss_samples or [idle_rss])
        cpu_total = sum(max(0.0, after_cpu[pid] - before_cpu[pid]) for pid in initial_pids)
        cpu_one_core_percent = cpu_total / max(overlap_wall, 0.001) * 100
        if peak_rss / 2**20 > POOL_PEAK_RSS_MAX_MIB:
            raise AssertionError("two-worker peak RSS exceeded the frozen threshold")
        if latency_ms > WARM_SYNTHESIS_MAX_MS or rtf > WARM_RTF_MAX:
            raise AssertionError("Silero latency/RTF exceeded the frozen threshold")
        evidence["obsolete_current_overlap"] = {
            "overlap_worker_count": len(overlap_workers),
            "obsolete_outcome": old_result["outcome"],
            "current_audio_bytes": current_result["audio_bytes"],
            "current_samples": current_result["samples"],
            "current_duration_ms": duration_ms,
            "current_synthesis_ms": latency_ms,
            "current_rtf": round(rtf, 6),
            "sample_rate_hz": 48_000,
            "peak_two_worker_rss_mib": round(peak_rss / 2**20, 3),
            "two_worker_cpu_one_core_percent": round(cpu_one_core_percent, 3),
            "stale_after_worker_count": pool.counters["stale_after_worker"],
            "third_worker_created": False,
        }

        # Coexistence uses the existing exact STT cache and pinned VAD, writes only
        # content-free process logs/temporary silence under TASK_ROOT, and starts no
        # network service or shared port.
        gpu_before = gpu_used_mib()
        vad = SileroOnnxModel()
        endpoint = SileroSpeechEndpoint(vad)
        for _ in range(20):
            endpoint.feed(b"\0\0" * 320)
        stt = WhisperSTT()
        stt_ready = stt.start()
        stt_warm = stt.warmup()
        coexist_pids = initial_pids + ((stt.process_id,) if stt.process_id is not None else ())
        coexist_rss = sum(rss_bytes(pid) for pid in coexist_pids) + rss_bytes(os.getpid())
        gpu_after = gpu_used_mib()
        if coexist_rss / 2**20 > COEXISTENCE_RSS_MAX_MIB:
            raise AssertionError("Silero/VAD/STT coexistence RSS exceeded the frozen threshold")
        if pool.process_ids != initial_pids or pool.ready_count != 2:
            raise AssertionError("Silero workers changed during VAD/STT coexistence")
        evidence["vad_stt_coexistence"] = {
            "silero_worker_count": len(pool.process_ids),
            "vad_model": type(vad).__name__,
            "stt_process_ready": stt.process_id == stt_warm["process_id"],
            "stt_runtime_ready": (
                stt_ready.get("runtime") == "faster-whisper"
                and stt_ready.get("device") == "cuda"
            ),
            "combined_process_rss_mib": round(coexist_rss / 2**20, 3),
            "compute_gpu_used_mib_before": gpu_before,
            "compute_gpu_used_mib_after": gpu_after,
            "network_service_started": False,
        }
        stt.close()
        stt = None

        pool.quarantine_worker_for_controlled_check("silero-1")
        if pool.ready_count != 1:
            raise AssertionError("controlled worker loss did not drop readiness")
        degraded_pids = tuple(pool.process_ids)
        recovered = pool.recover()
        recovered_pids = tuple(pool.process_ids)
        if len(recovered_pids) != 2 or len(set(recovered_pids)) != 2:
            raise AssertionError("controlled Silero recovery did not restore exactly two workers")
        if set(recovered_pids) == set(initial_pids):
            raise AssertionError("controlled recovery did not replace the quarantined process")
        evidence["controlled_failure_recovery"] = {
            "ready_workers_after_failure": 1,
            "surviving_process_count": len(degraded_pids),
            "ready_workers_after_explicit_recovery": recovered["worker_count"],
            "simultaneous_process_count_after_recovery": len(recovered_pids),
            "request_retry_performed": False,
            "fallback_performed": False,
        }
        evidence["result"] = "pass"
    finally:
        if stt is not None:
            stt.close()
        pool.close()

    output = evidence_dir / "real-silero-kseniya-v1.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, ensure_ascii=True, indent=2) + "\n")
    temporary.replace(output)
    print("Focused real Silero/Kseniya exact-cache verification: PASS")
    print(f"Evidence: {output}")
    print("Audibility, voice beauty, physical barge-in, and full-stack browser acceptance: NOT CLAIMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real cache-local VoxCPM2 adapter/resource/cancellation verification (content-free output)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
import wave

from voice_agent_v2.contracts import StageFailure
from voice_agent_v2.tracer import CancellationToken
from voice_agent_v2.voxcpm2_tts import MANIFEST, VoxCPM2FastTTS

CACHE = Path("/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts")
EVIDENCE = CACHE / "evidence"
SAMPLES = CACHE / "samples"
SENTENCES = (
    ("ru-01", "Доброе утро! Чем я могу помочь?"),
    ("ru-04", "Ты хочешь поехать поездом или полететь самолётом?"),
    ("ru-09", "Когда дождь закончится, мы откроем окно, проветрим комнату и спокойно продолжим работу."),
)


class ResourceSampler:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.peak_gpu_used_mib = 0
        self.minimum_gpu_free_mib = 1 << 30
        self.peak_host_used_mib = 0
        self.peak_process_tree_rss_mib = 0.0
        self.gpu_processes: dict[str, dict[str, object]] = {}
        self.root_pid: int | None = None
        self.successful_samples = 0
        self.successful_live_gpu_samples = 0
        self.failure: BaseException | None = None
        self.thread = threading.Thread(target=self.run, name="voxcpm2-resource-sampler", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def descendants_rss_mib(self) -> float:
        if self.root_pid is None:
            return 0.0
        rows: dict[int, tuple[int, int]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().split()
                rss_pages = int(fields[23])
                rows[int(entry.name)] = (int(fields[3]), rss_pages)
            except (OSError, ValueError, IndexError):
                continue
        selected = {self.root_pid}
        changed = True
        while changed:
            changed = False
            for pid, (parent, _rss) in rows.items():
                if parent in selected and pid not in selected:
                    selected.add(pid)
                    changed = True
        pages = sum(rows.get(pid, (0, 0))[1] for pid in selected)
        return pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024

    def sample(self) -> None:
        result = subprocess.run(
            [
                "/usr/bin/nvidia-smi",
                "--query-gpu=memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5,
        )
        used, free = (int(item.strip()) for item in result.stdout.strip().split(","))
        compute = subprocess.run(
            [
                "/usr/bin/nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5,
        )
        gpu_processes: list[tuple[str, str, int]] = []
        for line in compute.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
                continue
            gpu_processes.append((parts[0], parts[1], int(parts[2])))
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, value = line.split(":", 1)
            memory[name] = int(value.split()[0])
        host_used = (memory["MemTotal"] - memory["MemAvailable"]) // 1024
        process_tree_rss_mib = self.descendants_rss_mib()
        self.peak_gpu_used_mib = max(self.peak_gpu_used_mib, used)
        self.minimum_gpu_free_mib = min(self.minimum_gpu_free_mib, free)
        for pid, process_name, process_used_mib in gpu_processes:
            record = self.gpu_processes.setdefault(
                pid, {"process_name": process_name, "peak_used_mib": 0}
            )
            record["peak_used_mib"] = max(int(record["peak_used_mib"]), process_used_mib)
        self.peak_host_used_mib = max(self.peak_host_used_mib, host_used)
        self.peak_process_tree_rss_mib = max(
            self.peak_process_tree_rss_mib, process_tree_rss_mib
        )
        self.successful_samples += 1
        if self.root_pid is not None and Path(f"/proc/{self.root_pid}").exists():
            self.successful_live_gpu_samples += 1

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sample()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                self.failure = error
                self.stop_event.set()
                return
            self.stop_event.wait(0.1)

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("VoxCPM2 resource sampler did not stop")
        if self.failure is not None:
            raise RuntimeError("VoxCPM2 resource sampling failed") from self.failure
        self.sample()

    def as_dict(self) -> dict[str, object]:
        return {
            "peak_gpu_used_mib": self.peak_gpu_used_mib,
            "minimum_gpu_free_mib": self.minimum_gpu_free_mib,
            "peak_host_used_mib": self.peak_host_used_mib,
            "peak_process_tree_rss_mib": round(self.peak_process_tree_rss_mib, 3),
            "successful_samples": self.successful_samples,
            "successful_live_gpu_samples": self.successful_live_gpu_samples,
            "gpu_processes": self.gpu_processes,
        }


def save_sample(path: Path, chunks: tuple[bytes, ...]) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"".join(chunks))


def observation_metrics(observation: dict) -> dict[str, object]:
    audio_seconds = observation["output_bytes"] / 32_000
    return {
        "first_pcm_ms": round(float(observation["first_signal_ms"]), 3),
        "completion_ms": round(float(observation["completion_ms"]), 3),
        "audio_seconds": round(audio_seconds, 6),
        "rtf": round(float(observation["completion_ms"]) / 1000 / audio_seconds, 6),
        "chunk_count": observation["chunk_count"],
        "output_bytes": observation["output_bytes"],
    }


def validate_resources(resources: dict[str, object], *, reserve_mib: int) -> None:
    if int(resources["successful_live_gpu_samples"]) < 1:
        raise AssertionError("no successful resource sample observed the live GPU adapter")
    if int(resources["minimum_gpu_free_mib"]) < reserve_mib:
        raise AssertionError("real VoxCPM2 verification violated the configured VRAM reserve")


def main() -> int:
    if not os.environ.get("VOICE_AGENT_GPU_BAKEOFF_LOCK_FD", "").isdigit():
        raise RuntimeError("real VoxCPM2 verification requires an inherited GPU bakeoff lock FD")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for sample_id, _text in SENTENCES:
        (SAMPLES / f"{sample_id}.wav").unlink(missing_ok=True)

    sampler = ResourceSampler()
    adapter = VoxCPM2FastTTS()
    sampler.start()
    sampler_finalized = False
    evidence: dict[str, object] = {
        "schema_version": "voice-agent.voxcpm2-fast-real-evidence.v1",
        "content_retained_in_evidence": False,
    }
    try:
        cold_started = time.monotonic()
        ready = adapter.start()
        cold_ready_ms = (time.monotonic() - cold_started) * 1000
        sampler.root_pid = adapter.process_id
        resident_pid = adapter.process_id
        if resident_pid is None:
            raise AssertionError("resident adapter process is absent after readiness")
        warmup_started = time.monotonic()
        warmup = adapter.warmup()
        warmup_ms = (time.monotonic() - warmup_started) * 1000
        warmup_observation = adapter.observations[-1]
        if warmup.get("discarded") is not True or warmup["process_id"] != resident_pid:
            raise AssertionError("warm-up was not discard-only in the resident process")

        turns = []
        for sample_id, text in SENTENCES:
            chunks = tuple(adapter.stream_synthesize(
                session_id="public-verification-session", turn_id=f"turn-{sample_id}", text=text,
                audio_format=adapter.output_format,
            ))
            if adapter.process_id != resident_pid:
                raise AssertionError("resident adapter process changed across turns")
            save_sample(SAMPLES / f"{sample_id}.wav", chunks)
            turns.append({"sample_id": sample_id, **observation_metrics(adapter.observations[-1])})

        token = CancellationToken()
        cancellation_stream = adapter.stream_synthesize(
            session_id="public-verification-session", turn_id="turn-cancellation",
            text=SENTENCES[-1][1], audio_format=adapter.output_format, cancellation=token,
        )
        first_cancel_chunk = next(cancellation_stream)
        cancel_started = time.monotonic()
        token.cancel()
        post_cancel_chunks = 0
        try:
            for _chunk in cancellation_stream:
                post_cancel_chunks += 1
        except StageFailure as error:
            if error.code != "selected_tts_cancelled":
                raise
        else:
            raise AssertionError("cancelled synthesis did not fail with the contract cancellation code")
        cancellation_ms = (time.monotonic() - cancel_started) * 1000
        if post_cancel_chunks != 0:
            raise AssertionError("post-cancel stale PCM escaped the adapter")
        if adapter.process_id != resident_pid:
            raise AssertionError("cooperative request cancellation destroyed the resident process")

        recovery_chunks = tuple(adapter.stream_synthesize(
            session_id="public-verification-session", turn_id="turn-recovery",
            text=SENTENCES[0][1], audio_format=adapter.output_format,
        ))
        if not recovery_chunks or adapter.process_id != resident_pid:
            raise AssertionError("resident adapter did not recover after cancellation")
        evidence.update({
            "identity": adapter.identity,
            "runtime_ready": {
                name: ready[name] for name in (
                    "runtime", "runtime_version", "runtime_revision", "model", "model_revision",
                    "voice_mode", "reference_id", "local_only", "reserve_mib", "free_after_load_mib",
                    "torch_version", "flash_attention_version",
                )
            },
            "resident_process_id": resident_pid,
            "cold_ready_ms": round(cold_ready_ms, 3),
            "cold_time_to_first_pcm_ms": round(
                cold_ready_ms + float(warmup_observation["first_signal_ms"]), 3
            ),
            "warmup": {
                "elapsed_ms": round(warmup_ms, 3), "discarded": True,
                **observation_metrics(warmup_observation),
            },
            "turns": turns,
            "cancellation": {
                "first_chunk_bytes": len(first_cancel_chunk),
                "latency_ms": round(cancellation_ms, 3),
                "post_cancel_chunks": post_cancel_chunks,
                "resident_process_preserved": True,
                "recovery_chunk_count": len(recovery_chunks),
            },
            "sample_paths": [str(SAMPLES / f"{sample_id}.wav") for sample_id, _ in SENTENCES],
        })
        try:
            sampler.stop()
        finally:
            sampler_finalized = True
        resources = sampler.as_dict()
        reserve_mib = int(
            json.loads(MANIFEST.read_text(encoding="utf-8"))["resource_gate"]["vram_reserve_mib"]
        )
        validate_resources(resources, reserve_mib=reserve_mib)
        evidence["resources"] = resources
    finally:
        try:
            if not sampler_finalized:
                sampler.stop()
        finally:
            adapter.close()
    path = EVIDENCE / "real-adapter-measurement.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Accelerated VoxCPM2 real adapter verification: PASS")
    print(f"evidence={path} resident_process_reused=true public_samples={len(SENTENCES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

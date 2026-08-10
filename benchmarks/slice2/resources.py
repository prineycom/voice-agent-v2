"""Host-wide resource sampler used for cold, warm, sustained, and overlap runs."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
import time
from typing import Any


def _memory() -> tuple[int, int, int]:
    fields: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            name, value = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable", "SwapFree"}:
                fields[name] = int(value.split()[0])
    used_mib = (fields["MemTotal"] - fields["MemAvailable"]) // 1024
    return used_mib, fields["MemAvailable"] // 1024, fields["SwapFree"] // 1024


def _cpu_ticks() -> tuple[int, int]:
    fields = open("/proc/stat", encoding="ascii").readline().split()[1:]
    ticks = [int(value) for value in fields]
    idle = ticks[3] + ticks[4]
    return sum(ticks), idle


def _gpu() -> tuple[int, int, int]:
    output = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,utilization.gpu,utilization.memory",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    fields = output.split(", ")
    if len(fields) != 3:
        raise RuntimeError("resource sampler supports exactly one NVIDIA GPU")
    return tuple(int(field) for field in fields)  # type: ignore[return-value]


@dataclass(frozen=True)
class Sample:
    monotonic_seconds: float
    host_ram_used_mib: int
    host_ram_available_mib: int
    swap_free_mib: int
    cpu_percent: float
    gpu_vram_used_mib: int
    gpu_percent: int
    gpu_memory_percent: int


class ResourceSampler:
    """Sample host-wide pressure; it deliberately records no process command lines."""

    def __init__(self, interval_seconds: float = 0.05) -> None:
        if not 0.02 <= interval_seconds <= 1.0:
            raise ValueError("sampling interval must be between 20 ms and 1 s")
        self.interval_seconds = interval_seconds
        self.samples: list[Sample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def __enter__(self) -> "ResourceSampler":
        total, idle = _cpu_ticks()

        def collect() -> None:
            nonlocal total, idle
            try:
                while not self._stop.is_set():
                    next_total, next_idle = _cpu_ticks()
                    delta_total = max(1, next_total - total)
                    cpu_percent = 100.0 * (1.0 - (next_idle - idle) / delta_total)
                    total, idle = next_total, next_idle
                    used, available, swap_free = _memory()
                    gpu_used, gpu_percent, gpu_memory_percent = _gpu()
                    self.samples.append(
                        Sample(
                            monotonic_seconds=time.monotonic(),
                            host_ram_used_mib=used,
                            host_ram_available_mib=available,
                            swap_free_mib=swap_free,
                            cpu_percent=cpu_percent,
                            gpu_vram_used_mib=gpu_used,
                            gpu_percent=gpu_percent,
                            gpu_memory_percent=gpu_memory_percent,
                        )
                    )
                    self._stop.wait(self.interval_seconds)
            except BaseException as error:  # surfaced on exit, never silently loses measurement
                self._error = error

        self._thread = threading.Thread(target=collect, name="slice2-resource-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        assert self._thread is not None
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("resource sampler did not stop")
        if self._error is not None:
            raise RuntimeError("resource sampler failed") from self._error
        if not self.samples:
            raise RuntimeError("resource sampler captured no samples")

    def summary(self) -> dict[str, Any]:
        if not self.samples:
            raise ValueError("no samples captured")
        return {
            "sample_interval_milliseconds": self.interval_seconds * 1000,
            "sample_count": len(self.samples),
            "host_ram_idle_mib": self.samples[0].host_ram_used_mib,
            "host_ram_peak_mib": max(sample.host_ram_used_mib for sample in self.samples),
            "host_ram_minimum_available_mib": min(sample.host_ram_available_mib for sample in self.samples),
            "swap_minimum_free_mib": min(sample.swap_free_mib for sample in self.samples),
            "cpu_peak_percent": max(sample.cpu_percent for sample in self.samples),
            "gpu_vram_idle_mib": self.samples[0].gpu_vram_used_mib,
            "gpu_vram_peak_mib": max(sample.gpu_vram_used_mib for sample in self.samples),
            "gpu_peak_percent": max(sample.gpu_percent for sample in self.samples),
            "gpu_memory_peak_percent": max(sample.gpu_memory_percent for sample in self.samples),
        }

"""Live, whitelisted canonical-host inventory capture with no environment dump."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
from typing import Any

from .safety import cache_path


def _run(*command: str) -> str:
    completed = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def _os_release() -> dict[str, str]:
    allowed = {"ID", "PRETTY_NAME"}
    result: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in allowed:
            result[key.lower()] = value.strip().strip('"')
    return result


def _lscpu() -> dict[str, Any]:
    raw = json.loads(_run("lscpu", "--json"))["lscpu"]
    fields = {entry["field"].rstrip(":"): entry["data"] for entry in raw}
    cores_per_socket = int(fields["Core(s) per socket"])
    sockets = int(fields["Socket(s)"])
    return {
        "architecture": fields["Architecture"],
        "model": fields["Model name"],
        "logical_cpus": int(fields["CPU(s)"]),
        "physical_cores": cores_per_socket * sockets,
        "threads_per_core": int(fields["Thread(s) per core"]),
    }


def _memory() -> dict[str, int]:
    fields: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        name, value = line.split(":", 1)
        match = re.fullmatch(r"\s*(\d+) kB\s*", value)
        if match:
            fields[name] = int(match.group(1)) * 1024
    return {
        "total_bytes": fields["MemTotal"],
        "available_at_capture_bytes": fields["MemAvailable"],
        "swap_total_bytes": fields["SwapTotal"],
        "swap_free_at_capture_bytes": fields["SwapFree"],
    }


def _gpu() -> dict[str, Any]:
    fields = _run(
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,utilization.memory",
        "--format=csv,noheader,nounits",
    ).split(", ")
    if len(fields) != 6:
        raise RuntimeError("expected exactly one NVIDIA GPU and six whitelisted fields")
    return {
        "model": fields[0],
        "driver_version": fields[1],
        "vram_total_mib": int(fields[2]),
        "vram_used_at_capture_mib": int(fields[3]),
        "gpu_utilization_at_capture_percent": int(fields[4]),
        "memory_utilization_at_capture_percent": int(fields[5]),
    }


def _version(command: tuple[str, ...], pattern: str | None = None) -> str | None:
    try:
        output = _run(*command).splitlines()[0]
    except (FileNotFoundError, subprocess.CalledProcessError, IndexError):
        return None
    if pattern is None:
        return output[:160]
    match = re.search(pattern, output)
    return match.group(1) if match else output[:160]


def _vulkan_version() -> str | None:
    try:
        output = _run("vulkaninfo", "--summary")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    match = re.search(r"Vulkan Instance Version:\s*([^\s]+)", output)
    return match.group(1) if match else "available-version-unparsed"


def capture() -> dict[str, Any]:
    return {
        "schema_version": "voice-agent.slice2-host.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "redaction": "hostname, user identity, device UUIDs, process command lines, and environment omitted",
        "os": {**_os_release(), "kernel": platform.release(), "machine": platform.machine()},
        "cpu": _lscpu(),
        "memory": _memory(),
        "gpu": _gpu(),
        "benchmark_capabilities": {
            "python": _version(("python3", "--version"), r"Python (.+)"),
            "ffmpeg": _version(("ffmpeg", "-version"), r"ffmpeg version ([^ ]+)"),
            "podman": _version(("podman", "--version"), r"podman version (.+)"),
            "vulkan_instance": _vulkan_version(),
            "cmake": _version(("cmake", "--version"), r"cmake version (.+)"),
            "nvidia_toolkit_compiler": _version(("nvcc", "--version")),
        },
    }


def write_capture(output: Path) -> dict[str, Any]:
    destination = cache_path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"refusing to replace host capture: {destination}")
    evidence = capture()
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence

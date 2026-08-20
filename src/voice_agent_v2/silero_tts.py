"""Exact-cache Silero/Kseniya TTS v2 adapter and two-process resident pool."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable, Iterator

from .contracts import AudioFormat, StageFailure, valid_correlation_id
from .v2_audio import (
    TTS_OUTPUT_AUDIO_FORMAT,
    TTS_SEGMENT_MAX_BYTES,
    TTS_V2_OUTPUT_MEDIA_MAX_BYTES,
    TTS_V2_OUTPUT_MEDIA_MAX_SECONDS,
)
from .v2_contracts import (
    TTSRequestKey,
    TTS_V2_VERSION,
    pcm_duration_ms_matches_samples,
)
from .process_adapter import AdapterProcess, AdapterProcessError, AdapterRequestError
from .runtime_config import load_production_runtime_config
from .tracer import CancellationToken
from .tts_text import SHAPING_VERSION, shape_russian_tts

ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_CONFIG = load_production_runtime_config()
_PRODUCTION = _RUNTIME_CONFIG is not None
_RELEASE_ROOT = _RUNTIME_CONFIG["release_root"] if _PRODUCTION else None
MANIFEST_PATH = ROOT / "config" / "silero-kseniya-tts-v1.json"
if _PRODUCTION:
    CACHE_ROOT = _RUNTIME_CONFIG["paths"]["state"]
    MODEL_PATH = _RUNTIME_CONFIG["models"]["tts"] / "v5_5_ru.pt"
    PYTHON_PATH = _RUNTIME_CONFIG["executables"]["python"]
    DEFAULT_RUNTIME_ROOT = _RUNTIME_CONFIG["paths"]["state"] / "tts"
else:
    CACHE_ROOT = Path.home() / ".cache" / "voice-agent-v2" / "experiments" / "silero-baya-tts"
    MODEL_PATH = CACHE_ROOT / "downloads" / "v5_5_ru.pt"
    PYTHON_PATH = CACHE_ROOT / "venv" / "bin" / "python"
    DEFAULT_RUNTIME_ROOT = Path.home() / ".cache" / "voice-agent-v2" / "experiments" / "silero-kseniya-48k-ship"
WORKER_SCRIPT = ROOT / "scripts" / "silero_kseniya_worker.py"
MODEL_IDENTITY = "snakers4/silero-models@d9355348e2781dc8fa25a135d1602c530afae24c#v5_5_ru"
MODEL_SIZE = 145_420_684
MODEL_SHA256 = "50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437"
PYTHON_SHA256 = "021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7"
TORCH_C_SHA256 = "5dc8a93ddc69041dbd3f796794e0c2b3f72b47e0dc24ae2b46d9e7d0dc78953b"
LIBTORCH_CPU_SHA256 = "629d28a5fb24e2c33df077e2d98d5da0c73d0b53e628bb826fd4ba36616b482b"
SPEAKER = "kseniya"
WORKER_IDS = ("silero-1", "silero-2")
POOL_SIZE = 2
WORKER_PROTOCOL_VERSION = "voice-agent.silero-worker.v1"
CAPACITY_WAIT_SECONDS = 0.750
SYNTHESIS_TIMEOUT_SECONDS = 15.0
MAX_WORKER_CHUNKS = 64
MAX_SEGMENTS_PER_TURN = 16
WORKER_REQUEST_ERROR_CLASSES = frozenset({
    "invalid_request_shape",
    "invalid_request_version",
    "invalid_correlation",
    "text_out_of_bounds",
    "invalid_text_contract",
    "unsupported_audio_format",
    "request_too_large",
    "invalid_waveform",
    "waveform_out_of_bounds",
    "synthesis_failed",
})
WORKER_READY_EVENT_FIELDS = frozenset({
    "protocol_version", "event", "worker_id", "pid", "model_identity",
    "model_size_bytes", "model_sha256", "speaker", "native_sample_rates_hz",
    "output_audio", "complete_waveform", "cooperative_cancel",
    "max_parallel_requests", "intraop_threads", "interop_threads",
})
WORKER_CHUNK_EVENT_FIELDS = frozenset({
    "protocol_version", "event", "key", "request_id", "sequence", "bytes",
    "pcm_base64",
})
WORKER_FINAL_EVENT_FIELDS = frozenset({
    "protocol_version", "event", "key", "request_id", "status",
    "model_identity", "speaker", "encoding", "sample_rate_hz", "channels",
    "sample_width_bytes", "chunk_count", "audio_bytes", "samples",
    "duration_ms", "latency_ms", "terminal_count",
})
WORKER_ERROR_EVENT_FIELDS = frozenset({
    "protocol_version", "event", "key", "request_id", "error_class",
})
WORKER_CORRELATION_FIELDS = frozenset({
    "session_id",
    "stream_epoch",
    "turn_id",
    "turn_generation",
    "request_id",
    "segment_index",
})


def _protocol_value_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            type(left) is type(right)
            and (type(left) is int or type(left) is float and math.isfinite(left))
            and left == right
        )
    if isinstance(left, str) or isinstance(right, str):
        return type(left) is str and type(right) is str and left == right
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_protocol_value_equal(a, b) for a, b in zip(left, right))
        )
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(_protocol_value_equal(left[name], right[name]) for name in left)
        )
    if left is None or right is None:
        return left is None and right is None
    return False


def _valid_worker_key(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != WORKER_CORRELATION_FIELDS:
        return False
    if any(
        type(value.get(name)) is not str or not valid_correlation_id(value[name])
        for name in ("session_id", "turn_id", "request_id")
    ):
        return False
    return all(
        type(value.get(name)) is int and minimum <= value[name] <= maximum
        for name, minimum, maximum in (
            ("stream_epoch", 1, 1_000_000_000),
            ("turn_generation", 1, 1_000_000_000),
            ("segment_index", 0, 4_095),
        )
    )


def _worker_key_matches(value: object, expected: TTSRequestKey) -> bool:
    expected_value = expected.as_dict()
    return (
        _valid_worker_key(value)
        and _valid_worker_key(expected_value)
        and _protocol_value_equal(value, expected_value)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_silero_runtime() -> dict[str, object]:
    """Verify only the exact existing artifact/runtime; never download or replace."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not (
        manifest.get("schema_version") == "voice-agent.silero-tts-runtime.v1"
        and manifest.get("backend") == "silero"
        and manifest.get("model_identity") == MODEL_IDENTITY
        and manifest.get("model", {}).get("speaker") == SPEAKER
        and manifest.get("model", {}).get("native_sample_rates_hz") == [8_000, 24_000, 48_000]
        and manifest.get("runtime", {}).get("state_root") == "xdg-runtime/tts"
        and manifest.get("runtime", {}).get("worker_protocol")
        == WORKER_PROTOCOL_VERSION
        and manifest.get("runtime", {}).get("workers") == POOL_SIZE
        and manifest.get("runtime", {}).get("automatic_retry") is False
        and manifest.get("runtime", {}).get("automatic_fallback") is False
        and manifest.get("runtime", {}).get("download_allowed") is True
        and all(
            manifest.get("output_audio", {}).get(name) == value
            for name, value in TTS_OUTPUT_AUDIO_FORMAT.as_dict().items()
        )
    ):
        raise StageFailure("tts", "silero_manifest_mismatch")
    checks = [(MODEL_PATH, MODEL_SIZE, MODEL_SHA256, "silero_model_mismatch")]
    if not _PRODUCTION:
        checks.append((PYTHON_PATH.resolve(), None, PYTHON_SHA256, "silero_python_mismatch"))
    for path, expected_size, expected_hash, code in tuple(checks):
        if not path.is_file() or (expected_size is not None and path.stat().st_size != expected_size):
            raise StageFailure("tts", code)
        if _sha256(path) != expected_hash:
            raise StageFailure("tts", code)
    torch_root = (_RELEASE_ROOT / "runtime" / "python" if _PRODUCTION else CACHE_ROOT / "venv") / "lib" / "python3.12" / "site-packages" / "torch"
    torch_c = tuple(torch_root.glob("_C.cpython-312-*-linux-gnu.so"))
    libtorch_cpu = torch_root / "lib" / "libtorch_cpu.so"
    if len(torch_c) != 1 or _sha256(torch_c[0]) != TORCH_C_SHA256:
        raise StageFailure("tts", "silero_torch_mismatch")
    if not libtorch_cpu.is_file() or _sha256(libtorch_cpu) != LIBTORCH_CPU_SHA256:
        raise StageFailure("tts", "silero_torch_mismatch")
    return {
        "model_identity": MODEL_IDENTITY,
        "model_size_bytes": MODEL_SIZE,
        "model_sha256": MODEL_SHA256,
        "speaker": SPEAKER,
        "sample_rate_hz": TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz,
        "python_sha256": PYTHON_SHA256,
        "torch_c_sha256": TORCH_C_SHA256,
        "libtorch_cpu_sha256": LIBTORCH_CPU_SHA256,
        "workers": POOL_SIZE,
        "verified": True,
    }


def _environment(worker_id: str, runtime_root: Path) -> dict[str, str]:
    return {
        "HOME": str(runtime_root / "home" / worker_id),
        "XDG_CACHE_HOME": str(runtime_root / "xdg-cache" / worker_id),
        "TORCH_HOME": str(runtime_root / "torch-home" / worker_id),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(ROOT),
        "VOICE_AGENT_SILERO_MODEL": str(MODEL_PATH),
        "VOICE_AGENT_SILERO_WORKER_ID": worker_id,
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
    }


@dataclass
class _WorkerSlot:
    worker_id: str
    process: AdapterProcess | None = None
    state: str = "starting"
    active_key: TTSRequestKey | None = None
    last_idle: float = field(default_factory=time.monotonic)
    ready_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class SileroVoiceProfile:
    profile: str = "silero-kseniya"
    backend: str = "silero"
    speaker: str = "kseniya"
    output_sample_rate_hz: int = 48_000
    native_sample_rate_hz: int = 48_000
    license: str = "CC-BY-NC-SA-4.0"
    private_noncommercial_only: bool = True

    def public_metadata(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "backend": self.backend,
            "speaker": self.speaker,
            "output_sample_rate_hz": self.output_sample_rate_hz,
            "native_sample_rate_hz": self.native_sample_rate_hz,
            "license": self.license,
            "private_noncommercial_only": self.private_noncommercial_only,
        }


@dataclass
class SileroTurnBudget:
    deadline: float
    segments: int = 0
    output_bytes: int = 0

    @classmethod
    def create(cls) -> "SileroTurnBudget":
        return cls(deadline=time.monotonic() + TTS_V2_OUTPUT_MEDIA_MAX_SECONDS)

    def consume(self, byte_count: int) -> None:
        self.segments += 1
        self.output_bytes += byte_count
        if (
            self.segments > MAX_SEGMENTS_PER_TURN
            or self.output_bytes > TTS_V2_OUTPUT_MEDIA_MAX_BYTES
            or time.monotonic() >= self.deadline
        ):
            raise StageFailure("tts", "selected_tts_output_out_of_bounds")


ProcessFactory = Callable[[str, Path, dict[str, str]], AdapterProcess]


@dataclass(frozen=True)
class SileroPoolHealth:
    process_ids: tuple[int, ...]
    ready_worker_count: int
    started: bool

    @property
    def live_worker_count(self) -> int:
        return len(self.process_ids)

    @property
    def ready_for_admission(self) -> bool:
        return (
            self.started
            and self.ready_worker_count == POOL_SIZE
            and len(self.process_ids) == POOL_SIZE
            and len(set(self.process_ids)) == POOL_SIZE
        )


class SileroWorkerPool:
    """Exactly two isolated models, one call per worker, with silent stale drain."""

    def __init__(
        self,
        *,
        runtime_root: Path = DEFAULT_RUNTIME_ROOT,
        process_factory: ProcessFactory | None = None,
        verify_runtime: Callable[[], dict[str, object]] = verify_silero_runtime,
        capacity_wait_seconds: float = CAPACITY_WAIT_SECONDS,
    ) -> None:
        if capacity_wait_seconds <= 0 or capacity_wait_seconds > CAPACITY_WAIT_SECONDS:
            raise ValueError("Silero capacity wait must be in (0, 750ms]")
        self.runtime_root = runtime_root
        self.process_factory = process_factory or self._default_process_factory
        self.verify_runtime = verify_runtime
        self.capacity_wait_seconds = capacity_wait_seconds
        self._condition = threading.Condition()
        self._slots = [_WorkerSlot(worker_id) for worker_id in WORKER_IDS]
        self._turn_locks: dict[tuple[str, int, str, int], threading.Lock] = {}
        self._affinity: dict[tuple[str, int, str, int], str] = {}
        self._invalid_turns: set[tuple[str, int, str, int]] = set()
        self._invalid_keys: set[TTSRequestKey] = set()
        self._started = False
        self._starting = False
        self._recovering = False
        self._closed = False
        self.process_start_count = 0
        self.observations: list[dict[str, object]] = []
        self.counters = {
            "requests": 0,
            "failures": 0,
            "capacity_timeouts": 0,
            "stale_after_worker": 0,
            "stale_before_dispatch": 0,
            "worker_quarantines": 0,
        }

    def _default_process_factory(
        self, worker_id: str, log_path: Path, environment: dict[str, str]
    ) -> AdapterProcess:
        return AdapterProcess(
            [str(PYTHON_PATH), "-B", str(WORKER_SCRIPT)], log_path, environment
        )

    @property
    def slots(self) -> tuple[dict[str, object], ...]:
        with self._condition:
            return tuple({
                "worker_id": slot.worker_id,
                "state": slot.state,
                "pid": self._slot_pid(slot),
                "active": slot.active_key is not None,
            } for slot in self._slots)

    @staticmethod
    def _slot_pid(slot: _WorkerSlot) -> int | None:
        process = getattr(slot.process, "process", None)
        return getattr(process, "pid", None) if process is not None and process.poll() is None else None

    def health_snapshot(self) -> SileroPoolHealth:
        with self._condition:
            self._refresh_worker_health_locked()
            process_ids = tuple(
                pid
                for pid in (self._slot_pid(slot) for slot in self._slots)
                if pid is not None
            )
            ready_worker_count = (
                sum(slot.state in {"idle", "busy"} for slot in self._slots)
                if self._started
                else 0
            )
            return SileroPoolHealth(
                process_ids=process_ids,
                ready_worker_count=ready_worker_count,
                started=self._started,
            )

    @property
    def process_ids(self) -> tuple[int, ...]:
        return self.health_snapshot().process_ids

    @property
    def retained_turn_count(self) -> int:
        with self._condition:
            return len(set(self._turn_locks) | set(self._affinity) | set(self._invalid_turns))

    def _refresh_worker_health_locked(self) -> None:
        changed = False
        for slot in self._slots:
            if slot.state not in {"idle", "busy"}:
                continue
            if slot.ready_metadata is not None and self._slot_pid(slot) is not None:
                continue
            slot.state = "unhealthy"
            slot.ready_metadata = None
            self.counters["worker_quarantines"] += 1
            changed = True
        if changed:
            self._condition.notify_all()

    @property
    def ready_count(self) -> int:
        return self.health_snapshot().ready_worker_count

    def require_ready(self) -> None:
        if not self.health_snapshot().ready_for_admission:
            raise StageFailure("tts", "silero_pool_not_ready")

    def start(self) -> dict[str, object]:
        with self._condition:
            while self._starting:
                self._condition.wait()
            if self._closed:
                raise StageFailure("tts", "silero_pool_closed")
            if self._started:
                return self.readiness_metadata()
            self._starting = True
        started: list[_WorkerSlot] = []
        try:
            self.verify_runtime()
            for slot in self._slots:
                self._start_slot(slot, ready_state="warming")
                started.append(slot)
            # Warm every worker through the same public request/result validator.
            for index, slot in enumerate(self._slots):
                key = TTSRequestKey(
                    session_id="warmup-session",
                    stream_epoch=1,
                    turn_id=f"warmup-turn-{index + 1}",
                    turn_generation=index + 1,
                    request_id=f"warmup-request-{index + 1}",
                    segment_index=0,
                )
                pcm, _metadata = self._request_on_slot(slot, key, "Готово.")
                if not pcm:
                    raise StageFailure("tts", "silero_warmup_failed")
            with self._condition:
                for slot in self._slots:
                    slot.state = "idle"
                    slot.active_key = None
                    slot.last_idle = time.monotonic()
                self._started = True
                self._condition.notify_all()
            return self.readiness_metadata()
        except BaseException as error:
            for slot in started:
                self._stop_slot(slot)
            if isinstance(error, StageFailure):
                raise
            raise StageFailure("tts", "silero_pool_unavailable") from error
        finally:
            with self._condition:
                self._starting = False
                self._condition.notify_all()

    def _start_slot(self, slot: _WorkerSlot, *, ready_state: str = "idle") -> None:
        log_path = self.runtime_root / "logs" / f"{slot.worker_id}-{time.monotonic_ns()}.stderr.log"
        process = self.process_factory(
            slot.worker_id, log_path, _environment(slot.worker_id, self.runtime_root)
        )
        metadata = process.start(30)
        expected = {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "event": "ready",
            "worker_id": slot.worker_id,
            "model_identity": MODEL_IDENTITY,
            "model_size_bytes": MODEL_SIZE,
            "model_sha256": MODEL_SHA256,
            "speaker": SPEAKER,
            "native_sample_rates_hz": [8_000, 24_000, 48_000],
            "output_audio": TTS_OUTPUT_AUDIO_FORMAT.as_dict(),
            "complete_waveform": True,
            "cooperative_cancel": False,
            "max_parallel_requests": 1,
            "intraop_threads": 2,
            "interop_threads": 1,
        }
        if set(metadata) != WORKER_READY_EVENT_FIELDS or any(
            not _protocol_value_equal(metadata.get(name), value)
            for name, value in expected.items()
        ):
            process.close()
            raise StageFailure("tts", "silero_worker_identity_mismatch")
        pid = metadata.get("pid")
        if type(pid) is not int or pid <= 0:
            process.close()
            raise StageFailure("tts", "silero_worker_identity_mismatch")
        slot.process = process
        slot.ready_metadata = dict(metadata)
        slot.state = ready_state
        slot.last_idle = time.monotonic()
        self.process_start_count += 1

    def readiness_metadata(self) -> dict[str, object]:
        snapshot = self.health_snapshot()
        if not snapshot.ready_for_admission:
            raise StageFailure("tts", "silero_pool_not_ready")
        return {
            "backend": "silero",
            "model_identity": MODEL_IDENTITY,
            "speaker": SPEAKER,
            "sample_rate_hz": 48_000,
            "worker_count": POOL_SIZE,
            "worker_ids": list(WORKER_IDS),
            "process_ids": list(snapshot.process_ids),
            "warmed": True,
            "cooperative_cancel": False,
            "automatic_retry": False,
            "automatic_fallback": False,
        }

    @staticmethod
    def _turn_key(key: TTSRequestKey) -> tuple[str, int, str, int]:
        return (key.session_id, key.stream_epoch, key.turn_id, key.turn_generation)

    def _is_fresh(self, key: TTSRequestKey) -> bool:
        with self._condition:
            return key not in self._invalid_keys and self._turn_key(key) not in self._invalid_turns

    def invalidate_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        turn_key = (session_id, stream_epoch, turn_id, turn_generation)
        with self._condition:
            self._invalid_turns.add(turn_key)
            for slot in self._slots:
                if slot.active_key is not None and self._turn_key(slot.active_key) == turn_key:
                    self._invalid_keys.add(slot.active_key)
            self._condition.notify_all()

    def invalidate_key(self, key: TTSRequestKey) -> None:
        with self._condition:
            self._invalid_keys.add(key)
            self._condition.notify_all()

    def release_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        turn_key = (session_id, stream_epoch, turn_id, turn_generation)
        with self._condition:
            self._affinity.pop(turn_key, None)
            self._turn_locks.pop(turn_key, None)
            self._invalid_turns.discard(turn_key)
            self._invalid_keys = {
                key for key in self._invalid_keys if self._turn_key(key) != turn_key
            }

    def _turn_lock(self, key: TTSRequestKey) -> threading.Lock:
        turn_key = self._turn_key(key)
        with self._condition:
            return self._turn_locks.setdefault(turn_key, threading.Lock())

    def synthesize(
        self,
        key: TTSRequestKey,
        text: str,
        cancellation: CancellationToken | None,
    ) -> tuple[tuple[bytes, ...], dict[str, object]]:
        if not _valid_worker_key(key.as_dict()):
            raise StageFailure("tts", "invalid_correlation_id")
        self.require_ready()
        if not self._is_fresh(key) or (cancellation is not None and cancellation.cancelled):
            self.counters["stale_before_dispatch"] += 1
            raise StageFailure("tts", "selected_tts_cancelled")
        turn_lock = self._turn_lock(key)
        unregister = (
            cancellation.register(lambda: self.invalidate_key(key))
            if cancellation is not None else lambda: None
        )
        try:
            with turn_lock:
                if not self._is_fresh(key) or (cancellation is not None and cancellation.cancelled):
                    self.counters["stale_before_dispatch"] += 1
                    raise StageFailure("tts", "selected_tts_cancelled")
                slot = self._acquire_slot(key, cancellation)
                try:
                    chunks, metadata = self._request_on_slot(slot, key, text)
                except AdapterRequestError as error:
                    self.counters["failures"] += 1
                    raise StageFailure("tts", "silero_synthesis_failed") from error
                except (AdapterProcessError, OSError, ValueError, KeyError, TypeError) as error:
                    self._quarantine(slot)
                    self.counters["failures"] += 1
                    raise StageFailure("tts", "silero_worker_failed") from error
                finally:
                    self._release_slot(slot, key)
                if not self._is_fresh(key) or (cancellation is not None and cancellation.cancelled):
                    self.counters["stale_after_worker"] += 1
                    raise StageFailure("tts", "selected_tts_cancelled")
                return chunks, metadata
        finally:
            unregister()

    def _acquire_slot(
        self, key: TTSRequestKey, cancellation: CancellationToken | None
    ) -> _WorkerSlot:
        deadline = time.monotonic() + self.capacity_wait_seconds
        turn_key = self._turn_key(key)
        with self._condition:
            while True:
                if (
                    key in self._invalid_keys
                    or turn_key in self._invalid_turns
                    or (cancellation is not None and cancellation.cancelled)
                ):
                    self.counters["stale_before_dispatch"] += 1
                    raise StageFailure("tts", "selected_tts_cancelled")
                self.require_ready()
                affinity = self._affinity.get(turn_key)
                if affinity is not None:
                    selected = next(
                        (slot for slot in self._slots if slot.worker_id == affinity),
                        None,
                    )
                    if selected is None:
                        raise StageFailure("tts", "silero_pool_not_ready")
                    if selected.state == "idle":
                        selected.state = "busy"
                        selected.active_key = key
                        self.counters["requests"] += 1
                        return selected
                else:
                    idle = [slot for slot in self._slots if slot.state == "idle"]
                    if idle:
                        selected = min(idle, key=lambda slot: slot.last_idle)
                        selected.state = "busy"
                        selected.active_key = key
                        self._affinity[turn_key] = selected.worker_id
                        self.counters["requests"] += 1
                        return selected
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.counters["capacity_timeouts"] += 1
                    raise StageFailure("tts", "silero_capacity_timeout")
                self._condition.wait(remaining)

    def _release_slot(self, slot: _WorkerSlot, key: TTSRequestKey) -> None:
        with self._condition:
            if slot.active_key == key:
                slot.active_key = None
                if slot.state == "busy":
                    slot.state = "idle"
                    slot.last_idle = time.monotonic()
            self._condition.notify_all()

    def _request_on_slot(
        self, slot: _WorkerSlot, key: TTSRequestKey, text: str
    ) -> tuple[tuple[bytes, ...], dict[str, object]]:
        process = slot.process
        if process is None:
            raise AdapterProcessError("worker is absent")
        request = {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "command": "synthesize",
            "key": key.as_dict(),
            "request_id": key.request_id,
            "text": text,
            "text_format": "plain",
            "normalization_version": SHAPING_VERSION,
            "audio": TTS_OUTPUT_AUDIO_FORMAT.as_dict(),
        }
        pcm_parts: list[bytes] = []
        total_bytes = 0
        chunk_count = 0
        final: dict[str, object] | None = None

        def validate_error_event(event: dict) -> None:
            if (
                set(event) != WORKER_ERROR_EVENT_FIELDS
                or event.get("protocol_version") != WORKER_PROTOCOL_VERSION
                or event.get("event") != "error"
                or not _worker_key_matches(event.get("key"), key)
                or type(event.get("request_id")) is not str
                or event.get("request_id") != key.request_id
                or event.get("error_class") not in WORKER_REQUEST_ERROR_CLASSES
            ):
                raise AdapterProcessError("invalid Silero worker error frame")

        for event in process.stream(
            request,
            SYNTHESIS_TIMEOUT_SECONDS,
            error_validator=validate_error_event,
        ):
            if event.get("protocol_version") != WORKER_PROTOCOL_VERSION:
                raise AdapterProcessError("Silero worker protocol mismatch")
            if (
                not _worker_key_matches(event.get("key"), key)
                or type(event.get("request_id")) is not str
                or event.get("request_id") != key.request_id
            ):
                raise AdapterProcessError("Silero correlation mismatch")
            kind = event.get("event")
            if kind == "chunk":
                if set(event) != WORKER_CHUNK_EVENT_FIELDS:
                    raise AdapterProcessError("invalid Silero worker chunk frame")
                if (
                    type(event.get("sequence")) is not int
                    or event.get("sequence") != chunk_count
                ):
                    raise AdapterProcessError("Silero chunk sequence mismatch")
                encoded = event.get("pcm_base64")
                if type(encoded) is not str:
                    raise AdapterProcessError("Silero chunk encoding mismatch")
                chunk = base64.b64decode(encoded, validate=True)
                if (
                    type(event.get("bytes")) is not int
                    or event.get("bytes") != len(chunk)
                    or not chunk
                    or len(chunk) % 2
                    or len(chunk) > 65_536
                ):
                    raise AdapterProcessError("Silero chunk size mismatch")
                chunk_count += 1
                total_bytes += len(chunk)
                if chunk_count > MAX_WORKER_CHUNKS or total_bytes > TTS_SEGMENT_MAX_BYTES:
                    raise AdapterProcessError("Silero output exceeds segment bound")
                pcm_parts.append(chunk)
            elif kind == "final":
                if set(event) != WORKER_FINAL_EVENT_FIELDS:
                    raise AdapterProcessError("invalid Silero worker final frame")
                if final is not None:
                    raise AdapterProcessError("duplicate Silero terminal")
                final = event
            else:
                raise AdapterProcessError("unknown Silero event")
        pcm = b"".join(pcm_parts)
        if final is None or not pcm:
            raise AdapterProcessError("Silero produced no terminal audio")
        expected = {
            "status": "success",
            "model_identity": MODEL_IDENTITY,
            "speaker": SPEAKER,
            "encoding": "pcm_s16le",
            "sample_rate_hz": 48_000,
            "channels": 1,
            "sample_width_bytes": 2,
            "chunk_count": chunk_count,
            "audio_bytes": len(pcm),
            "samples": len(pcm) // 2,
            "terminal_count": 1,
        }
        if any(
            not _protocol_value_equal(final.get(name), value)
            for name, value in expected.items()
        ):
            raise AdapterProcessError("Silero terminal totals/identity mismatch")
        duration = final.get("duration_ms")
        latency = final.get("latency_ms")
        if (
            not (
                type(duration) is int
                or type(duration) is float and math.isfinite(duration)
            )
            or not 0 < duration <= 15_000
            or not pcm_duration_ms_matches_samples(
                duration, len(pcm) // 2, TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz
            )
            or not (
                type(latency) is int
                or type(latency) is float and math.isfinite(latency)
            )
            or latency < 0
        ):
            raise AdapterProcessError("Silero terminal timing mismatch")
        metadata = {
            "worker_id": slot.worker_id,
            "audio_bytes": len(pcm),
            "samples": len(pcm) // 2,
            "duration_ms": float(duration),
            "latency_ms": float(latency),
            "chunk_count": chunk_count,
        }
        return tuple(pcm_parts), metadata

    def _quarantine(
        self, slot: _WorkerSlot, *, expected_state: str | None = None
    ) -> None:
        with self._condition:
            if expected_state is not None and slot.state != expected_state:
                raise StageFailure("tts", "silero_recovery_not_admissible")
            if slot.state in {"quarantining", "stopping"}:
                return
            if slot.state == "unhealthy" and slot.process is None:
                return
            slot.state = "quarantining"
            slot.active_key = None
            self.counters["worker_quarantines"] += 1
            self._condition.notify_all()
        self._stop_slot(slot, final_state="unhealthy")

    def _stop_slot(self, slot: _WorkerSlot, *, final_state: str = "stopped") -> None:
        with self._condition:
            process = slot.process
            slot.state = "stopping"
            slot.active_key = None
            self._condition.notify_all()
        try:
            if process is not None:
                process.close()
        finally:
            with self._condition:
                if slot.process is process:
                    slot.process = None
                    slot.ready_metadata = None
                slot.state = final_state
                self._condition.notify_all()

    def recover(self) -> dict[str, object]:
        """Explicit post-degradation recovery; never used as a request retry."""
        with self._condition:
            self._refresh_worker_health_locked()
            unhealthy = [slot for slot in self._slots if slot.state == "unhealthy"]
            if (
                self._closed
                or self._recovering
                or not unhealthy
                or any(
                    slot.state not in {"idle", "unhealthy"}
                    or slot.active_key is not None
                    for slot in self._slots
                )
            ):
                raise StageFailure("tts", "silero_recovery_not_admissible")
            self._recovering = True
            for slot in unhealthy:
                slot.state = "recovering"
        recovered: list[_WorkerSlot] = []
        try:
            for slot in unhealthy:
                try:
                    if slot.process is not None:
                        self._stop_slot(slot, final_state="recovering")
                    self._start_slot(slot, ready_state="recovering")
                    key = TTSRequestKey(
                        "recovery-session", 1, f"recovery-{slot.worker_id}", 1,
                        f"recovery-request-{slot.worker_id[-1]}", 0,
                    )
                    pcm, _ = self._request_on_slot(slot, key, "Готово.")
                    if not pcm:
                        raise StageFailure("tts", "silero_recovery_failed")
                    recovered.append(slot)
                except Exception as error:
                    if slot.process is not None or slot.state != "unhealthy":
                        self._quarantine(slot)
                    for warmed in recovered:
                        self._quarantine(warmed)
                    raise StageFailure("tts", "silero_recovery_failed") from error
            with self._condition:
                for slot in recovered:
                    slot.state = "idle"
                    slot.active_key = None
                    slot.last_idle = time.monotonic()
                self._recovering = False
                self._condition.notify_all()
            return self.readiness_metadata()
        finally:
            with self._condition:
                if self._recovering:
                    self._recovering = False
                    self._condition.notify_all()

    def quarantine_worker_for_controlled_check(self, worker_id: str) -> None:
        slot = next((item for item in self._slots if item.worker_id == worker_id), None)
        if slot is None:
            raise StageFailure("tts", "silero_recovery_not_admissible")
        self._quarantine(slot, expected_state="idle")

    def close(self) -> None:
        with self._condition:
            while self._starting or self._recovering:
                self._condition.wait()
            if self._closed:
                return
            self._closed = True
        for slot in self._slots:
            self._stop_slot(slot)
        with self._condition:
            self._condition.notify_all()


class SileroKseniyaTTS:
    version = TTS_V2_VERSION
    identity = MODEL_IDENTITY
    speaker = SPEAKER
    output_format = TTS_OUTPUT_AUDIO_FORMAT
    capabilities = {
        "complete_waveform": True,
        "cooperative_cancel": False,
        "max_parallel_requests": 2,
        "native_sample_rate_hz": 48_000,
        "output_sample_rate_hz": 48_000,
        "text_format": "plain",
        "normalization_version": SHAPING_VERSION,
    }

    def __init__(self, pool: SileroWorkerPool | None = None) -> None:
        self.pool = pool or SileroWorkerPool()
        self.observations = self.pool.observations
        self._observation_lock = threading.Lock()
        self._turn_observations: dict[
            tuple[str, int, str, int, str], list[dict[str, object]]
        ] = {}
        self.ready_metadata: dict[str, object] | None = None

    @staticmethod
    def _observation_key(key: TTSRequestKey) -> tuple[str, int, str, int, str]:
        return (
            key.session_id,
            key.stream_epoch,
            key.turn_id,
            key.turn_generation,
            key.request_id,
        )

    def take_turn_observations(
        self,
        session_id: str,
        stream_epoch: int,
        turn_id: str,
        turn_generation: int,
        request_id: str,
    ) -> tuple[dict[str, object], ...]:
        correlation = (
            session_id,
            stream_epoch,
            turn_id,
            turn_generation,
            request_id,
        )
        with self._observation_lock:
            return tuple(self._turn_observations.pop(correlation, ()))

    def trim_observations(self, limit: int) -> None:
        with self._observation_lock:
            if len(self.observations) > limit:
                del self.observations[:-limit]

    @property
    def process_ids(self) -> tuple[int, ...]:
        return self.pool.process_ids

    def health_snapshot(self) -> SileroPoolHealth:
        return self.pool.health_snapshot()

    def start(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        if cancellation is not None and cancellation.cancelled:
            raise StageFailure("tts", "selected_tts_cancelled")
        self.ready_metadata = self.pool.start()
        if cancellation is not None and cancellation.cancelled:
            self.pool.close()
            self.ready_metadata = None
            raise StageFailure("tts", "selected_tts_cancelled")
        return dict(self.ready_metadata)

    def warmup(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        metadata = self.start(cancellation)
        return {
            "worker_count": metadata["worker_count"],
            "process_ids": metadata["process_ids"],
            "discarded": True,
            "warmed": True,
        }

    def ready_for_admission(self) -> bool:
        return self.health_snapshot().ready_for_admission

    def create_turn_budget(self) -> SileroTurnBudget:
        return SileroTurnBudget.create()

    def stream_synthesize(
        self,
        *,
        session_id: str,
        stream_epoch: int,
        turn_id: str,
        turn_generation: int,
        request_id: str,
        segment_index: int,
        text: str,
        audio_format: AudioFormat = TTS_OUTPUT_AUDIO_FORMAT,
        cancellation: CancellationToken | None = None,
        turn_budget: SileroTurnBudget | None = None,
    ) -> Iterator[bytes]:
        if not all(valid_correlation_id(value) for value in (session_id, turn_id, request_id)):
            raise StageFailure("tts", "invalid_correlation_id")
        if audio_format != TTS_OUTPUT_AUDIO_FORMAT:
            raise StageFailure("tts", "unsupported_tts_audio_format")
        if (
            not isinstance(stream_epoch, int)
            or isinstance(stream_epoch, bool)
            or not 1 <= stream_epoch <= 1_000_000_000
            or not isinstance(turn_generation, int)
            or isinstance(turn_generation, bool)
            or not 1 <= turn_generation <= 1_000_000_000
            or not isinstance(segment_index, int)
            or isinstance(segment_index, bool)
            or not 0 <= segment_index <= 4_095
        ):
            raise StageFailure("tts", "invalid_correlation_id")
        shaped = shape_russian_tts(text)
        key = TTSRequestKey(
            session_id, stream_epoch, turn_id, turn_generation, request_id, segment_index
        )
        started = time.monotonic()
        chunks, metadata = self.pool.synthesize(key, shaped.synthesis_text, cancellation)
        total_bytes = sum(len(chunk) for chunk in chunks)
        budget = turn_budget or self.create_turn_budget()
        budget.consume(total_bytes)
        if cancellation is not None and cancellation.cancelled:
            self.pool.invalidate_key(key)
            raise StageFailure("tts", "selected_tts_cancelled")
        observation = {
            "backend": "silero",
            "model_identity": MODEL_IDENTITY,
            "speaker": SPEAKER,
            "worker_id": metadata["worker_id"],
            "segment_index": segment_index,
            "audio_bytes": total_bytes,
            "samples": total_bytes // 2,
            "duration_ms": metadata["duration_ms"],
            "synthesis_ms": metadata["latency_ms"],
            "adapter_ms": round((time.monotonic() - started) * 1_000, 3),
            "sample_rate_hz": 48_000,
            "stale_discarded": False,
            "retained_by_adapter": False,
        }
        with self._observation_lock:
            self.observations.append(observation)
            self._turn_observations.setdefault(
                self._observation_key(key), []
            ).append(observation)
        if cancellation is not None and cancellation.cancelled:
            self.pool.invalidate_key(key)
            raise StageFailure("tts", "selected_tts_cancelled")
        yield from chunks

    def invalidate_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        self.pool.invalidate_turn(session_id, stream_epoch, turn_id, turn_generation)

    def release_turn(
        self, session_id: str, stream_epoch: int, turn_id: str, turn_generation: int
    ) -> None:
        self.pool.release_turn(session_id, stream_epoch, turn_id, turn_generation)
        with self._observation_lock:
            for correlation in tuple(self._turn_observations):
                if correlation[:4] == (
                    session_id,
                    stream_epoch,
                    turn_id,
                    turn_generation,
                ):
                    self._turn_observations.pop(correlation, None)

    def cancel_request(self) -> None:
        # Request-scoped CancellationToken callbacks own invalidation.  A global
        # "cancel current" would be able to invalidate a newer replacement turn.
        return None

    def close(self) -> None:
        self.pool.close()

"""Typed privacy-safe observation, health, failure, and report contracts for Slice 8.

This module is the single Python owner of the observation/readiness shapes and of
failure-to-user-state mapping. Storage remains in :mod:`diagnostics`; realtime
control transports only the public dictionaries produced here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Iterable, Mapping, Sequence

from .contracts import valid_correlation_id

OBSERVATION_VERSION = "voice-agent.observation.v1"
HEALTH_REPORT_VERSION = "voice-agent.health-readiness.v1"
PREREGISTRATION_VERSION = "voice-agent.observability-preregistration.v1"

COMPONENT_NAMES = (
    "livekit",
    "controller",
    "stt",
    "selected_llm",
    "tts",
    "avatar_host",
    "active_module",
)
LIVENESS_STATES = frozenset({"alive", "dead", "unknown"})
READINESS_STATES = frozenset({"ready", "unready", "degraded", "unknown"})
USER_STATES = frozenset({"available", "unavailable", "degraded", "retrying", "interrupted"})
DEPENDENCY_CLASSES = frozenset({"hard", "soft"})
SAFE_SCALAR_TYPES = (bool, float, int, str, type(None))
MAX_SAFE_STRING = 512
MAX_OBSERVATION_FIELDS = 64
MAX_OBSERVATION_SEQUENCE = 1_000_000_000
_FORBIDDEN_FIELD_WORDS = frozenset({
    "audio", "content", "pcm", "prompt", "response", "secret", "text", "token", "transcript",
})
_SAFE_COUNT_OR_TIMING_KEYS = frozenset({
    "provider_time_to_first_token_ms",
    "tts_time_to_first_audio_ms",
    "endpoint_to_first_accepted_pcm_ms",
    "server_pcm_queue_max_blocks",
})
_SAFE_STRING_FIELD_KEYS = frozenset({
    "backend",
    "capture_kind",
    "component",
    "contract_version",
    "decision",
    "dependency_class",
    "event_type",
    "failure_class",
    "failure_code",
    "failure_matrix_id",
    "failure_stage",
    "model_identity",
    "outcome",
    "provider_identity",
    "provider_mode",
    "request_id",
    "resource_phase",
    "slowest_stage",
    "speaker",
    "stage",
    "user_state",
    "worker_id",
})


def safe_observation_key(value: str) -> bool:
    lowered = value.lower()
    return (
        bool(value)
        and len(value) <= 64
        and (
            lowered in _SAFE_COUNT_OR_TIMING_KEYS
            or not any(word in lowered for word in _FORBIDDEN_FIELD_WORDS)
        )
    )


def safe_observation_scalar(value: object, *, key: str | None = None) -> bool:
    if not isinstance(value, SAFE_SCALAR_TYPES):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, str) and (
        key not in _SAFE_STRING_FIELD_KEYS
        or len(value) > MAX_SAFE_STRING
        or "\n" in value
        or "\r" in value
        or (key == "dependency_class" and value not in DEPENDENCY_CLASSES)
    ):
        return False
    return True


def validate_observation(document: object) -> dict[str, object]:
    """Validate and return one observation document without accepting content."""
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "record_sequence",
        "wall_time",
        "monotonic_ms",
        "session_id",
        "turn_id",
        "stream_epoch",
        "stage",
        "event",
        "fields",
    }:
        raise ValueError("invalid observation shape")
    fields = document.get("fields")
    if (
        document.get("schema_version") != OBSERVATION_VERSION
        or type(document.get("record_sequence")) is not int
        or not 1 <= document["record_sequence"] <= MAX_OBSERVATION_SEQUENCE
        or not isinstance(document.get("wall_time"), str)
        or not 1 <= len(document["wall_time"]) <= 64
        or type(document.get("monotonic_ms")) not in {int, float}
        or not math.isfinite(document["monotonic_ms"])
        or document["monotonic_ms"] < 0
        or not isinstance(document.get("session_id"), str)
        or not valid_correlation_id(document["session_id"])
        or not isinstance(document.get("turn_id"), str)
        or not valid_correlation_id(document["turn_id"])
        or type(document.get("stream_epoch")) is not int
        or not 1 <= document["stream_epoch"] <= MAX_OBSERVATION_SEQUENCE
        or not isinstance(document.get("stage"), str)
        or not safe_observation_key(document["stage"])
        or not isinstance(document.get("event"), str)
        or not safe_observation_key(document["event"])
        or not isinstance(fields, dict)
        or len(fields) > MAX_OBSERVATION_FIELDS
        or any(
            not isinstance(key, str)
            or not safe_observation_key(key)
            or not safe_observation_scalar(value, key=key)
            for key, value in fields.items()
        )
    ):
        raise ValueError("invalid or content-bearing observation")
    return document


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    liveness: str
    readiness: str
    compatible: bool
    identity: str
    contract_version: str
    reason_code: str | None = None
    retry_count: int = 0
    retry_limit: int = 0

    def as_dict(self) -> dict[str, object]:
        if (
            self.component not in COMPONENT_NAMES
            or self.liveness not in LIVENESS_STATES
            or self.readiness not in READINESS_STATES
            or not isinstance(self.compatible, bool)
            or not 1 <= len(self.identity) <= 256
            or not 1 <= len(self.contract_version) <= 128
            or self.reason_code is not None and not safe_observation_key(self.reason_code)
            or type(self.retry_count) is not int
            or type(self.retry_limit) is not int
            or not 0 <= self.retry_count <= self.retry_limit <= 16
        ):
            raise ValueError("invalid component health")
        return {
            "component": self.component,
            "liveness": self.liveness,
            "readiness": self.readiness,
            "compatible": self.compatible,
            "identity": self.identity,
            "contract_version": self.contract_version,
            "reason_code": self.reason_code,
            "retry_count": self.retry_count,
            "retry_limit": self.retry_limit,
        }


@dataclass(frozen=True)
class HealthReport:
    components: tuple[ComponentHealth, ...]
    provider_mode: str = "local"
    external_transfer: bool = False
    automatic_fallback: bool = False
    stt_location: str = "local"
    tts_location: str = "local"
    auth_boundary: str = "tailnet"
    wake_enabled: bool = False
    selected_avatar_module: str = "mvp-eye-svg-v1"

    def as_dict(self) -> dict[str, object]:
        component_documents = [component.as_dict() for component in self.components]
        names = [document["component"] for document in component_documents]
        if len(names) != len(set(names)) or any(name not in COMPONENT_NAMES for name in names):
            raise ValueError("health report components are not unique")
        if (
            self.provider_mode != "local"
            or self.external_transfer
            or self.automatic_fallback
            or self.stt_location != "local"
            or self.tts_location != "local"
            or self.auth_boundary != "tailnet"
            or self.wake_enabled
            or self.selected_avatar_module != "mvp-eye-svg-v1"
        ):
            raise ValueError("health report changed a fixed product boundary")
        hard = {"livekit", "controller", "stt", "selected_llm", "tts"}
        ready = all(
            component["liveness"] == "alive"
            and component["readiness"] == "ready"
            and component["compatible"] is True
            for component in component_documents
            if component["component"] in hard
        ) and hard.issubset(names)
        return {
            "schema_version": HEALTH_REPORT_VERSION,
            "overall_readiness": "ready" if ready else "unready",
            "components": component_documents,
            "provider_mode": self.provider_mode,
            "external_transfer": self.external_transfer,
            "automatic_fallback": self.automatic_fallback,
            "stt_location": self.stt_location,
            "tts_location": self.tts_location,
            "auth_boundary": self.auth_boundary,
            "wake_enabled": self.wake_enabled,
            "selected_avatar_module": self.selected_avatar_module,
        }


@dataclass(frozen=True)
class FailureDisposition:
    matrix_id: str
    dependency_class: str
    user_state: str
    admit_turn: bool
    voice_continues: bool
    text_salvageable: bool
    retry_count: int = 0
    retry_limit: int = 0
    operator_only: bool = False

    def as_dict(self) -> dict[str, object]:
        if (
            not safe_observation_key(self.matrix_id)
            or self.dependency_class not in DEPENDENCY_CLASSES
            or self.user_state not in USER_STATES
            or type(self.retry_count) is not int
            or type(self.retry_limit) is not int
            or not 0 <= self.retry_count <= self.retry_limit <= 16
        ):
            raise ValueError("invalid failure disposition")
        return {
            "failure_matrix_id": self.matrix_id,
            "dependency_class": self.dependency_class,
            "user_state": self.user_state,
            "admit_turn": self.admit_turn,
            "voice_continues": self.voice_continues,
            "text_salvageable": self.text_salvageable,
            "retry_count": self.retry_count,
            "retry_limit": self.retry_limit,
            "operator_only": self.operator_only,
            "provider_switched": False,
            "external_transfer_changed": False,
            "stt_or_tts_moved_to_cloud": False,
            "auth_changed": False,
            "wake_changed": False,
            "avatar_selection_changed": False,
        }


@dataclass(frozen=True)
class FailureMatrixCase:
    matrix_id: str
    architecture_failure: str
    stage: str
    code: str
    disposition: FailureDisposition
    injection: str


# Exact architecture §7 row coverage. Code aliases below resolve runtime-specific
# failures into one of these dispositions without moving policy into adapters/UI.
FAILURE_MATRIX: tuple[FailureMatrixCase, ...] = (
    FailureMatrixCase("livekit_unavailable", "LiveKit unavailable", "livekit", "livekit_unavailable", FailureDisposition("livekit_unavailable", "hard", "retrying", False, False, False, 1, 10), "controlled_transport_loss"),
    FailureMatrixCase("microphone_capture_failure", "Host microphone capture or cleanup failure", "microphone_capture", "microphone_capture_failed", FailureDisposition("microphone_capture_failure", "hard", "unavailable", False, False, False), "controlled_capture_failure"),
    FailureMatrixCase("stt_unavailable", "STT unavailable or temporary-audio failure", "stt", "selected_stt_unavailable", FailureDisposition("stt_unavailable", "hard", "unavailable", False, False, False), "controlled_process_loss"),
    FailureMatrixCase("selected_llm_failure", "Selected LLM provider unavailable or fails", "llm_provider", "local_lfm_unavailable", FailureDisposition("selected_llm_failure", "hard", "unavailable", False, False, False), "controlled_provider_loss"),
    FailureMatrixCase("cloud_configuration_invalid", "Cloud credential, allowlist, or privacy metadata invalid", "llm_provider", "cloud_configuration_invalid", FailureDisposition("cloud_configuration_invalid", "hard", "unavailable", False, False, False), "inactive_cloud_configuration"),
    FailureMatrixCase("tts_failure", "TTS unavailable or fails", "tts", "selected_tts_unavailable", FailureDisposition("tts_failure", "hard", "degraded", False, True, True), "controlled_process_loss"),
    FailureMatrixCase("avatar_input_invalid", "Avatar input invalid, missing, late, or stale", "avatar_host", "avatar_input_invalid", FailureDisposition("avatar_input_invalid", "soft", "degraded", True, True, True), "malformed_avatar_input"),
    FailureMatrixCase("avatar_runtime_failure", "Avatar module or runtime failure", "active_module", "render_loop_failed", FailureDisposition("avatar_runtime_failure", "soft", "degraded", True, True, True), "controlled_render_fault"),
    FailureMatrixCase("client_disconnect", "Client disconnect", "client", "client_disconnected", FailureDisposition("client_disconnect", "hard", "interrupted", False, False, False), "controlled_transport_loss"),
    FailureMatrixCase("local_inference_crash", "GPU out of memory or local model process crash", "local_inference", "gpu_allocation_failed", FailureDisposition("local_inference_crash", "hard", "unavailable", False, False, False), "controlled_allocation_or_process_loss"),
    FailureMatrixCase("tailscale_unavailable", "Tailscale unavailable", "tailscale", "tailscale_unavailable", FailureDisposition("tailscale_unavailable", "soft", "degraded", True, True, True), "controlled_remote_path_loss"),
    FailureMatrixCase("late_duplicate_event", "Late or duplicate event", "control", "late_or_duplicate_event", FailureDisposition("late_duplicate_event", "soft", "degraded", True, True, True), "duplicate_event"),
)

_MATRIX_BY_ID = {case.matrix_id: case for case in FAILURE_MATRIX}
_RUNTIME_ALIASES = {
    "tts_backend_not_ready": "tts_failure",
    "silero_pool_not_ready": "tts_failure",
    "silero_pool_unavailable": "tts_failure",
    "selected_tts_unavailable": "tts_failure",
    "selected_tts_output_out_of_bounds": "tts_failure",
    "selected_stt_unavailable": "stt_unavailable",
    "temporary_audio_setup_failed": "stt_unavailable",
    "temporary_audio_cleanup_failed": "stt_unavailable",
    "invalid_stt_result": "stt_unavailable",
    "local_lfm_unavailable": "selected_llm_failure",
    "local_lfm_transport_error": "selected_llm_failure",
    "local_lfm_health_failed": "selected_llm_failure",
    "local_lfm_request_timeout": "selected_llm_failure",
    "empty_selected_provider_response": "selected_llm_failure",
    "cloud_configuration_invalid": "cloud_configuration_invalid",
    "client_disconnected": "client_disconnect",
    "reconnect_ack_timeout": "livekit_unavailable",
    "control_publish_failed": "livekit_unavailable",
    "microphone_stream_failed": "microphone_capture_failure",
    "microphone_device_failed": "microphone_capture_failure",
    "gpu_out_of_memory": "local_inference_crash",
    "gpu_allocation_failed": "local_inference_crash",
    "model_process_crashed": "local_inference_crash",
    "render_loop_failed": "avatar_runtime_failure",
    "avatar_input_invalid": "avatar_input_invalid",
    "tailscale_unavailable": "tailscale_unavailable",
    "late_or_duplicate_event": "late_duplicate_event",
}


def failure_disposition(stage: str, code: str) -> FailureDisposition:
    """Map one content-free runtime error to its user-visible failure policy."""
    matrix_id = _RUNTIME_ALIASES.get(code)
    if matrix_id is None:
        if stage == "tts":
            matrix_id = "tts_failure"
        elif stage == "stt":
            matrix_id = "stt_unavailable"
        elif stage == "llm_provider":
            matrix_id = "selected_llm_failure"
        elif stage in {"transport", "livekit", "publication"}:
            matrix_id = "livekit_unavailable"
        elif stage in {"input", "microphone_capture"}:
            matrix_id = "microphone_capture_failure"
        elif stage in {"avatar_host", "active_module"}:
            matrix_id = "avatar_runtime_failure"
        else:
            return FailureDisposition(
                "controller_failure", "hard", "unavailable", False, False, False
            )
    return _MATRIX_BY_ID[matrix_id].disposition


def failure_payload(stage: str, code: str) -> dict[str, object]:
    return failure_disposition(stage, code).as_dict()


@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_utilization_percent: float | None
    host_ram_used_mib: float | None
    process_rss_mib: float | None
    gpu_vram_used_mib: float | None
    gpu_utilization_percent: float | None

    def as_fields(self, *, phase: str) -> dict[str, object]:
        if not safe_observation_key(phase):
            raise ValueError("invalid resource phase")
        return {
            "resource_phase": phase,
            "cpu_utilization_percent": self.cpu_utilization_percent,
            "host_ram_used_mib": self.host_ram_used_mib,
            "process_rss_mib": self.process_rss_mib,
            "gpu_vram_used_mib": self.gpu_vram_used_mib,
            "gpu_utilization_percent": self.gpu_utilization_percent,
        }


def _default_proc_reader(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _default_gpu_reader() -> tuple[float, float] | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        first = completed.stdout.splitlines()[0].split(",")
        if len(first) != 2:
            return None
        return float(first[0].strip()), float(first[1].strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class ResourceSampler:
    """Small safe host sampler; failure yields missing numeric metadata, never content."""

    def __init__(
        self,
        *,
        proc_reader: Callable[[str], str] = _default_proc_reader,
        gpu_reader: Callable[[], tuple[float, float] | None] = _default_gpu_reader,
    ) -> None:
        self._proc_reader = proc_reader
        self._gpu_reader = gpu_reader
        self._last_cpu: tuple[int, int] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _mib_from_kib(value: str) -> float:
        return round(float(value.split()[0]) / 1024, 3)

    def sample(self) -> ResourceSnapshot:
        with self._lock:
            return self._sample_locked()

    def _sample_locked(self) -> ResourceSnapshot:
        cpu: float | None = None
        host_ram: float | None = None
        process_rss: float | None = None
        try:
            cpu_values = [int(item) for item in self._proc_reader("/proc/stat").splitlines()[0].split()[1:]]
            total = sum(cpu_values)
            idle = cpu_values[3] + (cpu_values[4] if len(cpu_values) > 4 else 0)
            if self._last_cpu is not None:
                total_delta = total - self._last_cpu[0]
                idle_delta = idle - self._last_cpu[1]
                if total_delta > 0 and 0 <= idle_delta <= total_delta:
                    cpu = round((1 - idle_delta / total_delta) * 100, 3)
            self._last_cpu = (total, idle)
        except (OSError, ValueError, IndexError):
            pass
        try:
            memory = {}
            for line in self._proc_reader("/proc/meminfo").splitlines():
                name, _, value = line.partition(":")
                memory[name] = value.strip()
            total_mib = self._mib_from_kib(memory["MemTotal"])
            available_mib = self._mib_from_kib(memory["MemAvailable"])
            host_ram = round(max(0.0, total_mib - available_mib), 3)
        except (OSError, KeyError, ValueError):
            pass
        try:
            for line in self._proc_reader("/proc/self/status").splitlines():
                if line.startswith("VmRSS:"):
                    process_rss = self._mib_from_kib(line.partition(":")[2].strip())
                    break
        except (OSError, ValueError):
            pass
        gpu = self._gpu_reader()
        return ResourceSnapshot(
            cpu,
            host_ram,
            process_rss,
            None if gpu is None else round(gpu[0], 3),
            None if gpu is None else round(gpu[1], 3),
        )


@dataclass(frozen=True)
class TurnTimeline:
    session_id: str
    turn_id: str
    terminal_outcome: str
    dependency_class: str | None
    failure_matrix_id: str | None
    failure_stage: str | None
    failure_code: str | None
    user_state: str | None
    endpoint_to_stt_final_ms: float | None
    provider_time_to_first_token_ms: float | None
    provider_completion_ms: float | None
    tts_time_to_first_audio_ms: float | None
    cancellation_latency_ms: float | None
    total_turn_ms: float | None
    slowest_stage: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "terminal_outcome": self.terminal_outcome,
            "dependency_class": self.dependency_class,
            "failure_matrix_id": self.failure_matrix_id,
            "failure_stage": self.failure_stage,
            "failure_code": self.failure_code,
            "user_state": self.user_state,
            "endpoint_to_stt_final_ms": self.endpoint_to_stt_final_ms,
            "provider_time_to_first_token_ms": self.provider_time_to_first_token_ms,
            "provider_completion_ms": self.provider_completion_ms,
            "tts_time_to_first_audio_ms": self.tts_time_to_first_audio_ms,
            "cancellation_latency_ms": self.cancellation_latency_ms,
            "total_turn_ms": self.total_turn_ms,
            "slowest_stage": self.slowest_stage,
        }


def _nearest_rank(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return round(ordered[rank - 1], 3)


def load_preregistration(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != PREREGISTRATION_VERSION
        or document.get("percentile_method") != "nearest-rank"
        or not isinstance(document.get("percentiles"), list)
        or any(type(value) is not int or not 1 <= value <= 100 for value in document["percentiles"])
        or not isinstance(document.get("timing_metrics_ms"), list)
        or not isinstance(document.get("resource_metrics"), list)
    ):
        raise ValueError("invalid observability preregistration")
    return document


def reconstruct_timelines(records: Iterable[Mapping[str, object]]) -> tuple[TurnTimeline, ...]:
    turns: dict[tuple[str, str], dict[str, object]] = {}
    for raw in records:
        document = validate_observation(dict(raw))
        turn_id = str(document["turn_id"])
        if turn_id == "session":
            continue
        key = (str(document["session_id"]), turn_id)
        state = turns.setdefault(key, {"events": {}, "terminal": "active", "fields": {}})
        events = state["events"]
        assert isinstance(events, dict)
        fields = document["fields"]
        assert isinstance(fields, dict)
        event_type = fields.get("event_type") if document["stage"] == "control" else None
        if isinstance(event_type, str) and event_type not in events:
            events[event_type] = float(document["monotonic_ms"])
        if isinstance(event_type, str) and event_type.startswith("turn.") and fields.get("terminal") is True:
            state["terminal"] = event_type.removeprefix("turn.")
        merged = state["fields"]
        assert isinstance(merged, dict)
        for name in (
            "dependency_class",
            "failure_matrix_id",
            "failure_stage",
            "failure_code",
            "user_state",
        ):
            value = fields.get(name)
            if isinstance(value, str):
                merged[name] = value
        for name in (
            "endpoint_to_stt_final_ms",
            "provider_time_to_first_token_ms",
            "provider_completion_ms",
            "tts_time_to_first_audio_ms",
            "cancellation_latency_ms",
            "total_turn_ms",
        ):
            value = fields.get(name)
            if type(value) in {int, float} and math.isfinite(value) and value >= 0:
                merged[name] = float(value)
    timelines: list[TurnTimeline] = []
    for (session_id, turn_id), state in sorted(turns.items()):
        fields = state["fields"]
        events = state["events"]
        assert isinstance(fields, dict) and isinstance(events, dict)
        endpoint = events.get("turn.listening")
        terminal_times = [
            value for name, value in events.items()
            if name in {"turn.completed", "turn.failed", "turn.interrupted"}
        ]
        if "total_turn_ms" not in fields and isinstance(endpoint, float) and terminal_times:
            fields["total_turn_ms"] = max(0.0, terminal_times[-1] - endpoint)
        stage_values = {
            "stt": fields.get("endpoint_to_stt_final_ms"),
            "selected_llm": fields.get("provider_completion_ms"),
            "tts": fields.get("tts_time_to_first_audio_ms"),
        }
        numeric_stages = {
            name: value for name, value in stage_values.items()
            if isinstance(value, float)
        }
        slowest = max(numeric_stages, key=numeric_stages.get) if numeric_stages else None
        timelines.append(TurnTimeline(
            session_id,
            turn_id,
            str(state["terminal"]),
            fields.get("dependency_class"),
            fields.get("failure_matrix_id"),
            fields.get("failure_stage"),
            fields.get("failure_code"),
            fields.get("user_state"),
            fields.get("endpoint_to_stt_final_ms"),
            fields.get("provider_time_to_first_token_ms"),
            fields.get("provider_completion_ms"),
            fields.get("tts_time_to_first_audio_ms"),
            fields.get("cancellation_latency_ms"),
            fields.get("total_turn_ms"),
            slowest,
        ))
    return tuple(timelines)


def percentile_report(
    records: Iterable[Mapping[str, object]], preregistration: Mapping[str, object]
) -> dict[str, object]:
    materialized = [dict(record) for record in records]
    timelines = reconstruct_timelines(materialized)
    percentiles = preregistration.get("percentiles")
    timing_names = preregistration.get("timing_metrics_ms")
    resource_names = preregistration.get("resource_metrics")
    if not isinstance(percentiles, list) or not isinstance(timing_names, list) or not isinstance(resource_names, list):
        raise ValueError("invalid observability preregistration")
    report: dict[str, object] = {
        "schema_version": "voice-agent.observation-report.v1",
        "turn_count": len(timelines),
        "timelines": [timeline.as_dict() for timeline in timelines],
        "percentile_method": "nearest-rank",
        "timing_percentiles_ms": {},
        "resource_percentiles": {},
    }
    timing_output = report["timing_percentiles_ms"]
    resource_output = report["resource_percentiles"]
    assert isinstance(timing_output, dict) and isinstance(resource_output, dict)
    for name in timing_names:
        if not isinstance(name, str):
            raise ValueError("invalid timing metric name")
        values = [
            float(value)
            for timeline in timelines
            if type(value := timeline.as_dict().get(name)) in {int, float}
        ]
        timing_output[name] = {
            f"p{percentile}": _nearest_rank(values, percentile)
            for percentile in percentiles
        }
    resource_values: dict[str, list[float]] = {
        str(name): [] for name in resource_names if isinstance(name, str)
    }
    for record in materialized:
        document = validate_observation(record)
        fields = document["fields"]
        assert isinstance(fields, dict)
        for name in resource_values:
            value = fields.get(name)
            if type(value) in {int, float} and math.isfinite(value):
                resource_values[name].append(float(value))
    for name, values in resource_values.items():
        resource_output[name] = {
            f"p{percentile}": _nearest_rank(values, percentile)
            for percentile in percentiles
        }
    return report

"""Optional pinned local Parakeet STT adapter backed by resident NeMo-Speech.cpp."""

from __future__ import annotations

from array import array
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time
import wave

from .contracts import AudioFormat, STT_VERSION, StageFailure, valid_correlation_id
from .local_stt import TemporaryAudioFailure, _cleanup_temporary_audio
from .process_adapter import AdapterProcess, AdapterProcessError, AdapterRequestError
from .tracer import CancellationToken

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE = Path("/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt")
MANIFEST = PROJECT_ROOT / "config" / "parakeet-stt-v1.json"
MODEL = CACHE / "model" / "parakeet-tdt-0.6b-v3.q8_0.gguf"
RUNTIME_REVISION = "9bc876635af36df537d9bc6d3f57ad1b76e4f74a"
RUNTIME = CACHE / "runtime" / f"nemo-speech-cpp-{RUNTIME_REVISION}-cpu"
LIBRARY = RUNTIME / "lib" / "libnemo_speech_asr_c.so.1"
TEMP = CACHE / "runtime" / "turn-temp"
LOGS = CACHE / "logs"
EXPECTED_FORMAT = AudioFormat()
MODEL_SIZE = 713_975_456
MODEL_SHA256 = "e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e"
MODEL_REVISION = "541d1f99c6b0c3cd0b11a95167540bb8edefd82b"
MAX_TRANSCRIPT_CHARS = 4_096
NEAR_SILENCE_PEAK = 16

RUNTIME_HASHES = {
    "bin/nemo-speech": "b8a48d5452fe4cf98a7e111cb6c5b814b1a22b5293c4aad6af0013d1242759b2",
    "lib/libggml-base.so.0.12.0": "d1ba07aa27499f7bf5d85198415687da64cefadf57afb40f8647c5e5644f55a5",
    "lib/libggml-cpu.so.0.12.0": "ef06c5f3268627a6e5e655c3c46f7f8ba04eb0f3ac109f9b213ab6f571600b09",
    "lib/libggml.so.0.12.0": "a5c1a8de5f660e68c5d28c739d302a36192d282ef516ebcc4bc84371eea5a204",
    "lib/libnemo_speech_asr.so": "588b2ed54aa2f7146bbcaaaa816c370be03d61cb74785f96be456fb80747d018",
    "lib/libnemo_speech_asr_c.so.1": "60e18393f020760b545e99d6f146a61d754b65676a740519272765f6c71a74d4",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_parakeet_artifacts() -> dict[str, object]:
    try:
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StageFailure("stt", "selected_stt_unavailable") from error
    expected_artifacts = {
        item.get("path"): item.get("sha256")
        for item in document.get("runtime", {}).get("artifacts", [])
        if isinstance(item, dict)
    }
    if not (
        document.get("schema_version") == "voice-agent.parakeet-stt.v1"
        and document.get("backend_name") == "parakeet"
        and document.get("cache_root") == str(CACHE)
        and document.get("model", {}).get("revision") == MODEL_REVISION
        and document.get("model", {}).get("sha256") == MODEL_SHA256
        and document.get("model", {}).get("bytes") == MODEL_SIZE
        and document.get("model", {}).get("language") == "ru"
        and document.get("model", {}).get("full_utterance_only") is True
        and document.get("runtime", {}).get("revision") == RUNTIME_REVISION
        and document.get("runtime", {}).get("backend") == "cpu"
        and document.get("runtime", {}).get("network_at_runtime") is False
        and document.get("runtime", {}).get("automatic_fallback") is False
        and expected_artifacts == RUNTIME_HASHES
    ):
        raise StageFailure("stt", "selected_stt_unavailable")
    try:
        if MODEL.stat().st_size != MODEL_SIZE or _sha256(MODEL) != MODEL_SHA256:
            raise StageFailure("stt", "selected_stt_unavailable")
        for relative, expected_hash in RUNTIME_HASHES.items():
            artifact = RUNTIME / relative
            if not artifact.is_file() or _sha256(artifact) != expected_hash:
                raise StageFailure("stt", "selected_stt_unavailable")
    except OSError as error:
        raise StageFailure("stt", "selected_stt_unavailable") from error
    return {
        "model_revision": MODEL_REVISION,
        "model_sha256": MODEL_SHA256,
        "runtime_revision": RUNTIME_REVISION,
        "runtime_backend": "cpu",
        "full_utterance_only": True,
        "automatic_fallback": False,
    }


def _environment() -> dict[str, str]:
    return {
        "HOME": str(CACHE / "home"),
        "XDG_CACHE_HOME": str(CACHE / "xdg"),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(PROJECT_ROOT),
        "LD_LIBRARY_PATH": str(RUNTIME / "lib"),
        "OMP_NUM_THREADS": "8",
    }


def _peak_amplitude(pcm: bytes) -> int:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return max((abs(value) for value in samples), default=0)


class ParakeetSTT:
    version = STT_VERSION
    identity = (
        f"nvidia/parakeet-tdt-0.6b-v3@{MODEL_REVISION}"
        f"#q8_0:{MODEL_SHA256}"
    )

    def __init__(self) -> None:
        self._process: AdapterProcess | None = None
        self._request_lock = threading.Lock()
        self._request_generation = 0
        self._active_request: int | None = None
        self._cancelled_requests: set[int] = set()
        self.ready_metadata: dict[str, object] | None = None
        self.observations: list[dict[str, object]] = []

    @property
    def process_id(self) -> int | None:
        process = self._process
        child = process.process if process is not None else None
        return child.pid if child is not None and child.poll() is None else None

    def _begin_request(self, cancellation: CancellationToken | None) -> int:
        with self._request_lock:
            self._request_generation += 1
            generation = self._request_generation
            self._active_request = generation
            if cancellation is not None and cancellation.cancelled:
                self._cancelled_requests.add(generation)
            return generation

    def _request_cancelled(self, generation: int) -> bool:
        with self._request_lock:
            return generation in self._cancelled_requests

    def _finish_request(self, generation: int) -> None:
        with self._request_lock:
            if self._active_request == generation:
                self._active_request = None
            self._cancelled_requests.discard(generation)

    def cancel_request(self, generation: int | None = None) -> float:
        with self._request_lock:
            active = self._active_request if generation is None else generation
            if active is None:
                return 0.0
            self._cancelled_requests.add(active)
            process = self._process
            self._process = None
            self.ready_metadata = None
        return process.cancel(timeout_seconds=0.25) if process is not None else 0.0

    def start(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        existing = self._process
        if existing is not None:
            if cancellation is not None and cancellation.cancelled:
                raise StageFailure("stt", "selected_stt_cancelled")
            if self.process_id is not None:
                return dict(self.ready_metadata or {})
            try:
                existing.close()
            except (AdapterProcessError, OSError):
                pass
            finally:
                if self._process is existing:
                    self._process = None
                    self.ready_metadata = None
        artifact_metadata = verify_parakeet_artifacts()
        command = [
            sys.executable,
            "-m",
            "benchmarks.slice2.runners.parakeet_nemo_speech_runner",
            "--model",
            str(MODEL),
            "--library",
            str(LIBRARY),
            "--runtime-revision",
            RUNTIME_REVISION,
        ]
        log = LOGS / f"parakeet-{time.monotonic_ns()}.stderr.log"
        process = AdapterProcess(command, log, _environment())
        self._process = process
        unregister = cancellation.register(process.cancel) if cancellation is not None else lambda: None
        try:
            if cancellation is not None and cancellation.cancelled:
                process.cancel()
            ready = process.start(60)
            expected_ready = (
                ready.get("runtime") == "nemo-speech.cpp"
                and ready.get("runtime_version") == "nemo-speech-asr 1.0.0"
                and ready.get("runtime_revision") == RUNTIME_REVISION
                and ready.get("device") == "cpu"
                and ready.get("compute_type") == "q8_0"
                and ready.get("language") == "ru"
                and ready.get("full_utterance_only") is True
                and ready.get("warmed") is False
            )
            if not expected_ready:
                raise AdapterProcessError("Parakeet runner readiness identity mismatch")
            self.ready_metadata = {**ready, **artifact_metadata}
            if cancellation is not None and cancellation.cancelled:
                self.cancel()
                raise StageFailure("stt", "selected_stt_cancelled")
        except StageFailure:
            raise
        except (AdapterProcessError, OSError) as error:
            try:
                process.close()
            finally:
                if self._process is process:
                    self._process = None
                self.ready_metadata = None
            raise StageFailure("stt", "selected_stt_unavailable") from error
        finally:
            unregister()
        return dict(self.ready_metadata)

    def warmup(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        self.start(cancellation)
        process = self._process
        process_id = self.process_id
        if process is None or process_id is None:
            raise StageFailure("stt", "selected_stt_unavailable")
        generation = self._begin_request(cancellation)
        unregister = (
            cancellation.register(lambda: self.cancel_request(generation))
            if cancellation is not None
            else lambda: None
        )
        try:
            response = process.request(
                {"operation": "warmup", "request_id": f"warmup-{generation:08d}"}, 60
            )
            if self._request_cancelled(generation):
                raise StageFailure("stt", "selected_stt_cancelled")
            if response.get("discarded") is not True:
                raise StageFailure("stt", "selected_stt_unavailable")
        except StageFailure:
            if self._request_cancelled(generation):
                try:
                    process.close()
                finally:
                    if self._process is process:
                        self._process = None
                    self.ready_metadata = None
            raise
        except (AdapterProcessError, AdapterRequestError, OSError) as error:
            cancelled = self._request_cancelled(generation)
            try:
                process.close()
            finally:
                if self._process is process:
                    self._process = None
                self.ready_metadata = None
            code = "selected_stt_cancelled" if cancelled else "selected_stt_unavailable"
            raise StageFailure("stt", code) from error
        finally:
            unregister()
            self._finish_request(generation)
        if self.process_id != process_id:
            raise StageFailure("stt", "selected_stt_unavailable")
        assert self.ready_metadata is not None
        self.ready_metadata["warmed"] = True
        return {
            "process_id": process_id,
            "discarded": True,
            "warmup_ms": response.get("warmup_ms"),
            "resident": True,
        }

    def _ensure_warmed(
        self, cancellation: CancellationToken | None = None
    ) -> None:
        metadata = self.ready_metadata
        if self._process is None or metadata is None or metadata.get("warmed") is not True:
            self.warmup(cancellation)
        if self._process is None or self.ready_metadata is None:
            raise StageFailure("stt", "selected_stt_unavailable")
        if self.ready_metadata.get("warmed") is not True:
            raise StageFailure("stt", "selected_stt_unavailable")

    def transcribe(
        self,
        *,
        session_id: str,
        turn_id: str,
        pcm: bytes,
        audio_format: AudioFormat,
        cancellation: CancellationToken | None = None,
    ) -> str:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("stt", "invalid_correlation_id")
        if audio_format != EXPECTED_FORMAT:
            raise StageFailure("stt", "unsupported_audio_format")
        if not pcm or len(pcm) % 2 or len(pcm) > 30 * 16_000 * 2:
            raise StageFailure("stt", "invalid_audio_payload")
        peak_amplitude = _peak_amplitude(pcm)
        if peak_amplitude <= NEAR_SILENCE_PEAK:
            raise StageFailure("stt", "empty_transcript")
        try:
            TEMP.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StageFailure("stt", "temporary_audio_setup_failed") from error
        path = TEMP / f"{session_id}-{turn_id}-{time.monotonic_ns()}.wav"
        if cancellation is not None and cancellation.cancelled:
            raise StageFailure("stt", "selected_stt_cancelled")
        self._ensure_warmed(cancellation)
        process = self._process
        if process is None:
            raise StageFailure("stt", "selected_stt_unavailable")
        generation = self._begin_request(cancellation)
        unregister = (
            cancellation.register(lambda: self.cancel_request(generation))
            if cancellation is not None
            else lambda: None
        )
        started = time.monotonic()
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(pcm)
            response = process.request(
                {
                    "operation": "transcribe",
                    "request_id": f"{session_id}-{turn_id}",
                    "audio_path": str(path),
                },
                60,
            )
            if self._request_cancelled(generation):
                raise StageFailure("stt", "selected_stt_cancelled")
        except StageFailure:
            if self._request_cancelled(generation):
                try:
                    process.close()
                finally:
                    if self._process is process:
                        self._process = None
                    self.ready_metadata = None
            raise
        except AdapterRequestError as error:
            raise StageFailure("stt", "selected_stt_unavailable") from error
        except (AdapterProcessError, OSError) as error:
            cancelled = self._request_cancelled(generation)
            if self._process is process:
                self._process = None
                self.ready_metadata = None
            try:
                process.close()
            except (OSError, AdapterProcessError):
                pass
            code = "selected_stt_cancelled" if cancelled else "selected_stt_unavailable"
            raise StageFailure("stt", code) from error
        finally:
            unregister()
            self._finish_request(generation)
            _cleanup_temporary_audio(path)
        transcript = response.get("hypothesis")
        confidence = response.get("confidence")
        finalization_ms = response.get("finalization_ms")
        realtime_factor = response.get("realtime_factor")
        if (
            not isinstance(transcript, str)
            or not transcript.strip()
            or len(transcript) > MAX_TRANSCRIPT_CHARS
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not isinstance(finalization_ms, (int, float))
            or isinstance(finalization_ms, bool)
            or not math.isfinite(float(finalization_ms))
            or float(finalization_ms) < 0
            or not isinstance(realtime_factor, (int, float))
            or isinstance(realtime_factor, bool)
            or not math.isfinite(float(realtime_factor))
            or float(realtime_factor) < 0
        ):
            raise StageFailure("stt", "empty_transcript" if isinstance(transcript, str) else "invalid_stt_result")
        self.observations.append({
            "session_id_present": bool(session_id),
            "turn_id_present": bool(turn_id),
            "input_bytes": len(pcm),
            "audio_duration_ms": len(pcm) / 2 / 16_000 * 1_000,
            "input_peak_amplitude": peak_amplitude,
            "latency_ms": (time.monotonic() - started) * 1_000,
            "runtime_finalization_ms": float(finalization_ms),
            "realtime_factor": float(realtime_factor),
            "temporary_audio_retained": False,
            "identity": self.identity,
            "process_id": self.process_id,
        })
        return transcript.strip()

    def cancel(self) -> float:
        with self._request_lock:
            process = self._process
            self._process = None
            self.ready_metadata = None
        if process is None:
            return 0.0
        latency = process.cancel()
        process.close()
        return latency

    def close(self) -> None:
        self.cancel()


__all__ = [
    "CACHE",
    "LIBRARY",
    "MANIFEST",
    "MODEL",
    "ParakeetSTT",
    "RUNTIME",
    "TemporaryAudioFailure",
    "verify_parakeet_artifacts",
]

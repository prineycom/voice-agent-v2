"""Selected local Whisper STT adapter with bounded temporary audio retention."""

from __future__ import annotations

from pathlib import Path
import os
import threading
import time
import wave

from .contracts import AudioFormat, STT_VERSION, StageFailure, valid_correlation_id
from .v2_audio import INPUT_AUDIO_FORMAT
from .process_adapter import AdapterProcess, AdapterProcessError, AdapterRequestError
from .tracer import CancellationToken

CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
MODEL = CACHE / "artifacts" / "stt-whisper-large-v3-turbo"
VENV = CACHE / "runtime" / "stt-tts-venv"
_TASK_RUNTIME_ROOT = os.environ.get("VOICE_AGENT_TASK_RUNTIME_ROOT")
TEMP = (
    Path(_TASK_RUNTIME_ROOT) / "stt-temp"
    if _TASK_RUNTIME_ROOT
    else CACHE / "runtime" / "turn-temp"
)
LOGS = (
    Path(_TASK_RUNTIME_ROOT) / "stt-logs"
    if _TASK_RUNTIME_ROOT
    else CACHE / "raw" / "service-logs"
)
EXPECTED_FORMAT = INPUT_AUDIO_FORMAT


class TemporaryAudioFailure(StageFailure):
    def __init__(self, *, input_retained: bool) -> None:
        super().__init__("stt", "temporary_audio_cleanup_failed")
        self.input_retained = input_retained


def _cleanup_temporary_audio(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        return
    except OSError:
        pass
    try:
        with path.open("r+b") as retained:
            retained.truncate(0)
    except OSError:
        pass
    try:
        path.unlink(missing_ok=True)
        return
    except OSError as error:
        try:
            input_retained = path.stat().st_size > 0
        except OSError:
            input_retained = True
        raise TemporaryAudioFailure(input_retained=input_retained) from error


def _environment() -> dict[str, str]:
    packages = VENV / "lib" / "python3.14" / "site-packages"
    libraries = [packages / "nvidia" / name / "lib" for name in ("cublas", "cudnn", "cuda_nvrtc")]
    return {
        "HOME": str(Path(_TASK_RUNTIME_ROOT) / "stt-home") if _TASK_RUNTIME_ROOT else str(CACHE / "home"),
        "XDG_CACHE_HOME": str(Path(_TASK_RUNTIME_ROOT) / "stt-xdg") if _TASK_RUNTIME_ROOT else str(CACHE / "xdg"),
        "HF_HOME": str(CACHE / "huggingface"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LD_LIBRARY_PATH": ":".join(str(path) for path in libraries),
    }


class WhisperSTT:
    version = STT_VERSION
    identity = "mobiuslabsgmbh/faster-whisper-large-v3-turbo@0e7a9a46fd2ec6300297f1665cd1b1f2c8b15c11"

    def __init__(self) -> None:
        self._process: AdapterProcess | None = None
        self._request_lock = threading.Lock()
        self._request_generation = 0
        self._active_request: int | None = None
        self._cancelled_requests: set[int] = set()
        self.ready_metadata: dict | None = None
        self.observations: list[dict] = []

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

    def cancel_request(self, generation: int | None = None) -> None:
        with self._request_lock:
            active = self._active_request if generation is None else generation
            if active is not None:
                self._cancelled_requests.add(active)

    def _finish_request(self, generation: int) -> None:
        with self._request_lock:
            if self._active_request == generation:
                self._active_request = None
            self._cancelled_requests.discard(generation)

    def start(self, cancellation: CancellationToken | None = None) -> dict:
        if self._process is not None:
            if cancellation is not None and cancellation.cancelled:
                raise StageFailure("stt", "selected_stt_cancelled")
            return dict(self.ready_metadata or {})
        command = [
            str(VENV / "bin" / "python"), "-m", "benchmarks.slice2.runners.faster_whisper_runner",
            "--model", str(MODEL), "--device", "cuda", "--compute-type", "float16",
        ]
        log = LOGS / f"whisper-{time.monotonic_ns()}.stderr.log"
        process = AdapterProcess(command, log, _environment())
        self._process = process
        unregister = (
            cancellation.register(process.cancel)
            if cancellation is not None
            else lambda: None
        )
        try:
            if cancellation is not None and cancellation.cancelled:
                process.cancel()
            self.ready_metadata = process.start(60)
            if cancellation is not None and cancellation.cancelled:
                self.cancel()
                raise StageFailure("stt", "selected_stt_cancelled")
        except (AdapterProcessError, OSError) as error:
            try:
                process.close()
            finally:
                self._process = None
                self.ready_metadata = None
            raise StageFailure("stt", "selected_stt_unavailable") from error
        finally:
            unregister()
        return dict(self.ready_metadata)

    def warmup(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        """Run the public inference path once and discard its content result."""
        self.start(cancellation)
        process_id = self.process_id
        if process_id is None:
            raise StageFailure("stt", "selected_stt_unavailable")
        try:
            self.transcribe(
                session_id="warmup-session",
                turn_id="warmup-turn",
                pcm=b"\0\0" * 16_000,
                audio_format=EXPECTED_FORMAT,
                cancellation=cancellation,
            )
        except StageFailure as error:
            if error.code != "empty_transcript":
                raise
        if self.process_id != process_id:
            raise StageFailure("stt", "selected_stt_unavailable")
        return {"process_id": process_id, "discarded": True}

    def transcribe(
        self, *, session_id: str, turn_id: str, pcm: bytes, audio_format: AudioFormat,
        cancellation: CancellationToken | None = None,
    ) -> str:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("stt", "invalid_correlation_id")
        if audio_format != EXPECTED_FORMAT:
            raise StageFailure("stt", "unsupported_audio_format")
        if not pcm or len(pcm) % 2 or len(pcm) > 30 * 16_000 * 2:
            raise StageFailure("stt", "invalid_audio_payload")
        try:
            TEMP.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StageFailure("stt", "temporary_audio_setup_failed") from error
        path = TEMP / f"{session_id}-{turn_id}-{time.monotonic_ns()}.wav"
        if cancellation is not None and cancellation.cancelled:
            raise StageFailure("stt", "selected_stt_cancelled")
        self.start(cancellation)
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
                {"operation": "transcribe", "request_id": f"{session_id}-{turn_id}", "audio_path": str(path)},
                60,
            )
            if self._request_cancelled(generation):
                raise StageFailure("stt", "selected_stt_cancelled")
        except AdapterRequestError as error:
            raise StageFailure("stt", "selected_stt_unavailable") from error
        except (AdapterProcessError, OSError) as error:
            cancelled = self._request_cancelled(generation)
            if not cancelled:
                self.cancel()
            code = "selected_stt_cancelled" if cancelled else "selected_stt_unavailable"
            raise StageFailure("stt", code) from error
        finally:
            unregister()
            self._finish_request(generation)
            _cleanup_temporary_audio(path)
        transcript = response.get("hypothesis")
        if not isinstance(transcript, str):
            raise StageFailure("stt", "invalid_stt_result")
        if not transcript.strip():
            raise StageFailure("stt", "empty_transcript")
        self.observations.append({
            "session_id": session_id, "turn_id": turn_id,
            "session_id_present": bool(session_id), "turn_id_present": bool(turn_id),
            "input_bytes": len(pcm), "audio_duration_ms": len(pcm) / 2 / 16_000 * 1000,
            "latency_ms": (time.monotonic() - started) * 1000,
            "temporary_audio_retained": False, "identity": self.identity,
        })
        return transcript.strip()

    def cancel(self) -> float:
        process = self._process
        if process is None:
            return 0.0
        latency = process.cancel()
        if getattr(process, "process", None) is not None:
            process.close()
        self._process = None
        self.ready_metadata = None
        return latency

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None
            self.ready_metadata = None

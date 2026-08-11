"""Selected local Whisper STT adapter with bounded temporary audio retention."""

from __future__ import annotations

from pathlib import Path
import time
import wave

from .contracts import AudioFormat, STT_VERSION, StageFailure, valid_correlation_id
from .process_adapter import AdapterProcess, AdapterProcessError

CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
MODEL = CACHE / "artifacts" / "stt-whisper-large-v3-turbo"
VENV = CACHE / "runtime" / "stt-tts-venv"
TEMP = CACHE / "runtime" / "turn-temp"
LOGS = CACHE / "raw" / "service-logs"
EXPECTED_FORMAT = AudioFormat()


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
        "HOME": str(CACHE / "home"), "XDG_CACHE_HOME": str(CACHE / "xdg"),
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
        self.ready_metadata: dict | None = None
        self.observations: list[dict] = []

    def start(self) -> dict:
        if self._process is not None:
            return dict(self.ready_metadata or {})
        command = [
            str(VENV / "bin" / "python"), "-m", "benchmarks.slice2.runners.faster_whisper_runner",
            "--model", str(MODEL), "--device", "cuda", "--compute-type", "float16",
        ]
        log = LOGS / f"whisper-{time.monotonic_ns()}.stderr.log"
        process = AdapterProcess(command, log, _environment())
        self._process = process
        try:
            self.ready_metadata = process.start(60)
        except (AdapterProcessError, OSError) as error:
            try:
                process.close()
            finally:
                self._process = None
                self.ready_metadata = None
            raise StageFailure("stt", "selected_stt_unavailable") from error
        return dict(self.ready_metadata)

    def transcribe(self, *, session_id: str, turn_id: str, pcm: bytes, audio_format: AudioFormat) -> str:
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
        self.start()
        process = self._process
        if process is None:
            raise StageFailure("stt", "selected_stt_unavailable")
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
        except (AdapterProcessError, OSError) as error:
            raise StageFailure("stt", "selected_stt_unavailable") from error
        finally:
            _cleanup_temporary_audio(path)
        transcript = response.get("hypothesis")
        if not isinstance(transcript, str) or not transcript.strip():
            raise StageFailure("stt", "empty_transcript")
        self.observations.append({
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

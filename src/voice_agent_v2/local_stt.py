"""Selected local Whisper STT adapter with bounded temporary audio retention."""

from __future__ import annotations

from pathlib import Path
import time
import wave

from .contracts import AudioFormat, STT_VERSION, StageFailure
from .process_adapter import AdapterProcess, AdapterProcessError

CACHE = Path("/home/priney/.cache/voice-agent-v2/slice-2")
MODEL = CACHE / "artifacts" / "stt-whisper-large-v3-turbo"
VENV = CACHE / "runtime" / "stt-tts-venv"
TEMP = CACHE / "runtime" / "turn-temp"
LOGS = CACHE / "raw" / "service-logs"
EXPECTED_FORMAT = AudioFormat()


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
        self._process = AdapterProcess(command, log, _environment())
        self.ready_metadata = self._process.start(60)
        return dict(self.ready_metadata)

    def transcribe(self, *, session_id: str, turn_id: str, pcm: bytes, audio_format: AudioFormat) -> str:
        if audio_format != EXPECTED_FORMAT:
            raise StageFailure("stt", "unsupported_audio_format")
        if not pcm or len(pcm) % 2:
            raise StageFailure("stt", "invalid_audio_payload")
        self.start()
        assert self._process is not None
        TEMP.mkdir(parents=True, exist_ok=True)
        path = TEMP / f"{session_id}-{turn_id}-{time.monotonic_ns()}.wav"
        started = time.monotonic()
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(pcm)
            response = self._process.request(
                {"operation": "transcribe", "request_id": f"{session_id}-{turn_id}", "audio_path": str(path)},
                60,
            )
        except (AdapterProcessError, OSError) as error:
            raise StageFailure("stt", "selected_stt_unavailable") from error
        finally:
            path.unlink(missing_ok=True)
        transcript = response.get("hypothesis")
        if not isinstance(transcript, str) or not transcript.strip():
            raise StageFailure("stt", "empty_transcript")
        self.observations.append({
            "session_id_present": bool(session_id), "turn_id_present": bool(turn_id),
            "input_bytes": len(pcm), "audio_duration_ms": len(pcm) / 2 / 16_000 * 1000,
            "latency_ms": (time.monotonic() - started) * 1000,
            "temporary_audio_retained": path.exists(), "identity": self.identity,
        })
        return transcript.strip()

    def cancel(self) -> float:
        if self._process is None:
            return 0.0
        latency = self._process.cancel()
        self._process.close()
        self._process = None
        self.ready_metadata = None
        return latency

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None
            self.ready_metadata = None

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.parakeet_stt import ParakeetSTT
from voice_agent_v2.slice6_config import Slice6ConfigurationError, Slice6Settings
from voice_agent_v2.stt_backend import create_stt_backend
from voice_agent_v2.tracer import CancellationToken


class Child:
    pid = 7301

    @staticmethod
    def poll():
        return None


class StubProcess:
    def __init__(self) -> None:
        self.process = Child()
        self.cancelled = threading.Event()
        self.last_path: Path | None = None
        self.closed = False

    def start(self, _timeout: float) -> dict[str, object]:
        return {
            "event": "ready",
            "load_ms": 1.0,
            "runtime": "nemo-speech.cpp",
            "runtime_version": "nemo-speech-asr 1.0.0",
            "runtime_revision": "9bc876635af36df537d9bc6d3f57ad1b76e4f74a",
            "device": "cpu",
            "compute_type": "q8_0",
            "language": "ru",
            "full_utterance_only": True,
            "warmed": False,
        }

    def request(self, value: dict[str, object], _timeout: float) -> dict[str, object]:
        if value["operation"] == "warmup":
            return {
                "event": "final", "request_id": value["request_id"],
                "discarded": True, "warmup_ms": 2.0,
            }
        self.last_path = Path(str(value["audio_path"]))
        if not self.last_path.is_file():
            raise AssertionError("temporary WAV is absent")
        return {
            "event": "final",
            "request_id": value["request_id"],
            "hypothesis": "Публичный тест.",
            "confidence": 1.0,
            "finalization_ms": 3.0,
            "realtime_factor": 0.1,
        }

    def cancel(self, timeout_seconds: float = 0.25) -> float:
        del timeout_seconds
        self.cancelled.set()
        return 4.0

    def close(self) -> None:
        self.closed = True


class BlockingProcess(StubProcess):
    def request(self, value: dict[str, object], _timeout: float) -> dict[str, object]:
        del value
        self.cancelled.wait(1)
        raise OSError("injected cancellation")


class ParakeetContractTests(unittest.TestCase):
    def test_backend_factory_keeps_whisper_default_and_requires_explicit_parakeet(self) -> None:
        self.assertEqual(type(create_stt_backend("whisper")).__name__, "WhisperSTT")
        self.assertIsInstance(create_stt_backend("parakeet"), ParakeetSTT)
        with self.assertRaises(ValueError):
            create_stt_backend("automatic")

    def test_slice6_backend_configuration_is_closed_and_defaults_to_whisper(self) -> None:
        values = {
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "s" * 32,
            "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
            "LIVEKIT_PUBLIC_URL": "wss://voice.test.ts.net:7443",
            "SLICE6_APP_PUBLIC_URL": "https://voice.test.ts.net:8443",
        }
        self.assertEqual(Slice6Settings.from_environment(values).stt_backend, "whisper")
        self.assertEqual(
            Slice6Settings.from_environment({**values, "VOICE_AGENT_STT_BACKEND": "parakeet"}).stt_backend,
            "parakeet",
        )
        with self.assertRaises(Slice6ConfigurationError):
            Slice6Settings.from_environment({**values, "VOICE_AGENT_STT_BACKEND": "fallback"})

    def test_transcription_uses_exact_audio_contract_and_retains_no_content(self) -> None:
        stt = ParakeetSTT()
        process = StubProcess()
        stt._process = process
        stt.ready_metadata = {"event": "ready", "warmed": True}
        with tempfile.TemporaryDirectory() as directory, patch(
            "voice_agent_v2.parakeet_stt.TEMP", Path(directory)
        ):
            transcript = stt.transcribe(
                session_id="session-test-0001",
                turn_id="turn-test-0001",
                pcm=b"\xe8\x03" * 1_600,
                audio_format=AudioFormat(),
            )
            self.assertEqual(transcript, "Публичный тест.")
            self.assertFalse(list(Path(directory).iterdir()))
        observation = stt.observations[-1]
        self.assertNotIn("transcript", observation)
        self.assertNotIn("text", observation)
        self.assertFalse(observation["temporary_audio_retained"])
        self.assertEqual(observation["process_id"], 7301)

    def test_format_correlation_and_near_silence_fail_before_runtime(self) -> None:
        cases = (
            {"session_id": "../escape", "audio_format": AudioFormat(), "pcm": b"\xe8\x03" * 160},
            {"session_id": "session-test", "audio_format": AudioFormat(sample_rate_hz=24_000), "pcm": b"\xe8\x03" * 160},
            {"session_id": "session-test", "audio_format": AudioFormat(), "pcm": b"\x08\x00" * 160},
        )
        for values in cases:
            with self.subTest(values=values):
                stt = ParakeetSTT()
                with self.assertRaises(StageFailure):
                    stt.transcribe(
                        session_id=values["session_id"], turn_id="turn-test",
                        pcm=values["pcm"], audio_format=values["audio_format"],
                    )
                self.assertIsNone(stt._process)

    def test_warmup_executes_discard_only_inference_and_preserves_process(self) -> None:
        stt = ParakeetSTT()
        process = StubProcess()
        with (
            patch("voice_agent_v2.parakeet_stt.verify_parakeet_artifacts", return_value={}),
            patch("voice_agent_v2.parakeet_stt.AdapterProcess", return_value=process),
        ):
            ready = stt.start()
            metadata = stt.warmup()
        self.assertFalse(ready["warmed"])
        self.assertTrue(stt.ready_metadata["warmed"])
        self.assertEqual(metadata["process_id"], 7301)
        self.assertTrue(metadata["discarded"])
        self.assertEqual(stt.process_id, 7301)

    def test_active_cancellation_is_bounded_and_destroys_only_that_residency(self) -> None:
        stt = ParakeetSTT()
        process = BlockingProcess()
        stt._process = process
        stt.ready_metadata = {"event": "ready", "warmed": True}
        token = CancellationToken()
        failures: list[StageFailure] = []

        def run() -> None:
            with tempfile.TemporaryDirectory() as directory, patch(
                "voice_agent_v2.parakeet_stt.TEMP", Path(directory)
            ):
                try:
                    stt.transcribe(
                        session_id="session-test-0001", turn_id="turn-test-0001",
                        pcm=b"\xe8\x03" * 1_600, audio_format=AudioFormat(),
                        cancellation=token,
                    )
                except StageFailure as error:
                    failures.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 0.5
        while stt._active_request is None and time.monotonic() < deadline:
            time.sleep(0.001)
        started = time.monotonic()
        token.cancel()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertLess((time.monotonic() - started) * 1_000, 300)
        self.assertEqual(failures[0].code, "selected_stt_cancelled")
        self.assertTrue(process.cancelled.is_set())
        self.assertIsNone(stt.process_id)

    @unittest.skipUnless(
        os.environ.get("VOICE_AGENT_VERIFY_PARAKEET") == "1",
        "requires pinned cache-local Parakeet assets",
    )
    def test_real_model_warmup_and_public_russian_fixture_are_resident(self) -> None:
        stt = ParakeetSTT()
        ready = stt.start()
        process_id = stt.process_id
        warmup = stt.warmup()
        fixture = Path(
            "/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt/fixtures/ruls-v1/ruls-test-0000.wav"
        )
        with wave.open(str(fixture), "rb") as source:
            pcm = source.readframes(source.getnframes())
        transcript = stt.transcribe(
            session_id="session-real-0001", turn_id="turn-real-0001",
            pcm=pcm, audio_format=AudioFormat(),
        )
        self.assertEqual(ready["runtime_backend"], "cpu")
        self.assertTrue(warmup["discarded"])
        self.assertTrue(transcript)
        self.assertEqual(stt.process_id, process_id)
        stt.close()


if __name__ == "__main__":
    unittest.main()

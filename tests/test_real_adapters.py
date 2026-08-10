from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.local_stt import WhisperSTT


class FailingProcess:
    def request(self, value: dict, timeout: float) -> dict:
        del value, timeout
        raise OSError("injected process loss")

    def close(self) -> None:
        return None


class StubProcess:
    def request(self, value: dict, timeout: float) -> dict:
        del timeout
        self.last_path = Path(value["audio_path"])
        if not self.last_path.exists():
            raise AssertionError("temporary WAV was not created")
        return {"event": "final", "request_id": value["request_id"], "hypothesis": "Публичный тест."}

    def cancel(self) -> float:
        return 12.0

    def close(self) -> None:
        return None


class LocalSTTContractTests(unittest.TestCase):
    def test_format_mismatch_fails_before_model_start(self) -> None:
        stt = WhisperSTT()
        with self.assertRaises(StageFailure) as raised:
            stt.transcribe(
                session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0",
                audio_format=AudioFormat(sample_rate_hz=24_000),
            )
        self.assertEqual(raised.exception.code, "unsupported_audio_format")
        self.assertIsNone(stt._process)

    def test_temporary_wav_is_deleted_and_observation_omits_transcript(self) -> None:
        stt = WhisperSTT()
        process = StubProcess()
        stt._process = process  # dependency seam; no GPU/process in unit tests
        stt.ready_metadata = {"event": "ready"}
        with tempfile.TemporaryDirectory() as directory, patch("voice_agent_v2.local_stt.TEMP", Path(directory)):
            transcript = stt.transcribe(
                session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                audio_format=AudioFormat(),
            )
            self.assertEqual(transcript, "Публичный тест.")
            self.assertFalse(list(Path(directory).iterdir()))
        self.assertNotIn("transcript", stt.observations[-1])
        self.assertFalse(stt.observations[-1]["temporary_audio_retained"])

    def test_process_loss_has_explicit_stage_failure_and_no_retained_audio(self) -> None:
        stt = WhisperSTT()
        stt._process = FailingProcess()
        stt.ready_metadata = {"event": "ready"}
        with tempfile.TemporaryDirectory() as directory, patch("voice_agent_v2.local_stt.TEMP", Path(directory)):
            with self.assertRaises(StageFailure) as raised:
                stt.transcribe(
                    session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                    audio_format=AudioFormat(),
                )
            self.assertEqual(raised.exception.code, "selected_stt_unavailable")
            self.assertFalse(list(Path(directory).iterdir()))

    def test_cancel_stops_selected_process(self) -> None:
        stt = WhisperSTT()
        stt._process = StubProcess()
        self.assertEqual(stt.cancel(), 12.0)
        self.assertIsNone(stt._process)


if __name__ == "__main__":
    unittest.main()

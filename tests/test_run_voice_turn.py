from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("voice_agent_run_voice_turn", ROOT / "scripts" / "run_voice_turn.py")
assert SPEC is not None and SPEC.loader is not None
run_voice_turn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_voice_turn)


class MicrophoneCaptureContractTests(unittest.TestCase):
    def test_pipewire_exit_one_with_exact_bounded_pcm_is_success_and_deleted(self) -> None:
        expected_bytes = 16_000 * 2
        seen = {}

        def record(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            Path(command[-1]).write_bytes(b"\0" * expected_bytes)
            return subprocess.CompletedProcess(command, 1)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(run_voice_turn, "CACHE", Path(directory)),
            patch.object(run_voice_turn.subprocess, "run", side_effect=record),
            redirect_stdout(StringIO()),
        ):
            pcm = run_voice_turn.microphone_pcm(1.0)
            leftovers = list((Path(directory) / "runtime" / "turn-temp").iterdir())
        self.assertEqual(len(pcm), expected_bytes)
        self.assertEqual(seen["command"][:9], [
            "pw-record", "--rate", "16000", "--channels", "1", "--format", "s16",
            "--raw", "--sample-count",
        ])
        self.assertEqual(seen["command"][9], "16000")
        self.assertFalse(seen["kwargs"]["check"])
        self.assertEqual(seen["kwargs"]["timeout"], 6.0)
        self.assertEqual(leftovers, [])

    def test_recorder_failures_are_content_free_and_temporary_file_is_deleted(self) -> None:
        cases = (
            (FileNotFoundError(), "recorder_unavailable"),
            (subprocess.TimeoutExpired(["pw-record"], 6), "recorder_timeout"),
            (subprocess.CompletedProcess(["pw-record"], 2), "recorder_nonzero"),
            (subprocess.CompletedProcess(["pw-record"], 0), "capture_output_missing"),
        )
        for outcome, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                tempfile.TemporaryDirectory() as directory,
                patch.object(run_voice_turn, "CACHE", Path(directory)),
                patch.object(
                    run_voice_turn.subprocess, "run",
                    side_effect=outcome if isinstance(outcome, BaseException) else None,
                    return_value=None if isinstance(outcome, BaseException) else outcome,
                ),
                redirect_stdout(StringIO()),
            ):
                with self.assertRaises(run_voice_turn.MicrophoneCaptureFailure) as raised:
                    run_voice_turn.microphone_pcm(1.0)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("pw-record", str(raised.exception))
                self.assertEqual(list((Path(directory) / "runtime" / "turn-temp").iterdir()), [])

    def test_cache_setup_failure_is_normalized_before_model_start(self) -> None:
        args = argparse.Namespace(microphone=True, input_wav=None, duration=8.0, play=True)
        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "not-a-directory"
            cache.write_bytes(b"")
            with (
                patch.object(run_voice_turn, "CACHE", cache),
                patch.object(run_voice_turn, "arguments", return_value=args),
                patch.object(run_voice_turn, "WhisperSTT") as stt,
                patch.object(run_voice_turn, "LiteLLMProvider") as llm,
                patch.object(run_voice_turn, "Qwen3TTS") as tts,
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                result = run_voice_turn.main()
        self.assertEqual(result, 2)
        self.assertIn("Terminal: turn.failed", stderr.getvalue())
        self.assertIn("Failure: microphone_capture/capture_setup_failed", stderr.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
        stt.assert_not_called()
        llm.assert_not_called()
        tts.assert_not_called()

    def test_cleanup_failure_erases_pcm_and_blocks_model_start(self) -> None:
        expected_bytes = 16_000 * 2
        args = argparse.Namespace(microphone=True, input_wav=None, duration=1.0, play=True)
        captured_path = None
        original_unlink = Path.unlink

        def record(command, **_kwargs):
            nonlocal captured_path
            captured_path = Path(command[-1])
            captured_path.write_bytes(b"\0" * expected_bytes)
            return subprocess.CompletedProcess(command, 0)

        def reject_capture_unlink(path, *, missing_ok=False):
            if path.name.startswith("microphone-"):
                raise PermissionError("capture cleanup blocked")
            return original_unlink(path, missing_ok=missing_ok)

        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(run_voice_turn, "CACHE", Path(directory)),
                patch.object(run_voice_turn, "arguments", return_value=args),
                patch.object(run_voice_turn.subprocess, "run", side_effect=record),
                patch.object(Path, "unlink", new=reject_capture_unlink),
                patch.object(run_voice_turn, "WhisperSTT") as stt,
                patch.object(run_voice_turn, "LiteLLMProvider") as llm,
                patch.object(run_voice_turn, "Qwen3TTS") as tts,
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                result = run_voice_turn.main()
                evidence_path = next((Path(directory) / "evidence").glob("*.json"))
                evidence = json.loads(evidence_path.read_text())
                self.assertIsNotNone(captured_path)
                self.assertEqual(captured_path.read_bytes(), b"")
        self.assertEqual(result, 2)
        self.assertIn("Failure: microphone_capture/capture_cleanup_failed", stderr.getvalue())
        self.assertIn("Retention: microphone=false", stderr.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
        self.assertFalse(evidence["input_retained"])
        stt.assert_not_called()
        llm.assert_not_called()
        tts.assert_not_called()

    def test_wrong_sized_success_is_explicit_failure(self) -> None:
        def short_record(command, **_kwargs):
            Path(command[-1]).write_bytes(b"\0\0")
            return subprocess.CompletedProcess(command, 0)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(run_voice_turn, "CACHE", Path(directory)),
            patch.object(run_voice_turn.subprocess, "run", side_effect=short_record),
            redirect_stdout(StringIO()),
        ):
            with self.assertRaises(run_voice_turn.MicrophoneCaptureFailure) as raised:
                run_voice_turn.microphone_pcm(1.0)
            self.assertEqual(raised.exception.code, "capture_output_size_mismatch")
            self.assertEqual(list((Path(directory) / "runtime" / "turn-temp").iterdir()), [])

    def test_main_normalizes_capture_failure_without_traceback_or_model_start(self) -> None:
        args = argparse.Namespace(microphone=True, input_wav=None, duration=8.0, play=True)
        stdout = StringIO()
        stderr = StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(run_voice_turn, "CACHE", Path(directory)),
            patch.object(run_voice_turn, "arguments", return_value=args),
            patch.object(
                run_voice_turn, "microphone_pcm",
                side_effect=run_voice_turn.MicrophoneCaptureFailure("recorder_nonzero"),
            ),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            result = run_voice_turn.main()
            evidence_paths = list((Path(directory) / "evidence").glob("*.json"))
            evidence = json.loads(evidence_paths[0].read_text())
        self.assertEqual(result, 2)
        self.assertIn("Terminal: turn.failed", stderr.getvalue())
        self.assertIn("Failure: microphone_capture/recorder_nonzero", stderr.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(len(evidence_paths), 1)
        self.assertEqual(evidence["terminal"], "turn.failed")
        self.assertEqual(evidence["stage"], "microphone_capture")
        self.assertEqual(evidence["error_class"], "recorder_nonzero")
        self.assertFalse(evidence["input_retained"])
        self.assertFalse(evidence["output_retained"])

    def test_main_normalizes_each_model_startup_failure(self) -> None:
        args = argparse.Namespace(microphone=True, input_wav=None, duration=8.0, play=False)
        cases = (
            ("readiness", "llm_provider", "capability_probe_unavailable"),
            ("stt_start", "stt", "selected_stt_unavailable"),
            ("tts_start", "tts", "selected_tts_unavailable"),
            ("turn", "llm_provider", "selected_provider_http_503"),
        )
        for location, stage, code in cases:
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                stdout = StringIO()
                stderr = StringIO()
                with (
                    patch.object(run_voice_turn, "CACHE", Path(directory)),
                    patch.object(run_voice_turn, "arguments", return_value=args),
                    patch.object(run_voice_turn, "microphone_pcm", return_value=b"\0\0" * 160),
                    patch.object(run_voice_turn, "WhisperSTT") as stt_class,
                    patch.object(run_voice_turn, "LiteLLMProvider") as llm_class,
                    patch.object(run_voice_turn, "Qwen3TTS") as tts_class,
                    patch.object(run_voice_turn, "RealTurnController") as controller_class,
                    redirect_stdout(stdout), redirect_stderr(stderr),
                ):
                    stt = stt_class.return_value
                    llm = llm_class.return_value
                    tts = tts_class.return_value
                    failure = run_voice_turn.StageFailure(stage, code)
                    if location == "readiness":
                        llm.readiness.side_effect = failure
                    elif location == "stt_start":
                        stt.start.side_effect = failure
                    elif location == "tts_start":
                        tts.start.side_effect = failure
                    else:
                        controller_class.return_value.run_turn.side_effect = failure
                    result = run_voice_turn.main()
                    evidence_path = next((Path(directory) / "evidence").glob("*.json"))
                    evidence = json.loads(evidence_path.read_text())
                self.assertEqual(result, 2)
                self.assertIn("Terminal: turn.failed", stderr.getvalue())
                self.assertIn(f"Failure: {stage}/{code}", stderr.getvalue())
                self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
                self.assertEqual(evidence["stage"], stage)
                self.assertEqual(evidence["error_class"], code)
                self.assertFalse(evidence["conversation_content_retained"])
                stt.close.assert_called_once_with()
                tts.close.assert_called_once_with()

    def test_failed_turn_result_does_not_print_or_persist_conversation_content(self) -> None:
        args = argparse.Namespace(microphone=True, input_wav=None, duration=8.0, play=False)
        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(run_voice_turn, "CACHE", Path(directory)),
                patch.object(run_voice_turn, "arguments", return_value=args),
                patch.object(run_voice_turn, "microphone_pcm", return_value=b"\0\0" * 160),
                patch.object(run_voice_turn, "WhisperSTT"),
                patch.object(run_voice_turn, "LiteLLMProvider"),
                patch.object(run_voice_turn, "Qwen3TTS"),
                patch.object(run_voice_turn, "RealTurnController") as controller_class,
                redirect_stdout(stdout), redirect_stderr(stderr),
            ):
                failed = type("FailedResult", (), {
                    "events": ({"type": "stt.final", "payload": {"transcript": "sensitive transcript"}},),
                    "terminal_event": {
                        "type": "turn.failed",
                        "payload": {
                            "stage": "stt", "code": "temporary_audio_cleanup_failed",
                            "input_retained": True,
                        },
                    },
                })()
                controller_class.return_value.run_turn.return_value = failed
                result = run_voice_turn.main()
                evidence_path = next((Path(directory) / "evidence").glob("*.json"))
                evidence_text = evidence_path.read_text()
        self.assertEqual(result, 2)
        self.assertIn("Failure: stt/temporary_audio_cleanup_failed", stderr.getvalue())
        self.assertIn("Retention: microphone=true", stderr.getvalue())
        self.assertNotIn("sensitive transcript", stdout.getvalue() + stderr.getvalue() + evidence_text)
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

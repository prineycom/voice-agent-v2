from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.diagnostics import MAX_TRACE_FILES, PrivacySafeTrace, TraceIdentity


class PrivacySafeTraceTests(unittest.TestCase):
    def test_writes_correlated_content_free_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = PrivacySafeTrace(path, TraceIdentity("session-test", "turn-test", 2))
            trace.emit("vad", "speech_started", {"probability": 0.812, "rms": 742})
            record = json.loads(path.read_text())
            self.assertEqual(record["session_id"], "session-test")
            self.assertEqual(record["turn_id"], "turn-test")
            self.assertEqual(record["stream_epoch"], 2)
            self.assertEqual(record["fields"], {"probability": 0.812, "rms": 742})

    def test_rejects_content_without_escaping_the_diagnostic_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            trace = PrivacySafeTrace(path, TraceIdentity("session-test"))
            for fields in ({"transcript": "secret words"}, {"token": "secret"}, {"failure": "x" * 513}):
                self.assertFalse(trace.emit("test", "rejected", fields))
            self.assertFalse(trace.emit("publication", "pcm_chunk", {"byte_count": 640}))
            self.assertEqual(trace.failure_counts["validation"], 4)
            self.assertTrue(trace.emit("session", "ready"))
            self.assertEqual(json.loads(path.read_text())["event"], "ready")

    def test_prunes_the_cross_session_trace_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(MAX_TRACE_FILES + 5):
                (root / f"old-{index:03d}.jsonl").write_text("{}\n")
            trace = PrivacySafeTrace(root / "current.jsonl", TraceIdentity("session-current"))
            trace.emit("session", "ready")
            files = list(root.glob("*.jsonl"))
            self.assertLessEqual(len(files), MAX_TRACE_FILES)
            self.assertIn(root / "current.jsonl", files)

    def test_diagnostic_write_failure_is_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = PrivacySafeTrace(Path(directory) / "trace.jsonl", TraceIdentity("session-test"))
            with patch("pathlib.Path.open", side_effect=OSError("disk full")):
                self.assertFalse(trace.emit("session", "ready"))
            self.assertFalse(trace.emit("session", "ignored_after_failure"))
            self.assertEqual(trace.failure_counts["write"], 1)
            self.assertEqual(trace.failure_counts["validation"], 0)
            self.assertFalse(trace.path.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from voice_agent_v2.diagnostics import PrivacySafeTrace, TraceIdentity


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

    def test_rejects_content_secrets_and_unbounded_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = PrivacySafeTrace(Path(directory) / "trace.jsonl", TraceIdentity("session-test"))
            for fields in ({"transcript": "secret words"}, {"token": "secret"}, {"failure": "x" * 513}):
                with self.assertRaises(ValueError):
                    trace.emit("test", "rejected", fields)


if __name__ == "__main__":
    unittest.main()

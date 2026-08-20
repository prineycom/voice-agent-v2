from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.runtime_config import load_production_runtime_config


class ProductionRuntimeConfigTests(unittest.TestCase):
    def document(self, root: Path) -> dict[str, object]:
        release = root / "release"
        return {
            "schema": "voice-agent.runtime-config.v1",
            "application_protocol": 1,
            "release_root": str(release),
            "executables": {
                "python": str(release / "runtime/python/bin/python3"),
                "livekit": str(release / "runtime/livekit/bin/livekit-server"),
                "llama": str(release / "runtime/llama/bin/llama-server"),
            },
            "models": {kind: str(root / "views" / kind) for kind in ("stt", "llm", "tts", "vad")},
            "paths": {
                "agent_data": str(root / "data/agent-environment"),
                "logs": str(root / "state/logs"),
                "state": str(root / "state/runtime"),
                "temp": str(root / "runtime/service"),
                "web": str(release / "web"),
            },
        }

    def test_one_generated_descriptor_owns_every_production_path(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value).resolve()
            filename = root / "runtime.json"
            filename.write_text(json.dumps(self.document(root)), encoding="utf-8")
            with patch.dict(os.environ, {"VOICE_AGENT_RUNTIME_CONFIG": str(filename)}, clear=True):
                observed = load_production_runtime_config()
            self.assertEqual(observed["release_root"], root / "release")
            self.assertEqual(set(observed["models"]), {"stt", "llm", "tts", "vad"})
            self.assertTrue(all(path.is_absolute() for path in observed["executables"].values()))

    def test_missing_unknown_or_escaping_production_authority_fails_closed(self) -> None:
        with patch.dict(os.environ, {"VOICE_AGENT_RUNTIME_CONFIG": "/missing/runtime.json"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                load_production_runtime_config()
        with tempfile.TemporaryDirectory() as value:
            root = Path(value).resolve(); filename = root / "runtime.json"; document = self.document(root)
            document["executables"]["python"] = str(root / "host-python")
            filename.write_text(json.dumps(document), encoding="utf-8")
            with patch.dict(os.environ, {"VOICE_AGENT_RUNTIME_CONFIG": str(filename)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "escaped"):
                    load_production_runtime_config()
            document = self.document(root); document["ambient_cache"] = str(root / "cache")
            filename.write_text(json.dumps(document), encoding="utf-8")
            with patch.dict(os.environ, {"VOICE_AGENT_RUNTIME_CONFIG": str(filename)}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "unsupported"):
                    load_production_runtime_config()


if __name__ == "__main__":
    unittest.main()

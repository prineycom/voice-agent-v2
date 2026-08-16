from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("VOICE_AGENT_VERIFY_SLICE6_RUNTIME") == "1",
    "requires the pinned Slice 6 runtime",
)
class Slice9RuntimeTests(unittest.TestCase):
    def test_registry_closes_admission_before_shutdown_drain(self) -> None:
        from tests.test_checkpoint_ab import load_runtime

        runtime = load_runtime()

        async def scenario() -> None:
            registry = object.__new__(runtime.SessionRegistry)
            registry.settings = SimpleNamespace(max_sessions=1)
            registry._controllers = {}
            registry._lock = asyncio.Lock()
            registry._accepting = False
            with self.assertRaisesRegex(RuntimeError, "draining"):
                await registry.create()

        asyncio.run(scenario())

    def test_public_status_reports_build_health_client_and_no_external_supervision(self) -> None:
        from voice_agent_v2.slice6_gateway import status as public_gateway_status

        health = json.loads(
            (ROOT / "contracts/fixtures/health-readiness.v1.json").read_text()
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            registry=SimpleNamespace(
                accepting=True,
                active_count=0,
                operational_health=lambda: health,
            ),
            settings=SimpleNamespace(build_id="a" * 40, release_id="b" * 24),
        )))
        report = asyncio.run(public_gateway_status(request))
        self.assertTrue(report["available"])
        self.assertEqual(report["build_id"], "a" * 40)
        self.assertEqual(report["release_id"], "b" * 24)
        self.assertEqual(report["health"], health)
        self.assertEqual(report["selected_avatar_module"], "mvp-eye-svg-v1")
        self.assertFalse(report["external_provider_supervised"])
        self.assertFalse(report["automatic_fallback"])


if __name__ == "__main__":
    unittest.main()

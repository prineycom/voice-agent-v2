from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


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

    def test_parent_owned_process_loss_is_immediately_unready(self) -> None:
        from voice_agent_v2 import livekit_runtime
        from voice_agent_v2.observability import ComponentHealth

        registry = object.__new__(livekit_runtime.SessionRegistry)
        registry.settings = SimpleNamespace(
            build_id="a" * 40,
            release_id="b" * 24,
            supervised_livekit_process="123:456",
            supervised_lfm_process="789:1011",
            max_sessions=1,
        )
        registry._accepting = True
        registry.runner = SimpleNamespace(readiness_components=lambda: (
            ComponentHealth(
                "stt", "alive", "ready", True, "whisper", "voice-agent.stt.v1",
            ),
            ComponentHealth(
                "selected_llm", "alive", "ready", True,
                "selected-local-lfm", "voice-agent.llm-provider.v1",
            ),
            ComponentHealth(
                "tts", "alive", "ready", True, "silero", "voice-agent.tts.v2",
            ),
        ))
        with patch.object(
            livekit_runtime, "supervised_process_alive", side_effect=(False, False),
        ):
            health = registry.operational_health()
        components = {row["component"]: row for row in health["components"]}
        self.assertEqual(health["overall_readiness"], "unready")
        self.assertEqual(components["livekit"]["liveness"], "dead")
        self.assertEqual(components["selected_llm"]["liveness"], "dead")

        registry.settings.supervised_livekit_process = None
        registry.settings.supervised_lfm_process = None
        missing_identity_health = registry.operational_health()
        self.assertEqual(missing_identity_health["overall_readiness"], "unready")

        registry.settings.supervised_livekit_process = "123:456"
        registry.settings.supervised_lfm_process = "789:1011"
        registry._controllers = {}
        registry._lock = asyncio.Lock()
        with (
            patch.object(livekit_runtime, "supervised_process_alive", return_value=False),
            patch.object(livekit_runtime, "LiveKitRoomController") as controller,
            self.assertRaisesRegex(RuntimeError, "unavailable"),
        ):
            asyncio.run(registry.create())
        controller.assert_not_called()

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

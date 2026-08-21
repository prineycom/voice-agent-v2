from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Slice9RuntimeTests(unittest.TestCase):
    def test_registry_closes_admission_before_shutdown_drain(self) -> None:
        from tests.support.realtime_fakes import load_runtime

        runtime = load_runtime()

        async def scenario() -> None:
            registry = object.__new__(runtime.SessionRegistry)
            registry.settings = SimpleNamespace(max_sessions=1)
            registry._controllers = {}
            registry._attempts = {}
            registry._attempt_by_session = {}
            registry._retired_attempts = {}
            registry._lock = asyncio.Lock()
            registry._accepting = False
            with self.assertRaisesRegex(RuntimeError, "draining"):
                await registry.create("attempt-draining")

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
        registry._attempts = {}
        registry._attempt_by_session = {}
        registry._retired_attempts = {}
        registry._lock = asyncio.Lock()
        with (
            patch.object(livekit_runtime, "supervised_process_alive", return_value=False),
            patch.object(livekit_runtime, "LiveKitRoomController") as controller,
            self.assertRaisesRegex(RuntimeError, "unavailable"),
        ):
            asyncio.run(registry.create("attempt-unready"))
        controller.assert_not_called()

    def test_fresh_livekit_and_lfm_probes_override_cached_ready_state(self) -> None:
        from voice_agent_v2 import livekit_runtime
        from voice_agent_v2.observability import ComponentHealth

        registry = object.__new__(livekit_runtime.SessionRegistry)
        registry.settings = SimpleNamespace(
            build_id="a" * 40,
            release_id="b" * 24,
            livekit_internal_url="ws://127.0.0.1:7880",
            supervised_livekit_process="123:456",
            supervised_lfm_process="789:1011",
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
        with (
            patch.object(livekit_runtime, "supervised_process_alive", return_value=True),
            patch.object(livekit_runtime, "livekit_endpoint_ready", return_value=True),
            patch.object(livekit_runtime, "local_lfm_endpoint_health", return_value="ready"),
        ):
            self.assertEqual(
                registry.operational_health()["overall_readiness"], "ready",
            )
        with (
            patch.object(livekit_runtime, "supervised_process_alive", return_value=True),
            patch.object(livekit_runtime, "livekit_endpoint_ready", return_value=False),
            patch.object(
                livekit_runtime, "local_lfm_endpoint_health", return_value="unavailable",
            ),
        ):
            health = registry.operational_health()
        components = {row["component"]: row for row in health["components"]}
        self.assertEqual(health["overall_readiness"], "unready")
        self.assertEqual(components["livekit"]["reason_code"], "livekit_unavailable")
        self.assertEqual(
            components["selected_llm"]["reason_code"], "local_lfm_unavailable",
        )

    def test_bounded_endpoint_probes_observe_current_listener_health(self) -> None:
        from voice_agent_v2 import livekit_runtime

        state = {"status": 200, "body": b"OK"}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = state["body"]
                self.send_response(state["status"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_arguments: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            self.assertTrue(
                livekit_runtime.livekit_endpoint_ready(f"ws://{host}:{port}")
            )
            state["body"] = b'{"status":"ok"}'
            self.assertFalse(
                livekit_runtime.livekit_endpoint_ready(f"ws://{host}:{port}")
            )
            self.assertEqual(
                livekit_runtime.local_lfm_endpoint_health(host, port), "ready",
            )
            state.update(status=503, body=b'{"status":"loading"}')
            self.assertFalse(
                livekit_runtime.livekit_endpoint_ready(f"ws://{host}:{port}")
            )
            self.assertEqual(
                livekit_runtime.local_lfm_endpoint_health(host, port), "unready",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertFalse(
            livekit_runtime.livekit_endpoint_ready(f"ws://{host}:{port}")
        )
        self.assertEqual(
            livekit_runtime.local_lfm_endpoint_health(host, port), "unavailable",
        )

    def test_livekit_probe_rejects_a_listener_that_never_answers(self) -> None:
        from voice_agent_v2 import livekit_runtime

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host, port = listener.getsockname()
        release_connection = threading.Event()

        def accept_without_response() -> None:
            connection, _address = listener.accept()
            with connection:
                release_connection.wait(1.0)

        thread = threading.Thread(target=accept_without_response, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            self.assertFalse(
                livekit_runtime.livekit_endpoint_ready(
                    f"ws://{host}:{port}", timeout=0.05,
                )
            )
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            release_connection.set()
            listener.close()
            thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())

    def test_public_status_reports_build_health_client_and_no_external_supervision(self) -> None:
        from voice_agent_v2.slice6_gateway import status as public_gateway_status

        health = json.loads(
            (ROOT / "contracts/fixtures/health-readiness.v1.json").read_text()
        )
        agent_runtime = json.loads(
            (ROOT / "contracts/fixtures/public-operational-status.v2.json").read_text()
        )["agent_runtime"]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            registry=SimpleNamespace(
                accepting=True,
                active_count=0,
                agent_runtime=SimpleNamespace(status_document=lambda: agent_runtime),
                operational_health=lambda: health,
            ),
            settings=SimpleNamespace(build_id="a" * 40, release_id="b" * 24),
        )))
        report = asyncio.run(public_gateway_status(request))
        self.assertTrue(report["available"])
        self.assertEqual(report["build_id"], "a" * 40)
        self.assertEqual(report["release_id"], "b" * 24)
        self.assertEqual(report["health"], health)
        self.assertEqual(report["agent_runtime"], agent_runtime)
        self.assertEqual(report["selected_avatar_module"], "mvp-eye-svg-v1")
        self.assertFalse(report["external_provider_supervised"])
        self.assertFalse(report["automatic_fallback"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch

from scripts import run_slice6


class RunningProcess:
    @staticmethod
    def poll():
        return None


class BackendReadinessTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("VOICE_AGENT_VERIFY_SLICE6_RUNTIME") == "1",
        "requires the Slice 6 runtime verification phase",
    )
    def test_operational_monitor_requires_ready_local_no_fallback_report(self) -> None:
        report = {
            "provider_mode": "local",
            "external_provider_supervised": False,
            "automatic_fallback": False,
            "build_id": "a" * 40,
            "release_id": "b" * 24,
            "health": {"overall_readiness": "ready"},
        }

        class StatusHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps(report).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_arguments: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(run_slice6, "GATEWAY_PORT", server.server_address[1]):
                self.assertTrue(run_slice6.gateway_operational_ready())
                self.assertTrue(run_slice6.gateway_operational_ready(
                    expected_build_id="a" * 40,
                    expected_release_id="b" * 24,
                ))
                self.assertFalse(run_slice6.gateway_operational_ready(
                    expected_build_id="c" * 40,
                    expected_release_id="b" * 24,
                ))
                report["health"] = {"overall_readiness": "unready"}
                self.assertFalse(run_slice6.gateway_operational_ready())
                report["health"] = {"overall_readiness": "ready"}
                report["automatic_fallback"] = True
                self.assertFalse(run_slice6.gateway_operational_ready())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    @unittest.skipUnless(
        os.environ.get("VOICE_AGENT_VERIFY_SLICE6_RUNTIME") == "1",
        "requires the Slice 6 runtime verification phase",
    )
    def test_local_lfm_waits_for_healthy_model_after_port_bind(self) -> None:
        model_ready = threading.Event()
        health_requested = threading.Event()

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                health_requested.set()
                if model_ready.is_set():
                    status, body = 200, b'{"status":"ok"}'
                else:
                    status, body = 503, b'{"status":"loading model"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_arguments: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        port = server.server_address[1]
        completed = threading.Event()
        failures: list[BaseException] = []

        def wait_for_readiness() -> None:
            try:
                run_slice6.wait_for_port(RunningProcess(), port, "local LFM", timeout=2)
            except BaseException as error:
                failures.append(error)
            finally:
                completed.set()

        try:
            with patch.object(run_slice6, "LLAMA_PORT", port):
                waiter = threading.Thread(target=wait_for_readiness)
                waiter.start()
                self.assertTrue(health_requested.wait(0.5))
                self.assertFalse(completed.is_set())
                model_ready.set()
                waiter.join(1)
                self.assertFalse(waiter.is_alive())
                self.assertFalse(failures)
        finally:
            model_ready.set()
            server.shutdown()
            server.server_close()
            server_thread.join()


if __name__ == "__main__":
    unittest.main()

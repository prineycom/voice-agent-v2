from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from scripts import run_slice6


class RunningProcess:
    @staticmethod
    def poll():
        return None


class BackendReadinessTests(unittest.TestCase):
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

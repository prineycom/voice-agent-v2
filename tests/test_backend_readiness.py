from __future__ import annotations

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from scripts import run_slice6


class RunningProcess:
    @staticmethod
    def poll():
        return None


class ExitedProcess:
    @staticmethod
    def poll():
        return 1

    @staticmethod
    def terminate() -> None:
        pass

    @staticmethod
    def wait(timeout: float | None = None) -> int:
        del timeout
        return 1

    @staticmethod
    def kill() -> None:
        pass


class BackendReadinessTests(unittest.TestCase):
    def test_gateway_inherits_configured_gpu_bakeoff_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=run_slice6.ROOT) as temporary_directory:
            temporary = Path(temporary_directory)
            cache = temporary / "cache"
            tooling = cache / "voice-agent-v2" / "slice-6" / "tooling"
            runtime = cache / "voice-agent-v2" / "slice-6" / "runtime" / "venv" / "bin"
            tooling.mkdir(parents=True)
            runtime.mkdir(parents=True)
            binary = tooling / f"livekit-server-v{run_slice6.LIVEKIT_VERSION}"
            python = runtime / "python"
            binary.touch(mode=0o700)
            python.touch()
            lock_path = temporary / "gpu-bakeoff.lock"
            settings = types.SimpleNamespace(
                livekit_api_key="candidate-key",
                livekit_api_secret="candidate-secret-value",
                app_public_url="https://host.example.ts.net:8443",
                livekit_public_url="wss://host.example.ts.net:7443",
                web_dist=temporary,
            )
            status = types.SimpleNamespace(
                returncode=0,
                stdout=(
                    '{"Self":{"DNSName":"host.example.ts.net.",'
                    '"TailscaleIPs":["100.64.0.1"],"Online":true}}'
                ),
            )
            with lock_path.open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_fd = lock.fileno()
                environment = {
                    "VOICE_AGENT_GPU_BAKEOFF_LOCK_FD": str(lock_fd),
                    "VOICE_AGENT_TTS_BACKEND": "voxcpm2-fast",
                    "XDG_CACHE_HOME": str(cache),
                    "SLICE6_LIVEKIT_NODE_IP": "100.64.0.1",
                    "SLICE6_APP_HTTPS_PORT": "8443",
                    "SLICE6_SIGNAL_HTTPS_PORT": "7443",
                    "SLICE6_ENABLE_TAILSCALE_SERVE": "1",
                }
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(
                        run_slice6.Slice6Settings,
                        "from_environment",
                        return_value=settings,
                    ),
                    patch.object(run_slice6.shutil, "which", return_value="tailscale"),
                    patch.object(run_slice6.subprocess, "run", return_value=status),
                    patch.object(run_slice6, "verify_local_lfm_artifacts"),
                    patch.object(run_slice6, "wait_for_port"),
                    patch.object(run_slice6, "LFM_CACHE", temporary / "lfm-cache"),
                    patch.object(
                        run_slice6.subprocess,
                        "Popen",
                        return_value=ExitedProcess(),
                    ) as popen,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "development process exited unexpectedly",
                    ):
                        run_slice6.main()

            gateway_call = next(
                call
                for call in popen.call_args_list
                if "voice_agent_v2.slice6_gateway:app" in call.args[0]
            )
            self.assertEqual(gateway_call.kwargs["pass_fds"], (lock_fd,))

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

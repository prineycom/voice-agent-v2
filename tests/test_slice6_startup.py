from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts import run_slice6
from voice_agent_v2.slice6_config import Slice6ConfigurationError


class FakeProcess:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is not None
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class SystemdReadinessTests(unittest.TestCase):
    def test_supervised_core_exit_before_readiness_is_recoverable(self) -> None:
        with self.assertRaisesRegex(
            run_slice6.ServiceProcessFailure, "LiveKit exited before readiness",
        ):
            run_slice6.wait_for_port(
                FakeProcess(returncode=1), run_slice6.SIGNAL_PORT, "LiveKit", timeout=0.1,
            )

    def test_process_identity_loss_after_readiness_is_recoverable(self) -> None:
        process = FakeProcess()
        process.pid = 12345
        with (
            patch.object(
                run_slice6, "supervised_process_identity",
                side_effect=Slice6ConfigurationError("process disappeared"),
            ),
            self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "identity was lost",
            ),
        ):
            run_slice6.supervised_child_identity(process, "LiveKit")

    def test_startup_wait_stops_immediately_when_shutdown_is_requested(self) -> None:
        process = FakeProcess()
        with patch.object(
            run_slice6.socket, "create_connection",
            side_effect=AssertionError("cancelled startup must not poll the port"),
        ):
            ready = run_slice6.wait_for_port(
                process,
                run_slice6.SIGNAL_PORT,
                "LiveKit",
                timeout=80,
                stop_requested=lambda: True,
            )
        self.assertFalse(ready)
        self.assertIsNone(process.returncode)

    def test_final_ready_boundary_repolls_owned_children_after_exact_status(self) -> None:
        class ExitsAfterStatus(FakeProcess):
            polls = 0

            def poll(self) -> int | None:
                self.polls += 1
                return None if self.polls == 1 else 1

        supervisor = run_slice6.ProcessSupervisor()
        child = ExitsAfterStatus()
        supervisor.processes.append(child)
        with (
            patch.object(run_slice6, "gateway_operational_ready", return_value=True) as ready,
            patch.object(run_slice6, "require_runtime_listener_custody") as custody,
            patch.object(run_slice6, "systemd_notify_ready") as notify,
        ):
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "exited before readiness",
            ):
                run_slice6.publish_systemd_readiness(
                    supervisor, build_id="a" * 40, release_id="b" * 24,
                )
        ready.assert_called_once_with(
            timeout=1.0,
            expected_build_id="a" * 40,
            expected_release_id="b" * 24,
        )
        custody.assert_called_once_with(supervisor)
        notify.assert_not_called()

    def test_operational_probe_allows_gateway_component_probe_budget(self) -> None:
        document = json.dumps({
            "accepting": True,
            "provider_mode": "local",
            "external_provider_supervised": False,
            "automatic_fallback": False,
            "health": {"overall_readiness": "ready"},
        }).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                time.sleep(0.3)
                self.send_response(200)
                self.send_header("Content-Length", str(len(document)))
                self.end_headers()
                self.wfile.write(document)

            def log_message(self, _format: str, *_arguments: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(run_slice6, "GATEWAY_PORT", server.server_port):
                self.assertTrue(run_slice6.gateway_operational_ready())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_shutdown_request_prevents_systemd_ready_publication(self) -> None:
        supervisor = run_slice6.ProcessSupervisor()
        with patch.object(run_slice6, "systemd_notify_ready") as notify:
            with self.assertRaises(run_slice6.ServiceStopRequested):
                run_slice6.publish_systemd_readiness(
                    supervisor,
                    build_id="a" * 40,
                    release_id="b" * 24,
                    stop_requested=lambda: True,
                )
        notify.assert_not_called()

    def test_exact_ready_notification_reaches_systemd_socket(self) -> None:
        class Notifier:
            address: str | None = None
            message: bytes | None = None
            closed = False

            def settimeout(self, _timeout: float) -> None:
                pass

            def connect(self, address: str) -> None:
                self.address = address

            def sendall(self, message: bytes) -> None:
                self.message = message

            def close(self) -> None:
                self.closed = True

        notifier = Notifier()
        with (
            patch.dict(os.environ, {"NOTIFY_SOCKET": "@voice-agent-notify"}),
            patch.object(run_slice6.socket, "socket", return_value=notifier),
        ):
            run_slice6.systemd_notify_ready()
        self.assertEqual(notifier.address, "\0voice-agent-notify")
        self.assertEqual(
            notifier.message,
            b"READY=1\nSTATUS=Voice Agent exact release ready\n",
        )
        self.assertTrue(notifier.closed)


class LoopbackRuntimeConfigurationTests(unittest.TestCase):
    def test_livekit_server_is_restricted_to_loopback(self) -> None:
        document = json.loads(run_slice6.livekit_server_config())
        self.assertEqual(document["bind_addresses"], ["127.0.0.1"])
        self.assertEqual(document["rtc"]["node_ip"], "127.0.0.1")
        self.assertEqual(document["rtc"]["interfaces"], {"includes": ["lo"]})
        self.assertEqual(document["rtc"]["ips"], {"includes": ["127.0.0.1/32"]})


class ParentProcessIdentityTests(unittest.TestCase):
    def test_linux_process_identity_rejects_stale_generation(self) -> None:
        identity = run_slice6.supervised_process_identity(os.getpid())
        from voice_agent_v2.slice6_config import supervised_process_alive

        self.assertTrue(supervised_process_alive(identity))
        pid, start_time = identity.split(":", 1)
        self.assertFalse(supervised_process_alive(f"{pid}:{int(start_time) + 1}"))

    def test_only_the_systemd_runtime_exports_main_process_custody(self) -> None:
        identity = run_slice6.systemd_service_main_process({
            "XDG_RUNTIME_DIR": str(run_slice6.SYSTEMD_RUNTIME_ROOT),
        })
        self.assertEqual(
            identity, run_slice6.supervised_process_identity(os.getpid()),
        )
        self.assertIsNone(run_slice6.systemd_service_main_process({
            "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        }))


class RuntimePortCustodyTests(unittest.TestCase):
    def test_final_listener_custody_rejects_an_unrelated_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            net = proc / "net"
            net.mkdir()
            header = "slot local remote state queues timers retrnsmt uid timeout inode\n"
            (net / "tcp").write_text(
                header + "0: 0100007F:1F40 00000000:0000 0A 0 0 0 1000 0 555\n",
                encoding="ascii",
            )
            for name in ("tcp6", "udp", "udp6"):
                (net / name).write_text(header, encoding="ascii")
            process = proc / "123"
            (process / "task/123").mkdir(parents=True)
            (process / "task/123/children").write_text("", encoding="ascii")
            (process / "fd").mkdir()
            descriptor = process / "fd/3"
            descriptor.symlink_to("socket:[555]")
            owned = FakeProcess()
            owned.pid = 123
            supervisor = run_slice6.ProcessSupervisor()
            supervisor.processes.append(owned)
            supervisor._roles[id(owned)] = "fixture"
            run_slice6.require_runtime_listener_custody(
                supervisor,
                proc_root=proc,
                requirements=(("fixture", "tcp", 8000),),
            )
            descriptor.unlink()
            descriptor.symlink_to("socket:[777]")
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "not owned",
            ):
                run_slice6.require_runtime_listener_custody(
                    supervisor,
                    proc_root=proc,
                    requirements=(("fixture", "tcp", 8000),),
                )

    def test_preexisting_runtime_listener_fails_before_child_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            net = proc / "net"
            net.mkdir()
            header = "slot local remote state\n"
            (net / "tcp").write_text(
                header + "0: 0100007F:1F40 00000000:0000 0A\n",
                encoding="ascii",
            )
            for name in ("tcp6", "udp", "udp6"):
                (net / name).write_text(header, encoding="ascii")
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "already owned: 8000",
            ):
                run_slice6.require_runtime_ports_free(proc)


class Slice6WrapperEnvironmentTests(unittest.TestCase):
    def run_wrapper(
        self, *, inherited: dict[str, str], configured: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapper = root / "run-slice6"
            wrapper.write_bytes((Path(__file__).parents[1] / "run-slice6").read_bytes())
            wrapper.chmod(0o755)
            (root / ".env.slice6").write_text(
                "".join(f"{name}={value}\n" for name, value in configured.items()),
                encoding="utf-8",
            )
            fake_python = (
                root / "cache" / "voice-agent-v2" / "slice-6" / "runtime" / "venv" / "bin" / "python"
            )
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text(
                "#!/bin/sh\n"
                "if [ \"${LITELLM_BASE_URL+present}\" = present ] || "
                "[ \"${LITELLM_TOKEN_FILE+present}\" = present ]; then exit 42; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = dict(os.environ)
            environment.update(inherited)
            environment["XDG_CACHE_HOME"] = str(root / "cache")
            return subprocess.run(
                [str(wrapper)],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    def test_inherited_forbidden_cloud_names_are_cleared_before_operator_config(self) -> None:
        result = self.run_wrapper(
            inherited={
                "LITELLM_BASE_URL": "https://ambient.invalid",
                "LITELLM_TOKEN_FILE": "/ambient/token",
            },
            configured={},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_operator_configured_forbidden_cloud_names_are_not_cleared(self) -> None:
        for name in sorted(run_slice6.FORBIDDEN_CLOUD_NAMES):
            with self.subTest(name=name):
                result = self.run_wrapper(
                    inherited={name: "ambient"},
                    configured={name: "configured"},
                )
                self.assertEqual(result.returncode, 42)

    def test_python_runtime_refuses_a_configured_forbidden_name(self) -> None:
        with patch.dict(os.environ, {"LITELLM_BASE_URL": "configured"}, clear=True):
            with self.assertRaisesRegex(Slice6ConfigurationError, "LiteLLM configuration is forbidden"):
                run_slice6.main()


class SupervisorLifecycleTests(unittest.TestCase):
    def test_supervisor_refuses_new_children_after_shutdown_request(self) -> None:
        supervisor = run_slice6.ProcessSupervisor(stop_requested=lambda: True)
        with (
            patch.object(
                run_slice6.subprocess, "Popen",
                side_effect=AssertionError("shutdown must prevent child launch"),
            ),
            self.assertRaises(run_slice6.ServiceStopRequested),
        ):
            supervisor.start(["fixture"], role="livekit")
        self.assertEqual(supervisor.processes, [])

    def test_ctrl_c_path_stops_owned_children_without_touching_external_exposure(self) -> None:
        first = FakeProcess()
        second = FakeProcess()
        supervisor = run_slice6.ProcessSupervisor()
        supervisor.processes.extend([first, second])
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            supervisor.close()
        self.assertTrue(first.terminated)
        self.assertTrue(second.terminated)
        self.assertFalse(first.killed)
        self.assertFalse(second.killed)

    def test_operational_shutdown_drains_before_media_and_inference_within_hard_stop(self) -> None:
        roles = (
            "local-llm",
            "livekit",
            "gateway-controller-stt-tts-provider",
        )
        processes = [FakeProcess() for _role in roles]
        supervisor = run_slice6.ProcessSupervisor()
        with patch.object(run_slice6.subprocess, "Popen", side_effect=processes):
            for role in roles:
                supervisor.start(["fixture"], role=role)
        stopped: list[tuple[str, float, float, float | None]] = []

        def record_stop(
            process: FakeProcess,
            *,
            timeout: float = 5.0,
            kill_timeout: float = 1.0,
            deadline: float | None = None,
            process_group: int | None = None,
        ) -> None:
            del process_group
            stopped.append((supervisor.role(process), timeout, kill_timeout, deadline))
            process.terminate()

        with patch.object(run_slice6, "stop", side_effect=record_stop):
            supervisor.close(run_slice6.SHUTDOWN_ORDER)
        self.assertEqual([role for role, *_budget in stopped], list(run_slice6.SHUTDOWN_ORDER))
        budgets = {role: (grace, kill) for role, grace, kill, _deadline in stopped}
        self.assertEqual(
            budgets["gateway-controller-stt-tts-provider"], (54.0, 1.0),
        )
        self.assertEqual(len({deadline for *_budget, deadline in stopped}), 1)
        self.assertLess(
            sum(grace + kill for _role, grace, kill, _deadline in stopped),
            75.0,
        )
        self.assertLess(
            [role for role, *_budget in stopped].index(
                "gateway-controller-stt-tts-provider"
            ),
            [role for role, *_budget in stopped].index("livekit"),
        )

    def test_close_kills_descendants_after_the_role_parent_exits(self) -> None:
        supervisor = run_slice6.ProcessSupervisor()
        parent = supervisor.start(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',"
                "\"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)\"]); "
                "print(child.pid,flush=True)",
            ],
            role="gateway-controller-stt-tts-provider",
            stdout=subprocess.PIPE,
            text=True,
        )
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        parent.stdout.close()
        process_group = parent.pid
        parent.wait(timeout=2)
        supervisor.close(run_slice6.SHUTDOWN_ORDER)
        self.assertFalse(run_slice6._process_group_exists(process_group))
        deadline = time.monotonic() + 2
        child_state = None
        while time.monotonic() < deadline:
            try:
                child_state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                child_state = None
            if child_state in {None, "Z"}:
                break
            time.sleep(0.02)
        self.assertIn(child_state, {None, "Z"})

    def test_forced_stop_reports_a_process_group_that_survives_its_budget(self) -> None:
        process = FakeProcess(returncode=0)
        with (
            patch.object(run_slice6, "_process_group_exists", return_value=True),
            patch.object(run_slice6.os, "killpg"),
            self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "survived forced stop",
            ),
        ):
            run_slice6.stop(
                process, timeout=0, kill_timeout=0.01, process_group=12345,
            )

    def test_forced_stop_reaps_within_its_total_role_budget(self) -> None:
        class StubbornProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__()
                self.wait_timeouts: list[float] = []

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: float | None = None) -> int:
                assert timeout is not None
                self.wait_timeouts.append(timeout)
                if not self.killed:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                self.returncode = -9
                return self.returncode

            def kill(self) -> None:
                self.killed = True

        process = StubbornProcess()
        run_slice6.stop(process, timeout=0.05, kill_timeout=0.02)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(len(process.wait_timeouts), 2)
        self.assertLessEqual(sum(process.wait_timeouts), 0.07)


if __name__ == "__main__":
    unittest.main()

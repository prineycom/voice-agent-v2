from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
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
        stopped: list[tuple[str, float]] = []

        def record_stop(process: FakeProcess, *, timeout: float = 5.0) -> None:
            stopped.append((supervisor.role(process), timeout))
            process.terminate()

        with patch.object(run_slice6, "stop", side_effect=record_stop):
            supervisor.close(run_slice6.SHUTDOWN_ORDER)
        self.assertEqual([role for role, _timeout in stopped], list(run_slice6.SHUTDOWN_ORDER))
        self.assertEqual(
            dict(stopped)["gateway-controller-stt-tts-provider"], 54.0,
        )
        self.assertLess(sum(timeout for _role, timeout in stopped), 75.0)
        self.assertLess(
            [role for role, _timeout in stopped].index(
                "gateway-controller-stt-tts-provider"
            ),
            [role for role, _timeout in stopped].index("livekit"),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_slice6
from voice_agent_v2.slice6_config import Slice6ConfigurationError


HOSTNAME = "priney-arch.darter-smoot.ts.net"
APP_TARGET = "http://127.0.0.1:8000"
SIGNAL_TARGET = "http://127.0.0.1:7880"
ROUTES = ((8443, APP_TARGET), (7443, SIGNAL_TARGET))


def serve_document(*routes: tuple[int, str]) -> dict[str, object]:
    tcp: dict[str, object] = {"443": {"HTTPS": True}}
    web: dict[str, object] = {
        f"{HOSTNAME}:443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}
        }
    }
    for port, target in routes:
        tcp[str(port)] = {"HTTPS": True}
        web[f"{HOSTNAME}:{port}"] = {"Handlers": {"/": {"Proxy": target}}}
    return {"TCP": tcp, "Web": web}


def foreground_serve_document(*routes: tuple[int, str]) -> dict[str, object]:
    document = serve_document()
    document["Foreground"] = {
        f"owner-{port}": {
            "TCP": {str(port): {"HTTPS": True}},
            "Web": {
                f"{HOSTNAME}:{port}": {"Handlers": {"/": {"Proxy": target}}}
            },
        }
        for port, target in routes
    }
    return document


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
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        owned = FakeProcess()
        owned.pid = os.getpid()
        supervisor = run_slice6.ProcessSupervisor()
        supervisor.processes.append(owned)
        supervisor._roles[id(owned)] = "fixture"
        try:
            run_slice6.require_runtime_listener_custody(
                supervisor, requirements=(("fixture", "tcp", port),),
            )
            unrelated = subprocess.Popen(
                [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            supervisor.processes[0] = unrelated
            supervisor._roles = {id(unrelated): "fixture"}
            try:
                with self.assertRaisesRegex(
                    run_slice6.ServiceProcessFailure, "not owned",
                ):
                    run_slice6.require_runtime_listener_custody(
                        supervisor, requirements=(("fixture", "tcp", port),),
                    )
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)
        finally:
            listener.close()

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
            configured={"SLICE6_ENABLE_TAILSCALE_SERVE": "1"},
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


class TailscaleServeOwnershipTests(unittest.TestCase):
    def test_transient_offline_state_is_recoverable_but_identity_and_auth_are_static(self) -> None:
        with self.assertRaisesRegex(
            run_slice6.ServiceProcessFailure, "not online",
        ):
            run_slice6.validate_tailnet_identity(
                {"BackendState": "Running", "Self": {"Online": False}},
                node_ip="100.64.0.10",
                hostname=HOSTNAME,
            )
        for document in (
            {"BackendState": "NeedsLogin"},
            {
                "BackendState": "Running",
                "Self": {
                    "Online": True,
                    "DNSName": "other.example.ts.net",
                    "TailscaleIPs": ["100.64.0.10"],
                },
            },
        ):
            with self.subTest(document=document), self.assertRaises(
                Slice6ConfigurationError,
            ):
                run_slice6.validate_tailnet_identity(
                    document,
                    node_ip="100.64.0.10",
                    hostname=HOSTNAME,
                )

    def test_duplicate_route_ports_fail_before_status_or_mutation(self) -> None:
        supervisor = run_slice6.ProcessSupervisor()
        with patch.object(run_slice6, "read_tailscale_serve_status") as read_status:
            with self.assertRaisesRegex(Slice6ConfigurationError, "ports must differ"):
                run_slice6.reconcile_serve_routes(
                    supervisor=supervisor,
                    environment={},
                    hostname=HOSTNAME,
                    routes=((8443, APP_TARGET), (8443, SIGNAL_TARGET)),
                )
        read_status.assert_not_called()
        self.assertEqual(supervisor.processes, [])

    def test_route_states_accept_exact_reject_conflict_and_preserve_unrelated(self) -> None:
        original = serve_document((8443, APP_TARGET))
        document = copy.deepcopy(original)
        self.assertEqual(
            run_slice6.serve_route_state(
                document, hostname=HOSTNAME, https_port=8443, target=APP_TARGET,
            ),
            "preexisting",
        )
        self.assertEqual(
            run_slice6.serve_route_state(
                document, hostname=HOSTNAME, https_port=7443, target=SIGNAL_TARGET,
            ),
            "absent",
        )
        self.assertEqual(
            run_slice6.serve_route_state(
                document, hostname=HOSTNAME, https_port=8443, target=SIGNAL_TARGET,
            ),
            "conflict",
        )
        foreground = foreground_serve_document((8443, APP_TARGET))
        self.assertEqual(
            run_slice6.serve_route_state(
                foreground, hostname=HOSTNAME, https_port=8443, target=APP_TARGET,
            ),
            "preexisting",
        )
        self.assertEqual(document, original)
        self.assertEqual(
            document["Web"][f"{HOSTNAME}:443"]["Handlers"]["/"]["Proxy"],
            "http://127.0.0.1:3000",
        )

    def test_exact_preexisting_routes_start_no_children_and_survive_cleanup(self) -> None:
        for document in (serve_document(*ROUTES), foreground_serve_document(*ROUTES)):
            with self.subTest(foreground="Foreground" in document):
                original = copy.deepcopy(document)
                supervisor = run_slice6.ProcessSupervisor()
                with (
                    patch.object(run_slice6, "read_tailscale_serve_status", return_value=document),
                    patch.object(run_slice6.subprocess, "Popen") as popen,
                ):
                    states = run_slice6.reconcile_serve_routes(
                        supervisor=supervisor,
                        environment={},
                        hostname=HOSTNAME,
                        routes=ROUTES,
                    )
                    supervisor.close()
                self.assertEqual(states, {8443: "preexisting", 7443: "preexisting"})
                self.assertEqual(supervisor.processes, [])
                popen.assert_not_called()
                self.assertEqual(document, original)

    def test_absent_routes_are_created_serially_and_both_children_are_supervised(self) -> None:
        events: list[str] = []
        app_process = FakeProcess()
        signal_process = FakeProcess()
        statuses = iter([
            serve_document(),
            foreground_serve_document((8443, APP_TARGET)),
            foreground_serve_document(*ROUTES),
        ])

        def read_status(_environment: dict[str, str], *, timeout: float = 5.0) -> dict[str, object]:
            events.append("status")
            return next(statuses)

        def popen(command: list[str], **_kwargs: object) -> FakeProcess:
            port = "8443" if "--https=8443" in command else "7443"
            events.append(f"start-{port}")
            return app_process if port == "8443" else signal_process

        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(run_slice6, "read_tailscale_serve_status", side_effect=read_status),
            patch.object(run_slice6.subprocess, "Popen", side_effect=popen),
        ):
            states = run_slice6.reconcile_serve_routes(
                supervisor=supervisor,
                environment={},
                hostname=HOSTNAME,
                routes=ROUTES,
            )
        self.assertEqual(
            events,
            ["status", "start-8443", "status", "start-7443", "status"],
        )
        self.assertEqual(states, {8443: "owned", 7443: "owned"})
        self.assertEqual(supervisor.processes, [app_process, signal_process])
        supervisor.close()
        self.assertTrue(app_process.terminated)
        self.assertTrue(signal_process.terminated)

    def test_conflicting_second_route_blocks_all_mutation(self) -> None:
        document = serve_document((7443, "http://127.0.0.1:9999"))
        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(run_slice6, "read_tailscale_serve_status", return_value=document),
            patch.object(run_slice6.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(Slice6ConfigurationError, "HTTPS/7443"):
                run_slice6.reconcile_serve_routes(
                    supervisor=supervisor,
                    environment={},
                    hostname=HOSTNAME,
                    routes=ROUTES,
                )
        popen.assert_not_called()
        self.assertEqual(supervisor.processes, [])
        self.assertEqual(document, serve_document((7443, "http://127.0.0.1:9999")))

    def test_etag_style_early_exit_is_registered_before_failure_cleanup(self) -> None:
        exited = FakeProcess(returncode=1)
        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(run_slice6, "read_tailscale_serve_status", return_value=serve_document()),
            patch.object(run_slice6.subprocess, "Popen", return_value=exited),
        ):
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "exited before readiness",
            ):
                try:
                    run_slice6.reconcile_serve_routes(
                        supervisor=supervisor,
                        environment={},
                        hostname=HOSTNAME,
                        routes=ROUTES,
                    )
                finally:
                    supervisor.close()
        self.assertEqual(supervisor.processes, [exited])

    def test_second_route_etag_exit_cleans_the_first_owned_route(self) -> None:
        first = FakeProcess()
        second = FakeProcess(returncode=1)
        statuses = iter([serve_document(), foreground_serve_document((8443, APP_TARGET))])
        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(
                run_slice6,
                "read_tailscale_serve_status",
                side_effect=lambda *_args, **_kwargs: next(statuses),
            ),
            patch.object(run_slice6.subprocess, "Popen", side_effect=[first, second]),
        ):
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure,
                "HTTPS/7443 exited before readiness",
            ):
                try:
                    run_slice6.reconcile_serve_routes(
                        supervisor=supervisor,
                        environment={},
                        hostname=HOSTNAME,
                        routes=ROUTES,
                    )
                finally:
                    supervisor.close()
        self.assertEqual(supervisor.processes, [first, second])
        self.assertTrue(first.terminated)

    def test_readiness_timeout_cleans_the_owned_foreground_child(self) -> None:
        process = FakeProcess()
        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(run_slice6, "read_tailscale_serve_status", return_value=serve_document()),
            patch.object(run_slice6.subprocess, "Popen", return_value=process),
        ):
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "did not register",
            ):
                try:
                    run_slice6.reconcile_serve_routes(
                        supervisor=supervisor,
                        environment={},
                        hostname=HOSTNAME,
                        routes=ROUTES,
                        timeout=0.01,
                    )
                finally:
                    supervisor.close()
        self.assertTrue(process.terminated)

    def test_exception_starting_second_route_is_recoverable_and_cleans_first_child(self) -> None:
        first = FakeProcess()
        statuses = iter([serve_document(), foreground_serve_document((8443, APP_TARGET))])
        supervisor = run_slice6.ProcessSupervisor()
        with (
            patch.object(run_slice6, "read_tailscale_serve_status", side_effect=lambda *_args, **_kwargs: next(statuses)),
            patch.object(run_slice6.subprocess, "Popen", side_effect=[first, OSError("launch failed")]),
        ):
            with self.assertRaisesRegex(
                run_slice6.ServiceProcessFailure, "could not start",
            ):
                try:
                    run_slice6.reconcile_serve_routes(
                        supervisor=supervisor,
                        environment={},
                        hostname=HOSTNAME,
                        routes=ROUTES,
                    )
                finally:
                    supervisor.close()
        self.assertEqual(supervisor.processes, [first])
        self.assertTrue(first.terminated)

    def test_ctrl_c_path_stops_owned_children_in_reverse_without_touching_external_routes(self) -> None:
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

    def test_operational_shutdown_closes_admission_then_drains_before_media_and_inference(self) -> None:
        roles = (
            "local-llm",
            "livekit",
            "gateway-controller-stt-tts-provider",
            "tailnet-app-route",
            "tailnet-signal-route",
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
        self.assertEqual(dict(stopped)["gateway-controller-stt-tts-provider"], 60.0)
        self.assertLess(
            [role for role, _timeout in stopped].index("gateway-controller-stt-tts-provider"),
            [role for role, _timeout in stopped].index("livekit"),
        )


if __name__ == "__main__":
    unittest.main()

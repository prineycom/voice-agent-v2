from __future__ import annotations

import base64
from collections import namedtuple
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from voice_agent_v2.agent_environment import (
    AgentEnvironment, AgentEnvironmentError, DockerResult,
    GENERATION_LABEL, MANAGED_LABEL, OWNER_LABEL, SCHEMA_LABEL, SPEC_LABEL,
)
from voice_agent_v2.agent_environment_config import (
    DEFAULT_CONFIG_BYTES, PINNED_IMAGE, parse_agent_config_v2, upgrade_v1_to_v2,
)
from voice_agent_v2.agent_run import AgentRealtimeIdentity, AgentRun
from voice_agent_v2.local_lfm import PROVIDER_IDENTITY
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.tracer import CancellationToken

Disk = namedtuple("Disk", "total used free")


class FakeDocker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.commands: list[tuple[str, ...]] = []
        self.containers: dict[str, dict[str, object]] = {}
        self.next_id = 1
        self.endpoint = b'{"ID":"engine-a","Os":"linux","Arch":"amd64"}'
        self.partial_inspect = False
        self.ambiguous_exec = False
        self.reject_stopped_once = False
        self.fail_next_start = False
        self.claims: dict[str, dict[str, object]] = {}
        self.dispatch_count: dict[str, int] = {}
        self.markers: dict[str, str] = {}
        self.processes: dict[str, dict[str, object]] = {}

    def _id(self) -> str:
        value = f"{self.next_id:064x}"
        self.next_id += 1
        return value

    def _inspect(self, identifier: str) -> dict[str, object]:
        container = self.containers[identifier]
        return {
            "Id": identifier,
            "Image": "sha256:560f855490a6e6a0bd96515510ab6051611a4ec99b0a762c7a07701b3d152b95",
            "Platform": "linux",
            "Config": {
                "Image": PINNED_IMAGE,
                "User": "1000:1000",
                "Labels": dict(container["labels"]),
                "ExposedPorts": None,
            },
            "State": {"Status": container["state"]},
            "HostConfig": {
                "RestartPolicy": {"Name": "no"},
                "NanoCpus": 2_000_000_000,
                "Memory": 4096 * 1024 * 1024,
                "PidsLimit": 128,
                "ShmSize": 1024 * 1024 * 1024,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges"],
                "Privileged": False,
                "PidMode": "",
                "IpcMode": "private",
                "UTSMode": "",
                "NetworkMode": "bridge",
                "Devices": [],
                "Tmpfs": {
                    "/scratch": "size=1024m,exec,nosuid,nodev",
                    "/tmp": "size=512m,exec,nosuid,nodev",
                    "/var/tmp": "size=256m,noexec,nosuid,nodev",
                    "/run": "size=64m,noexec,nosuid,nodev",
                },
                "PortBindings": None,
            },
            "Mounts": list(container["mounts"]),
        }

    @staticmethod
    def _value(arguments: tuple[str, ...], name: str) -> str:
        return arguments[arguments.index(name) + 1]

    def run(self, arguments: tuple[str, ...], *, stdin: bytes = b"", timeout: float = 15) -> DockerResult:
        with self.lock:
            self.commands.append(arguments)
            if arguments == ("context", "show"):
                return DockerResult(0, b"default\n")
            if arguments[:2] == ("version", "--format"):
                return DockerResult(0, self.endpoint + b"\n")
            if arguments[:3] == ("container", "ls", "-a"):
                owner_filter = next(value for value in arguments if value.startswith("label=" + OWNER_LABEL))
                owner = owner_filter.rsplit("=", 1)[1]
                ids = [identifier for identifier, item in self.containers.items() if item["labels"].get(OWNER_LABEL) == owner and item["labels"].get(MANAGED_LABEL) == "1"]
                return DockerResult(0, ("\n".join(ids) + ("\n" if ids else "")).encode())
            if arguments[:2] == ("container", "inspect"):
                if self.partial_inspect:
                    return DockerResult(0, b"[{}]")
                identifier = arguments[2]
                if identifier not in self.containers:
                    return DockerResult(1, stderr=b"No such container")
                return DockerResult(0, json.dumps([self._inspect(identifier)]).encode())
            if arguments[:2] == ("container", "create"):
                identifier = self._id()
                labels: dict[str, str] = {}
                for index, value in enumerate(arguments):
                    if value == "--label":
                        key, item = arguments[index + 1].split("=", 1)
                        labels[key] = item
                mounts = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--mount"]
                inspected_mounts = []
                for declaration in mounts:
                    fields = dict(
                        part.split("=", 1) for part in declaration.split(",") if "=" in part
                    )
                    inspected_mounts.append({
                        "Type": "bind", "Source": fields["src"], "Destination": fields["dst"],
                        "RW": declaration.endswith(",rw"),
                    })
                self.containers[identifier] = {
                    "labels": labels,
                    "state": "created",
                    "workspace": mounts[0].split(",")[1].removeprefix("src="),
                    "cache": mounts[1].split(",")[1].removeprefix("src="),
                    "mounts": inspected_mounts,
                }
                return DockerResult(0, (identifier + "\n").encode())
            if arguments[:2] == ("container", "start"):
                if self.fail_next_start:
                    self.fail_next_start = False
                    return DockerResult(1, stderr=b"injected start failure")
                self.containers[arguments[2]]["state"] = "running"
                return DockerResult(0, (arguments[2] + "\n").encode())
            if arguments[:2] == ("container", "stop"):
                identifier = arguments[-1]
                self.containers[identifier]["state"] = "exited"
                for process in self.processes.values():
                    if process["container_id"] == identifier:
                        process["state"] = "gone"
                return DockerResult(0, (identifier + "\n").encode())
            if arguments[:2] == ("container", "rm"):
                identifier = arguments[2]
                del self.containers[identifier]
                return DockerResult(0, (identifier + "\n").encode())
            if arguments[:3] == ("container", "exec", arguments[2]) and arguments[-1] == "readiness":
                return DockerResult(0, b'{"ready":true,"schema":1}')
            if arguments[:2] == ("container", "exec") and "cancel-call" in arguments:
                return DockerResult(0)
            if arguments[:2] == ("container", "exec") and "claim-execute" in arguments:
                identifier = arguments[3]
                call_id = self._value(arguments, "--call-id")
                if self.reject_stopped_once:
                    self.reject_stopped_once = False
                    self.containers[identifier]["state"] = "exited"
                    return DockerResult(125, stderr=b"container stopped", accepted=False)
                if self.ambiguous_exec:
                    self.ambiguous_exec = False
                    self.dispatch_count[call_id] = self.dispatch_count.get(call_id, 0) + 1
                    return DockerResult(1, stderr=b"transport lost", accepted=None)
                if call_id in self.claims:
                    prior = dict(self.claims[call_id]); prior["replayed"] = True
                    return DockerResult(0, json.dumps(prior).encode(), accepted=True)
                self.dispatch_count[call_id] = self.dispatch_count.get(call_id, 0) + 1
                data = json.loads(stdin)
                tool = self._value(arguments, "--tool")
                if data.get("background") is True:
                    receipt = data["_voice_agent_process_receipt"]
                    identity = {
                        "pid": 101 + len(self.processes), "pgid": 101 + len(self.processes),
                        "start_time": 9001 + len(self.processes),
                        "executable": "/bin/sh", "init_start_time": 77,
                    }
                    self.processes[receipt] = {
                        "container_id": identifier, "identity": identity, "state": "running",
                        "stdout": b"preview ready\n", "stdin": b"",
                    }
                    document = {
                        "status": "completed", "exit_code": 0, "stdout_base64": "",
                        "stderr_base64": "", "cwd": "/workspace", "replayed": False,
                        "details": {
                            "schema_version": "voice-agent.process-receipt.v1",
                            "kind": "background_process", "process_receipt": receipt,
                            "state": "running", "writable_stdin": True,
                            "operations": ["poll", "logs", "wait", "write", "kill"],
                            "_voice_agent_identity": identity,
                        },
                    }
                    return DockerResult(0, json.dumps(document).encode(), accepted=True)
                if tool in {"process", "receipt"}:
                    receipt = data.get("receipt")
                    process = self.processes.get(receipt)
                    if process is None or data.get("_voice_agent_expected_identity") != process["identity"]:
                        document = {
                            "status": "failed", "exit_code": 2, "stdout_base64": "",
                            "stderr_base64": base64.b64encode(b"process_identity_unknown").decode(),
                            "cwd": "/workspace", "replayed": False,
                            "details": {"kind": "process", "state": "unknown"},
                        }
                        return DockerResult(0, json.dumps(document).encode(), accepted=True)
                    action = data.get("action", "poll")
                    if action == "kill" and process["state"] == "running":
                        process["state"] = "killed"
                    if action == "write" and process["state"] == "running":
                        process["stdin"] += base64.b64decode(data.get("data_base64", ""))
                    output = process["stdout"] if action == "logs" else b""
                    document = {
                        "status": "completed", "exit_code": 0,
                        "stdout_base64": base64.b64encode(output).decode(), "stderr_base64": "",
                        "cwd": "/workspace", "replayed": False,
                        "details": {
                            "kind": "process", "process_receipt": receipt, "action": action,
                            "state": process["state"], "timed_out": action == "wait" and process["state"] == "running",
                        },
                    }
                    return DockerResult(0, json.dumps(document).encode(), accepted=True)
                command = str(data.get("command", ""))
                if command.startswith("set "):
                    key, value = command[4:].split("=", 1); self.markers[key] = value; output = value.encode()
                elif command.startswith("get "):
                    output = self.markers.get(command[4:], "missing").encode()
                else:
                    output = command.encode()
                observed_cwd = command[4:] if command.startswith("cwd ") else "/workspace"
                if observed_cwd == "/deleted":
                    observed_cwd = "/workspace"
                document = {
                    "status": "completed", "exit_code": 0,
                    "stdout_base64": base64.b64encode(output).decode(),
                    "stderr_base64": "", "cwd": observed_cwd, "replayed": False,
                }
                self.claims[call_id] = document
                return DockerResult(0, json.dumps(document).encode(), accepted=True)
            raise AssertionError(f"unexpected fake Docker command: {arguments!r}")


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-environment-")
        root = Path(self.temp.name)
        self.docker = FakeDocker()
        self.manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            runner=self.docker, disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def close(self) -> None:
        self.temp.cleanup()


class ConfigV2Tests(unittest.TestCase):
    def test_v2_is_strict_and_v1_upgrade_drops_identity(self) -> None:
        parsed = parse_agent_config_v2(DEFAULT_CONFIG_BYTES)
        self.assertEqual(parsed.model.schema_version, "voice-agent.config.v2")
        for field in ("profile_id", "environment_id", "runtime", "adapter", "backend", "extra_args"):
            document = json.loads(json.dumps(parsed.model.model_dump(mode="json")))
            document[field] = "forbidden"
            with self.assertRaises(AgentConfigError):
                parse_agent_config_v2(json.dumps(document).encode())
        upgraded = upgrade_v1_to_v2(
            b"schema_version: voice-agent.config.v1\nprofile_id: private-name\naccess: {capabilities: {}}\n"
        )
        self.assertNotIn(b"private-name", upgraded)
        self.assertNotIn(b"profile_id", upgraded)
        upgraded_snapshot = parse_agent_config_v2(upgraded)
        self.assertFalse(upgraded_snapshot.model.agent.enabled)
        self.assertFalse(upgraded_snapshot.model.agent_environment.enabled)
        self.assertIsNone(upgraded_snapshot.model.agent_environment.docker.endpoint)
        self.assertNotEqual(upgraded_snapshot.semantic_revision, parsed.semantic_revision)
        root = Path(__file__).resolve().parents[1]
        validate_schema(
            parsed.model.model_dump(mode="json"),
            json.loads((root / "contracts/agent-config.v2.schema.json").read_text()),
        )
        public = json.loads(
            (root / "contracts/fixtures/public-operational-status.v2.json").read_text()
        )
        validate_schema(
            public,
            json.loads((root / "contracts/public-operational-status.v2.schema.json").read_text()),
        )


from voice_agent_v2.agent_config import AgentConfigError


class AgentEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_concurrent_first_use_creates_once_and_later_calls_reuse_state(self) -> None:
        ids: list[str] = []
        threads = [threading.Thread(target=lambda: ids.append(self.fixture.manager.ensure_running().container_id)) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(set(ids)), 1)
        creates = [item for item in self.fixture.docker.commands if item[:2] == ("container", "create")]
        self.assertEqual(len(creates), 1)
        first = self.fixture.manager.execute("shell.exec", {"command": "set rootfs=kept"})
        second = self.fixture.manager.execute("shell.exec", {"command": "get rootfs"})
        self.assertEqual(second.stdout, b"kept")
        self.assertEqual(first.container_id, second.container_id)
        restarted_controller = AgentEnvironment(
            self.fixture.manager.config,
            state_root=self.fixture.manager.state_root,
            workspace=self.fixture.manager.workspace,
            cache=self.fixture.manager.cache,
            runner=self.fixture.docker,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        later = restarted_controller.execute("shell.exec", {"command": "get rootfs"})
        self.assertEqual(later.container_id, first.container_id)
        self.assertEqual(later.stdout, b"kept")

    def test_logical_cwd_is_restart_persistent_and_deleted_directory_visibly_falls_back(self) -> None:
        first = self.fixture.manager.execute("shell.exec", {"command": "cwd /workspace/project"})
        self.assertEqual(first.cwd, "/workspace/project")
        restarted = AgentEnvironment(
            self.fixture.manager.config, state_root=self.fixture.manager.state_root,
            workspace=self.fixture.manager.workspace, cache=self.fixture.manager.cache,
            runner=self.fixture.docker, disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        restarted.execute("shell.exec", {"command": "cwd /deleted"})
        dispatches = [item for item in self.fixture.docker.commands if "claim-execute" in item]
        self.assertEqual(dispatches[-1][dispatches[-1].index("--cwd") + 1], "/workspace/project")
        self.assertEqual(restarted.status()["logical_cwd_persists_across_exec_and_controller_restart"], True)
        fallback = restarted.execute("shell.exec", {"command": "get anything"})
        self.assertEqual(fallback.cwd, "/workspace")

    def test_stop_starts_same_id_but_normal_events_issue_no_teardown(self) -> None:
        facts = self.fixture.manager.ensure_running()
        before = list(self.fixture.docker.commands)
        for _event in ("turn", "session", "quit", "idle", "shutdown", "controller_crash"):
            pass
        self.assertEqual(before, self.fixture.docker.commands)
        self.fixture.docker.run(("container", "stop", "--time", "5", facts.container_id))
        recovered = self.fixture.manager.ensure_running()
        self.assertEqual(recovered.container_id, facts.container_id)
        self.assertEqual(recovered.state, "running")

    def test_endpoint_partial_duplicate_and_stale_truth_fail_closed(self) -> None:
        facts = self.fixture.manager.ensure_running()
        create_count = sum(command[:2] == ("container", "create") for command in self.fixture.docker.commands)
        self.fixture.docker.endpoint = b'{"ID":"engine-b"}'
        with self.assertRaises(AgentEnvironmentError) as changed:
            self.fixture.manager.ensure_running()
        self.assertEqual(changed.exception.code, "docker_endpoint_changed")
        self.fixture.docker.endpoint = b'{"ID":"engine-a","Os":"linux","Arch":"amd64"}'
        self.fixture.docker.partial_inspect = True
        with self.assertRaises(AgentEnvironmentError) as partial:
            self.fixture.manager.ensure_running()
        self.assertEqual(partial.exception.code, "docker_truth_uncertain")
        self.fixture.docker.partial_inspect = False
        duplicate = self.fixture.docker._id()
        self.fixture.docker.containers[duplicate] = dict(self.fixture.docker.containers[facts.container_id])
        with self.assertRaises(AgentEnvironmentError) as conflict:
            self.fixture.manager.ensure_running()
        self.assertEqual(conflict.exception.code, "environment_identity_conflict")
        del self.fixture.docker.containers[duplicate]
        self.fixture.docker.containers[facts.container_id]["labels"][SPEC_LABEL] = "different"
        with self.assertRaises(AgentEnvironmentError) as stale:
            self.fixture.manager.ensure_running()
        self.assertEqual(stale.exception.code, "stale_spec")
        self.assertIn(facts.container_id, self.fixture.docker.containers)
        self.assertEqual(create_count, sum(command[:2] == ("container", "create") for command in self.fixture.docker.commands))

    def test_at_most_once_and_only_same_id_preacceptance_retry(self) -> None:
        self.fixture.manager.ensure_running()
        call = "1" * 32
        first = self.fixture.manager.execute("shell.exec", {"command": "set once=yes"}, call_id=call)
        replay = self.fixture.manager.execute("shell.exec", {"command": "set once=no"}, call_id=call)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.fixture.docker.dispatch_count[call], 1)
        self.fixture.docker.claims.clear()  # container-ledger loss cannot authorize replay
        replay_after_loss = self.fixture.manager.execute(
            "shell.exec", {"command": "set once=lost"}, call_id=call
        )
        self.assertTrue(replay_after_loss.replayed)
        self.assertEqual(self.fixture.docker.dispatch_count[call], 1)
        unknown = "2" * 32
        self.fixture.docker.ambiguous_exec = True
        with self.assertRaises(AgentEnvironmentError) as caught:
            self.fixture.manager.execute("shell.exec", {"command": "set ambiguous=yes"}, call_id=unknown)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(self.fixture.docker.dispatch_count[unknown], 1)
        with self.assertRaises(AgentEnvironmentError) as repeated_unknown:
            self.fixture.manager.execute("shell.exec", {"command": "set ambiguous=no"}, call_id=unknown)
        self.assertEqual(repeated_unknown.exception.code, "execution_outcome_unknown")
        self.assertEqual(self.fixture.docker.dispatch_count[unknown], 1)
        retry = "3" * 32
        self.fixture.docker.reject_stopped_once = True
        receipt = self.fixture.manager.execute("shell.exec", {"command": "get once"}, call_id=retry)
        self.assertEqual(receipt.container_id, first.container_id)
        self.assertEqual(self.fixture.docker.dispatch_count[retry], 1)

    def test_all_tool_routes_are_fixed_docker_exec_and_model_has_no_identity_input(self) -> None:
        for index, tool in enumerate((
            "shell.exec", "file.read", "file.search", "file.write", "file.edit",
            "file.patch", "execute_code",
        ), start=1):
            self.fixture.manager.execute(tool, {"command": f"route-{tool}"}, call_id=f"{index:032x}")
        dispatches = [
            command for command in self.fixture.docker.commands
            if command[:2] == ("container", "exec") and "claim-execute" in command
        ]
        self.assertEqual(len(dispatches), 7)
        container_ids = {command[3] for command in dispatches}
        self.assertEqual(len(container_ids), 1)
        self.assertTrue(all(command[4] == "/usr/local/lib/voice-agent/agent-helper" for command in dispatches))

    def test_background_receipt_reconciles_after_controller_restart_and_isolated_kill(self) -> None:
        started = self.fixture.manager.execute(
            "shell.exec", {"command": "preview", "background": True}
        )
        details = started.metadata["details"]
        receipt = details["process_receipt"]
        self.assertRegex(receipt, r"^[a-f0-9]{32}$")
        self.assertNotIn("pid", json.dumps(details))
        container_id = started.container_id
        restarted = AgentEnvironment(
            self.fixture.manager.config, state_root=self.fixture.manager.state_root,
            workspace=self.fixture.manager.workspace, cache=self.fixture.manager.cache,
            runner=self.fixture.docker, disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        poll = restarted.execute("process", {"action": "poll", "receipt": receipt})
        self.assertEqual(poll.metadata["details"]["state"], "running")
        logs = restarted.execute("process", {
            "action": "logs", "receipt": receipt, "offset": 0, "maximum_bytes": 1024,
        })
        self.assertEqual(logs.stdout, b"preview ready\n")
        restarted.execute("process", {
            "action": "write", "receipt": receipt,
            "data_base64": base64.b64encode(b"reload\n").decode(),
        })
        self.assertEqual(self.fixture.docker.processes[receipt]["stdin"], b"reload\n")
        waited = restarted.execute("process", {
            "action": "wait", "receipt": receipt, "timeout_seconds": 0.01,
        })
        self.assertTrue(waited.metadata["details"]["timed_out"])
        other = restarted.execute(
            "shell.exec", {"command": "watcher", "background": True}
        ).metadata["details"]["process_receipt"]
        restarted.cancel_call("f" * 32, container_id)
        self.assertEqual(self.fixture.docker.processes[receipt]["state"], "running")
        killed = restarted.execute("process", {"action": "kill", "receipt": receipt})
        self.assertEqual(killed.metadata["details"]["state"], "killed")
        self.assertEqual(self.fixture.docker.processes[other]["state"], "running")
        restarted.execute("process", {"action": "kill", "receipt": other})

    def test_stale_or_tampered_process_identity_never_signals_and_stop_truth_is_gone(self) -> None:
        started = self.fixture.manager.execute(
            "shell.exec", {"command": "watch", "background": True}
        )
        receipt = started.metadata["details"]["process_receipt"]
        registry = json.loads(self.fixture.manager.registry_path.read_text())
        registry["processes"][receipt]["identity"]["start_time"] += 1
        from voice_agent_v2.agent_environment import _atomic_private
        _atomic_private(self.fixture.manager.registry_path, registry)
        rejected = self.fixture.manager.execute("process", {"action": "kill", "receipt": receipt})
        self.assertEqual(rejected.status, "failed")
        self.assertEqual(self.fixture.docker.processes[receipt]["state"], "running")
        # Restore trusted controller identity, then an actual container stop
        # eliminates the old process while preserving and restarting the same ID.
        registry["processes"][receipt]["identity"]["start_time"] -= 1
        _atomic_private(self.fixture.manager.registry_path, registry)
        container_id = started.container_id
        self.fixture.docker.run(("container", "stop", "--time", "5", container_id))
        stopped_status = self.fixture.manager.status()
        self.assertEqual(stopped_status["process_receipts"][0]["state"], "gone")
        gone = self.fixture.manager.execute("process", {"action": "poll", "receipt": receipt})
        self.assertEqual(gone.metadata["details"]["state"], "gone")
        old_logs = self.fixture.manager.execute("process", {
            "action": "logs", "receipt": receipt, "offset": 0, "maximum_bytes": 1024,
        })
        self.assertEqual(old_logs.stdout, b"preview ready\n")
        self.assertEqual(self.fixture.manager.ensure_running().container_id, container_id)

    def test_resource_reserve_stops_but_never_removes_exact_environment(self) -> None:
        facts = self.fixture.manager.ensure_running()
        self.fixture.manager.disk_usage = lambda _path: Disk(10 << 30, 9 << 30, 1 << 30)
        with self.assertRaises(AgentEnvironmentError) as caught:
            self.fixture.manager.execute("shell.exec", {"command": "pressure"})
        self.assertEqual(caught.exception.code, "host_free_reserve_breached")
        self.assertEqual(self.fixture.manager.status()["state"], "resource_stopped")
        self.assertIn(facts.container_id, self.fixture.docker.containers)
        self.assertFalse(any(command[:2] == ("container", "rm") for command in self.fixture.docker.commands))

    def test_rebuild_selects_new_generation_and_retains_old_as_nonselectable(self) -> None:
        old = self.fixture.manager.ensure_running()
        rebuilt = self.fixture.manager.lifecycle("rebuild", confirmed=True)
        self.assertEqual(rebuilt["state"], "running")
        selected = self.fixture.manager.ensure_running()
        self.assertNotEqual(selected.container_id, old.container_id)
        self.assertEqual(selected.generation, old.generation + 1)
        self.assertIn(old.container_id, self.fixture.docker.containers)
        retained = self.fixture.manager.status()["retained_generations"]
        self.assertEqual(retained[0]["container_id_prefix"], old.container_id[:12])
        self.assertFalse(retained[0]["selectable"])
        self.fixture.manager.lifecycle("retire", confirmed=True)
        self.assertNotIn(old.container_id, self.fixture.docker.containers)
        self.assertIn(selected.container_id, self.fixture.docker.containers)

    def test_reset_is_confirmed_exact_and_next_use_lazily_creates_same_spec(self) -> None:
        old = self.fixture.manager.ensure_running()
        sentinel = self.fixture.manager.workspace / "kept.txt"
        sentinel.write_text("kept", encoding="utf-8")
        with self.assertRaisesRegex(AgentEnvironmentError, "confirmation_required"):
            self.fixture.manager.lifecycle("reset", confirmed=False)
        reset = self.fixture.manager.lifecycle("reset", confirmed=True)
        self.assertEqual(reset["state"], "absent")
        self.assertEqual(sentinel.read_text(), "kept")
        replacement = self.fixture.manager.ensure_running()
        self.assertNotEqual(replacement.container_id, old.container_id)
        self.assertEqual(replacement.generation, old.generation + 1)
        self.assertEqual(replacement.spec, old.spec)

    def test_failed_rebuild_never_selects_unvalidated_generation(self) -> None:
        old = self.fixture.manager.ensure_running()
        self.fixture.docker.fail_next_start = True
        with self.assertRaisesRegex(AgentEnvironmentError, "environment_unhealthy"):
            self.fixture.manager.lifecycle("rebuild", confirmed=True)
        registry = json.loads(self.fixture.manager.registry_path.read_text())
        self.assertEqual(registry["selected_container_id"], old.container_id)
        self.assertEqual(registry["generation"], old.generation)
        self.assertEqual(registry["retained"], [])
        self.assertEqual(set(self.fixture.docker.containers), {old.container_id})
        self.assertEqual(self.fixture.manager.ensure_running().container_id, old.container_id)

    def test_status_and_explicit_lifecycle_are_truthful_and_exact(self) -> None:
        facts = self.fixture.manager.ensure_running()
        status = self.fixture.manager.status()
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["rootfs_and_files_persist"])
        self.assertFalse(status["processes_survive_container_stop"])
        root = Path(__file__).resolve().parents[1]
        validate_schema(status, json.loads((root / "contracts/agent-environment-status.v2.schema.json").read_text()))
        receipt = self.fixture.manager.execute("shell.exec", {"command": "status-receipt"})
        validate_schema(receipt.document(), json.loads((root / "contracts/agent-call-receipt.v2.schema.json").read_text()))
        with self.assertRaises(AgentEnvironmentError):
            self.fixture.manager.lifecycle("remove", confirmed=False)
        removed = self.fixture.manager.lifecycle("remove", confirmed=True)
        self.assertEqual(removed["state"], "absent")
        self.assertNotIn(facts.container_id, self.fixture.docker.containers)
        destructive = [item for item in self.fixture.docker.commands if item[:2] in {("container", "stop"), ("container", "rm")}]
        self.assertTrue(all(item[-1] == facts.container_id or item[2] == facts.container_id for item in destructive))
        self.assertFalse(any("prune" in item for command in self.fixture.docker.commands for item in command))


class AdditionalMountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-mounts-")
        self.root = Path(self.temp.name)
        self.ro = self.root / "ro-data"; self.ro.mkdir(mode=0o700)
        self.rw = self.root / "rw-data"; self.rw.mkdir(mode=0o700)
        self.docker = FakeDocker()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _config(self, mounts: list[dict[str, str]]):
        document = parse_agent_config_v2(DEFAULT_CONFIG_BYTES).model.model_dump(mode="json")
        document["agent_environment"]["additional_mounts"] = mounts
        return parse_agent_config_v2(json.dumps(document).encode())

    def _manager(self, mounts: list[dict[str, str]]) -> AgentEnvironment:
        return AgentEnvironment(
            self._config(mounts), state_root=self.root / "private",
            workspace=self.root / "workspace", cache=self.root / "cache",
            runner=self.docker, disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def test_typed_ro_rw_mounts_are_explicit_pinned_and_disclosed(self) -> None:
        declarations = [
            {"source": str(self.ro), "destination": "/data/reference", "mode": "read_only"},
            {"source": str(self.rw), "destination": "/data/output", "mode": "read_write"},
        ]
        config = self._config(declarations)
        root = Path(__file__).resolve().parents[1]
        validate_schema(
            config.model.model_dump(mode="json"),
            json.loads((root / "contracts/agent-config.v2.schema.json").read_text()),
        )
        (self.ro / "sentinel").write_text("readable", encoding="utf-8")
        (self.rw / "sentinel").write_text("writable", encoding="utf-8")
        manager = AgentEnvironment(
            config, state_root=self.root / "private", workspace=self.root / "workspace",
            cache=self.root / "cache", runner=self.docker,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        facts = manager.ensure_running()
        mounts = self.docker._inspect(facts.container_id)["Mounts"]
        observed = {(item["Destination"], item["RW"]) for item in mounts}
        self.assertIn(("/data/reference", False), observed)
        self.assertIn(("/data/output", True), observed)
        status = manager.status()
        self.assertEqual(status["additional_mounts"], declarations)
        self.assertIn("exfiltratable", status["additional_mount_authority_warning"])
        self.assertFalse(status["docker_socket_mounted"])
        self.assertEqual(status["published_ports"], [])
        root = Path(__file__).resolve().parents[1]
        validate_schema(status, json.loads((root / "contracts/agent-environment-status.v2.schema.json").read_text()))

        changed = self._manager([
            {**declarations[0], "mode": "read_write"}, declarations[1],
        ])
        stale = changed.status()
        self.assertEqual(stale["state"], "stale_spec")
        self.assertIn(facts.container_id, self.docker.containers)
        changed.lifecycle("remove", confirmed=True)
        self.assertEqual((self.ro / "sentinel").read_text(), "readable")
        self.assertEqual((self.rw / "sentinel").read_text(), "writable")

    def test_mount_admission_rejects_symlink_custody_overlap_and_model_escape_fields(self) -> None:
        link = self.root / "linked"
        link.symlink_to(self.ro, target_is_directory=True)
        with self.assertRaisesRegex(AgentEnvironmentError, "additional_mount_custody_invalid"):
            self._manager([{"source": str(link), "destination": "/data/link", "mode": "read_only"}])
        with self.assertRaisesRegex(AgentEnvironmentError, "additional_mount_destination_forbidden"):
            self._manager([{"source": str(self.ro), "destination": "/proc/host", "mode": "read_only"}])
        document = parse_agent_config_v2(DEFAULT_CONFIG_BYTES).model.model_dump(mode="json")
        document["agent_environment"]["additional_mounts"] = [{
            "source": str(self.ro), "destination": "/data", "mode": "read_only",
            "docker_args": ["-v", "/:/host"],
        }]
        with self.assertRaises(AgentConfigError):
            parse_agent_config_v2(json.dumps(document).encode())

    def test_changed_mount_custody_blocks_as_stale_without_replacement(self) -> None:
        manager = self._manager([
            {"source": str(self.ro), "destination": "/data/reference", "mode": "read_only"},
        ])
        facts = manager.ensure_running()
        old = self.root / "old-ro"
        self.ro.rename(old)
        self.ro.mkdir(mode=0o700)
        status = manager.status()
        self.assertEqual(status["state"], "stale_spec")
        self.assertIn("mount_custody", status["mismatch_fields"])
        self.assertIn(facts.container_id, self.docker.containers)
        self.assertEqual(sum(command[:2] == ("container", "create") for command in self.docker.commands), 1)


class FakeModel:
    provider_mode = "local"
    provider_identity = PROVIDER_IDENTITY
    def __init__(self) -> None: self.count = 0
    def decide(self, request: str, cancellation: CancellationToken):
        self.count += 1
        json.loads(request)
        if self.count == 1: return {"kind": "operation", "tool": "shell.exec", "arguments": {"command": "set multi=шаг"}}
        if self.count == 2: return {"kind": "operation", "tool": "shell.exec", "arguments": {"command": "get multi"}}
        return {"kind": "final", "answer": "Готово: выполнила два шага."}
    def cancel(self) -> None: pass


class BlockingModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancelled = False
    def decide(self, request: str, cancellation: CancellationToken):
        self.entered.set()
        self.release.wait(1)
        return {"kind": "final", "answer": "late output"}
    def cancel(self) -> None:
        self.cancelled = True


class AgentRunTests(unittest.TestCase):
    def test_cancellation_drops_noncooperative_late_model_output(self) -> None:
        fixture = Fixture()
        try:
            model = BlockingModel()
            run = AgentRun(fixture.manager, model=model)
            errors: list[BaseException] = []
            thread = threading.Thread(
                target=lambda: self._capture_run_error(run, errors), daemon=True
            )
            thread.start()
            self.assertTrue(model.entered.wait(0.5))
            run.cancel()
            model.release.set()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(model.cancelled)
            self.assertEqual(getattr(errors[0], "code", None), "selected_provider_cancelled")
            self.assertEqual(fixture.docker.containers, {})
        finally:
            fixture.close()

    @staticmethod
    def _capture_run_error(run: AgentRun, errors: list[BaseException]) -> None:
        try:
            run.run(
                transcript="cancel this",
                identity=AgentRealtimeIdentity("session-cancel", 1, "turn-cancel", "request-cancel", 1),
            )
        except BaseException as error:
            errors.append(error)

    def test_natural_multistep_run_uses_exact_model_identity_and_persistent_container(self) -> None:
        fixture = Fixture()
        try:
            run = AgentRun(fixture.manager, model=FakeModel()).run(
                transcript="Создай отметку и проверь её.",
                identity=AgentRealtimeIdentity("session-e24", 1, "turn-e24", "request-e24", 1),
            )
            self.assertEqual(run.operations, 2)
            self.assertEqual(run.answer, "Готово: выполнила два шага.")
            self.assertEqual(run.document()["provider_identity"], PROVIDER_IDENTITY)
            self.assertFalse(run.document()["automatic_fallback"])
            self.assertEqual(len(fixture.docker.containers), 1)
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()

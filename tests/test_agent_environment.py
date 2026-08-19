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
        self.claims: dict[str, dict[str, object]] = {}
        self.dispatch_count: dict[str, int] = {}
        self.markers: dict[str, str] = {}

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
            "Mounts": [
                {"Type": "bind", "Source": container["workspace"], "Destination": "/workspace", "RW": True},
                {"Type": "bind", "Source": container["cache"], "Destination": "/cache", "RW": True},
            ],
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
                self.containers[identifier] = {
                    "labels": labels,
                    "state": "created",
                    "workspace": mounts[0].split(",")[1].removeprefix("src="),
                    "cache": mounts[1].split(",")[1].removeprefix("src="),
                }
                return DockerResult(0, (identifier + "\n").encode())
            if arguments[:2] == ("container", "start"):
                self.containers[arguments[2]]["state"] = "running"
                return DockerResult(0, (arguments[2] + "\n").encode())
            if arguments[:2] == ("container", "stop"):
                identifier = arguments[-1]
                self.containers[identifier]["state"] = "exited"
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
                command = str(data.get("command", ""))
                if command.startswith("set "):
                    key, value = command[4:].split("=", 1); self.markers[key] = value; output = value.encode()
                elif command.startswith("get "):
                    output = self.markers.get(command[4:], "missing").encode()
                else:
                    output = command.encode()
                document = {
                    "status": "completed", "exit_code": 0,
                    "stdout_base64": base64.b64encode(output).decode(),
                    "stderr_base64": "", "cwd": "/workspace", "replayed": False,
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
        self.assertEqual(parse_agent_config_v2(upgraded).semantic_revision, parsed.semantic_revision)


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
        retry = "3" * 32
        self.fixture.docker.reject_stopped_once = True
        receipt = self.fixture.manager.execute("shell.exec", {"command": "get once"}, call_id=retry)
        self.assertEqual(receipt.container_id, first.container_id)
        self.assertEqual(self.fixture.docker.dispatch_count[retry], 1)

    def test_all_tool_routes_are_fixed_docker_exec_and_model_has_no_identity_input(self) -> None:
        for index, tool in enumerate((
            "shell.exec", "file.read", "file.search", "file.write", "file.edit",
            "file.patch", "execute_code", "process", "receipt",
        ), start=1):
            self.fixture.manager.execute(tool, {"command": f"route-{tool}"}, call_id=f"{index:032x}")
        dispatches = [
            command for command in self.fixture.docker.commands
            if command[:2] == ("container", "exec") and "claim-execute" in command
        ]
        self.assertEqual(len(dispatches), 9)
        container_ids = {command[3] for command in dispatches}
        self.assertEqual(len(container_ids), 1)
        self.assertTrue(all(command[4] == "/usr/local/lib/voice-agent/agent-helper" for command in dispatches))

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
        self.fixture.manager.lifecycle("retire", confirmed=True)
        self.assertNotIn(old.container_id, self.fixture.docker.containers)
        self.assertIn(selected.container_id, self.fixture.docker.containers)

    def test_status_and_explicit_lifecycle_are_truthful_and_exact(self) -> None:
        facts = self.fixture.manager.ensure_running()
        status = self.fixture.manager.status()
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["rootfs_and_files_persist"])
        self.assertFalse(status["processes_survive_container_stop"])
        with self.assertRaises(AgentEnvironmentError):
            self.fixture.manager.lifecycle("remove", confirmed=False)
        removed = self.fixture.manager.lifecycle("remove", confirmed=True)
        self.assertEqual(removed["state"], "absent")
        self.assertNotIn(facts.container_id, self.fixture.docker.containers)
        destructive = [item for item in self.fixture.docker.commands if item[:2] in {("container", "stop"), ("container", "rm")}]
        self.assertTrue(all(item[-1] == facts.container_id or item[2] == facts.container_id for item in destructive))
        self.assertFalse(any("prune" in item for command in self.fixture.docker.commands for item in command))


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


class AgentRunTests(unittest.TestCase):
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

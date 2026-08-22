from __future__ import annotations

import base64
from collections import namedtuple
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from voice_agent_v2.agent_config import AgentUserContext
from voice_agent_v2.agent_environment import (
    AgentEnvironment, AgentEnvironmentError, CallReceipt, DOCKER_DAEMON_INFO_FORMAT,
    DockerCLI, DockerResult,
    GENERATION_LABEL, MANAGED_LABEL, OWNER_LABEL, SCHEMA_LABEL, SPEC_LABEL,
)
from voice_agent_v2.agent_environment_config import (
    DEFAULT_CONFIG_BYTES, load_agent_config_v2, parse_agent_config_v2, upgrade_v1_to_v2,
)
from voice_agent_v2.agent_environment_image import (
    IMAGE_CONTEXT_LABEL, IMAGE_MANAGED_LABEL, IMAGE_SCHEMA_LABEL,
    ImageCommandResult, PreparedImageError, PreparedImageSelection, load_native_image_contract,
    load_prepared_image, prepare_native_image,
)
from voice_agent_v2.agent_run import (
    MAX_CONVERSATION_BYTES, AgentRealtimeIdentity, AgentRun,
)
from voice_agent_v2.agent_run_provider import AgentRunProvider
from voice_agent_v2.contracts import (
    AudioFormat, LLM_VERSION, STT_VERSION, TTS_VERSION, StageFailure,
)
from voice_agent_v2.local_lfm import PROVIDER_IDENTITY
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.stand_dev import _make_immutable
from voice_agent_v2.tracer import CancellationToken

Disk = namedtuple("Disk", "total used free")
TEST_PREPARED_IMAGE = PreparedImageSelection(
    "sha256:" + "5" * 64, "c" * 64, "linux/amd64", "amd64",
)


class DockerCLICustodyTests(unittest.TestCase):
    UID = os.geteuid()
    ENDPOINT = Path(f"/run/user/{UID}/docker.sock")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="private-docker-client-")
        self.private_home = Path(self.temporary.name) / "private/client"
        self.private_home.parent.mkdir(mode=0o700)
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.engine_id = "rootless-engine-a"
        self.rootless = True
        self.image_present = True

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _metadata(mode: int, *, uid: int, inode: int) -> os.stat_result:
        return os.stat_result((mode, inode, 7, 1, uid, uid, 0, 0, 0, 0))

    def _lstat(self, path: Path) -> os.stat_result:
        facts = {
            Path("/run"): self._metadata(stat.S_IFDIR | 0o755, uid=0, inode=1),
            Path("/run/user"): self._metadata(stat.S_IFDIR | 0o755, uid=0, inode=2),
            self.ENDPOINT.parent: self._metadata(stat.S_IFDIR | 0o700, uid=self.UID, inode=3),
            self.ENDPOINT: self._metadata(stat.S_IFSOCK | 0o660, uid=self.UID, inode=4),
        }
        if path not in facts:
            raise FileNotFoundError(path)
        return facts[path]

    @staticmethod
    def _image_document() -> dict[str, object]:
        return {
            "Id": TEST_PREPARED_IMAGE.image_id,
            "Os": "linux", "Architecture": "amd64",
            "Config": {
                "User": "1000:1000",
                "Entrypoint": ["/sbin/tini", "--"],
                "Cmd": ["/usr/local/lib/voice-agent/agent-helper", "init-container"],
                "Labels": {
                    IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1",
                    IMAGE_CONTEXT_LABEL: TEST_PREPARED_IMAGE.context_revision,
                },
            },
            "RootFS": {"Layers": ["sha256:" + "6" * 64]},
        }

    def _subprocess(self, arguments, **options) -> subprocess.CompletedProcess[bytes]:
        command = tuple(arguments[1:])
        self.commands.append(command)
        self.environments.append(dict(options["env"]))
        if command == ("info", "--format", DOCKER_DAEMON_INFO_FORMAT):
            options = ["name=seccomp", "name=rootless"] if self.rootless else ["name=seccomp"]
            output = json.dumps({
                "ID": self.engine_id, "OSType": "linux", "Architecture": "amd64",
                "SecurityOptions": options,
            }).encode()
            return subprocess.CompletedProcess(arguments, 0, output, b"")
        if command == ("image", "inspect", TEST_PREPARED_IMAGE.image_id):
            if not self.image_present:
                return subprocess.CompletedProcess(arguments, 1, b"[]\n", b"private path must stay private")
            return subprocess.CompletedProcess(
                arguments, 0, json.dumps([self._image_document()]).encode(), b"",
            )
        raise AssertionError(f"unexpected private Docker command: {command!r}")

    def _client(self) -> DockerCLI:
        with patch("voice_agent_v2.agent_environment.os.geteuid", return_value=self.UID):
            client = DockerCLI(self.private_home)
        client.binary = "/usr/bin/docker"
        return client

    def test_absent_product_root_and_nested_client_are_private_under_permissive_umask(self) -> None:
        root = Path(self.temporary.name) / "environment"
        root.mkdir(mode=0o700)
        manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        previous_umask = os.umask(0)
        try:
            with (
                patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
                patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
            ):
                document = manager.status()
        finally:
            os.umask(previous_umask)
        self.assertEqual(document["state"], "absent")
        self.assertIsNone(document["reason_code"])
        self.assertEqual(stat.S_IMODE(manager.state_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((manager.state_root / "docker-client").stat().st_mode), 0o700)
        self.assertEqual(list((manager.state_root / "docker-client").iterdir()), [])
        self.assertFalse(manager.registry_path.exists())
        self.assertFalse(any(command[:2] == ("container", "create") for command in self.commands))

        docker = FakeDocker()
        manager.runner = docker
        facts = manager.ensure_running()
        self.assertEqual(facts.state, "running")
        self.assertTrue(manager.registry_path.is_file())
        self.assertEqual(len(docker.containers), 1)
        self.assertEqual(stat.S_IMODE(manager.state_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((manager.state_root / "docker-client").stat().st_mode), 0o700)

    def test_explicit_current_user_rootless_endpoint_ignores_ambient_context_and_credentials(self) -> None:
        client = self._client()
        ambient = {
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_CONTEXT": "ssh://remote.invalid/host-selected-context",
            "DOCKER_CONFIG": "/host/docker-config",
            "DOCKER_AUTH_CONFIG": "host-registry-credential",
            "XDG_RUNTIME_DIR": "/foreign/runtime",
        }
        with (
            patch.dict(os.environ, ambient, clear=False),
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            result = client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.commands, [
            ("info", "--format", DOCKER_DAEMON_INFO_FORMAT),
            ("image", "inspect", TEST_PREPARED_IMAGE.image_id),
        ])
        expected = {
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "HOME": str(self.private_home), "DOCKER_CONFIG": str(self.private_home),
            "DOCKER_HOST": f"unix://{self.ENDPOINT}",
        }
        self.assertTrue(self.environments)
        self.assertTrue(all(environment == expected for environment in self.environments))
        self.assertEqual(list(self.private_home.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.private_home.stat().st_mode), 0o700)

    def test_prepared_image_inspect_reaches_pinned_daemon_and_status_progresses_to_absent(self) -> None:
        root = Path(self.temporary.name) / "environment"
        root.mkdir(mode=0o700)
        manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        manager.runner = self._client()
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            document = manager.status()
        self.assertEqual(document["state"], "absent")
        self.assertIsNone(document["reason_code"])
        self.assertIn(("image", "inspect", TEST_PREPARED_IMAGE.image_id), self.commands)
        self.assertFalse(any(command[:2] == ("container", "create") for command in self.commands))
        self.assertFalse(manager.registry_path.exists())

    def test_status_distinguishes_actual_image_absence_from_endpoint_failure(self) -> None:
        root = Path(self.temporary.name) / "missing-image"
        root.mkdir(mode=0o700)
        manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        manager.runner = self._client()
        self.image_present = False
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            document = manager.status()
        self.assertEqual(document["state"], "unavailable")
        self.assertEqual(document["reason_code"], "prepared_image_missing")
        self.assertNotIn("docker.sock", json.dumps(document))
        self.assertIn(("image", "inspect", TEST_PREPARED_IMAGE.image_id), self.commands)

    def test_missing_foreign_changed_or_rootful_endpoint_fails_before_requested_operation(self) -> None:
        cases: list[tuple[str, object, str]] = []

        def missing(path: Path) -> os.stat_result:
            if path == self.ENDPOINT:
                raise FileNotFoundError(path)
            return self._lstat(path)

        def foreign(path: Path) -> os.stat_result:
            fact = self._lstat(path)
            if path == self.ENDPOINT:
                return self._metadata(fact.st_mode, uid=0, inode=fact.st_ino)
            return fact

        cases.extend((
            ("missing", missing, "docker_endpoint_unavailable"),
            ("foreign", foreign, "docker_endpoint_incompatible"),
        ))
        for name, lstat_side_effect, reason in cases:
            with self.subTest(name=name):
                self.commands.clear(); self.environments.clear()
                client = self._client()
                with (
                    patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=lstat_side_effect),
                    patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
                ):
                    with self.assertRaises(AgentEnvironmentError) as caught:
                        client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
                self.assertEqual(caught.exception.code, reason)
                self.assertEqual(str(caught.exception), reason)
                self.assertEqual(self.commands, [])

        self.commands.clear(); self.environments.clear()
        root = Path(self.temporary.name) / "missing-status"
        root.mkdir(mode=0o700)
        manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        manager.runner = self._client()
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=missing),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            document = manager.status()
        self.assertEqual(document["state"], "unavailable")
        self.assertEqual(document["reason_code"], "docker_endpoint_unavailable")
        self.assertNotIn("/run/", json.dumps(document))
        self.assertEqual(self.commands, [])

        self.commands.clear(); self.environments.clear(); self.rootless = False
        client = self._client()
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            with self.assertRaises(AgentEnvironmentError) as caught:
                client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
        self.assertEqual(caught.exception.code, "docker_endpoint_incompatible")
        self.assertNotIn(("image", "inspect", TEST_PREPARED_IMAGE.image_id), self.commands)

        self.commands.clear(); self.environments.clear(); self.rootless = True
        socket_observations = 0

        def changed(path: Path) -> os.stat_result:
            nonlocal socket_observations
            fact = self._lstat(path)
            if path == self.ENDPOINT:
                socket_observations += 1
                return self._metadata(fact.st_mode, uid=self.UID, inode=3 + socket_observations)
            return fact

        client = self._client()
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=changed),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            with self.assertRaises(AgentEnvironmentError) as caught:
                client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
        self.assertEqual(caught.exception.code, "docker_endpoint_changed")
        self.assertIn(("image", "inspect", TEST_PREPARED_IMAGE.image_id), self.commands)

    def test_daemon_identity_change_is_rejected_without_retry_or_fallback(self) -> None:
        client = self._client()
        with (
            patch("voice_agent_v2.agent_environment.Path.lstat", autospec=True, side_effect=self._lstat),
            patch("voice_agent_v2.agent_environment.subprocess.run", side_effect=self._subprocess),
        ):
            client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
            self.engine_id = "rootless-engine-b"
            with self.assertRaises(AgentEnvironmentError) as caught:
                client.run(("image", "inspect", TEST_PREPARED_IMAGE.image_id))
        self.assertEqual(caught.exception.code, "docker_endpoint_changed")
        self.assertEqual(
            self.commands.count(("image", "inspect", TEST_PREPARED_IMAGE.image_id)), 1,
        )
        self.assertTrue(all(
            environment["DOCKER_HOST"] == f"unix://{self.ENDPOINT}"
            for environment in self.environments
        ))


class FakeDocker:
    def __init__(self, prepared_image: PreparedImageSelection = TEST_PREPARED_IMAGE) -> None:
        self.lock = threading.Lock()
        self.prepared_image = prepared_image
        self.image_mismatch = False
        self.commands: list[tuple[str, ...]] = []
        self.containers: dict[str, dict[str, object]] = {}
        self.next_id = 1
        self.endpoint = b'{"ID":"engine-a","OSType":"linux","Architecture":"amd64","SecurityOptions":["name=rootless"]}'
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
            "Image": self.prepared_image.image_id,
            "Platform": "linux",
            "Config": {
                "Image": self.prepared_image.image_id,
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
            if arguments == ("info", "--format", DOCKER_DAEMON_INFO_FORMAT):
                return DockerResult(0, self.endpoint + b"\n")
            if arguments[:3] == ("container", "ls", "-a"):
                owner_filter = next(value for value in arguments if value.startswith("label=" + OWNER_LABEL))
                owner = owner_filter.rsplit("=", 1)[1]
                ids = [identifier for identifier, item in self.containers.items() if item["labels"].get(OWNER_LABEL) == owner and item["labels"].get(MANAGED_LABEL) == "1"]
                return DockerResult(0, ("\n".join(ids) + ("\n" if ids else "")).encode())
            if arguments[:2] == ("image", "inspect"):
                if arguments[2] != self.prepared_image.image_id:
                    return DockerResult(1, stderr=b"No such image")
                document = {
                    "Id": self.prepared_image.image_id,
                    "Os": "linux", "Architecture": self.prepared_image.architecture,
                    "Config": {
                        "User": "0:0" if self.image_mismatch else "1000:1000",
                        "Entrypoint": ["/sbin/tini", "--"],
                        "Cmd": ["/usr/local/lib/voice-agent/agent-helper", "init-container"],
                        "Labels": {
                            IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1",
                            IMAGE_CONTEXT_LABEL: self.prepared_image.context_revision,
                        },
                    },
                    "RootFS": {"Layers": ["sha256:" + "6" * 64]},
                }
                return DockerResult(0, json.dumps([document]).encode())
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


class FakeNativePreparationDocker:
    """Build-only fake that selects one exact image for the runtime fake."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.builds = 0

    def run(self, arguments, *, cwd: Path | None = None) -> ImageCommandResult:
        del cwd
        command = tuple(arguments)
        self.commands.append(command)
        if command == ("docker", "version", "--format", "{{json .Server}}"):
            return ImageCommandResult(0, '{"ID":"engine-a","Os":"linux","Arch":"amd64"}\n')
        if command == ("docker", "info", "--format", "{{json .SecurityOptions}}"):
            return ImageCommandResult(0, '["name=rootless","name=cgroupns"]\n')
        if command[:4] == ("docker", "buildx", "build", "--load"):
            self.builds += 1
            revision = command[command.index("--build-arg") + 1].split("=", 1)[1]
            tag = command[command.index("--tag") + 1]
            image_id = "sha256:" + f"{self.builds:064x}"
            document = {
                "Id": image_id, "Os": "linux", "Architecture": "amd64",
                "Config": {
                    "User": "1000:1000",
                    "Entrypoint": ["/sbin/tini", "--"],
                    "Cmd": ["/usr/local/lib/voice-agent/agent-helper", "init-container"],
                    "Labels": {
                        IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1",
                        IMAGE_CONTEXT_LABEL: revision,
                    },
                },
                "RootFS": {"Layers": ["sha256:" + "6" * 64]},
            }
            self.images[tag] = document
            self.images[image_id] = document
            return ImageCommandResult(0)
        if command[:3] == ("docker", "image", "inspect"):
            document = self.images.get(command[3])
            if document is None:
                return ImageCommandResult(1, stderr="missing")
            return ImageCommandResult(0, json.dumps([document]))
        return ImageCommandResult(1, stderr="unexpected")


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-environment-")
        root = Path(self.temp.name)
        self.docker = FakeDocker()
        self.manager = AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            runner=self.docker, prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def close(self) -> None:
        self.temp.cleanup()


class AgentEnvironmentPrivateRootCustodyTests(unittest.TestCase):
    def _manager(self, root: Path, docker: FakeDocker) -> AgentEnvironment:
        return AgentEnvironment(
            parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
            state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
            runner=docker, prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def test_existing_safe_broad_root_is_hardened_before_status_and_first_use(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-environment-private-root-") as temporary:
            root = Path(temporary)
            state_root = root / "private"
            state_root.mkdir(mode=0o755)
            state_root.chmod(0o755)
            docker = FakeDocker()
            manager = self._manager(root, docker)

            document = manager.status()

            self.assertEqual(document["state"], "absent")
            self.assertIsNone(document["reason_code"])
            self.assertEqual(stat.S_IMODE(state_root.stat().st_mode), 0o700)
            self.assertFalse(manager.registry_path.exists())
            self.assertEqual(docker.containers, {})
            self.assertFalse(any(
                command[:2] == ("container", "create") for command in docker.commands
            ))

            facts = manager.ensure_running()

            self.assertEqual(facts.state, "running")
            self.assertTrue(manager.registry_path.is_file())
            self.assertEqual(len(docker.containers), 1)
            self.assertEqual(
                sum(command[:2] == ("container", "create") for command in docker.commands),
                1,
            )

    def test_unsafe_root_types_and_custody_fail_before_docker_or_registry_mutation(self) -> None:
        cases = ("symlink", "non-directory", "foreign-owner", "cross-device", "identity-drift", "writable")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix=f"agent-environment-private-root-{case}-"
            ) as temporary:
                root = Path(temporary)
                state_root = root / "private"
                target: Path | None = None
                if case == "symlink":
                    target = root / "target"
                    target.mkdir(mode=0o755)
                    target.chmod(0o755)
                    state_root.symlink_to(target, target_is_directory=True)
                elif case == "non-directory":
                    state_root.write_bytes(b"not a directory")
                    state_root.chmod(0o600)
                else:
                    state_root.mkdir(mode=0o700)
                    if case == "writable":
                        state_root.chmod(0o777)
                docker = FakeDocker()
                manager = self._manager(root, docker)
                real_lstat = os.lstat
                real_fstat = os.fstat
                fstat_calls = 0

                def lstat(candidate, *arguments, **options):
                    fact = real_lstat(candidate, *arguments, **options)
                    if os.fspath(candidate) != os.fspath(state_root):
                        return fact
                    values = list(fact)
                    if case == "foreign-owner":
                        values[4] = fact.st_uid + 1
                    elif case == "cross-device":
                        values[2] = fact.st_dev + 1
                    return os.stat_result(values)

                def fstat(descriptor):
                    nonlocal fstat_calls
                    fact = real_fstat(descriptor)
                    fstat_calls += 1
                    if case == "identity-drift" and fstat_calls == 2:
                        values = list(fact)
                        values[1] = fact.st_ino + 1
                        return os.stat_result(values)
                    return fact

                with (
                    patch("voice_agent_v2.agent_environment.os.lstat", side_effect=lstat),
                    patch("voice_agent_v2.agent_environment.os.fstat", side_effect=fstat),
                    patch("voice_agent_v2.agent_environment.os.fchmod", wraps=os.fchmod) as fchmod,
                ):
                    document = manager.status()

                self.assertEqual(document["state"], "unavailable")
                self.assertEqual(document["reason_code"], "environment_state_unsafe")
                self.assertFalse(manager.registry_path.exists())
                self.assertEqual(docker.commands, [])
                self.assertEqual(docker.containers, {})
                fchmod.assert_not_called()
                if target is not None:
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)


class ImmutableReleasePreparedImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="immutable-runtime-image-")
        self.root = Path(self.temporary.name)
        self.canonical = self.root / "canonical-source"
        self.canonical.mkdir()
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "agent-environment",
            self.canonical / "agent-environment",
        )
        self.image_state = self.root / "agent-image/private"
        self.preparation = FakeNativePreparationDocker()
        prepare_native_image(
            state_root=self.image_state,
            source_root=self.canonical,
            runner=self.preparation,
        )
        contract = load_native_image_contract(self.canonical)
        self.selection = load_prepared_image(self.image_state, contract).selected
        self.instance = self.root / "instances/dev"
        profile = self.instance / "config/agent-profile"
        profile.mkdir(mode=0o700, parents=True)
        for name in ("memory", "sessions", "skills"):
            (profile / name).mkdir(mode=0o700)
        (profile / "config.yaml").write_bytes(DEFAULT_CONFIG_BYTES)
        (profile / "config.yaml").chmod(0o600)
        (profile / "SOUL.md").write_bytes(b"")
        (profile / "SOUL.md").chmod(0o600)
        with patch.dict(os.environ, {"VOICE_AGENT_INSTANCE_ROOT": str(self.instance)}):
            context = AgentUserContext.effective()
            self.config = load_agent_config_v2(
                context.profile_root / "config.yaml", context=context,
            )

    def tearDown(self) -> None:
        for directory, _, files in os.walk(self.root, topdown=False):
            for name in files:
                Path(directory, name).chmod(0o600)
            Path(directory).chmod(0o700)
        self.temporary.cleanup()

    def _release_source(self, name: str) -> Path:
        source = self.root / name / "source"
        source.mkdir(parents=True)
        shutil.copytree(
            self.canonical / "agent-environment", source / "agent-environment",
        )
        return source

    def _manager(self, name: str, runner: FakeDocker) -> AgentEnvironment:
        runtime_root = self.root / f"runtime-{name}"
        runtime_root.mkdir(mode=0o700, exist_ok=True)
        return AgentEnvironment(
            self.config,
            state_root=runtime_root / "private",
            workspace=runtime_root / "workspace",
            cache=runtime_root / "cache",
            runner=runner,
            image_state_root=self.image_state,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def _assert_unavailable(
        self, name: str, source: Path, reason: str, *, image_mismatch: bool = False,
    ) -> None:
        runner = FakeDocker(self.selection)
        runner.image_mismatch = image_mismatch
        manager = self._manager(name, runner)
        with patch("voice_agent_v2.agent_environment.IMAGE_SOURCE_ROOT", source):
            document = manager.status()
        self.assertEqual(document["state"], "unavailable")
        self.assertEqual(document["reason_code"], reason)
        self.assertTrue(manager.state_root.is_dir())
        self.assertEqual(stat.S_IMODE(manager.state_root.stat().st_mode), 0o700)
        self.assertFalse(manager.registry_path.exists())
        self.assertEqual(runner.containers, {})
        self.assertFalse(any(
            command[:2] in {
                ("container", "create"), ("container", "start"),
                ("container", "stop"), ("container", "exec"), ("container", "rm"),
            }
            for command in runner.commands
        ))

    def test_prepared_image_runtime_accepts_only_exact_immutable_release_transform(self) -> None:
        self.assertEqual(self.preparation.builds, 1)

        exact = self._release_source("exact")
        _make_immutable(exact)
        self.assertEqual((exact / "agent-environment/Dockerfile").stat().st_mode & 0o777, 0o444)
        self.assertEqual((exact / "agent-environment/helpers/agent-helper").stat().st_mode & 0o777, 0o555)
        with self.assertRaisesRegex(PreparedImageError, "image_context_invalid"):
            load_native_image_contract(exact)

        unexpected_mode = self._release_source("unexpected-mode")
        _make_immutable(unexpected_mode)
        (unexpected_mode / "agent-environment/helpers/agent-helper").chmod(0o500)
        self._assert_unavailable(
            "unexpected-mode", unexpected_mode, "image_context_invalid",
        )

        mixed_modes = self._release_source("mixed-modes")
        (mixed_modes / "agent-environment/Dockerfile").chmod(0o444)
        self._assert_unavailable("mixed-modes", mixed_modes, "image_context_invalid")

        changed_bytes = self._release_source("changed-bytes")
        _make_immutable(changed_bytes)
        dockerfile = changed_bytes / "agent-environment/Dockerfile"
        dockerfile.chmod(0o644)
        dockerfile.write_bytes(dockerfile.read_bytes() + b"# changed after release\n")
        dockerfile.chmod(0o444)
        self._assert_unavailable("changed-bytes", changed_bytes, "image_context_invalid")

        changed_context = self._release_source("changed-context")
        helper = changed_context / "agent-environment/helpers/agent-helper"
        helper.write_bytes(helper.read_bytes() + b"# different locked context\n")
        helper.chmod(0o755)
        lock_path = changed_context / "agent-environment/image-lock.v2.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for item in lock["context_files"]:
            if item["path"] == "helpers/agent-helper":
                item["sha256"] = hashlib.sha256(helper.read_bytes()).hexdigest()
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        _make_immutable(changed_context)
        self._assert_unavailable(
            "changed-context", changed_context, "image_preparation_stale",
        )

        self._assert_unavailable(
            "inspect-mismatch", exact, "prepared_image_mismatch", image_mismatch=True,
        )

        runner = FakeDocker(self.selection)
        manager = self._manager("exact", runner)
        with patch("voice_agent_v2.agent_environment.IMAGE_SOURCE_ROOT", exact):
            facts = manager.ensure_running()
            document = manager.status()
        self.assertEqual(facts.raw["Image"], self.selection.image_id)
        self.assertEqual(document["state"], "running")
        self.assertEqual(len(runner.containers), 1)
        self.assertEqual(
            sum(command[:2] == ("container", "create") for command in runner.commands),
            1,
        )


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
        image = json.loads(json.dumps(parsed.model.model_dump(mode="json")))
        image["agent_environment"]["image"]["reference"] = "mutable:latest"
        with self.assertRaises(AgentConfigError):
            parse_agent_config_v2(json.dumps(image).encode())
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

    def test_missing_preparation_is_unavailable_and_runtime_never_builds_pulls_or_publishes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="missing-prepared-image-") as temporary:
            root = Path(temporary)
            docker = FakeDocker()
            manager = AgentEnvironment(
                parse_agent_config_v2(DEFAULT_CONFIG_BYTES),
                state_root=root / "private", workspace=root / "workspace", cache=root / "cache",
                image_state_root=root / "shared-image/private", runner=docker,
                disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
            )
            self.assertEqual(manager.status()["reason_code"], "image_preparation_missing")
            with self.assertRaisesRegex(AgentEnvironmentError, "image_preparation_missing"):
                manager.ensure_running()
            flattened = " ".join(" ".join(command) for command in docker.commands)
            self.assertNotIn("build", flattened)
            self.assertNotIn("pull", flattened)
            self.assertNotIn("push", flattened)
            self.assertEqual(docker.containers, {})

    def test_prepared_image_is_freshly_inspected_before_every_reuse(self) -> None:
        self.fixture.manager.ensure_running()
        self.fixture.manager.ensure_running()
        inspections = [
            command for command in self.fixture.docker.commands
            if command[:2] == ("image", "inspect")
        ]
        self.assertEqual(len(inspections), 2)
        self.assertTrue(all(command[2] == TEST_PREPARED_IMAGE.image_id for command in inspections))

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
            runner=self.fixture.docker, prepared_image=TEST_PREPARED_IMAGE,
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
            runner=self.fixture.docker, prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
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
        self.fixture.docker.endpoint = b'{"ID":"engine-b","OSType":"linux","Architecture":"amd64","SecurityOptions":["name=rootless"]}'
        with self.assertRaises(AgentEnvironmentError) as changed:
            self.fixture.manager.ensure_running()
        self.assertEqual(changed.exception.code, "docker_endpoint_changed")
        self.fixture.docker.endpoint = b'{"ID":"engine-a","OSType":"linux","Architecture":"amd64","SecurityOptions":["name=rootless"]}'
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
            runner=self.fixture.docker, prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
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
            runner=self.docker, prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
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
            prepared_image=TEST_PREPARED_IMAGE,
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
        self.count += 1
        self.entered.set()
        self.release.wait(1)
        return {"kind": "final", "answer": "late output"}
    def cancel(self) -> None:
        self.cancelled = True


class AgentRunTests(unittest.TestCase):
    def test_agent_run_language_admission_precedes_visibility_and_any_tts_media(self) -> None:
        class FinalModel:
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            def __init__(self, answer: str) -> None:
                self.answer = answer
            def decide(self, _request: str, _cancellation: CancellationToken):
                return {"kind": "final", "answer": self.answer}
            def cancel(self) -> None: pass

        class StaticSTT:
            version = STT_VERSION
            def transcribe(self, **_arguments) -> str: return "Проверка."

        class CountingTTS:
            version = TTS_VERSION
            output_format = AudioFormat()
            def __init__(self) -> None: self.calls = 0
            def stream_synthesize(self, **_arguments):
                self.calls += 1
                yield b"\0\0"

        class VoiceProvider:
            version = LLM_VERSION
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            supports_visible_handoff = True
            visible_handoff_is_cumulative = True
            def __init__(self, run: AgentRun) -> None: self.run = run
            def respond_with_handoff(
                self, *, session_id, stream_epoch, turn_id, request_id,
                turn_generation, transcript, on_sentence, on_visible_sentence,
                cancellation,
            ):
                result = self.run.run(
                    transcript=transcript,
                    identity=AgentRealtimeIdentity(
                        session_id, stream_epoch, turn_id, request_id,
                        turn_generation,
                    ),
                    cancellation=cancellation,
                )
                on_visible_sentence(result.answer)
                on_sentence(result.answer)
                return result.answer
            def cancel_request(self) -> None: self.run.cancel()

        cases = (
            ("all-english", "This answer is entirely unsupported."),
            ("mixed", "Русский ответ with an English clarification."),
            ("structural", "связи English clarification follows."),
        )
        for name, answer in cases:
            with self.subTest(name=name):
                fixture = Fixture(); tts = CountingTTS()
                observations: list[dict[str, object]] = []
                try:
                    controller = RealTurnController(
                        StaticSTT(), VoiceProvider(AgentRun(
                            fixture.manager, model=FinalModel(answer),
                            observation=observations.append,
                        )), tts,
                    )
                    result = controller.run_turn(
                        session_id=f"session-{name}", turn_id=f"turn-{name}",
                        request_id=f"request-{name}", input_pcm=b"\0\0" * 160,
                    )
                    self.assertEqual(result.terminal_event["payload"]["code"], "tts_language_unsupported")
                    self.assertNotIn("llm.visible", [item["type"] for item in result.events])
                    self.assertEqual(tts.calls, 0)
                    self.assertEqual(fixture.docker.containers, {})
                    self.assertEqual(len(observations), 1)
                    self.assertEqual(
                        observations[0]["reason_code"],
                        "tts_language_unsupported",
                    )
                    rendered = json.dumps(observations[0], ensure_ascii=False)
                    self.assertNotIn("text", rendered.lower())
                    self.assertNotIn("hash", rendered.lower())
                finally:
                    fixture.close()

        fixture = Fixture(); tts = CountingTTS()
        try:
            answer = "Сегодня 14.08.2026 в 09:30; PDF, SSD и HTTP готовы."
            result = RealTurnController(
                StaticSTT(), VoiceProvider(AgentRun(
                    fixture.manager, model=FinalModel(answer)
                )), tts,
            ).run_turn(
                session_id="session-russian", turn_id="turn-russian",
                request_id="request-russian", input_pcm=b"\0\0" * 160,
            )
            self.assertEqual(result.terminal_event["type"], "turn.completed")
            self.assertEqual(tts.calls, 1)
        finally:
            fixture.close()

    def test_final_promise_is_explicitly_receipt_free_no_operation(self) -> None:
        fixture = Fixture()
        class PromiseModel:
            provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
            def decide(self, _request, _cancellation):
                return {"kind": "final", "answer": "Я создам файл позже."}
            def cancel(self): pass
        try:
            run = AgentRun(fixture.manager, model=PromiseModel()).run(
                transcript="Создай файл.",
                identity=AgentRealtimeIdentity(
                    "session-promise", 1, "turn-promise", "request-promise", 1,
                ),
            )
            self.assertEqual((run.operations, run.action_outcome), (0, "no_operation"))
            self.assertEqual(run.document()["operation_count"], 0)
            self.assertEqual(run.document()["action_outcome"], "no_operation")
            self.assertEqual(fixture.docker.containers, {})
        finally:
            fixture.close()

    def test_helper_and_speech_failures_keep_separate_server_outcomes(self) -> None:
        fixture = Fixture()

        class OperationThenFinalModel:
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            def __init__(self) -> None: self.count = 0
            def decide(self, _request, _cancellation):
                self.count += 1
                if self.count == 1:
                    return {
                        "kind": "operation", "tool": "shell.exec",
                        "arguments": {"command": "проверка"},
                    }
                return {"kind": "final", "answer": "Операция завершилась ошибкой."}
            def cancel(self): pass

        class StaticSTT:
            version = STT_VERSION
            def transcribe(self, **_arguments): return "Выполни проверку."

        class FailingTTS:
            version = TTS_VERSION
            output_format = AudioFormat()
            def stream_synthesize(self, **_arguments):
                raise StageFailure("tts", "silero_synthesis_failed")
                yield b""  # pragma: no cover

        model = OperationThenFinalModel()
        provider = AgentRunProvider.__new__(AgentRunProvider)
        provider.selected = model
        provider.environment = fixture.manager
        provider._context_lock = threading.Lock()
        provider._contexts = {}
        provider._turn_records = {}
        provider._turn_provenance = {}
        provider._observations = []
        provider.agent_run = AgentRun(fixture.manager, model=model)

        def failed_execute(_tool, _arguments, *, call_id, timeout_seconds):
            del timeout_seconds
            return CallReceipt(
                call_id, next(iter(fixture.docker.containers)), 1,
                "failed", 2, b"", b"synthetic failure", "/workspace",
            )

        try:
            with patch.object(fixture.manager, "execute", side_effect=failed_execute):
                result = RealTurnController(
                    StaticSTT(), provider, FailingTTS()
                ).run_turn(
                    session_id="session-failed-outcomes",
                    turn_id="turn-failed-outcomes",
                    request_id="request-failed-outcomes",
                    input_pcm=b"\0\0" * 160,
                )
            terminal = result.terminal_event["payload"]
            self.assertEqual(terminal["code"], "silero_synthesis_failed")
            self.assertEqual(terminal["operation_count"], 1)
            self.assertEqual(terminal["action_outcome"], "failed")
            self.assertEqual(terminal["speech_outcome"], "failed")
        finally:
            provider.close()
            fixture.close()

    def test_agent_run_provider_context_is_bounded_isolated_and_transactional(self) -> None:
        fixture = Fixture()
        class ContextModel:
            provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
            def __init__(self) -> None:
                self.requests: list[dict[str, object]] = []
                self.answer = "Контекст принят."
            def decide(self, request, _cancellation):
                document = json.loads(request); self.requests.append(document)
                return {"kind": "final", "answer": self.answer}
            def cancel(self): pass

        model = ContextModel()
        provider = AgentRunProvider.__new__(AgentRunProvider)
        provider.selected = model
        provider.environment = fixture.manager
        provider._context_lock = threading.Lock()
        provider._contexts = {}
        provider._turn_records = {}
        provider._turn_provenance = {}
        provider._observations = []
        provider.agent_run = AgentRun(fixture.manager, model=model)

        def turn(session: str, index: int, transcript: str = "Вопрос.") -> str:
            return provider.respond_with_handoff(
                session_id=session, stream_epoch=1,
                turn_id=f"turn-{index:08d}", request_id=f"request-{index:08d}",
                turn_generation=index, transcript=transcript,
                on_sentence=lambda _value: None,
                on_visible_sentence=lambda _value: None,
            )

        try:
            turn("session-context", 1, "Первый вопрос.")
            first_snapshot = provider.snapshot_session("session-context")
            self.assertEqual(len(first_snapshot), 2)
            turn("session-context", 2, "Второй вопрос.")
            self.assertEqual(model.requests[1]["conversation"], list(first_snapshot))
            self.assertEqual(model.requests[1]["history"], [])
            self.assertEqual(model.requests[-1]["conversation"][-1]["terminal_status"], "completed")
            self.assertEqual(provider.snapshot_session("session-other"), ())

            before_third = provider.snapshot_session("session-context")
            turn("session-context", 3, "Третий вопрос.")
            bounded = provider.snapshot_session("session-context")
            self.assertEqual(len(bounded), 4)
            self.assertNotIn("Первый вопрос.", json.dumps(bounded, ensure_ascii=False))
            self.assertLessEqual(
                len(json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()),
                MAX_CONVERSATION_BYTES,
            )

            provider.restore_session("session-context", before_third)
            self.assertEqual(provider.snapshot_session("session-context"), before_third)
            provider.restore_session("session-context", first_snapshot)
            provider.retain_visible_failed_turn("session-context", "turn-00000003")
            visible_failed = provider.snapshot_session("session-context")
            self.assertEqual(visible_failed[-1]["terminal_status"], "visible_tts_failed")
            self.assertEqual(visible_failed[-1]["action_outcome"], "no_operation")
            self.assertNotIn("receipt", json.dumps(visible_failed, ensure_ascii=False).lower())
            provider.finish_turn("session-context", "turn-00000003")
            self.assertEqual(provider._turn_records, {})

            provider.reset_session("session-context")
            self.assertEqual(provider.snapshot_session("session-context"), ())
            turn("session-context", 4, "После переподключения.")
            self.assertEqual(model.requests[-1]["conversation"], [])

            provider.reset_session("session-context")
            model.answer = "я " * 650
            bounded_input = "у " * 1_024
            turn("session-context", 5, bounded_input)
            turn("session-context", 6, bounded_input)
            byte_bounded = provider.snapshot_session("session-context")
            self.assertEqual(len(byte_bounded), 2)
            self.assertLessEqual(
                len(json.dumps(
                    byte_bounded, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode()),
                MAX_CONVERSATION_BYTES,
            )

            provider.close()
            self.assertEqual(provider.snapshot_session("session-context"), ())
        finally:
            fixture.close()

    def test_operation_and_no_operation_turns_share_receipt_free_conversation(self) -> None:
        fixture = Fixture()

        class OperationContextModel:
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            def __init__(self) -> None: self.requests: list[dict[str, object]] = []
            def decide(self, request, _cancellation):
                document = json.loads(request)
                self.requests.append(document)
                if document["user_request"] == "Выполни операцию." and not document["history"]:
                    return {
                        "kind": "operation", "tool": "shell.exec",
                        "arguments": {"command": "готово"},
                    }
                return {"kind": "final", "answer": "Готово."}
            def cancel(self): pass

        model = OperationContextModel()
        provider = AgentRunProvider.__new__(AgentRunProvider)
        provider.selected = model
        provider.environment = fixture.manager
        provider._context_lock = threading.Lock()
        provider._contexts = {}
        provider._turn_records = {}
        provider._turn_provenance = {}
        provider._observations = []
        provider.agent_run = AgentRun(fixture.manager, model=model)

        def turn(index: int, transcript: str) -> None:
            provider.respond_with_handoff(
                session_id="session-operation-context", stream_epoch=1,
                turn_id=f"turn-{index:08d}", request_id=f"request-{index:08d}",
                turn_generation=index, transcript=transcript,
                on_sentence=lambda _value: None,
                on_visible_sentence=lambda _value: None,
            )

        try:
            turn(1, "Выполни операцию.")
            operation_context = provider.snapshot_session(
                "session-operation-context"
            )
            self.assertEqual(operation_context[-1]["operation_count"], 1)
            self.assertEqual(operation_context[-1]["action_outcome"], "completed")
            serialized = json.dumps(operation_context, ensure_ascii=False).lower()
            for forbidden in ("receipt", "call_id", "stdout", "stderr", "container"):
                self.assertNotIn(forbidden, serialized)

            turn(2, "Продолжи разговор.")
            followup = model.requests[-1]
            self.assertEqual(followup["conversation"], list(operation_context))
            self.assertEqual(followup["history"], [])
            latest = provider.snapshot_session("session-operation-context")[-1]
            self.assertEqual(latest["operation_count"], 0)
            self.assertEqual(latest["action_outcome"], "no_operation")
        finally:
            provider.close()
            fixture.close()

    def test_invalid_decision_fails_one_turn_before_environment_or_tts_mutation(self) -> None:
        marker = "PRIVATE_INVALID_DECISION_7391"
        fixture = Fixture()

        class InvalidModel(FakeModel):
            def decide(self, request: str, cancellation: CancellationToken):
                json.loads(request)
                return {"kind": "unknown", "private": marker}

        run = AgentRun(fixture.manager, model=InvalidModel())

        class AgentVoiceProvider:
            version = LLM_VERSION
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            supports_visible_handoff = True
            visible_handoff_is_cumulative = True

            def respond_with_handoff(
                self, *, session_id, stream_epoch, turn_id, request_id,
                turn_generation, transcript, on_sentence,
                on_visible_sentence, cancellation,
            ):
                result = run.run(
                    transcript=transcript,
                    identity=AgentRealtimeIdentity(
                        session_id, stream_epoch, turn_id, request_id,
                        turn_generation,
                    ),
                    cancellation=cancellation,
                )
                on_visible_sentence(result.answer)
                on_sentence(result.answer)
                return result.answer

            def cancel_request(self) -> None:
                run.cancel()

        class StaticSTT:
            version = STT_VERSION

            def transcribe(self, **_arguments) -> str:
                return "harmless request"

        class CountingTTS:
            version = TTS_VERSION
            output_format = AudioFormat()

            def __init__(self) -> None:
                self.calls = 0

            def stream_synthesize(self, **_arguments):
                self.calls += 1
                yield b"\0\0"

        tts = CountingTTS()
        controller = RealTurnController(StaticSTT(), AgentVoiceProvider(), tts)
        try:
            with (
                patch.object(
                    fixture.manager, "ensure_running",
                    wraps=fixture.manager.ensure_running,
                ) as ensure_running,
                patch.object(
                    fixture.manager, "execute", wraps=fixture.manager.execute,
                ) as execute,
            ):
                result = controller.run_turn(
                    session_id="session-invalid-decision",
                    turn_id="turn-invalid-decision",
                    request_id="request-invalid-decision",
                    input_pcm=b"\0\0" * 160,
                )
            terminal = result.terminal_event
            self.assertEqual(terminal["type"], "turn.failed")
            self.assertEqual(terminal["payload"], {
                "outcome": "failed",
                "stage": "llm_provider",
                "code": "agent_decision_invalid",
            })
            self.assertNotIn(marker, json.dumps(result.events))
            ensure_running.assert_not_called()
            execute.assert_not_called()
            self.assertEqual(tts.calls, 0)
            self.assertEqual(fixture.docker.containers, {})
        finally:
            fixture.close()

    def test_first_use_custody_failure_is_typed_without_helper_tts_or_readiness_mutation(self) -> None:
        fixture = Fixture()
        model = FakeModel()
        model.runtime_health = {"status": "ready", "failure_count": 0}
        readiness_before = dict(model.runtime_health)
        run = AgentRun(fixture.manager, model=model)

        class AgentVoiceProvider:
            version = LLM_VERSION
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            supports_visible_handoff = True
            visible_handoff_is_cumulative = True

            def respond_with_handoff(
                self, *, session_id, stream_epoch, turn_id, request_id,
                turn_generation, transcript, on_sentence,
                on_visible_sentence, cancellation,
            ):
                result = run.run(
                    transcript=transcript,
                    identity=AgentRealtimeIdentity(
                        session_id, stream_epoch, turn_id, request_id,
                        turn_generation,
                    ),
                    cancellation=cancellation,
                )
                on_visible_sentence(result.answer)
                on_sentence(result.answer)
                return result.answer

            def cancel_request(self) -> None:
                run.cancel()

        class StaticSTT:
            version = STT_VERSION

            def transcribe(self, **_arguments) -> str:
                return "harmless operation"

        class CountingTTS:
            version = TTS_VERSION
            output_format = AudioFormat()

            def __init__(self) -> None:
                self.calls = 0

            def stream_synthesize(self, **_arguments):
                self.calls += 1
                yield b"\0\0"

        tts = CountingTTS()
        controller = RealTurnController(StaticSTT(), AgentVoiceProvider(), tts)
        try:
            with (
                patch.object(
                    fixture.manager, "ensure_running",
                    side_effect=AgentEnvironmentError("environment_state_unsafe"),
                ) as ensure_running,
                patch.object(
                    fixture.manager, "execute", wraps=fixture.manager.execute,
                ) as execute,
            ):
                result = controller.run_turn(
                    session_id="session-private-root",
                    turn_id="turn-private-root",
                    request_id="request-private-root",
                    input_pcm=b"\0\0" * 160,
                )
            self.assertEqual(result.terminal_event["type"], "turn.failed")
            self.assertEqual(result.terminal_event["payload"], {
                "outcome": "failed",
                "stage": "agent_environment",
                "code": "environment_state_unsafe",
            })
            ensure_running.assert_called_once()
            execute.assert_not_called()
            self.assertEqual(tts.calls, 0)
            self.assertEqual(fixture.docker.commands, [])
            self.assertEqual(fixture.docker.containers, {})
            self.assertEqual(model.runtime_health, readiness_before)
        finally:
            fixture.close()

    def test_invalid_decision_matrix_is_turn_local_before_environment_tool_or_tts(self) -> None:
        cases = (
            ("malformed", "not-a-document", "agent_decision_invalid"),
            ("empty-answer", {"kind": "final", "answer": ""}, "agent_decision_invalid"),
            ("oversized-answer", {"kind": "final", "answer": "я" * 1_001}, "agent_decision_invalid"),
            ("unknown-field", {"kind": "final", "answer": "ok", "private": "PRIVATE_MATRIX_7391"}, "agent_decision_invalid"),
            ("unknown-tool", {"kind": "operation", "tool": "unknown", "arguments": {}}, "agent_decision_invalid"),
            (
                "invalid-citation",
                {
                    "kind": "final",
                    "answer": "ok",
                    "citations": [{"receipt_id": "bad", "claims": ["ok"], "spans": ["ok"]}],
                },
                "research_citation_invalid",
            ),
        )

        class InvalidModel(FakeModel):
            def __init__(self, decision: object) -> None:
                self.decision = decision

            def decide(self, request: str, cancellation: CancellationToken):
                json.loads(request)
                return self.decision

        class AgentVoiceProvider:
            version = LLM_VERSION
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY
            supports_visible_handoff = True
            visible_handoff_is_cumulative = True

            def __init__(self, run: AgentRun) -> None:
                self.run = run

            def respond_with_handoff(
                self, *, session_id, stream_epoch, turn_id, request_id,
                turn_generation, transcript, on_sentence,
                on_visible_sentence, cancellation,
            ):
                result = self.run.run(
                    transcript=transcript,
                    identity=AgentRealtimeIdentity(
                        session_id, stream_epoch, turn_id, request_id,
                        turn_generation,
                    ),
                    cancellation=cancellation,
                )
                on_visible_sentence(result.answer)
                on_sentence(result.answer)
                return result.answer

            def cancel_request(self) -> None:
                self.run.cancel()

        class StaticSTT:
            version = STT_VERSION

            def transcribe(self, **_arguments) -> str:
                return "harmless request"

        class CountingTTS:
            version = TTS_VERSION
            output_format = AudioFormat()

            def __init__(self) -> None:
                self.calls = 0

            def stream_synthesize(self, **_arguments):
                self.calls += 1
                yield b"\0\0"

        for name, decision, code in cases:
            with self.subTest(name=name):
                fixture = Fixture()
                tts = CountingTTS()
                run = AgentRun(fixture.manager, model=InvalidModel(decision))
                controller = RealTurnController(StaticSTT(), AgentVoiceProvider(run), tts)
                try:
                    with (
                        patch.object(
                            fixture.manager, "ensure_running",
                            wraps=fixture.manager.ensure_running,
                        ) as ensure_running,
                        patch.object(
                            fixture.manager, "execute",
                            wraps=fixture.manager.execute,
                        ) as execute,
                    ):
                        result = controller.run_turn(
                            session_id=f"session-{name}",
                            turn_id=f"turn-{name}",
                            request_id=f"request-{name}",
                            input_pcm=b"\0\0" * 160,
                        )
                    self.assertEqual(result.terminal_event["type"], "turn.failed")
                    self.assertEqual(result.terminal_event["payload"], {
                        "outcome": "failed",
                        "stage": "llm_provider",
                        "code": code,
                    })
                    self.assertNotIn("PRIVATE_MATRIX_7391", json.dumps(result.events))
                    ensure_running.assert_not_called()
                    execute.assert_not_called()
                    self.assertEqual(tts.calls, 0)
                    self.assertEqual(fixture.docker.containers, {})
                finally:
                    fixture.close()

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
            self.assertEqual(model.count, 1)
            self.assertEqual(getattr(errors[0], "code", None), "selected_provider_cancelled")
            self.assertEqual(run.provenance(AgentRealtimeIdentity(
                "session-cancel", 1, "turn-cancel", "request-cancel", 1,
            )), {"operation_count": 0, "action_outcome": "no_operation"})
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
            self.assertEqual(run.action_outcome, "completed")
            self.assertEqual(run.answer, "Готово: выполнила два шага.")
            self.assertEqual(run.document()["operation_count"], 2)
            self.assertEqual(run.document()["action_outcome"], "completed")
            self.assertEqual(run.document()["provider_identity"], PROVIDER_IDENTITY)
            self.assertFalse(run.document()["automatic_fallback"])
            self.assertEqual(len(fixture.docker.containers), 1)
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()

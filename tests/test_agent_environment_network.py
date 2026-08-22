from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from voice_agent_v2.agent_environment import (
    AgentEnvironment, AgentEnvironmentError, ByteStreamReceipt, DockerResult,
    TransportAcknowledgement,
)
from voice_agent_v2.agent_environment_config import DEFAULT_DOCUMENT, parse_agent_config_v2
from voice_agent_v2.agent_environment_credentials import InstallationCredentialStore
from voice_agent_v2.agent_config import AgentConfigError
from voice_agent_v2.agent_run import AgentRealtimeIdentity, AgentRun
from voice_agent_v2.local_lfm import PROVIDER_IDENTITY
from voice_agent_v2.schema import validate as validate_schema
from tests.test_agent_environment import Disk, FakeDocker, TEST_PREPARED_IMAGE


SYNTHETIC_OLD = b"synthetic-token-old-38"
SYNTHETIC_NEW = b"synthetic-token-new-38"
CREATE_OLD = b"synthetic-create-old-38"
CREATE_NEW = b"synthetic-create-new-38"
FILE_SECRET = b"\x00synthetic-file-token-38\xff"
DOWNLOAD = bytes(range(256)) * 257


class MutableCredentialStore:
    def __init__(self) -> None:
        self.environment = {"CREATE_TOKEN": CREATE_OLD, "API_TOKEN": SYNTHETIC_OLD}
        self.files = {"CREATE_TOKEN_FILE": b"create-file-38", "API_TOKEN_FILE": FILE_SECRET}
        self.key = b"k" * 32

    def resolve(self, kind: str, name: str) -> bytes:
        return (self.environment if kind == "environment" else self.files)[name]

    def fingerprint(self, kind: str, name: str) -> str:
        value = self.resolve(kind, name)
        return hmac.new(self.key, kind.encode() + b"\0" + name.encode() + b"\0" + value, hashlib.sha256).hexdigest()


def configured(*, network: bool = True):
    document = json.loads(json.dumps(DEFAULT_DOCUMENT))
    document["agent_environment"]["network"]["enabled"] = network
    document["agent_environment"]["credentials"] = {
        "creation_environment_names": ["CREATE_TOKEN"],
        "creation_file_names": ["CREATE_TOKEN_FILE"],
        "exec_environment_names": ["API_TOKEN"],
        "exec_file_names": ["API_TOKEN_FILE"],
    }
    return parse_agent_config_v2(json.dumps(document).encode())


class NetworkDocker(FakeDocker):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, bytes] = {}
        self.file_modes: dict[str, int] = {}
        self.credential_file_observations: list[tuple[str, int]] = []
        self.requests: list[tuple[str, bytes]] = []
        self.git_actions: list[str] = []
        self.old_process_token: bytes | None = None
        self.exec_environments: list[dict[str, str]] = []

    def _inspect(self, identifier: str) -> dict[str, object]:
        result = super()._inspect(identifier)
        container = self.containers[identifier]
        result["Config"]["Env"] = list(container.get("environment", []))
        result["HostConfig"]["NetworkMode"] = container.get("network", "bridge")
        return result

    @staticmethod
    def _options(arguments: tuple[str, ...], helper_index: int) -> dict[str, str]:
        result: dict[str, str] = {}
        for index, value in enumerate(arguments[:helper_index]):
            if value == "-e":
                key, item = arguments[index + 1].split("=", 1)
                result[key] = item
        return result

    @staticmethod
    def _argument(arguments: tuple[str, ...], name: str) -> str:
        return arguments[arguments.index(name) + 1]

    @staticmethod
    def _receipt(output: bytes, *, status: str = "completed") -> DockerResult:
        document = {
            "status": status, "exit_code": 0 if status == "completed" else 7,
            "stdout_base64": base64.b64encode(output).decode(), "stderr_base64": "",
            "stdout_bytes": len(output), "stdout_sha256": hashlib.sha256(output).hexdigest(),
            "stdout_truncated": False, "stderr_bytes": 0,
            "stderr_sha256": hashlib.sha256(b"").hexdigest(), "stderr_truncated": False,
            "cwd": "/workspace", "replayed": False,
            "details": {"kind": "synthetic_network_transcript"},
        }
        return DockerResult(0, json.dumps(document).encode(), accepted=True)

    def run(self, arguments: tuple[str, ...], *, stdin: bytes = b"", timeout: float = 15) -> DockerResult:
        if arguments[:2] == ("container", "stop"):
            for path in tuple(self.files):
                if path.startswith("/run/"):
                    del self.files[path]
                    self.file_modes.pop(path, None)
            return super().run(arguments, stdin=stdin, timeout=timeout)
        if arguments[:2] == ("container", "create"):
            result = super().run(arguments, stdin=stdin, timeout=timeout)
            identifier = result.stdout.decode().strip()
            environment = [arguments[index + 1] for index, item in enumerate(arguments) if item == "--env"]
            if "--env-file" in arguments:
                content = Path(self._argument(arguments, "--env-file")).read_text()
                environment.extend(line for line in content.splitlines() if line)
            self.containers[identifier]["environment"] = environment
            self.containers[identifier]["network"] = self._argument(arguments, "--network")
            values = dict(item.split("=", 1) for item in environment)
            for index in range(int(values.get("VOICE_AGENT_CREATE_FILE_COUNT", "0"))):
                name = values[f"VOICE_AGENT_CREATE_FILE_NAME_{index}"]
                path = f"/var/lib/voice-agent/credentials/{name}"
                self.files[path] = base64.b64decode(values[f"VOICE_AGENT_CREATE_FILE_B64_{index}"])
                self.file_modes[path] = 0o600
            return result
        if arguments[:2] != ("container", "exec"):
            return super().run(arguments, stdin=stdin, timeout=timeout)
        helper = "/usr/local/lib/voice-agent/agent-helper"
        if helper not in arguments:
            return super().run(arguments, stdin=stdin, timeout=timeout)
        helper_index = arguments.index(helper)
        container_id = arguments[helper_index - 1]
        command = arguments[helper_index + 1]
        if command in {"readiness", "cancel-call"}:
            return super().run(arguments, stdin=stdin, timeout=timeout)
        if command == "credential-file-put":
            scope = self._argument(arguments, "--scope")
            name = self._argument(arguments, "--name")
            if scope == "creation":
                path = f"/var/lib/voice-agent/credentials/{name}"
            else:
                call_id = self._argument(arguments, "--call-id")
                path = f"/run/voice-agent-credentials/{call_id}/{name}"
            if int(self._argument(arguments, "--expected-bytes")) != len(stdin):
                return DockerResult(2)
            if self._argument(arguments, "--expected-sha256") != hashlib.sha256(stdin).hexdigest():
                return DockerResult(2)
            self.files[path] = stdin
            self.file_modes[path] = 0o600
            self.credential_file_observations.append((path, 0o600))
            return DockerResult(0, b'{"status":"stored"}')
        if command == "credential-file-clean":
            prefix = f"/run/voice-agent-credentials/{self._argument(arguments, '--call-id')}/"
            for path in tuple(self.files):
                if path.startswith(prefix):
                    del self.files[path]
                    del self.file_modes[path]
            return DockerResult(0)
        if command == "stream-in":
            relative = self._argument(arguments, "--path")
            root = self._argument(arguments, "--root")
            transfer_id = self._argument(arguments, "--transfer-id")
            path = f"/{root}/{relative}"
            digest = hashlib.sha256(stdin).hexdigest()
            if int(self._argument(arguments, "--expected-bytes")) != len(stdin) or self._argument(arguments, "--expected-sha256") != digest:
                return DockerResult(2, accepted=True)
            self.files[path] = stdin
            self.file_modes[path] = 0o600
            return DockerResult(0, json.dumps({
                "atomic": True, "bytes": len(stdin), "mode": "0600", "path": path,
                "sha256": digest, "status": "stored", "transfer_id": transfer_id,
            }).encode(), accepted=True)
        if command == "stream-stat":
            path = f"/{self._argument(arguments, '--root')}/{self._argument(arguments, '--path')}"
            content = self.files[path]
            return DockerResult(0, json.dumps({"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}).encode())
        if command == "stream-out":
            path = f"/{self._argument(arguments, '--root')}/{self._argument(arguments, '--path')}"
            content = self.files[path]
            return DockerResult(0, content, accepted=True)
        if command != "claim-execute":
            raise AssertionError(arguments)
        environment = dict(
            item.split("=", 1) for item in self.containers[container_id].get("environment", [])
        )
        environment.update(self._options(arguments, helper_index))
        self.exec_environments.append(environment)
        request = json.loads(stdin)
        operation = str(request.get("command", ""))
        network = self.containers[container_id].get("network")
        if operation == "probe-dns-tls-curl":
            return self._receipt(b"dns+tls+curl-ok" if network == "bridge" else b"network-disabled", status="completed" if network == "bridge" else "failed")
        if operation in {"send-token-a", "send-token-b"}:
            token = environment["API_TOKEN"].encode()
            self.requests.append((operation[-1], token))
            return self._receipt(token)
        if operation == "read-file-token":
            return self._receipt(self.files[environment["API_TOKEN_FILE"]])
        if operation == "start-token-process":
            self.old_process_token = environment["API_TOKEN"].encode()
            return self._receipt(b"started")
        if operation == "read-token-process":
            return self._receipt(self.old_process_token or b"")
        if operation == "download":
            self.files["/workspace/download.bin"] = DOWNLOAD
            self.file_modes["/workspace/download.bin"] = 0o600
            return self._receipt(hashlib.sha256(DOWNLOAD).hexdigest().encode())
        if operation in {"upload", "multipart-upload"}:
            content = self.files["/workspace/download.bin"]
            self.requests.append((operation, content))
            return self._receipt(f"{len(content)}:{hashlib.sha256(content).hexdigest()}".encode())
        if operation in {"git-clone", "git-fetch", "git-push"}:
            self.git_actions.append(operation)
            self.requests.append((operation, environment["API_TOKEN"].encode()))
            return self._receipt(operation.encode())
        if operation == "persisted-download-hash":
            return self._receipt(hashlib.sha256(self.files["/workspace/download.bin"]).hexdigest().encode())
        return self._receipt(operation.encode())


class Fixture:
    def __init__(self, *, network: bool = True, store: MutableCredentialStore | None = None) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-environment-network-")
        root = Path(self.temp.name)
        self.store = store or MutableCredentialStore()
        self.docker = NetworkDocker()
        self.manager = AgentEnvironment(
            configured(network=network), state_root=root / "private", workspace=root / "workspace",
            cache=root / "cache", runner=self.docker, credential_store=self.store,
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )

    def close(self) -> None:
        self.temp.cleanup()


class NetworkCredentialStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_network_http_transfers_remote_git_and_no_domain_binding(self) -> None:
        manager = self.fixture.manager
        self.assertEqual(manager.execute("shell.exec", {"command": "probe-dns-tls-curl"}).status, "completed")
        raw = manager.execute("shell.exec", {"command": "send-token-a"}).stdout
        manager.execute("shell.exec", {"command": "send-token-b"})
        self.assertEqual(raw, SYNTHETIC_OLD)
        self.assertEqual(manager.redact_display(raw), b"[REDACTED]")
        self.assertEqual(self.fixture.docker.requests[:2], [("a", SYNTHETIC_OLD), ("b", SYNTHETIC_OLD)])
        downloaded = manager.execute("shell.exec", {"command": "download"})
        self.assertEqual(downloaded.stdout, hashlib.sha256(DOWNLOAD).hexdigest().encode())
        for command in ("upload", "multipart-upload", "git-clone", "git-fetch", "git-push"):
            self.assertEqual(manager.execute("shell.exec", {"command": command}).status, "completed")
        self.assertEqual(self.fixture.docker.git_actions, ["git-clone", "git-fetch", "git-push"])
        restarted = AgentEnvironment(
            manager.config, state_root=manager.state_root, workspace=manager.workspace, cache=manager.cache,
            runner=self.fixture.docker, credential_store=self.fixture.store,
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        self.assertEqual(
            restarted.execute("shell.exec", {"command": "persisted-download-hash"}).stdout,
            hashlib.sha256(DOWNLOAD).hexdigest().encode(),
        )
        self.assertTrue(all("SSH_AUTH_SOCK" not in item and "GIT_CONFIG_GLOBAL" not in item for item in self.fixture.docker.exec_environments))

    def test_model_receives_raw_tool_bytes_but_final_human_copy_is_redacted(self) -> None:
        class Model:
            provider_mode = "local"
            provider_identity = PROVIDER_IDENTITY

            def __init__(self) -> None:
                self.calls = 0

            def decide(self, request: str, _cancellation) -> dict[str, object]:
                self.calls += 1
                document = json.loads(request)
                if self.calls == 1:
                    return {"kind": "operation", "tool": "shell.exec", "arguments": {"command": "send-token-a"}}
                raw = base64.b64decode(document["history"][-1]["result"]["receipt"]["stdout"]["data_base64"])
                self.test_case.assertEqual(raw, SYNTHETIC_OLD)
                return {"kind": "final", "answer": f"Секрет: {raw.decode()}"}

            def cancel(self) -> None:
                return None

        model = Model()
        model.test_case = self
        result = AgentRun(self.fixture.manager, model=model).run(
            transcript="synthetic credential display boundary",
            identity=AgentRealtimeIdentity("session-e32", 1, "turn-e32", "request-e32", 1),
        )
        self.assertEqual(result.answer, "Секрет: [REDACTED]")

    def test_per_exec_rotation_running_process_and_scoped_file_cleanup(self) -> None:
        manager = self.fixture.manager
        original_spec = manager.spec
        self.assertEqual(manager.execute("shell.exec", {"command": "send-token-a"}).stdout, SYNTHETIC_OLD)
        manager.execute("shell.exec", {"command": "start-token-process"})
        file_result = manager.execute("shell.exec", {"command": "read-file-token"})
        self.assertEqual(file_result.stdout, FILE_SECRET)
        self.assertFalse(any(path.startswith("/run/voice-agent-credentials/") for path in self.fixture.docker.files))
        self.assertTrue(any(path.startswith("/run/voice-agent-credentials/") and mode == 0o600 for path, mode in self.fixture.docker.credential_file_observations))
        self.fixture.store.environment["API_TOKEN"] = SYNTHETIC_NEW
        rotated_file = b"rotated-file-token-38"
        self.fixture.store.files["API_TOKEN_FILE"] = rotated_file
        self.assertEqual(manager.execute("shell.exec", {"command": "send-token-a"}).stdout, SYNTHETIC_NEW)
        self.assertEqual(manager.execute("shell.exec", {"command": "read-file-token"}).stdout, rotated_file)
        self.assertEqual(manager.execute("shell.exec", {"command": "read-token-process"}).stdout, SYNTHETIC_OLD)
        self.assertEqual(manager.redact_display(SYNTHETIC_OLD + b"/" + SYNTHETIC_NEW), b"[REDACTED]/[REDACTED]")
        self.assertEqual(manager.spec, original_spec)
        container_id = manager.ensure_running().container_id
        self.fixture.docker.files["/run/voice-agent-credentials/leftover"] = b"old"
        self.fixture.docker.run(("container", "stop", "--time", "1", container_id))
        self.assertNotIn("/run/voice-agent-credentials/leftover", self.fixture.docker.files)
        self.assertEqual(manager.execute("shell.exec", {"command": "send-token-a"}).container_id, container_id)
        create_path = "/var/lib/voice-agent/credentials/CREATE_TOKEN_FILE"
        self.assertEqual(self.fixture.docker.file_modes[create_path], 0o600)

    def test_create_time_rotation_is_restart_pinned_stale_and_rebuilt_explicitly(self) -> None:
        manager = self.fixture.manager
        old = manager.ensure_running().container_id
        old_spec = manager.spec
        self.fixture.store.environment["CREATE_TOKEN"] = CREATE_NEW
        self.fixture.store.files["CREATE_TOKEN_FILE"] = b"create-file-rotated-38"
        self.assertEqual(manager.status()["state"], "running")
        replacement_controller = AgentEnvironment(
            manager.config, state_root=manager.state_root, workspace=manager.workspace, cache=manager.cache,
            runner=self.fixture.docker, credential_store=self.fixture.store,
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        self.assertNotEqual(replacement_controller.spec, old_spec)
        self.assertEqual(replacement_controller.status()["state"], "stale_spec")
        self.assertEqual(replacement_controller.lifecycle("rebuild", confirmed=True)["state"], "running")
        registry = json.loads(replacement_controller.registry_path.read_text())
        self.assertNotEqual(registry["selected_container_id"], old)
        labels = json.dumps([item["labels"] for item in self.fixture.docker.containers.values()])
        commands = json.dumps(self.fixture.docker.commands)
        direct_hash = hashlib.sha256(CREATE_NEW).hexdigest()
        self.assertNotIn(CREATE_NEW.decode(), commands)
        self.assertNotIn(CREATE_NEW.decode(), labels)
        self.assertNotIn(direct_hash, labels)
        status = json.dumps(replacement_controller.status())
        for secret in (CREATE_NEW, SYNTHETIC_NEW, FILE_SECRET):
            self.assertNotIn(base64.b64encode(secret).decode(), status)
            self.assertNotIn(secret.decode("utf-8", "ignore"), status)

    def test_binary_streams_exact_ack_unknown_and_no_host_path_interpretation(self) -> None:
        payload = b"\x00\xffstream-38\r\n" * 1000
        inbound = self.fixture.manager.stream_inbound(payload, root="workspace", relative_path="incoming/payload.bin")
        self.assertEqual((inbound.byte_count, inbound.sha256), (len(payload), hashlib.sha256(payload).hexdigest()))
        streamed, outbound = self.fixture.manager.stream_outbound(root="workspace", relative_path="incoming/payload.bin")
        self.assertEqual(streamed, payload)
        calls = 0

        def acknowledged(data: bytes) -> TransportAcknowledgement:
            nonlocal calls
            calls += 1
            self.assertEqual(data, payload)
            return TransportAcknowledgement("acknowledged", "controlled-target", len(data), hashlib.sha256(data).hexdigest())

        sent = self.fixture.manager.deliver_outbound(
            root="workspace", relative_path="incoming/payload.bin", target="controlled-target", transport=acknowledged,
        )
        self.assertEqual((sent.outcome, sent.acknowledged, calls), ("sent", True, 1))
        unknown_calls = 0

        def unknown(_data: bytes) -> TransportAcknowledgement:
            nonlocal unknown_calls
            unknown_calls += 1
            return TransportAcknowledgement("unknown")

        uncertain = self.fixture.manager.deliver_outbound(
            root="workspace", relative_path="incoming/payload.bin", target="controlled-target", transport=unknown,
        )
        self.assertEqual((uncertain.outcome, uncertain.acknowledged, unknown_calls), ("unknown", False, 1))
        with self.assertRaisesRegex(AgentEnvironmentError, "stream_input_out_of_bounds"):
            self.fixture.manager.stream_inbound(b"x" * 262145, root="workspace", relative_path="too-large.bin")
        with self.assertRaises(AgentEnvironmentError):
            self.fixture.manager.stream_inbound(payload, root="workspace", relative_path="/tmp/host-looking")
        with self.assertRaises(AgentEnvironmentError):
            self.fixture.manager.stream_outbound(root="workspace", relative_path="../host")
        self.assertTrue(all(command[:2] == ("container", "exec") for command in self.fixture.docker.commands if "stream-" in command))

    def test_status_is_nonsecret_and_truthful_about_authority_and_remote_effects(self) -> None:
        status = self.fixture.manager.status()
        self.assertEqual(status["credential_configuration_count"], 1)
        self.assertEqual({item["name"] for item in status["credential_exposure"]}, {"CREATE_TOKEN", "CREATE_TOKEN_FILE", "API_TOKEN", "API_TOKEN_FILE"})
        self.assertFalse(status["credential_values_or_fingerprints_exposed"])
        self.assertFalse(status["remote_effects_rolled_back_by_cancellation"])
        self.assertFalse(status["automatic_network_or_stream_retry"])
        self.assertEqual(status["published_ports"], [])
        root = Path(__file__).resolve().parents[1]
        validate_schema(status, json.loads((root / "contracts/agent-environment-status.v2.schema.json").read_text()))


class PrivateCustodyAndHelperTests(unittest.TestCase):
    def test_private_store_is_exact_mode_0600_and_rotates_without_ambient_lookup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="credential-store-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            path = root / "credentials.json"
            document = {
                "schema_version": "voice-agent.credentials.v1",
                "environment": {"API_TOKEN": base64.b64encode(SYNTHETIC_OLD).decode()},
                "files": {"API_TOKEN_FILE": base64.b64encode(FILE_SECRET).decode()},
            }
            path.write_text(json.dumps(document))
            path.chmod(0o600)
            previous = os.environ.get("API_TOKEN")
            os.environ["API_TOKEN"] = "ambient-must-not-win"
            try:
                store = InstallationCredentialStore(root)
                self.assertEqual(store.resolve("environment", "API_TOKEN"), SYNTHETIC_OLD)
                old_fingerprint = store.fingerprint("environment", "API_TOKEN")
                document["environment"]["API_TOKEN"] = base64.b64encode(SYNTHETIC_NEW).decode()
                path.write_text(json.dumps(document)); path.chmod(0o600)
                self.assertEqual(store.resolve("environment", "API_TOKEN"), SYNTHETIC_NEW)
                self.assertNotEqual(store.fingerprint("environment", "API_TOKEN"), old_fingerprint)
                self.assertEqual((root / "credential-fingerprint.key").stat().st_mode & 0o777, 0o600)
            finally:
                if previous is None:
                    del os.environ["API_TOKEN"]
                else:
                    os.environ["API_TOKEN"] = previous

    def test_helper_forwards_only_declared_names_and_keeps_raw_bytes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        helper = root / "agent-environment/helpers/agent-helper"
        with tempfile.TemporaryDirectory(prefix="credential-helper-") as temporary:
            environment = {
                "PATH": os.environ["PATH"], "VOICE_AGENT_LEDGER": str(Path(temporary) / "ledger"),
                "VOICE_AGENT_EXEC_ENV_NAMES": "API_TOKEN", "API_TOKEN": SYNTHETIC_OLD.decode(),
                "SSH_AUTH_SOCK": "/ambient/agent.sock", "HOST_ONLY_TOKEN": "ambient-secret",
            }
            command = "printf '%s|%s|%s' \"$API_TOKEN\" \"${SSH_AUTH_SOCK-absent}\" \"${HOST_ONLY_TOKEN-absent}\""
            completed = subprocess.run(
                [str(helper), "claim-execute", "--call-id", "a" * 32, "--tool", "shell-exec", "--cwd", temporary],
                input=json.dumps({"command": command}).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment, check=False, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = base64.b64decode(json.loads(completed.stdout)["stdout_base64"])
            self.assertEqual(output, SYNTHETIC_OLD + b"|absent|absent")


class NetworkDisabledAndSchemaTests(unittest.TestCase):
    def test_missing_private_credentials_disable_only_agent_operations_not_ordinary_answer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="missing-private-credential-") as temporary:
            root = Path(temporary)
            manager = AgentEnvironment(
                configured(), state_root=root / "private", workspace=root / "workspace",
                cache=root / "cache", runner=NetworkDocker(),
                prepared_image=TEST_PREPARED_IMAGE,
                disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
            )
            self.assertEqual(manager.status()["state"], "unavailable")
            with self.assertRaises(AgentEnvironmentError):
                manager.ensure_running()

            class FinalOnly:
                provider_mode = "local"
                provider_identity = PROVIDER_IDENTITY
                def decide(self, _request, _cancellation):
                    return {"kind": "final", "answer": "Обычный голосовой ответ остаётся доступен."}
                def cancel(self):
                    return None

            result = AgentRun(manager, model=FinalOnly()).run(
                transcript="ordinary voice",
                identity=AgentRealtimeIdentity("session-ready", 1, "turn-ready", "request-ready", 1),
            )
            self.assertEqual(result.answer, "Обычный голосовой ответ остаётся доступен.")

    def test_network_disabled_fixture_fails_closed_without_ports_or_fallback(self) -> None:
        fixture = Fixture(network=False)
        try:
            receipt = fixture.manager.execute("shell.exec", {"command": "probe-dns-tls-curl"})
            self.assertEqual((receipt.status, receipt.stdout), ("failed", b"network-disabled"))
            create = next(item for item in fixture.docker.commands if item[:2] == ("container", "create"))
            self.assertEqual(create[create.index("--network") + 1], "none")
            self.assertNotIn("--publish", create)
            self.assertNotIn("-p", create)
        finally:
            fixture.close()

    def test_schema_has_one_declaration_no_selector_overlap_or_raw_value(self) -> None:
        document = json.loads(json.dumps(DEFAULT_DOCUMENT))
        document["agent_environment"]["credentials"]["exec_environment_names"] = ["API_TOKEN"]
        self.assertEqual(parse_agent_config_v2(json.dumps(document).encode()).model.agent_environment.credentials.exec_environment_names, ("API_TOKEN",))
        for field, value in (("profiles", []), ("selector", "other"), ("API_TOKEN", "secret")):
            invalid = json.loads(json.dumps(document))
            invalid["agent_environment"]["credentials"][field] = value
            with self.assertRaises(AgentConfigError):
                parse_agent_config_v2(json.dumps(invalid).encode())
        overlap = json.loads(json.dumps(document))
        overlap["agent_environment"]["credentials"]["exec_file_names"] = ["API_TOKEN"]
        with self.assertRaises(AgentConfigError):
            parse_agent_config_v2(json.dumps(overlap).encode())


if __name__ == "__main__":
    unittest.main()

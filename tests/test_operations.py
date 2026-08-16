from __future__ import annotations

import configparser
from contextlib import redirect_stderr
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from voice_agent_v2 import operations_cli
from voice_agent_v2.operations import (
    DEFAULT_MANIFEST_RELATIVE,
    OperationalError,
    ReleaseStore,
    ValidationReport,
    _canonical_json,
    _operational_release_id,
    _run_git,
    _sha256_bytes,
    evaluate_sustained_run,
    execute_release,
    load_operations_manifest,
    parse_server_configuration,
    release_tree_digest,
    sha256_file,
    validate_server_configuration,
    verify_artifacts,
    verify_disk_policy,
    verify_tailnet,
    verify_tracked_manifest_alignment,
)
from voice_agent_v2.runtime_directory import SYSTEMD_RUNTIME_ROOT
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.slice6_config import Slice6Settings


ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = "priney-arch.example.ts.net"


def configuration_values(secret: str = "0123456789abcdef") -> dict[str, str]:
    return {
        "LIVEKIT_API_KEY": "slice9key",
        "LIVEKIT_API_SECRET": secret,
        "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_PUBLIC_URL": f"wss://{HOSTNAME}:7443",
        "SLICE6_LIVEKIT_NODE_IP": "100.64.0.10",
        "SLICE6_APP_PUBLIC_URL": f"https://{HOSTNAME}:8443",
        "SLICE6_APP_HTTPS_PORT": "8443",
        "SLICE6_SIGNAL_HTTPS_PORT": "7443",
        "SLICE6_ENABLE_TAILSCALE_SERVE": "1",
    }


def write_configuration(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(0o600)


class OperationsManifestTests(unittest.TestCase):
    def test_manifest_closes_component_order_restart_and_external_cloud_boundaries(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        components = {component["name"]: component for component in manifest["components"]}
        self.assertEqual(
            set(components),
            {
                "livekit", "web-gateway", "controller", "local-stt", "local-tts",
                "selected-local-llm", "provider-adapter", "avatar-host", "mvp-eye", "cloud-llm",
            },
        )
        self.assertEqual(components["cloud-llm"]["supervision"], "external-readiness-only")
        self.assertFalse(components["cloud-llm"]["active"])
        self.assertEqual(manifest["lifecycle"]["restart_policy"]["automatic_recoveries_per_failure_window"], 1)
        self.assertEqual(manifest["disk"]["cleanup_policy"], "refuse-without-deleting")
        verify_tracked_manifest_alignment(source_root=ROOT, operations_manifest=manifest)
        with tempfile.TemporaryDirectory() as temporary:
            changed = dict(manifest, unknown_policy=True)
            path = Path(temporary) / "operations.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(OperationalError, "unknown fields"):
                load_operations_manifest(path)

    def test_systemd_unit_matches_executable_restart_and_process_custody_contract(self) -> None:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(ROOT / "ops/systemd/voice-agent-v2.service")
        service = parser["Service"]
        unit = parser["Unit"]
        self.assertEqual(unit["StartLimitBurst"], "2")
        self.assertEqual(unit["StartLimitIntervalSec"], "600")
        self.assertEqual(service["Type"], "notify")
        self.assertEqual(service["NotifyAccess"], "main")
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(service["RestartPreventExitStatus"], "2")
        self.assertEqual(service["TimeoutStopSec"], "75s")
        self.assertEqual(service["KillMode"], "mixed")
        self.assertEqual(service["User"], "priney")
        self.assertIn("voice-agent-ops run", service["ExecStart"])
        self.assertNotIn(".local/share/voice-agent-v2", service["ReadWritePaths"])
        self.assertEqual(service["RuntimeDirectory"], "voice-agent-v2")
        self.assertEqual(service["RuntimeDirectoryMode"], "0700")
        self.assertEqual(service["RuntimeDirectoryPreserve"], "no")
        self.assertEqual(
            service["Environment"],
            "PYTHONPYCACHEPREFIX=/run/voice-agent-v2/pycache",
        )
        self.assertNotIn("LITELLM", "\n".join(service.values()))


class ServiceApplicationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, SimpleNamespace]:
        state = root / "state"
        release = state / "releases" / ("a" * 24)
        unit = release / "ops/systemd/voice-agent-v2.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n", encoding="utf-8")
        (state / "current").symlink_to(f"releases/{release.name}")
        return state, release, SimpleNamespace(state_root=state, restart=False)

    def test_reapplying_ready_service_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": release.name},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            self.assertFalse(any(command[0] in {"install", "systemctl"} and (
                command[0] == "install" or command[1] in {"enable", "start", "restart"}
            ) for command in calls))
            wait.assert_called_once_with(release.name)
            self.assertFalse(output.call_args.args[0]["changed"])

    def test_changed_unit_and_release_restart_before_ready_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(
                    returncode=1 if command[:2] == ("cmp", "-s") else 0,
                )

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}),
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": "b" * 24},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            self.assertIn(("systemctl", "restart", operations_cli.SERVICE_NAME), calls)
            wait.assert_called_once_with(release.name)
            self.assertTrue(output.call_args.args[0]["changed"])
            self.assertTrue(output.call_args.args[0]["ready"])
            self.assertTrue(output.call_args.args[0]["service_restarted"])

    def test_explicit_restart_revalidates_and_restarts_the_unchanged_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state, release, arguments = self._fixture(Path(temporary))
            arguments.restart = True
            calls: list[tuple[str, ...]] = []

            def sudo(*command: str, allowed: tuple[int, ...] = (0,)) -> SimpleNamespace:
                calls.append(command)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(operations_cli, "validate_release", return_value={}) as validate,
                patch.object(operations_cli, "_sudo", side_effect=sudo),
                patch.object(
                    operations_cli, "_runtime_status",
                    return_value={"release_id": release.name},
                ),
                patch.object(operations_cli, "_wait_for_runtime_release") as wait,
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_install_service(arguments)
            validate.assert_called_once_with(
                release, state_root=state.resolve(), verify_host_state=True,
            )
            self.assertIn(("systemctl", "restart", operations_cli.SERVICE_NAME), calls)
            wait.assert_called_once_with(release.name)
            result = output.call_args.args[0]
            self.assertTrue(result["restart_requested"])
            self.assertTrue(result["service_restarted"])
            self.assertTrue(result["changed"])

    def test_deploy_apply_signal_is_limited_to_release_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            arguments = SimpleNamespace(state_root=state, config=state / "runtime.env")
            with (
                patch.object(operations_cli, "DEFAULT_STATE_ROOT", state),
                patch.object(
                    operations_cli.ReleaseStore, "deploy",
                    return_value={"changed": False, "status": "no-op"},
                ),
                patch.object(operations_cli, "_systemctl_show", return_value={"load": "loaded"}),
                patch.object(operations_cli, "_print") as output,
            ):
                operations_cli.command_deploy(arguments)
            result = output.call_args.args[0]
            self.assertFalse(result["release_service_apply_required"])
            self.assertNotIn("service_apply_required", result)

    def test_run_exit_status_retries_only_transient_tailnet_unavailability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = [
                "voice-agent-ops", "run", "--state-root", str(Path(temporary).resolve()),
            ]
            for code, expected in (("tailnet_unavailable", 1), ("tailnet_incompatible", 2)):
                with self.subTest(code=code), patch.object(sys, "argv", arguments), patch.object(
                    operations_cli,
                    "execute_release",
                    side_effect=OperationalError(code, "content-free failure"),
                ), redirect_stderr(StringIO()):
                    self.assertEqual(operations_cli.main(), expected)


class ConfigurationAndArtifactTests(unittest.TestCase):
    def test_config_is_mode_guarded_without_persistable_secret_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            first = configuration_values("a" * 16)
            write_configuration(path, first)
            first_report = validate_server_configuration(parse_server_configuration(path))
            second = configuration_values("b" * 32)
            write_configuration(path, second)
            second_report = validate_server_configuration(parse_server_configuration(path))
            self.assertEqual(first_report["public_fingerprint"], second_report["public_fingerprint"])
            self.assertNotIn("configuration_revision", first_report)
            self.assertNotIn("configuration_revision", second_report)
            serialized = json.dumps(first_report)
            self.assertNotIn(first["LIVEKIT_API_SECRET"], serialized)
            self.assertNotIn(second["LIVEKIT_API_SECRET"], json.dumps(second_report))
            link = Path(temporary) / "runtime-link.env"
            link.symlink_to(path)
            with self.assertRaisesRegex(OperationalError, "unavailable"):
                parse_server_configuration(link)
            path.chmod(0o644)
            with self.assertRaisesRegex(OperationalError, "ownership or mode"):
                parse_server_configuration(path)

    def test_operational_capture_requires_the_systemd_runtime_boundary(self) -> None:
        values = configuration_values()
        values.update({
            "VOICE_AGENT_DIAGNOSTIC_CAPTURE": "1",
            "VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT": str(
                SYSTEMD_RUNTIME_ROOT / "private-captures"
            ),
        })
        report = validate_server_configuration(values)
        self.assertEqual(report["diagnostic_capture_ttl_seconds"], 900)
        values["VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT"] = (
            f"/run/user/{os.geteuid()}/voice-agent-v2/private-captures"
        )
        with self.assertRaisesRegex(OperationalError, "capture root is invalid"):
            validate_server_configuration(values)

    def test_equal_public_https_ports_are_rejected(self) -> None:
        values = configuration_values()
        values["LIVEKIT_PUBLIC_URL"] = f"wss://{HOSTNAME}:8443"
        values["SLICE6_SIGNAL_HTTPS_PORT"] = "8443"
        with self.assertRaisesRegex(OperationalError, "ports must differ"):
            validate_server_configuration(values)

    def test_tailnet_temporarily_offline_is_retryable_but_identity_and_auth_are_static(self) -> None:
        configuration = {
            "tailnet_hostname": HOSTNAME,
            "tailnet_node_ip": "100.64.0.10",
        }
        offline = subprocess.CompletedProcess(
            [], 0, json.dumps({
                "BackendState": "Running",
                "Self": {
                    "Online": False,
                    "DNSName": HOSTNAME,
                    "TailscaleIPs": ["100.64.0.10"],
                },
            }), "",
        )
        with patch("voice_agent_v2.operations.subprocess.run", return_value=offline):
            with self.assertRaises(OperationalError) as unavailable:
                verify_tailnet(configuration)
        self.assertEqual(unavailable.exception.code, "tailnet_unavailable")
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
            result = subprocess.CompletedProcess([], 0, json.dumps(document), "")
            with self.subTest(document=document), patch(
                "voice_agent_v2.operations.subprocess.run", return_value=result,
            ):
                with self.assertRaises(OperationalError) as incompatible:
                    verify_tailnet(configuration)
            self.assertEqual(incompatible.exception.code, "tailnet_incompatible")

    def test_unknown_provider_configuration_is_rejected_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            values = configuration_values()
            values["LITELLM_BASE_URL"] = "https://provider.invalid"
            write_configuration(path, values)
            with self.assertRaisesRegex(OperationalError, "unsupported name"):
                parse_server_configuration(path)

    def test_artifact_manifest_verifies_behavior_and_rejects_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            artifact = home / "artifact.bin"
            artifact.write_bytes(b"pinned artifact")
            manifest = {
                "artifacts": [{
                    "name": "fixture",
                    "path": "{home}/artifact.bin",
                    "size_bytes": artifact.stat().st_size,
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                }]
            }
            self.assertEqual(verify_artifacts(manifest, home=home), 1)
            artifact.write_bytes(b"changed artifact")
            with self.assertRaisesRegex(OperationalError, "size/type|checksum"):
                verify_artifacts(manifest, home=home)

    def test_disk_pressure_refuses_without_deleting_cache_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            retained = cache / "retained.bin"
            retained.write_bytes(b"retained")
            manifest = {
                "disk": {
                    "minimum_free_bytes": 1,
                    "cache_roots": [{
                        "name": "fixture", "path": str(cache), "maximum_bytes": 1,
                    }],
                }
            }
            with self.assertRaisesRegex(OperationalError, "non-destructive bound"):
                verify_disk_policy(manifest, home=root, filesystem_path=root)
            self.assertEqual(retained.read_bytes(), b"retained")


class ReleaseAndRollbackTests(unittest.TestCase):
    @staticmethod
    def _complete_inventory(root: Path) -> bytes:
        records: dict[str, dict[str, object]] = {}
        paths = [root, *root.rglob("*")]
        for path in sorted(
            paths,
            key=lambda item: "." if item == root else item.relative_to(root).as_posix(),
        ):
            relative = "." if path == root else path.relative_to(root).as_posix()
            metadata = path.lstat()
            record: dict[str, object] = {
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "mtime_ns": metadata.st_mtime_ns,
            }
            if path.is_symlink():
                record.update(kind="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):
                record.update(kind="directory")
            elif stat.S_ISREG(metadata.st_mode):
                record.update(
                    kind="file", size=metadata.st_size, sha256=sha256_file(path),
                )
            else:
                record.update(kind="unsupported")
            records[relative] = record
        return _canonical_json(records)

    def test_recovered_release_tracer_twice_preserves_complete_inventory(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            release = root / "state" / "releases" / ("a" * 24)
            release.parent.mkdir(parents=True)
            shutil.copytree(
                ROOT,
                release,
                symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".git", ".env.slice6", "node_modules", "__pycache__", "tmp",
                ),
            )
            current = root / "state" / "current"
            current.symlink_to(f"releases/{release.name}")
            mutable = root / "mutable"
            environment = dict(os.environ, VOICE_AGENT_MUTABLE_STATE_ROOT=str(mutable))
            direct = subprocess.run(
                [str(ROOT / "verify"), "--tracer-only"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(direct.returncode, 0, direct.stderr.decode(errors="replace"))
            before_inventory = self._complete_inventory(release)
            before_digest = release_tree_digest(release)
            recovered_outputs: list[bytes] = []
            for _run in range(2):
                recovered = subprocess.run(
                    [str(current / "verify"), "--tracer-only"],
                    cwd=current.resolve(),
                    env=environment,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    recovered.returncode, 0, recovered.stderr.decode(errors="replace"),
                )
                recovered_outputs.append(recovered.stdout)
                self.assertEqual(self._complete_inventory(release), before_inventory)
                self.assertEqual(release_tree_digest(release), before_digest)
            self.assertEqual(recovered_outputs, [direct.stdout, direct.stdout])

    def test_release_digest_covers_empty_directories_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_bytes(b"payload")
            payload.chmod(0o600)
            baseline = release_tree_digest(root)
            identity_inputs = {
                "commit": "a" * 40,
                "tree": "b" * 40,
                "manifest": "c" * 64,
                "configuration": "d" * 64,
                "configuration_locator": "e" * 64,
            }
            baseline_identity = _operational_release_id(
                **identity_inputs, release_tree=baseline,
            )
            (root / "empty-runtime-write").mkdir()
            changed = release_tree_digest(root)
            self.assertNotEqual(changed, baseline)
            self.assertNotEqual(
                _operational_release_id(**identity_inputs, release_tree=changed),
                baseline_identity,
            )
            (root / "empty-runtime-write").rmdir()
            payload.chmod(0o644)
            self.assertNotEqual(release_tree_digest(root), baseline)

    def test_deploying_same_release_is_an_observable_no_op(self) -> None:
        manifest_path = ROOT / DEFAULT_MANIFEST_RELATIVE
        commit = "a" * 40
        tree = "b" * 40
        fingerprint = "c" * 64
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            state = Path(temporary) / "state"
            config = Path(temporary) / "runtime.env"
            write_configuration(config, configuration_values())
            manifest_digest = sha256_file(manifest_path)
            locator_digest = _sha256_bytes(str(config.resolve()).encode("utf-8"))
            release_id = _sha256_bytes(_canonical_json({
                "commit": commit,
                "tree": tree,
                "manifest": manifest_digest,
                "configuration": fingerprint,
                "configuration_locator": locator_digest,
            }))[:24]
            target = state / "releases" / release_id
            target.mkdir(parents=True)
            (state / "current").symlink_to(f"releases/{release_id}")
            report = ValidationReport(
                None, None, "local", 1, {}, 10**12, fingerprint,
            )
            document = {
                "build_id": commit,
                "release_id": release_id,
                "source_tree": tree,
                "operations_manifest_sha256": manifest_digest,
                "configuration_path": str(config.resolve()),
                "configuration_locator_sha256": locator_digest,
                "configuration_fingerprint": fingerprint,
            }
            git_values = iter(["", commit, tree])
            with (
                patch("voice_agent_v2.operations._run_git", side_effect=lambda *_args: next(git_values)),
                patch("voice_agent_v2.operations.validate_host", return_value=report),
                patch("voice_agent_v2.operations.validate_release", return_value=document),
            ):
                result = ReleaseStore(state).deploy(source_root=ROOT, config_path=config)
            self.assertEqual(result["status"], "no-op")
            self.assertFalse(result["changed"])
            self.assertEqual((state / "current").resolve(), target.resolve())

    def test_release_archives_and_builds_the_captured_commit_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "config").mkdir()
            shutil.copy2(
                ROOT / DEFAULT_MANIFEST_RELATIVE,
                source / DEFAULT_MANIFEST_RELATIVE,
            )
            web = source / "web"
            (web / "src").mkdir(parents=True)
            (web / "src" / "build-marker.txt").write_text("committed", encoding="utf-8")
            (web / "package.json").write_text(
                '{"scripts":{"build":"fixture"}}\n', encoding="utf-8",
            )
            (source / ".gitignore").write_text("web/node_modules/\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Release Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "release@test.invalid"],
                check=True,
            )
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-qm", "captured"], check=True,
            )
            captured_commit = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            (web / "node_modules").mkdir()

            tools = root / "tools"
            tools.mkdir()
            npm = tools / "npm"
            npm.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--outDir') + 1])\n"
                "(out / 'assets').mkdir(parents=True, exist_ok=True)\n"
                "(out / 'index.html').write_text('<main></main>')\n"
                "marker = (pathlib.Path.cwd() / 'src/build-marker.txt').read_text()\n"
                "(out / 'assets/app.js').write_text(os.environ['VITE_APP_VERSION'] + marker)\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            config = root / "runtime.env"
            write_configuration(config, configuration_values())
            state = root / "state"
            report = ValidationReport(
                None, None, "local", 1, {}, 10**12, "c" * 64,
            )
            raced = False

            def run_git_with_head_change(path: Path, *arguments: str) -> str:
                nonlocal raced
                result = _run_git(path, *arguments)
                if not raced and arguments == ("rev-parse", f"{captured_commit}^{{tree}}"):
                    raced = True
                    (web / "src" / "build-marker.txt").write_text("raced", encoding="utf-8")
                    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
                    subprocess.run(
                        ["git", "-C", str(source), "commit", "-qm", "concurrent"],
                        check=True,
                    )
                return result

            environment = dict(os.environ)
            environment["PATH"] = f"{tools}:{environment['PATH']}"
            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "voice_agent_v2.operations._run_git",
                    side_effect=run_git_with_head_change,
                ),
                patch("voice_agent_v2.operations.validate_host", return_value=report),
                patch("voice_agent_v2.operations.validate_release", return_value={}),
            ):
                result = ReleaseStore(state).deploy(
                    source_root=source, config_path=config,
                )
            release = (state / "current").resolve()
            release_document = json.loads(
                (release / "release.json").read_text(encoding="utf-8")
            )
            built_asset = next((release / "web" / "dist" / "assets").glob("*.js"))
            self.assertEqual(
                release_document["schema_version"],
                "voice-agent.operational-release.v2",
            )
            self.assertNotIn("configuration_revision", release_document)
            self.assertNotIn(
                configuration_values()["LIVEKIT_API_SECRET"],
                json.dumps(release_document),
            )
            self.assertTrue(raced)
            self.assertEqual(result["build_id"], captured_commit)
            self.assertIn(captured_commit, built_asset.read_text(encoding="utf-8"))
            self.assertIn("committed", built_asset.read_text(encoding="utf-8"))
            self.assertNotIn("raced", built_asset.read_text(encoding="utf-8"))

    def test_execute_uses_the_exact_configuration_snapshot_that_was_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            release = state / "releases" / ("7" * 24)
            release.mkdir(parents=True)
            (state / "current").symlink_to(f"releases/{release.name}")
            values = configuration_values("snapshot-secret-value")
            document = {"build_id": "a" * 40, "release_id": release.name}
            captured: dict[str, object] = {}

            def execute(path: str, arguments: list[str], environment: dict[str, str]) -> None:
                captured.update(path=path, arguments=arguments, environment=environment)
                raise RuntimeError("exec boundary reached")

            with (
                patch(
                    "voice_agent_v2.operations._validate_release_snapshot",
                    return_value=(document, values),
                ),
                patch(
                    "voice_agent_v2.operations.parse_server_configuration",
                    side_effect=AssertionError("configuration must not be reread"),
                ),
                patch(
                    "voice_agent_v2.operations._prepare_mutable_runtime_directory",
                    side_effect=lambda path: path,
                ) as prepare_runtime,
                patch("voice_agent_v2.operations.os.chdir"),
                patch("voice_agent_v2.operations.os.execve", side_effect=execute),
            ):
                with self.assertRaisesRegex(RuntimeError, "exec boundary reached"):
                    execute_release(state)
            environment = captured["environment"]
            self.assertIsInstance(environment, dict)
            self.assertEqual(environment["LIVEKIT_API_SECRET"], "snapshot-secret-value")
            prepare_runtime.assert_called_once_with(SYSTEMD_RUNTIME_ROOT / "pycache")

    def test_rollback_rejects_incompatible_previous_without_moving_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("1" * 24)
            previous = releases / ("2" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")

            def validation(path: Path, **_kwargs: object) -> dict[str, object]:
                if path.resolve() == previous.resolve():
                    raise OperationalError("release_incompatible", "fixture incompatible")
                return {"build_id": "a" * 40}

            with patch("voice_agent_v2.operations.validate_release", side_effect=validation):
                with self.assertRaisesRegex(OperationalError, "fixture incompatible"):
                    ReleaseStore(state).rollback()
            self.assertEqual((state / "current").resolve(), current.resolve())
            self.assertEqual((state / "previous").resolve(), previous.resolve())

    def test_rollback_recovers_to_verified_previous_when_current_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("5" * 24)
            previous = releases / ("6" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")

            def validation(path: Path, **_kwargs: object) -> dict[str, object]:
                if path.resolve() == current.resolve():
                    raise OperationalError("release_incompatible", "current is corrupt")
                return {"build_id": "6" * 40}

            with patch("voice_agent_v2.operations.validate_release", side_effect=validation):
                result = ReleaseStore(state).rollback()
            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual(result["replaced_release_id"], current.name)
            self.assertIsNone(result["replaced_build_id"])
            self.assertFalse(result["replaced_release_compatible"])

    def test_verified_rollback_swaps_only_current_and_previous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            releases = state / "releases"
            current = releases / ("3" * 24)
            previous = releases / ("4" * 24)
            current.mkdir(parents=True)
            previous.mkdir()
            (state / "current").symlink_to(f"releases/{current.name}")
            (state / "previous").symlink_to(f"releases/{previous.name}")
            with patch(
                "voice_agent_v2.operations.validate_release",
                side_effect=lambda path, **_kwargs: {"build_id": path.name[0] * 40},
            ):
                result = ReleaseStore(state).rollback()
            self.assertEqual(result["release_id"], previous.name)
            self.assertEqual((state / "current").resolve(), previous.resolve())
            self.assertEqual((state / "previous").resolve(), current.resolve())


class LifecycleAndSustainedTests(unittest.TestCase):
    def test_public_status_fixture_composes_versioned_health_without_fallback(self) -> None:
        report = json.loads(
            (ROOT / "contracts/fixtures/public-operational-status.v1.json").read_text()
        )
        status_schema = json.loads(
            (ROOT / "contracts/public-operational-status.v1.schema.json").read_text()
        )
        health_schema = json.loads(
            (ROOT / "contracts/health-readiness.v1.schema.json").read_text()
        )
        validate_schema(report, status_schema)
        validate_schema(report["health"], health_schema)
        self.assertFalse(report["external_provider_supervised"])
        self.assertFalse(report["automatic_fallback"])

    def test_sustained_acceptance_uses_twenty_complete_content_free_turns(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        turns = [
            {
                "outcome": "completed",
                "total_turn_ms": 1000 + index,
                "cancellation_latency_ms": 100 if index in {5, 15} else None,
                "process_rss_mib": 1000 + index,
                "gpu_vram_used_mib": 3000 + index,
            }
            for index in range(20)
        ]
        report = evaluate_sustained_run(
            manifest, turns=turns, avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["turn_count"], 20)
        self.assertNotIn("transcript", json.dumps(report))
        turns[-1] = dict(turns[-1], outcome="failed")
        with self.assertRaisesRegex(OperationalError, "turn_success"):
            evaluate_sustained_run(
                manifest, turns=turns, avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
            )

    def test_sustained_acceptance_rejects_nonfinite_boolean_and_negative_values(self) -> None:
        manifest = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
        baseline = [{
            "outcome": "completed",
            "total_turn_ms": 1000,
            "cancellation_latency_ms": None,
            "process_rss_mib": 1000,
            "gpu_vram_used_mib": 3000,
        } for _index in range(20)]
        for field, value in (
            ("total_turn_ms", True),
            ("process_rss_mib", -1),
            ("gpu_vram_used_mib", float("inf")),
            ("cancellation_latency_ms", float("nan")),
            ("total_turn_ms", 10**400),
        ):
            turns = [dict(turn) for turn in baseline]
            turns[0][field] = value
            with self.assertRaisesRegex(OperationalError, "finite non-negative"):
                evaluate_sustained_run(
                    manifest,
                    turns=turns,
                    avatar={"healthy_frame_ratio": 0.999, "fps": 60.0},
                )
        for avatar in (
            {"healthy_frame_ratio": 1.1, "fps": 60.0},
            {"healthy_frame_ratio": 0.999, "fps": float("inf")},
        ):
            with self.assertRaisesRegex(OperationalError, "finite non-negative"):
                evaluate_sustained_run(manifest, turns=baseline, avatar=avatar)

    def test_settings_reject_forged_operational_build_identity(self) -> None:
        values = configuration_values()
        values["VOICE_AGENT_BUILD_ID"] = "not-a-commit"
        with self.assertRaisesRegex(ValueError, "build/release identity"):
            Slice6Settings.from_environment(values, project_root=ROOT)


if __name__ == "__main__":
    unittest.main()

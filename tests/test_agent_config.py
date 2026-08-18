from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from voice_agent_v2 import operations_cli
from voice_agent_v2.agent_config import (
    AgentConfigError,
    AgentConfigService,
    AgentUserContext,
    EMPTY_CAPABILITY_REGISTRY,
    FILE_MODE,
    DIRECTORY_MODE,
    MAX_CONFIG_BYTES,
    MINIMAL_CONFIG_BYTES,
    PROFILE_SCHEMA,
    load_production_registry,
    parse_agent_config,
    _verify_regular,
)
from voice_agent_v2.schema import validate as validate_schema


ROOT = Path(__file__).resolve().parents[1]


class DisposableAgentProfile:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="voice-agent-profile-test-")
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.context = AgentUserContext(uid=os.geteuid(), home=self.home)
        self.service = AgentConfigService(context=self.context)

    def close(self) -> None:
        self.temporary.cleanup()


class AgentConfigParserTests(unittest.TestCase):
    def test_minimal_model_is_frozen_and_revision_is_semantic(self) -> None:
        minimal = parse_agent_config(MINIMAL_CONFIG_BYTES)
        reordered = parse_agent_config(
            b"# private comment\naccess: {capabilities: {}}\n"
            b"profile_id: default\nschema_version: voice-agent.config.v1\n"
        )
        self.assertEqual(minimal.semantic_revision, reordered.semantic_revision)
        self.assertEqual(
            minimal.semantic_revision,
            "93f2250d3258b3d05c4920f34c0c2fc263cd9db95049373d1df62b3ee1f5ade0",
        )
        self.assertEqual(minimal.effective_capability_count, 0)
        with self.assertRaises((TypeError, ValidationError)):
            minimal.model.profile_id = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            minimal.model.access.capabilities["anything.v1"] = {}  # type: ignore[index]

    def test_strict_parser_rejection_matrix_has_stable_codes(self) -> None:
        deep = b"unknown: " + b"[" * 17 + b"]" * 17 + b"\n"
        many_nodes = "\n".join(f"k{i}: v" for i in range(1_100)).encode()
        cases = {
            "invalid-utf8": (b"\xff", "config_encoding"),
            "bom": (b"\xef\xbb\xbf" + MINIMAL_CONFIG_BYTES, "config_bom"),
            "nul": (MINIMAL_CONFIG_BYTES + b"\x00", "config_control_character"),
            "control": (MINIMAL_CONFIG_BYTES + b"\x1f", "config_control_character"),
            "oversized": (b"x" * (MAX_CONFIG_BYTES + 1), "config_too_large"),
            "duplicate-root": (
                MINIMAL_CONFIG_BYTES + b"profile_id: other\n",
                "config_duplicate_field",
            ),
            "duplicate-nested": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access:\n  capabilities: {}\n  capabilities: {}\n",
                "config_duplicate_field",
            ),
            "anchor": (
                b"schema_version: voice-agent.config.v1\nprofile_id: &p default\n"
                b"access: {capabilities: {}}\n",
                "config_anchor",
            ),
            "alias": (
                b"schema_version: &s voice-agent.config.v1\nprofile_id: *s\n"
                b"access: {capabilities: {}}\n",
                "config_anchor",
            ),
            "merge": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access:\n  <<: {}\n  capabilities: {}\n",
                "config_merge_key",
            ),
            "tag": (
                b"schema_version: voice-agent.config.v1\nprofile_id: !!str default\n"
                b"access: {capabilities: {}}\n",
                "config_tag",
            ),
            "non-string-key": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access: {capabilities: {}}\n[a]: b\n",
                "config_key_type",
            ),
            "implicit-on": (
                b"schema_version: voice-agent.config.v1\nprofile_id: on\n"
                b"access: {capabilities: {}}\n",
                "config_implicit_value",
            ),
            "unknown-root": (MINIMAL_CONFIG_BYTES + b"credential: private\n", "config_unknown_field"),
            "unknown-nested": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access: {capabilities: {}, extra: private}\n",
                "config_unknown_field",
            ),
            "missing": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n",
                "config_missing_field",
            ),
            "unsupported": (
                b"schema_version: voice-agent.config.v2\nprofile_id: default\n"
                b"access: {capabilities: {}}\n",
                "config_schema_unsupported",
            ),
            "unknown-capability": (
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access:\n  capabilities:\n    web.research.v1: {}\n",
                "config_capability_unknown",
            ),
            "depth": (deep, "config_nesting"),
            "nodes": (many_nodes, "config_node_limit"),
            "multiple-documents": (MINIMAL_CONFIG_BYTES + b"---\n{}\n", "config_syntax"),
        }
        for label, (source, expected) in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(AgentConfigError) as caught:
                    parse_agent_config(source)
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private", str(caught.exception))

    def test_production_registry_is_tracked_exactly_empty_and_immutable(self) -> None:
        registry = load_production_registry()
        self.assertIs(registry, EMPTY_CAPABILITY_REGISTRY)
        self.assertEqual(dict(registry), {})
        with self.assertRaises(TypeError):
            registry["web.research.v1"] = {}  # type: ignore[index]
        document = json.loads((ROOT / "config/agent-capabilities-v1.json").read_text())
        self.assertEqual(document, {
            "schema_version": "voice-agent.capability-registry.v1",
            "capabilities": {},
        })
        operations = json.loads((ROOT / "config/operations-v1.json").read_text())
        slice6 = next(item for item in operations["python_runtimes"] if item["name"] == "slice6")
        self.assertEqual(slice6["packages"]["pydantic"], "2.13.4")
        self.assertEqual(slice6["packages"]["PyYAML"], "6.0.3")
        self.assertIn("PyYAML==6.0.3\n", (ROOT / "requirements-slice6.lock").read_text())


class AgentConfigFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = DisposableAgentProfile()

    def tearDown(self) -> None:
        self.profile.close()

    def assert_mode(self, path: Path, expected: int) -> None:
        self.assertEqual(stat.S_IMODE(path.lstat().st_mode), expected)
        self.assertEqual(path.lstat().st_uid, os.geteuid())

    @staticmethod
    def inventory(root: Path) -> dict[str, tuple[str, int, int, str]]:
        result: dict[str, tuple[str, int, int, str]] = {}
        for path in sorted((root, *root.rglob("*"))):
            metadata = path.lstat()
            relative = "." if path == root else str(path.relative_to(root))
            kind = "dir" if stat.S_ISDIR(metadata.st_mode) else "file"
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if kind == "file" else ""
            result[relative] = (kind, stat.S_IMODE(metadata.st_mode), metadata.st_ino, digest)
        return result

    def test_init_creates_exact_private_tree_and_second_run_is_byte_preserving(self) -> None:
        first = self.profile.service.init()
        root = self.profile.context.profile_root
        self.assertTrue(first.changed)
        self.assertEqual((root / "config.yaml").read_bytes(), MINIMAL_CONFIG_BYTES)
        self.assertEqual((root / "SOUL.md").read_bytes(), b"")
        self.assertEqual(
            sorted(path.name for path in root.iterdir()),
            ["SOUL.md", "config.yaml", "memory", "sessions", "skills"],
        )
        self.assert_mode(root, DIRECTORY_MODE)
        for name in ("skills", "sessions", "memory"):
            self.assert_mode(root / name, DIRECTORY_MODE)
        for name in ("config.yaml", "SOUL.md"):
            self.assert_mode(root / name, FILE_MODE)
            self.assertEqual((root / name).stat().st_nlink, 1)
        before = self.inventory(root)
        second = self.profile.service.init()
        self.assertFalse(second.changed)
        self.assertEqual(self.inventory(root), before)

    def test_init_preserves_safe_operator_bytes_and_refuses_unsafe_tree_before_mutation(self) -> None:
        root = self.profile.context.profile_root
        root.mkdir(mode=0o700)
        config = root / "config.yaml"
        config.write_bytes(MINIMAL_CONFIG_BYTES + b"# keep exactly\n")
        config.chmod(0o644)
        before = config.read_bytes()
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.init()
        self.assertEqual(caught.exception.code, "config_permissions")
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in root.iterdir()), ["config.yaml"])

    def test_validate_candidate_does_not_activate_or_mutate_source_bytes(self) -> None:
        self.profile.service.init()
        active = self.profile.context.profile_root / "config.yaml"
        candidate = self.profile.base / "candidate.yaml"
        candidate.write_bytes(
            b"access: {capabilities: {}}\nprofile_id: candidate\n"
            b"schema_version: voice-agent.config.v1\n"
        )
        candidate.chmod(0o600)
        before_active = active.read_bytes()
        before_candidate = candidate.read_bytes()
        snapshot = self.profile.service.validate(candidate)
        self.assertEqual(snapshot.model.profile_id, "candidate")
        self.assertEqual(active.read_bytes(), before_active)
        self.assertEqual(candidate.read_bytes(), before_candidate)
        self.assertEqual(self.profile.service.status().model.profile_id, "default")

    def test_file_custody_rejects_symlink_hardlink_fifo_socket_mode_and_owner(self) -> None:
        candidate = self.profile.base / "candidate.yaml"
        candidate.write_bytes(MINIMAL_CONFIG_BYTES)
        candidate.chmod(0o600)

        link = self.profile.base / "candidate-link.yaml"
        link.symlink_to(candidate)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(link)
        self.assertEqual(caught.exception.code, "config_path_unsafe")

        ancestor = self.profile.base / "linked-parent"
        ancestor.symlink_to(self.profile.base, target_is_directory=True)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(ancestor / "candidate.yaml")
        self.assertEqual(caught.exception.code, "config_path_unsafe")

        hardlink = self.profile.base / "hardlink.yaml"
        os.link(candidate, hardlink)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(candidate)
        self.assertEqual(caught.exception.code, "config_hardlink")
        hardlink.unlink()

        candidate.chmod(0o640)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(candidate)
        self.assertEqual(caught.exception.code, "config_permissions")
        candidate.chmod(0o600)

        other_context = AgentUserContext(uid=os.geteuid() + 1, home=self.profile.home)
        with self.assertRaises(AgentConfigError) as caught:
            AgentConfigService(context=other_context).validate(candidate)
        self.assertEqual(caught.exception.code, "config_owner")

        fifo = self.profile.base / "candidate.fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(fifo)
        self.assertEqual(caught.exception.code, "config_file_type")

        # Creating a named UNIX socket is intentionally forbidden by the
        # hermetic gate's audit hook.  Exercise the same pre-open type owner
        # directly with socket metadata instead.
        socket_metadata = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600,
            st_uid=os.geteuid(),
            st_nlink=1,
        )
        with self.assertRaises(AgentConfigError) as caught:
            _verify_regular(socket_metadata, os.geteuid())
        self.assertEqual(caught.exception.code, "config_file_type")
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.validate(Path("/dev/null"))
        self.assertEqual(caught.exception.code, "config_file_type")

    def test_every_reserved_path_rejects_symlinks_and_wrong_modes(self) -> None:
        for target_name in ("SOUL.md", "skills", "sessions", "memory"):
            with self.subTest(target=target_name):
                self.profile.service.init()
                root = self.profile.context.profile_root
                target = root / target_name
                if target.is_dir():
                    target.rmdir()
                    target.symlink_to(self.profile.home, target_is_directory=True)
                else:
                    target.unlink()
                    target.symlink_to(root / "config.yaml")
                with self.assertRaises(AgentConfigError) as caught:
                    self.profile.service.status()
                self.assertEqual(caught.exception.code, "config_path_unsafe")
                self.profile.close()
                self.profile = DisposableAgentProfile()

        self.profile.service.init()
        root = self.profile.context.profile_root
        (root / "skills").chmod(0o750)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.status()
        self.assertEqual(caught.exception.code, "config_permissions")

    def test_profile_root_symlink_is_rejected(self) -> None:
        outside = self.profile.base / "outside"
        outside.mkdir(mode=0o700)
        self.profile.context.profile_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AgentConfigError) as caught:
            self.profile.service.init()
        self.assertEqual(caught.exception.code, "config_path_unsafe")
        self.assertEqual(list(outside.iterdir()), [])

    def test_effective_context_uses_account_database_not_home_environment(self) -> None:
        with patch.dict(os.environ, {"HOME": "/credential/ambient/private"}), patch(
            "voice_agent_v2.agent_config.pwd.getpwuid",
            return_value=SimpleNamespace(pw_dir=str(self.profile.home)),
        ):
            context = AgentUserContext.effective()
        self.assertEqual(context.home, self.profile.home)
        self.assertNotEqual(str(context.home), os.environ.get("HOME"))


class AgentConfigCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = DisposableAgentProfile()

    def tearDown(self) -> None:
        self.profile.close()

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        output = StringIO()
        error = StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = operations_cli.main(list(arguments), agent_context=self.profile.context)
        return code, output.getvalue(), error.getvalue()

    def test_cli_init_validate_status_human_and_json_are_content_free(self) -> None:
        code, output, error = self.invoke("agent-config", "init", "--json")
        self.assertEqual((code, error), (0, ""))
        initialized = json.loads(output)
        validate_schema(
            initialized,
            json.loads((ROOT / "contracts/agent-config-result.v1.schema.json").read_text()),
        )
        self.assertTrue(initialized["changed"])

        code, output, error = self.invoke("agent-config", "status", "--json")
        self.assertEqual((code, error), (0, ""))
        status_document = json.loads(output)
        validate_schema(
            status_document,
            json.loads((ROOT / "contracts/agent-config-status.v1.schema.json").read_text()),
        )
        self.assertEqual(set(status_document), {
            "schema_version", "profile_schema_version", "profile_id",
            "semantic_revision", "effective_capability_count",
        })
        self.assertEqual(status_document["effective_capability_count"], 0)

        code, output, error = self.invoke("agent-config", "validate")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("schema: voice-agent.config.v1", output)
        self.assertIn("effective capabilities: 0", output)
        forbidden = ("SOUL", "access", "capabilities: {}", "credential", "HOME=")
        self.assertFalse(any(value in output for value in forbidden))

    def test_cli_explicit_candidate_and_errors_never_echo_private_input_or_exception(self) -> None:
        self.profile.service.init()
        candidate = self.profile.base / "candidate.yaml"
        private_marker = "DO_NOT_ECHO_CREDENTIAL_123"
        candidate.write_text(
            MINIMAL_CONFIG_BYTES.decode() + f"secret: {private_marker}\n",
            encoding="utf-8",
        )
        candidate.chmod(0o600)
        before = candidate.read_bytes()
        code, output, error = self.invoke(
            "agent-config", "validate", "--path", str(candidate), "--json",
        )
        self.assertEqual((code, output), (2, ""))
        document = json.loads(error)
        validate_schema(
            document,
            json.loads((ROOT / "contracts/agent-config-error.v1.schema.json").read_text()),
        )
        self.assertEqual(document["code"], "config_unknown_field")
        self.assertNotIn(private_marker, error)
        self.assertNotIn(str(candidate), error)
        self.assertNotIn("Traceback", error)
        self.assertEqual(candidate.read_bytes(), before)

    def test_config_contract_accepts_only_the_generated_minimal_shape(self) -> None:
        schema = json.loads((ROOT / "contracts/agent-config.v1.schema.json").read_text())
        snapshot = parse_agent_config(MINIMAL_CONFIG_BYTES)
        document = {
            "schema_version": snapshot.model.schema_version,
            "profile_id": snapshot.model.profile_id,
            "access": {"capabilities": dict(snapshot.model.access.capabilities)},
        }
        validate_schema(document, schema)


# Imported lazily only for the expected frozen-assignment exception above.
from pydantic import ValidationError  # noqa: E402


if __name__ == "__main__":
    unittest.main()

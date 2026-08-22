from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import logging
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

from voice_agent_v2.agent_config import (
    AgentConfigService,
    AgentUserContext,
    MINIMAL_CONFIG_BYTES,
)
from voice_agent_v2.agent_profile_runtime import (
    ACTIVE_REASON,
    DENY_ALL_POLICY_REVISION,
    AgentProfileRuntime,
)
from voice_agent_v2.contracts import AudioFormat
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.slice6_gateway import public_status_document


ROOT = Path(__file__).resolve().parents[1]


class DisposableProfile:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="voice-agent-runtime-profile-")
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.context = AgentUserContext(uid=os.geteuid(), home=self.home)
        self.service = AgentConfigService(context=self.context)

    @property
    def root(self) -> Path:
        return self.context.profile_root

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    def close(self) -> None:
        self.temporary.cleanup()

    def source_inventory(self) -> tuple[tuple[str, str, int, str], ...]:
        if not self.root.exists() and not self.root.is_symlink():
            return ()
        result: list[tuple[str, str, int, str]] = []
        paths = (self.root, *sorted(self.root.rglob("*")))
        for path in paths:
            metadata = path.lstat()
            relative = "." if path == self.root else str(path.relative_to(self.root))
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                result.append((relative, "symlink", mode, os.readlink(path)))
            elif stat.S_ISREG(metadata.st_mode):
                result.append((relative, "file", mode, path.read_bytes().hex()))
            else:
                result.append((relative, "directory", mode, ""))
        return tuple(result)


class BoundedSTT:
    version = "voice-agent.stt.v1"

    def transcribe(self, **_kwargs) -> str:
        return "Проверка"


class BoundedLLM:
    version = "voice-agent.llm-provider.v1"
    provider_mode = "local"
    provider_identity = "disposable-local-fixture"

    def respond_with_handoff(self, *, on_sentence, **_kwargs) -> str:
        response = "Голосовой путь готов."
        on_sentence(response)
        return response

    def cancel(self) -> None:
        return None


class BoundedTTS:
    version = "voice-agent.tts.v1"
    output_format = AudioFormat()

    def stream_synthesize(self, **_kwargs):
        yield b"\0\0" * 32

    def cancel(self) -> float:
        return 0.0


def run_bounded_voice_turn(label: str):
    return RealTurnController(BoundedSTT(), BoundedLLM(), BoundedTTS()).run_turn(
        session_id=f"session-{label}",
        turn_id=f"turn-{label}",
        input_pcm=b"\0\0" * 160,
    )


class AgentProfileStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = DisposableProfile()

    def tearDown(self) -> None:
        self.profile.close()

    def test_valid_profile_is_loaded_once_into_an_immutable_deny_all_snapshot(self) -> None:
        initialized = self.profile.service.init()

        class CountingService:
            calls = 0

            def status(inner_self):
                inner_self.calls += 1
                return self.profile.service.status()

        service = CountingService()
        runtime = AgentProfileRuntime.startup(service)  # type: ignore[arg-type]
        first_document = runtime.status_document()
        second_document = runtime.status_document()

        self.assertEqual(service.calls, 1)
        self.assertEqual(first_document, second_document)
        self.assertEqual(runtime.status, "active")
        self.assertEqual(runtime.reason_code, ACTIVE_REASON)
        self.assertEqual(runtime.snapshot.profile_id, "default")
        self.assertEqual(
            runtime.snapshot.config_revision, initialized.snapshot.semantic_revision
        )
        self.assertEqual(runtime.snapshot.effective_policy_revision, DENY_ALL_POLICY_REVISION)
        self.assertEqual(runtime.snapshot.effective_capability_ids, ())
        self.assertFalse(runtime.snapshot.agent_capabilities_admitted)
        with self.assertRaises(FrozenInstanceError):
            runtime.snapshot.profile_id = "changed"  # type: ignore[misc]
        validate_schema(
            first_document,
            json.loads(
                (ROOT / "contracts/agent-profile-runtime-status.v1.schema.json").read_text()
            ),
        )

    def test_running_snapshot_is_pinned_until_a_new_process_composition(self) -> None:
        self.profile.service.init()
        running = AgentProfileRuntime.startup(self.profile.service)
        old_document = running.status_document()
        self.profile.config.write_bytes(
            b"schema_version: voice-agent.config.v1\n"
            b"profile_id: changed\naccess:\n  capabilities: {}\n"
        )

        self.assertEqual(running.status_document(), old_document)
        restarted = AgentProfileRuntime.startup(
            AgentConfigService(context=self.profile.context)
        )
        self.assertEqual(restarted.snapshot.profile_id, "changed")
        self.assertNotEqual(
            restarted.snapshot.config_revision, running.snapshot.config_revision
        )
        self.assertEqual(
            restarted.snapshot.effective_policy_revision,
            running.snapshot.effective_policy_revision,
        )
        self.assertFalse(restarted.snapshot.agent_capabilities_admitted)

    def test_service_sandbox_remains_read_only_for_the_entire_profile_tree(self) -> None:
        unit = (ROOT / "ops/systemd/voice-agent-v2.service").read_text()
        self.assertIn("ProtectHome=read-only\n", unit)
        self.assertEqual(
            [line for line in unit.splitlines() if line.startswith("ReadWritePaths=")],
            ["ReadWritePaths=/home/priney/.cache/voice-agent-v2"],
        )
        self.assertNotIn("ReadWritePaths=/home/priney/.voice-agent", unit)

    def test_unexpected_failure_is_normalized_without_raw_error_or_environment(self) -> None:
        marker = "DO_NOT_EXPOSE_PRIVATE_EXCEPTION_7391"

        class FailingService:
            def status(self):
                raise RuntimeError(marker)

        with self.assertLogs(
            "voice_agent_v2.agent_profile_runtime", level=logging.INFO
        ) as captured:
            runtime = AgentProfileRuntime.startup(FailingService())  # type: ignore[arg-type]
        observable = json.dumps(runtime.status_document()) + "\n" + "\n".join(captured.output)
        self.assertEqual(runtime.reason_code, "config_load_failed")
        self.assertNotIn(marker, observable)
        self.assertNotIn("RuntimeError", observable)
        self.assertNotIn("HOME=", observable)


class AgentProfileDegradedVoiceRegressionTests(unittest.TestCase):
    def make_case(self, label: str) -> tuple[DisposableProfile, AgentConfigService]:
        profile = DisposableProfile()
        service = profile.service
        if label == "missing":
            return profile, service
        profile.service.init()
        if label == "invalid_yaml":
            profile.config.write_bytes(b"schema_version: [\nPRIVATE_YAML_VALUE")
        elif label == "unsupported_schema":
            profile.config.write_bytes(
                MINIMAL_CONFIG_BYTES.replace(b"voice-agent.config.v1", b"voice-agent.config.v9")
            )
        elif label == "unknown_capability":
            profile.config.write_bytes(
                b"schema_version: voice-agent.config.v1\nprofile_id: default\n"
                b"access:\n  capabilities:\n    web.private.v1: {}\n"
            )
        elif label == "unsafe_mode":
            profile.config.chmod(0o640)
        elif label == "unsafe_owner":
            service = AgentConfigService(
                context=AgentUserContext(uid=os.geteuid() + 1, home=profile.home)
            )
        elif label == "symlink":
            target = profile.base / "operator-source.yaml"
            target.write_bytes(MINIMAL_CONFIG_BYTES)
            target.chmod(0o600)
            profile.config.unlink()
            profile.config.symlink_to(target)
        else:
            raise AssertionError(label)
        return profile, service

    def test_all_degraded_inputs_preserve_voice_readiness_and_one_bounded_turn(self) -> None:
        expected = {
            "missing": "config_missing",
            "invalid_yaml": "config_syntax",
            "unsupported_schema": "config_schema_unsupported",
            "unknown_capability": "config_capability_unknown",
            "unsafe_mode": "config_permissions",
            "unsafe_owner": "config_owner",
            "symlink": "config_path_unsafe",
        }
        for index, (label, reason) in enumerate(expected.items(), start=1):
            with self.subTest(label=label):
                profile, service = self.make_case(label)
                try:
                    before = profile.source_inventory()
                    runtime = AgentProfileRuntime.startup(service)
                    after = profile.source_inventory()
                    self.assertEqual(runtime.status, "degraded")
                    self.assertEqual(runtime.reason_code, reason)
                    self.assertEqual(before, after, "startup must not repair or rewrite source")
                    self.assertEqual(runtime.snapshot.effective_capability_ids, ())
                    self.assertFalse(runtime.snapshot.agent_capabilities_admitted)

                    active_status = {
                        "schema_version": "voice-agent.agent-runtime-status.v2",
                        "status": "degraded",
                        "reason_code": reason,
                        "config_revision": None,
                        "agent_enabled": False,
                        "agent_tools_admitted": False,
                        "environment_count": 1,
                        "environment_state": "disabled",
                        "environment_image_state": "unavailable",
                        "environment_container_provisioned": False,
                        "environment_reason_code": None,
                        "provider_mode": "local",
                        "automatic_fallback": False,
                    }
                    registry = SimpleNamespace(
                        accepting=True,
                        active_count=0,
                        agent_runtime=SimpleNamespace(status_document=lambda: active_status),
                        operational_health=lambda: {"overall_readiness": "ready"},
                    )
                    settings = SimpleNamespace(build_id="development", release_id="development")
                    public = public_status_document(registry, settings)
                    self.assertTrue(public["available"])
                    self.assertTrue(public["accepting"])
                    self.assertEqual(public["health"]["overall_readiness"], "ready")
                    self.assertEqual(public["agent_runtime"], active_status)

                    turn = run_bounded_voice_turn(f"profile-{index}")
                    self.assertEqual(turn.terminal_event["type"], "turn.completed")
                    self.assertGreater(turn.terminal_event["payload"]["output_bytes"], 0)
                    self.assertEqual(
                        [event["type"] for event in turn.events if event["terminal"]],
                        ["turn.completed"],
                    )
                finally:
                    profile.close()


if __name__ == "__main__":
    unittest.main()

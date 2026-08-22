from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from voice_agent_v2.agent_environment_image import (
    IMAGE_CONTEXT_LABEL, IMAGE_MANAGED_LABEL, IMAGE_SCHEMA_LABEL,
    ImageCommandResult, PreparedImageError, inspect_prepared_image,
    load_native_image_contract, load_prepared_image, prepare_native_image,
)


class FakeImageDocker:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.builds = 0
        self.mismatch = False

    def run(self, arguments, *, cwd: Path | None = None) -> ImageCommandResult:
        del cwd
        command = tuple(arguments)
        self.commands.append(command)
        if command == ("docker", "version", "--format", "{{json .Server}}"):
            return ImageCommandResult(0, '{"ID":"rootless-test","Os":"linux","Arch":"amd64"}\n')
        if command == ("docker", "info", "--format", "{{json .SecurityOptions}}"):
            return ImageCommandResult(0, '["name=rootless","name=cgroupns"]\n')
        if command[:4] == ("docker", "buildx", "build", "--load"):
            self.builds += 1
            revision = command[command.index("--build-arg") + 1].split("=", 1)[1]
            tag = command[command.index("--tag") + 1]
            image_id = "sha256:" + f"{self.builds:064x}"
            document = self._image(image_id, revision)
            self.images[tag] = document
            self.images[image_id] = document
            return ImageCommandResult(0)
        if command[:3] == ("docker", "image", "inspect"):
            document = self.images.get(command[3])
            if document is None:
                return ImageCommandResult(1, stderr="missing")
            value = json.loads(json.dumps(document))
            if self.mismatch:
                value["Config"]["User"] = "1000:1000"
            return ImageCommandResult(0, json.dumps([value]))
        return ImageCommandResult(1, stderr="unexpected")

    @staticmethod
    def _image(image_id: str, revision: str) -> dict[str, object]:
        return {
            "Id": image_id, "Os": "linux", "Architecture": "amd64",
            "Config": {
                "User": "0:0",
                "Entrypoint": ["/sbin/tini", "--"],
                "Cmd": ["/usr/local/lib/voice-agent/agent-helper", "init-container"],
                "Labels": {
                    IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1",
                    IMAGE_CONTEXT_LABEL: revision,
                },
            },
            "RootFS": {"Layers": ["sha256:" + "a" * 64]},
        }


class NativeImagePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="prepared-agent-image-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.context = self.source / "agent-environment"
        (self.context / "helpers").mkdir(parents=True)
        self.dockerfile = self.context / "Dockerfile"
        self.helper = self.context / "helpers/agent-helper"
        self.dockerfile.write_text(
            "ARG BASE_IMAGE=python:3.13-alpine@sha256:" + "b" * 64 + "\nFROM ${BASE_IMAGE}\n",
            encoding="utf-8",
        )
        os.chmod(self.dockerfile, 0o644)
        self.helper.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(self.helper, 0o755)
        self._write_lock()
        self.state = self.root / "state/private"
        self.docker = FakeImageDocker()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_lock(self) -> None:
        document = {
            "schema_version": "voice-agent.agent-environment-image-lock.v2",
            "base_image": "python:3.13-alpine@sha256:" + "b" * 64,
            "native_platforms": ["linux/amd64", "linux/arm64"],
            "context_files": [
                {"path": "Dockerfile", "sha256": hashlib.sha256(self.dockerfile.read_bytes()).hexdigest(), "mode": "0644"},
                {"path": "helpers/agent-helper", "sha256": hashlib.sha256(self.helper.read_bytes()).hexdigest(), "mode": "0755"},
            ],
            "runtime_pull": False,
            "registry_required": False,
            "entrypoint": ["/sbin/tini", "--"],
            "command": ["/usr/local/lib/voice-agent/agent-helper", "init-container"],
            "user": "0:0",
            "labels": {IMAGE_MANAGED_LABEL: "1", IMAGE_SCHEMA_LABEL: "1"},
        }
        (self.context / "image-lock.v2.json").write_text(json.dumps(document), encoding="utf-8")

    def test_native_prepare_is_idempotent_exact_and_never_remote_or_destructive(self) -> None:
        first = prepare_native_image(state_root=self.state, source_root=self.source, runner=self.docker)
        second = prepare_native_image(state_root=self.state, source_root=self.source, runner=self.docker)
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(self.docker.builds, 1)
        contract = load_native_image_contract(self.source)
        record = load_prepared_image(self.state, contract)
        self.assertEqual(record.selected.context_revision, contract.revision)
        self.assertEqual(record.selected.platform, "linux/amd64")
        build = next(command for command in self.docker.commands if command[:4] == ("docker", "buildx", "build", "--load"))
        self.assertEqual(build[build.index("--platform") + 1], "linux/amd64")
        self.assertNotIn("--push", build)
        flattened = " ".join(" ".join(command) for command in self.docker.commands)
        for forbidden in (" login ", " pull ", " push ", " prune ", " image rm "):
            self.assertNotIn(forbidden, f" {flattened} ")

    def test_context_change_selects_new_identity_and_retains_prior_without_deletion(self) -> None:
        prepare_native_image(state_root=self.state, source_root=self.source, runner=self.docker)
        first = json.loads((self.state / "prepared-image.json").read_text())["selected"]["image_id"]
        self.dockerfile.write_text(self.dockerfile.read_text() + "# changed\n", encoding="utf-8")
        self._write_lock()
        changed = prepare_native_image(state_root=self.state, source_root=self.source, runner=self.docker)
        contract = load_native_image_contract(self.source)
        record = load_prepared_image(self.state, contract)
        self.assertTrue(changed["changed"])
        self.assertEqual(self.docker.builds, 2)
        self.assertNotEqual(record.selected.image_id, first)
        self.assertEqual([item.image_id for item in record.retained], [first])
        self.assertFalse(any("rm" in command for command in self.docker.commands))

    def test_missing_stale_and_fresh_inspection_mismatch_are_explicit(self) -> None:
        missing = inspect_prepared_image(state_root=self.state, source_root=self.source, runner=self.docker)
        self.assertEqual(missing["state"], "missing")
        prepare_native_image(state_root=self.state, source_root=self.source, runner=self.docker)
        self.docker.mismatch = True
        mismatch = inspect_prepared_image(state_root=self.state, source_root=self.source, runner=self.docker)
        self.assertEqual(mismatch["state"], "unavailable")
        self.assertEqual(mismatch["reason_code"], "prepared_image_mismatch")
        self.docker.mismatch = False
        self.dockerfile.write_text(self.dockerfile.read_text() + "# stale\n", encoding="utf-8")
        self._write_lock()
        stale = inspect_prepared_image(state_root=self.state, source_root=self.source, runner=self.docker)
        self.assertEqual(stale["state"], "unavailable")
        self.assertEqual(stale["reason_code"], "image_preparation_stale")

    def test_lock_rejects_unpinned_or_changed_context(self) -> None:
        self.dockerfile.write_text("changed without lock\n", encoding="utf-8")
        with self.assertRaisesRegex(PreparedImageError, "image_context_invalid"):
            load_native_image_contract(self.source)


if __name__ == "__main__":
    unittest.main()

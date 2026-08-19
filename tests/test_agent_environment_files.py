from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "agent-environment/helpers/agent-helper"


class HelperFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-environment-files-")
        self.root = Path(self.temp.name)
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.ledger = self.root / "ledger"
        self.sequence = 0

    def close(self) -> None:
        self.temp.cleanup()

    def call(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        self.sequence += 1
        environment = {"PATH": os.environ["PATH"], "VOICE_AGENT_LEDGER": str(self.ledger)}
        completed = subprocess.run(
            [str(HELPER), "claim-execute", "--call-id", f"{self.sequence:032x}", "--tool", tool, "--cwd", str(self.cwd)],
            input=json.dumps(arguments).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False, timeout=10,
        )
        if completed.returncode:
            raise AssertionError(completed.stderr.decode("utf-8", "replace"))
        return json.loads(completed.stdout)


class PersistentOrdinaryFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = HelperFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_binary_range_hash_and_atomic_write_are_truthful(self) -> None:
        payload = b"\x00archive\xff\n" * 200
        document = self.fixture.call("file-write", {
            "path": "payload.bin", "data_base64": base64.b64encode(payload).decode(),
            "expected_bytes": len(payload), "expected_sha256": hashlib.sha256(payload).hexdigest(),
        })
        self.assertEqual(document["status"], "completed")
        self.assertTrue(document["details"]["atomic"])
        self.assertEqual((self.fixture.cwd / "payload.bin").stat().st_mode & 0o777, 0o600)
        read = self.fixture.call("file-read", {"path": "payload.bin", "offset": 3, "length": 17})
        self.assertEqual(base64.b64decode(read["stdout_base64"]), payload[3:20])
        self.assertEqual(read["details"]["file_bytes"], len(payload))
        self.assertEqual(read["details"]["file_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(read["details"]["range_truncated"])
        self.assertFalse(read["stdout_truncated"])
        before = (self.fixture.cwd / "payload.bin").read_bytes()
        mismatch = self.fixture.call("file-write", {
            "path": "payload.bin", "data_base64": base64.b64encode(b"changed").decode(),
            "expected_current_sha256": "0" * 64,
        })
        self.assertEqual(mismatch["status"], "failed")
        self.assertEqual((self.fixture.cwd / "payload.bin").read_bytes(), before)

    def test_edit_patch_search_and_shell_cwd_have_explicit_persistence_boundaries(self) -> None:
        self.fixture.call("file-write", {"path": "note.txt", "data_base64": base64.b64encode(b"one\ntwo\n").decode()})
        edited = self.fixture.call("file-edit", {"path": "note.txt", "old_text": "two", "new_text": "three"})
        self.assertEqual(edited["details"]["sha256"], hashlib.sha256(b"one\nthree\n").hexdigest())
        patched = self.fixture.call("file-patch", {"path": "note.txt", "patch": "@@ -1,2 +1,2 @@\n one\n-three\n+four\n"})
        self.assertEqual(patched["status"], "completed")
        search = self.fixture.call("file-search", {"path": ".", "query": "four"})
        self.assertIn(b"note.txt:", base64.b64decode(search["stdout_base64"]))
        (self.fixture.cwd / "child").mkdir()
        shell = self.fixture.call("shell-exec", {"command": "export TRANSIENT=value; cd child; pwd"})
        self.assertEqual(shell["cwd"], str(self.fixture.cwd / "child"))
        # A new noninteractive shell sees neither the previous export nor an
        # invented cwd. The controller, rather than an ambient helper process,
        # is the sole owner that persists the observed cwd for the next call.
        next_shell = self.fixture.call("shell-exec", {"command": "printf '%s' \"${TRANSIENT-unset}\""})
        self.assertEqual(base64.b64decode(next_shell["stdout_base64"]), b"unset")
        self.assertEqual(next_shell["cwd"], str(self.fixture.cwd))

    def test_image_admits_local_git_archives_and_privilege_for_package_manager_only_inside_container(self) -> None:
        dockerfile = (ROOT / "agent-environment/Dockerfile").read_text(encoding="utf-8")
        for package in ("git", "tar", "zip", "unzip", "sudo"):
            self.assertIn(package, dockerfile)
        self.assertIn("agent ALL=(ALL) NOPASSWD: ALL", dockerfile)
        self.assertNotIn("/root/.ssh", dockerfile)
        self.assertNotIn("docker.sock", dockerfile)


if __name__ == "__main__":
    unittest.main()

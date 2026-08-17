from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

from scripts.verify_support import (
    PhaseOwner,
    VerificationDeadline,
    inspect_owned,
    owned_pids,
)


class VerificationOwnershipTests(unittest.TestCase):
    def test_timeout_reaps_process_group_and_detached_owned_child(self) -> None:
        nonce = f"test-{uuid.uuid4().hex}"
        owner = PhaseOwner(nonce, grace_seconds=0.1)
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "detached.pid"
            command = [
                sys.executable,
                "-c",
                (
                    "import pathlib,subprocess,sys,time; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                    "start_new_session=True); "
                    "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
                ),
                str(pid_path),
            ]
            with self.assertRaises(VerificationDeadline):
                owner.run(
                    "injected-timeout",
                    command,
                    cwd=Path(temporary),
                    environment=dict(os.environ),
                    deadline=time.monotonic() + 0.2,
                )
            leaked_before_final_cleanup = inspect_owned(nonce)
            self.assertTrue(leaked_before_final_cleanup.pids)
            owner.cleanup()
            self.assertEqual(owned_pids(nonce), [])
            child_pid = int(pid_path.read_text(encoding="ascii"))
            stat = Path(f"/proc/{child_pid}/stat")
            state = stat.read_text().split()[2] if stat.exists() else None
            self.assertIn(state, {None, "Z"})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from voice_agent_v2.schema import validate as validate_schema


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "agent-environment/helpers/agent-helper"


class ProcessHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="process-helper-")
        self.root = Path(self.temporary.name)
        self.environment = {
            **os.environ,
            "VOICE_AGENT_LEDGER": str(self.root / "calls"),
            "VOICE_AGENT_PROCESS_LEDGER": str(self.root / "processes"),
            "VOICE_AGENT_PROCESS_RUNTIME": str(self.root / "runtime"),
        }
        for name in ("calls", "processes", "runtime"):
            (self.root / name).mkdir(mode=0o700)
        self.identities: list[dict[str, object]] = []
        self.counter = 0

    def tearDown(self) -> None:
        for identity in self.identities:
            pid = int(identity["pid"])
            try:
                current = self._proc_identity(pid)
                if current == identity:
                    os.killpg(int(identity["pgid"]), signal.SIGKILL)
            except OSError:
                pass
        self.temporary.cleanup()

    @staticmethod
    def _proc_identity(pid: int) -> dict[str, object] | None:
        try:
            suffix = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            init_suffix = Path("/proc/1/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            if suffix[0] == "Z":
                return None
            return {
                "pid": pid, "pgid": int(suffix[2]), "start_time": int(suffix[19]),
                "executable": os.readlink(f"/proc/{pid}/exe"),
                "init_start_time": int(init_suffix[19]),
            }
        except (OSError, ValueError, IndexError):
            return None

    def _call(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
        self.counter += 1
        completed = subprocess.run(
            [
                sys.executable, str(HELPER), "claim-execute", "--call-id", f"{self.counter:032x}",
                "--tool", tool, "--cwd", str(self.root),
            ],
            input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        return json.loads(completed.stdout)

    @staticmethod
    def _authority(identity: dict[str, object], receipt: str) -> dict[str, object]:
        return {
            "receipt": receipt, "timeout_seconds": 0,
            "_voice_agent_owner": "owner-a", "_voice_agent_spec": "spec-a",
            "_voice_agent_generation": 7, "_voice_agent_expected_identity": identity,
        }

    def test_background_write_logs_wait_and_exact_group_kill_across_helper_restarts(self) -> None:
        receipt = "a" * 32
        started = self._call("shell-exec", {
            "background": True, "command": "echo ready; read value; echo got:$value; sleep 30",
            "_voice_agent_process_receipt": receipt, "_voice_agent_owner": "owner-a",
            "_voice_agent_spec": "spec-a", "_voice_agent_generation": 7,
        })
        identity = started["details"].pop("_voice_agent_identity")
        self.identities.append(identity)
        validate_schema(
            started["details"],
            json.loads((ROOT / "contracts/agent-process-receipt.v1.schema.json").read_text()),
        )
        self.assertNotIn(str(identity["pid"]), json.dumps(started["details"]))

        authority = self._authority(identity, receipt)
        written = self._call("process", {
            **authority, "action": "write", "data_base64": base64.b64encode(b"reload\n").decode(),
        })
        self.assertEqual(written["details"]["written_bytes"], 7)
        waited = self._call("process", {**authority, "action": "wait", "timeout_seconds": 0.02})
        self.assertTrue(waited["details"]["timed_out"])
        time.sleep(0.05)
        logs = self._call("process", {
            **authority, "action": "logs", "offset": 0, "maximum_bytes": 4096,
        })
        self.assertIn(b"ready", base64.b64decode(logs["stdout_base64"]))
        self.assertIn(b"got:reload", base64.b64decode(logs["stdout_base64"]))
        killed = self._call("process", {**authority, "action": "kill"})
        self.assertEqual(killed["details"]["state"], "killed")
        self.assertIsNone(self._proc_identity(int(identity["pid"])))

    def test_foreground_cancellation_proves_its_call_group_and_leaves_background_alive(self) -> None:
        receipt = "c" * 32
        started = self._call("shell-exec", {
            "background": True, "command": "sleep 30; :",
            "_voice_agent_process_receipt": receipt, "_voice_agent_owner": "owner-a",
            "_voice_agent_spec": "spec-a", "_voice_agent_generation": 7,
        })
        background_identity = started["details"]["_voice_agent_identity"]
        self.identities.append(background_identity)
        call_id = "d" * 32
        foreground = subprocess.Popen(
            [
                sys.executable, str(HELPER), "claim-execute", "--call-id", call_id,
                "--tool", "shell-exec", "--cwd", str(self.root),
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment,
        )
        assert foreground.stdin is not None
        foreground.stdin.write(json.dumps({"command": "sleep 30; :"}).encode())
        foreground.stdin.close()
        identity_path = self.root / "calls" / call_id / "foreground-process.json"
        deadline = time.monotonic() + 2
        while not identity_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(identity_path.is_file())
        cancellation = subprocess.run(
            [sys.executable, str(HELPER), "cancel-call", "--call-id", call_id],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.environment,
            timeout=10, check=False,
        )
        self.assertEqual(json.loads(cancellation.stdout), {"outcome": "signalled"})
        foreground.wait(timeout=5)
        assert foreground.stdout is not None and foreground.stderr is not None
        foreground.stdout.close(); foreground.stderr.close()
        self.assertEqual(self._proc_identity(int(background_identity["pid"])), background_identity)
        authority = self._authority(background_identity, receipt)
        poll = self._call("process", {**authority, "action": "poll"})
        self.assertEqual(poll["details"]["state"], "running")
        self._call("process", {**authority, "action": "kill"})

    def test_tampered_or_pid_reused_claim_cannot_signal_unrelated_group(self) -> None:
        receipt = "b" * 32
        started = self._call("shell-exec", {
            "background": True, "command": "sleep 30; :",
            "_voice_agent_process_receipt": receipt, "_voice_agent_owner": "owner-a",
            "_voice_agent_spec": "spec-a", "_voice_agent_generation": 7,
        })
        identity = started["details"]["_voice_agent_identity"]
        self.identities.append(identity)
        authority = self._authority(identity, receipt)
        claim_path = self.root / "processes" / receipt / "process.json"
        original = claim_path.read_text(encoding="utf-8")
        tampered = json.loads(original); tampered["owner"] = "attacker"
        claim_path.write_text(json.dumps(tampered), encoding="utf-8")
        rejected = self._call("process", {**authority, "action": "kill"})
        self.assertEqual(rejected["status"], "failed")
        self.assertEqual(self._proc_identity(int(identity["pid"])), identity)
        claim_path.write_text(original, encoding="utf-8")

        reused = dict(identity); reused["start_time"] = int(reused["start_time"]) + 1
        rejected_reuse = self._call("process", {
            **authority, "_voice_agent_expected_identity": reused, "action": "kill",
        })
        self.assertEqual(rejected_reuse["status"], "failed")
        self.assertEqual(self._proc_identity(int(identity["pid"])), identity)
        killed = self._call("process", {**authority, "action": "kill"})
        self.assertEqual(killed["details"]["state"], "killed")


if __name__ == "__main__":
    unittest.main()

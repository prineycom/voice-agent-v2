from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD_ID = "a" * 40


class ReviewStandStatusTests(unittest.TestCase):
    def test_status_requires_exact_build_acceptance_and_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "run-review-stand"
            launcher.write_bytes((ROOT / "run-review-stand").read_bytes())
            launcher.chmod(0o755)
            (root / "tmp").mkdir()
            runner = root / "scripts/run_slice6.py"
            runner.parent.mkdir()
            runner.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
            runner.chmod(0o755)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            git = fake_bin / "git"
            git.write_text("#!/bin/sh\nprintf '%s\\n' \"$EXPECTED_BUILD\"\n", encoding="utf-8")
            git.chmod(0o755)
            curl = fake_bin / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                "  *\" --connect-timeout 1 --max-time 1 \"*) ;;\n"
                "  *) exit 64 ;;\n"
                "esac\n"
                "printf '%s\\n' \"$STATUS_DOCUMENT\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            process = subprocess.Popen([str(runner)])
            (root / "tmp/review-stand.pid").write_text(
                f"{process.pid}\n", encoding="ascii",
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["EXPECTED_BUILD"] = BUILD_ID
            try:
                baseline = {
                    "build_id": BUILD_ID,
                    "accepting": True,
                    "health": {"overall_readiness": "ready"},
                }
                for name, status_document, expected_code in (
                    ("ready", baseline, 0),
                    ("wrong build", {**baseline, "build_id": "b" * 40}, 1),
                    ("not accepting", {**baseline, "accepting": False}, 1),
                    (
                        "unready",
                        {**baseline, "health": {"overall_readiness": "unready"}},
                        1,
                    ),
                ):
                    with self.subTest(name=name):
                        environment["STATUS_DOCUMENT"] = json.dumps(status_document)
                        result = subprocess.run(
                            [str(launcher), "status"],
                            env=environment,
                            capture_output=True,
                            text=True,
                            timeout=5,
                            check=False,
                        )
                        self.assertEqual(result.returncode, expected_code, result.stderr)
                        if expected_code == 0:
                            self.assertIn(f"commit: {BUILD_ID}", result.stdout)
                        else:
                            self.assertIn("degraded:", result.stderr)
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

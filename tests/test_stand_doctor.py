from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from voice_agent_v2.operations import DEFAULT_MANIFEST_RELATIVE, load_operations_manifest
from voice_agent_v2.stand_doctor import CommandResult, Diagnosis, FileFact, diagnose, main


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_operations_manifest(ROOT / DEFAULT_MANIFEST_RELATIVE)
HOME = Path("/home/stand-test")


def expand(value: str) -> Path:
    return Path(value.replace("{home}", str(HOME)))


class ReadOnlyProbe:
    """A deterministic host whose public surface has no mutation operation."""

    def __init__(self, *, rootless: bool = True, missing_package: str | None = None) -> None:
        self.rootless = rootless
        self.missing_package = missing_package
        self.calls: list[tuple[str, tuple[str, ...] | str]] = []
        self.artifacts: dict[Path, FileFact] = {}
        for artifact in MANIFEST["artifacts"]:
            assert isinstance(artifact, dict)
            if "path" not in artifact:
                # The one glob has a concrete selected ABI match on this fake host.
                path = HOME / ".cache/voice-agent-v2/experiments/silero-baya-tts/venv/lib/python3.12/site-packages/torch/_C.cpython-312-x86_64-linux-gnu.so"
            else:
                path = expand(str(artifact["path"]))
            self.artifacts[path] = FileFact(
                True,
                regular=True,
                size=artifact.get("size_bytes", 1),
                executable=bool(artifact.get("executable")),
                sha256=str(artifact["sha256"]),
            )
        self.cache_directories = {
            expand(str(cache["path"]))
            for cache in MANIFEST["disk"]["cache_roots"]
            if cache["name"] not in {"stt-service-state", "silero-state"}
        }

    def command(self, arguments: tuple[str, ...]) -> CommandResult:
        self.calls.append(("command", arguments))
        if arguments[:2] == ("pacman", "-Q"):
            package = arguments[2]
            return CommandResult(1) if package == self.missing_package else CommandResult(0, f"{package} 1.0\n")
        if arguments[:2] == ("pacman", "-Qo"):
            binary = arguments[2]
            if self.missing_package == "shadow":
                return CommandResult(1)
            return CommandResult(0, f"{binary} is owned by shadow 4.20.0.arch1-1\n")
        if arguments in {
            ("node", "--version"), ("npm", "--version"), ("python3", "--version"),
            ("git", "--version"), ("nvidia-container-cli", "--version"),
        }:
            return CommandResult(0, "1.0\n")
        if arguments == ("nvidia-smi", "-L"):
            return CommandResult(0, "GPU 0: NVIDIA Test\n")
        if arguments[:2] == ("loginctl", "show-user"):
            return CommandResult(0, "yes\n")
        if arguments == ("systemctl", "--user", "is-active", "docker.service"):
            return CommandResult(0 if self.rootless else 3, "active\n" if self.rootless else "inactive\n")
        if arguments[:3] == ("docker", "info", "--format"):
            if arguments[-1] == "{{json .SecurityOptions}}":
                return CommandResult(0, '["name=rootless"]\n' if self.rootless else '["name=seccomp"]\n')
            return CommandResult(0, '{"nvidia":{"path":"nvidia-container-runtime"}}\n')
        if len(arguments) >= 4 and arguments[1:3] == ("-I", "-c"):
            wanted = json.loads(arguments[-1])
            for runtime in MANIFEST["python_runtimes"]:
                if sorted(runtime["packages"]) == wanted:
                    return CommandResult(0, json.dumps(runtime["packages"], sort_keys=True))
        raise AssertionError(f"unexpected inspection command: {arguments!r}")

    def os_release(self) -> dict[str, str]:
        self.calls.append(("os_release", ""))
        return {"ID": "endeavouros"}

    def system(self) -> str:
        return "Linux"

    def machine(self) -> str:
        return "x86_64"

    def user(self) -> str:
        return "stand-test"

    def home(self) -> Path:
        return HOME

    def file(self, path: Path, *, resolve_symlink: bool = False) -> FileFact:
        self.calls.append(("file", str(path)))
        if path in self.artifacts:
            return self.artifacts[path]
        if path in self.cache_directories:
            return FileFact(True, directory=True, writable=False)
        for runtime in MANIFEST["python_runtimes"]:
            if path == expand(str(runtime["python"])):
                return FileFact(True, regular=True, executable=True)
        return FileFact(False)

    def glob(self, pattern: str) -> tuple[Path, ...]:
        self.calls.append(("glob", pattern))
        return tuple(path for path in self.artifacts if "torch/_C." in str(path))

    def immutable_tree(self, path: Path) -> bool:
        self.calls.append(("immutable_tree", str(path)))
        return path in self.cache_directories


class StandDoctorTests(unittest.TestCase):
    def test_ready_host_reports_ready_from_exact_manifest(self) -> None:
        report = diagnose(ReadOnlyProbe(), source_root=ROOT)

        self.assertEqual(report.status, "ready")
        self.assertEqual(report.exit_code, 0)
        rendered = report.render()
        self.assertIn("stand doctor: READY", rendered)
        self.assertIn("artifact lfm2.5-q4-k-m", rendered)
        self.assertIn("immutable cache local-lfm-runtime", rendered)

    def test_missing_prerequisites_and_installed_but_not_rootless_are_incomplete(self) -> None:
        report = diagnose(ReadOnlyProbe(rootless=False, missing_package="git"), source_root=ROOT)

        self.assertEqual(report.status, "incomplete")
        self.assertEqual(report.exit_code, 1)
        rendered = report.render()
        self.assertIn("package git: not installed", rendered)
        self.assertIn("sudo -n pacman -Syu --needed git", rendered)
        self.assertIn("rootless Docker: Docker is installed but the rootless daemon is not ready", rendered)
        self.assertIn("dockerd-rootless-setuptool.sh install", rendered)

    def test_arch_rootless_mapping_uses_shadow_and_valid_remediation(self) -> None:
        report = diagnose(ReadOnlyProbe(missing_package="shadow"), source_root=ROOT)

        self.assertEqual(report.status, "incomplete")
        rendered = report.render()
        self.assertIn("rootless UID mapping", rendered)
        self.assertIn("sudo -n pacman -Syu --needed shadow", rendered)
        remedies = [line for line in rendered.splitlines() if "remedy:" in line]
        self.assertTrue(all("uidmap" not in line for line in remedies))

    def test_diagnosis_has_no_mutation_capability_or_side_effect(self) -> None:
        probe = ReadOnlyProbe()
        before = list(probe.calls)

        report = diagnose(probe, source_root=ROOT)

        self.assertEqual(report.status, "ready")
        self.assertEqual(before, [])
        self.assertTrue(probe.calls)
        self.assertFalse(hasattr(probe, "write"))
        self.assertFalse(hasattr(probe, "remove"))
        self.assertFalse(hasattr(probe, "mutate"))
        self.assertTrue(all(kind in {"command", "file", "glob", "immutable_tree", "os_release"} for kind, _ in probe.calls))

    def test_cli_renders_a_diagnosis_without_a_setup_path(self) -> None:
        report = Diagnosis(())
        output = StringIO()
        with patch("voice_agent_v2.stand_doctor.diagnose", return_value=report), redirect_stdout(output):
            self.assertEqual(main(("doctor",)), 0)
        self.assertEqual(output.getvalue(), "stand doctor: READY\n")


if __name__ == "__main__":
    unittest.main()

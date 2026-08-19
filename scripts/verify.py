#!/usr/bin/env python3
"""Single bounded PR-gate orchestrator; invoke through ./verify."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_support import (  # noqa: E402
    PhaseOwner,
    VerificationDeadline,
    VerificationFailure,
    inspect_owned,
)

PR_DEADLINE_SECONDS = 90.0
EXTENDED_DEADLINE_SECONDS = 600.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical Voice Agent v2 verification tier.")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument(
        "--tracer-only",
        action="store_true",
        help="run only the deterministic recovery tracer",
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def require_executable(path: Path, message: str) -> Path:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise VerificationFailure(message)
    return path


def private_artifacts(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.iterdir())


def main() -> int:
    arguments = parse_args()
    run_root = Path(os.environ["VOICE_AGENT_VERIFY_RUN_ROOT"]).resolve()
    private_root = run_root / "private"
    private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    nonce = f"voice-agent-{uuid.uuid4().hex}"
    budget = EXTENDED_DEADLINE_SECONDS if arguments.extended else PR_DEADLINE_SECONDS
    deadline = time.monotonic() + budget
    owner = PhaseOwner(nonce)
    interrupted = False

    def stop(_signal_number, _frame) -> None:
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        raise VerificationDeadline("verification received TERM at its hard deadline")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    cache_home = Path(os.environ["VOICE_AGENT_TEST_CACHE_HOME"]).resolve()
    runtime_python = cache_home / "voice-agent-v2/slice-6/runtime/venv/bin/python"
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(run_root / "home"),
            "XDG_CACHE_HOME": str(cache_home),
            "TMPDIR": str(run_root / "temp"),
            "PYTHONPYCACHEPREFIX": str(run_root / "pycache"),
            "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
            "NODE_DISABLE_COMPILE_CACHE": "1",
            "VOICE_AGENT_VERIFY_PRIVATE_ROOT": str(private_root),
            "VOICE_AGENT_SLICE6_CACHE": str(cache_home / "voice-agent-v2/slice-6"),
            "VOICE_AGENT_GECKODRIVER": str(
                cache_home / "voice-agent-v2/slice-6/tooling/geckodriver-v0.37.1"
            ),
            "LC_ALL": "C.UTF-8",
        }
    )
    tracer_command = [sys.executable, "-B", str(ROOT / "scripts/verify_tracer.py")]
    if arguments.output_directory is not None:
        tracer_command.extend(["--output-directory", str(arguments.output_directory)])

    failure: BaseException | None = None
    leaked_during_run = None
    try:
        owner.run("deterministic tracer", tracer_command, cwd=ROOT, environment=environment, deadline=deadline)
        if arguments.tracer_only:
            print("RESULT: PASS (tracer-only)")
            return 0

        owner.run(
            "signed release and read-only launcher behaviors",
            [
                str(ROOT / "launcher/test/run"),
                str(ROOT / "launcher/test/launcher.test.cjs"),
                str(ROOT / "launcher/test/install.test.cjs"),
                str(ROOT / "launcher/test/update.test.cjs"),
            ],
            cwd=ROOT,
            environment=environment,
            deadline=deadline,
        )
        python = require_executable(
            runtime_python,
            "test runtime is missing; run ./setup-test-runtime",
        )
        if arguments.extended:
            owner.run(
                "extended Python lifecycle/guardian owners",
                [str(python), "-B", str(ROOT / "scripts/run_behavior_tests.py"), "extended"],
                cwd=ROOT,
                environment=environment,
                deadline=deadline,
            )
            owner.run(
                "TypeScript typecheck",
                ["npm", "run", "typecheck"],
                cwd=ROOT / "web",
                environment=environment,
                deadline=deadline,
            )
            owner.run(
                "production web build",
                ["npm", "run", "build:production-only"],
                cwd=ROOT / "web",
                environment={**environment, "VITE_APP_VERSION": os.environ.get("VOICE_AGENT_BUILD_ID", "extended")},
                deadline=deadline,
            )
            owner.run(
                "review fixture build",
                ["npm", "run", "build:review-only"],
                cwd=ROOT / "web",
                environment={**environment, "VITE_APP_VERSION": os.environ.get("VOICE_AGENT_BUILD_ID", "extended")},
                deadline=deadline,
            )
            owner.run(
                "long Firefox/LiveKit lifecycle",
                [str(python), "-B", str(ROOT / "scripts/verify_firefox_livekit_extended.py")],
                cwd=ROOT,
                environment=environment,
                deadline=deadline,
            )
            print("RESULT: PASS (extended lifecycle/browser tier)")
            return 0

        owner.run(
            "bounded operation custody/failure matrix",
            [str(python), "-B", str(ROOT / "scripts/verify_operation_tracer.py")],
            cwd=ROOT,
            environment=environment,
            deadline=deadline,
        )
        owner.run(
            "hermetic current Python behaviors",
            [str(python), "-B", str(ROOT / "scripts/run_behavior_tests.py"), "hermetic"],
            cwd=ROOT,
            environment=environment,
            deadline=deadline,
        )
        owner.run(
            "bounded local-socket readiness",
            [str(python), "-B", str(ROOT / "scripts/run_behavior_tests.py"), "local-socket"],
            cwd=ROOT,
            environment={**environment, "VOICE_AGENT_VERIFY_SLICE6_RUNTIME": "1"},
            deadline=deadline,
        )
        owner.run(
            "installed SDK/capability/composition contract",
            [str(python), "-B", str(ROOT / "scripts/verify_runtime_contract.py")],
            cwd=ROOT,
            environment=environment,
            deadline=deadline,
        )
        owner.run(
            "full Vitest surface",
            ["npm", "test"],
            cwd=ROOT / "web",
            environment=environment,
            deadline=deadline,
        )
        owner.run(
            "TypeScript typecheck",
            ["npm", "run", "typecheck"],
            cwd=ROOT / "web",
            environment=environment,
            deadline=deadline,
        )
        build_id = os.environ.get("VOICE_AGENT_BUILD_ID", "pr-verification")
        owner.run(
            "production web build",
            ["npm", "run", "build:production-only"],
            cwd=ROOT / "web",
            environment={**environment, "VITE_APP_VERSION": build_id},
            deadline=deadline,
        )
        owner.run(
            "review fixture probe build",
            ["npm", "run", "build:review-only"],
            cwd=ROOT / "web",
            environment={**environment, "VITE_APP_VERSION": build_id},
            deadline=deadline,
        )
        owner.run(
            "short production Firefox/actual-LiveKit smoke",
            [str(python), "-B", str(ROOT / "scripts/verify_firefox_livekit.py")],
            cwd=ROOT,
            environment=environment,
            deadline=deadline,
        )
        print(f"canonical_pr_gate: PASS deadline_seconds={int(PR_DEADLINE_SECONDS)} unexpected_skips=0")
        print("RESULT: PASS")
        return 0
    except BaseException as error:
        failure = error
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        leaked_during_run = inspect_owned(nonce, exclude=(os.getpid(), os.getppid()))
        remaining = owner.cleanup()
        artifacts = [
            *private_artifacts(private_root),
            *private_artifacts(run_root / "temp"),
        ]
        if leaked_during_run.present:
            print(
                "owned process/listener leak detected and reaped: "
                f"pids={list(leaked_during_run.pids)} listeners={list(leaked_during_run.listeners)}",
                file=sys.stderr,
            )
        if remaining.present:
            print(
                f"owned survivors remain: pids={list(remaining.pids)} listeners={list(remaining.listeners)}",
                file=sys.stderr,
            )
        if artifacts:
            print(
                f"private artifacts remain: {[str(path) for path in artifacts]}",
                file=sys.stderr,
            )
        if failure is None and (leaked_during_run.present or remaining.present or artifacts):
            # A return from try has already selected 0; replace it with a process-level failure.
            # Raising from finally is intentional and is rendered without a traceback below.
            raise VerificationFailure(
                "owned cleanup/private-artifact invariant failed: "
                f"initial={leaked_during_run} remaining={remaining} "
                f"artifacts={[str(path) for path in artifacts]}"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

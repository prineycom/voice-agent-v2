#!/usr/bin/env python3
"""Process ownership, monotonic deadlines, and leak checks for test commands."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Mapping, Sequence


class VerificationFailure(RuntimeError):
    """A bounded verification phase failed."""


class VerificationDeadline(VerificationFailure):
    """The monotonic command deadline expired."""


def assert_browser_smoke_budgets(
    functional_seconds: float, cleanup_seconds: float
) -> None:
    """Keep the product probe short without charging cold tool provisioning to it."""
    if functional_seconds > 15:
        raise AssertionError(
            "short Firefox/LiveKit functional smoke exceeded 15 seconds: "
            f"{functional_seconds:.3f}s"
        )
    if cleanup_seconds > 10:
        raise AssertionError(
            f"Firefox/LiveKit owned cleanup exceeded 10 seconds: {cleanup_seconds:.3f}s"
        )


@dataclass(frozen=True)
class LeakReport:
    pids: tuple[int, ...]
    listeners: tuple[str, ...]

    @property
    def present(self) -> bool:
        return bool(self.pids or self.listeners)


def _pid_environment(pid: int) -> bytes:
    try:
        return (Path("/proc") / str(pid) / "environ").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return b""


def owned_pids(nonce: str, *, exclude: Sequence[int] = ()) -> list[int]:
    """Return live same-user processes carrying the per-command ownership nonce."""
    marker = f"VOICE_AGENT_VERIFY_OWNER={nonce}".encode()
    excluded = set(exclude)
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in excluded:
            continue
        if marker in _pid_environment(pid).split(b"\0"):
            found.append(pid)
    return sorted(found)


def _socket_inodes(pid: int) -> set[str]:
    inodes: set[str] = set()
    directory = Path("/proc") / str(pid) / "fd"
    try:
        descriptors = tuple(directory.iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return inodes
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[8:-1])
    return inodes


def owned_listeners(pids: Sequence[int]) -> list[str]:
    """Describe TCP listeners held by owned processes without opening a socket."""
    inode_owners = {
        inode: pid for pid in pids for inode in _socket_inodes(pid)
    }
    listeners: list[str] = []
    if not inode_owners:
        return listeners
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = table.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            inode = fields[9]
            if inode in inode_owners:
                listeners.append(
                    f"pid={inode_owners[inode]} table={table.name} local={fields[1]} inode={inode}"
                )
    return sorted(listeners)


def inspect_owned(nonce: str, *, exclude: Sequence[int] = ()) -> LeakReport:
    pids = owned_pids(nonce, exclude=exclude)
    return LeakReport(tuple(pids), tuple(owned_listeners(pids)))


def _group_members(process_group: int) -> list[str]:
    members: list[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            document = (entry / "stat").read_text(encoding="ascii")
            remainder = document.rsplit(") ", 1)[1].split()
            state, group = remainder[0], int(remainder[2])
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (IndexError, ValueError, OSError):
            continue
        if group == process_group:
            members.append(f"pid={entry.name} state={state} command={command}")
    return members


def _group_exists(process_group: int) -> bool:
    # A just-reaped phase can briefly leave an adopted zombie in its group.
    # Zombies own no listener or executable work and cannot be signalled; do
    # not misreport them as a leaked live process.
    observed_member = False
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        entries = ()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            document = (entry / "stat").read_text(encoding="ascii")
            remainder = document.rsplit(") ", 1)[1].split()
            state, group = remainder[0], int(remainder[2])
        except (IndexError, ValueError, OSError):
            continue
        if group == process_group:
            observed_member = True
            if state != "Z":
                return True
    if observed_member:
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_group(process_group: int, *, grace_seconds: float = 10.0) -> bool:
    """TERM a process group, wait no more than grace, then KILL it."""
    if not _group_exists(process_group):
        return False
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + max(0.0, min(grace_seconds, 10.0))
    while _group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_deadline = time.monotonic() + 1.0
        while _group_exists(process_group) and time.monotonic() < kill_deadline:
            time.sleep(0.02)
    return _group_exists(process_group)


def terminate_owned(
    nonce: str, *, exclude: Sequence[int] = (), grace_seconds: float = 10.0
) -> LeakReport:
    """Reap detached owned processes that escaped their original process group."""
    initial = inspect_owned(nonce, exclude=exclude)
    for pid in initial.pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + max(0.0, min(grace_seconds, 10.0))
    while time.monotonic() < deadline:
        if not owned_pids(nonce, exclude=exclude):
            break
        time.sleep(0.02)
    for pid in owned_pids(nonce, exclude=exclude):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    kill_deadline = time.monotonic() + 1.0
    while time.monotonic() < kill_deadline:
        if not owned_pids(nonce, exclude=exclude):
            break
        time.sleep(0.02)
    return inspect_owned(nonce, exclude=exclude)


class PhaseOwner:
    """Own every subprocess group launched by one verification command."""

    def __init__(self, nonce: str, *, grace_seconds: float = 10.0) -> None:
        self.nonce = nonce
        self.grace_seconds = min(grace_seconds, 10.0)
        self._groups: set[int] = set()
        self._current: subprocess.Popen[bytes] | None = None

    def run(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        deadline: float,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationDeadline(f"deadline expired before phase {name}")
        phase_environment = dict(environment)
        phase_environment["VOICE_AGENT_VERIFY_OWNER"] = self.nonce
        print(f"=== phase: {name} (remaining={remaining:.1f}s) ===", flush=True)
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=phase_environment,
            start_new_session=True,
        )
        self._current = process
        self._groups.add(process.pid)
        try:
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                terminate_group(process.pid, grace_seconds=self.grace_seconds)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                raise VerificationDeadline(
                    f"phase {name} exceeded the monotonic command deadline"
                ) from error
            if returncode != 0:
                terminate_group(process.pid, grace_seconds=self.grace_seconds)
                raise VerificationFailure(
                    f"phase {name} failed with exit status {returncode}"
                )
            settle_deadline = time.monotonic() + 1.0
            while _group_exists(process.pid) and time.monotonic() < settle_deadline:
                time.sleep(0.02)
            if _group_exists(process.pid):
                members = _group_members(process.pid)
                terminate_group(process.pid, grace_seconds=self.grace_seconds)
                raise VerificationFailure(
                    f"phase {name} leaked its owned process group {process.pid}: {members}"
                )
        finally:
            self._current = None
            if process.poll() is not None:
                self._groups.discard(process.pid)

    def cleanup(self) -> LeakReport:
        for process_group in tuple(self._groups):
            terminate_group(process_group, grace_seconds=self.grace_seconds)
            self._groups.discard(process_group)
        return terminate_owned(
            self.nonce,
            exclude=(os.getpid(), os.getppid()),
            grace_seconds=self.grace_seconds,
        )

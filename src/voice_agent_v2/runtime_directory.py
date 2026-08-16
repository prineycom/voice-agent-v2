"""Validation for private runtime-tmpfs storage."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import stat


SYSTEMD_RUNTIME_ROOT = Path("/run/voice-agent-v2")


def _tmpfs_mount(path: Path, mountinfo: str) -> bool:
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 7:
            continue
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if (
            len(fields) > separator + 1
            and fields[4] == str(path)
            and fields[separator + 1] == "tmpfs"
        ):
            return True
    return False


def require_lifetime_runtime_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    user_runtime_root = Path("/run/user") / str(os.getuid())
    try:
        status = resolved.stat()
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        username = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError) as error:
        raise ValueError("lifetime-scoped private runtime tmpfs is unavailable") from error
    owned_private_directory = (
        stat.S_ISDIR(status.st_mode)
        and status.st_uid == os.getuid()
        and not status.st_mode & 0o077
    )
    user_runtime = (
        resolved == user_runtime_root
        and not (Path("/var/lib/systemd/linger") / username).exists()
        and _tmpfs_mount(resolved, mountinfo)
    )
    systemd_runtime = (
        resolved == SYSTEMD_RUNTIME_ROOT
        and _tmpfs_mount(SYSTEMD_RUNTIME_ROOT.parent, mountinfo)
    )
    if not owned_private_directory or not (user_runtime or systemd_runtime):
        raise ValueError("runtime root is not the lifetime-scoped private runtime tmpfs")
    return resolved

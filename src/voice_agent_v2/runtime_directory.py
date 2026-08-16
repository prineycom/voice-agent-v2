"""Validation for logout-scoped private runtime storage."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import stat


def require_lifetime_runtime_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    expected = Path("/run/user") / str(os.getuid())
    try:
        status = resolved.stat()
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        username = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError) as error:
        raise ValueError("lifetime-scoped private runtime tmpfs is unavailable") from error
    mounted_as_tmpfs = False
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
            and fields[4] == str(resolved)
            and fields[separator + 1] == "tmpfs"
        ):
            mounted_as_tmpfs = True
            break
    linger_path = Path("/var/lib/systemd/linger") / username
    if (
        resolved != expected
        or linger_path.exists()
        or not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_mode & 0o077
        or not mounted_as_tmpfs
    ):
        raise ValueError("runtime root is not the lifetime-scoped private runtime tmpfs")
    return resolved

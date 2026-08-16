from __future__ import annotations

import ctypes
import os
import signal
import sys


PR_SET_PDEATHSIG = 1


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        return 2
    try:
        expected_parent = int(arguments[0])
    except ValueError:
        return 2
    if expected_parent <= 1:
        return 2
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        return 2
    if os.getppid() != expected_parent:
        return 1
    command = arguments[1:]
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

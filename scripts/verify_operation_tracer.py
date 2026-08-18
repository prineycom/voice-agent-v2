#!/usr/bin/env python3
"""Bounded E2.2 synthetic-operation matrix; owned by the canonical gate."""

from __future__ import annotations

import socket
import sys
import time
import unittest


class NetworkAccessDenied(RuntimeError):
    pass


def deny_network(event: str, arguments: tuple[object, ...]) -> None:
    if event == "socket.__new__" and len(arguments) > 1 and arguments[1] == socket.AF_UNIX:
        return
    if event.startswith("socket."):
        raise NetworkAccessDenied("network denied")


def main() -> int:
    started = time.monotonic()
    sys.addaudithook(deny_network)
    try:
        socket.socket()
    except NetworkAccessDenied:
        pass
    else:
        raise AssertionError("operation tracer network denial is inactive")
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_operation_tracer")
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
    elapsed = time.monotonic() - started
    if not result.wasSuccessful() or result.skipped or elapsed >= 8.0:
        return 1
    print(f"synthetic_operation_matrix: PASS cases={result.testsRun} network=denied elapsed_ms={int(elapsed * 1000)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

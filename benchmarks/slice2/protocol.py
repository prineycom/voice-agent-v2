"""Bounded JSON-lines process protocol for benchmark-only adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Any

from .safety import cache_path


class ProtocolError(RuntimeError):
    pass


class JsonLineProcess:
    """Start one adapter without inheriting secret-bearing environment values."""

    def __init__(
        self,
        command: list[str],
        stderr_path: Path,
        extra_environment: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.stderr_path = cache_path(stderr_path)
        self.extra_environment = extra_environment or {}
        self.process: subprocess.Popen[str] | None = None
        self._stderr = None
        self._queue: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self, timeout_seconds: float = 120) -> dict[str, Any]:
        if self.process is not None:
            raise ProtocolError("adapter already started")
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        if self.stderr_path.exists():
            raise ProtocolError(f"refusing to replace adapter log: {self.stderr_path}")
        self._stderr = self.stderr_path.open("x", encoding="utf-8")
        environment = {
            "HOME": "/home/priney/.cache/voice-agent-v2/slice-2/home",
            "XDG_CACHE_HOME": "/home/priney/.cache/voice-agent-v2/slice-2/xdg",
            "HF_HOME": "/home/priney/.cache/voice-agent-v2/slice-2/huggingface",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **self.extra_environment,
        }
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
            env=environment,
            start_new_session=True,
        )
        assert self.process.stdout is not None

        def read_lines() -> None:
            try:
                for line in self.process.stdout:
                    self._queue.put(json.loads(line))
                self._queue.put(None)
            except BaseException as error:
                self._queue.put(error)

        self._reader = threading.Thread(target=read_lines, name="slice2-adapter-reader", daemon=True)
        self._reader.start()
        ready = self.receive(timeout_seconds)
        if ready.get("event") != "ready":
            raise ProtocolError(f"adapter did not emit ready: {ready.get('event')}")
        return ready

    def send(self, value: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise ProtocolError("adapter is not running")
        self.process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout_seconds: float) -> dict[str, Any]:
        try:
            value = self._queue.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise ProtocolError("adapter response timed out") from error
        if value is None:
            code = None if self.process is None else self.process.poll()
            raise ProtocolError(f"adapter closed output unexpectedly, exit={code}")
        if isinstance(value, BaseException):
            raise ProtocolError("adapter output reader failed") from value
        return value

    def request(self, value: dict[str, Any], timeout_seconds: float = 120) -> dict[str, Any]:
        self.send(value)
        response = self.receive(timeout_seconds)
        if response.get("request_id") != value.get("request_id"):
            raise ProtocolError("adapter response correlation mismatch")
        if response.get("event") != "final":
            raise ProtocolError(f"adapter request failed: {response.get('code', response.get('event'))}")
        return response

    def cancel_process(self, timeout_seconds: float = 5) -> float:
        if self.process is None or self.process.poll() is not None:
            return 0.0
        started = time.perf_counter()
        os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=timeout_seconds)
        return (time.perf_counter() - started) * 1000

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                self.send({"operation": "shutdown"})
                stopped = self.receive(5)
                if stopped.get("event") != "stopped":
                    raise ProtocolError("adapter rejected shutdown")
                self.process.wait(timeout=5)
            except (OSError, ProtocolError, subprocess.TimeoutExpired):
                self.cancel_process()
        if self._reader is not None:
            self._reader.join(timeout=5)
        if self._stderr is not None:
            self._stderr.close()

    def __enter__(self) -> "JsonLineProcess":
        self.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

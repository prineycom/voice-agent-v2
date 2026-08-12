"""Minimal bounded JSON-lines child process used by real local inference adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Iterator


MAX_PROTOCOL_LINE_BYTES = 1_048_576
MAX_PROTOCOL_QUEUE_EVENTS = 32


class AdapterProcessError(RuntimeError):
    pass


class AdapterProcess:
    def __init__(self, command: list[str], log_path: Path, environment: dict[str, str]) -> None:
        self.command = command
        self.log_path = log_path
        self.environment = environment
        self.process: subprocess.Popen[str] | None = None
        self._log = None
        self._events: queue.Queue[dict | BaseException | None] = queue.Queue(
            maxsize=MAX_PROTOCOL_QUEUE_EVENTS
        )
        self._reader: threading.Thread | None = None
        self._cancel_requested = threading.Event()

    def start(self, timeout_seconds: float) -> dict:
        if self.process is not None:
            raise AdapterProcessError("adapter already started")
        self._events = queue.Queue(maxsize=MAX_PROTOCOL_QUEUE_EVENTS)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("x", encoding="utf-8")
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                text=True, bufsize=1, start_new_session=True, env=self.environment,
            )
            self.process = process
            if self._cancel_requested.is_set():
                self.cancel()
                raise AdapterProcessError("adapter startup cancelled")
            assert process.stdout is not None

            def publish(value: dict | BaseException | None) -> None:
                try:
                    self._events.put_nowait(value)
                except queue.Full as error:
                    raise AdapterProcessError("adapter event queue overflow") from error

            def fail_reader(error: BaseException) -> None:
                try:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=0.25)
                except (OSError, subprocess.SubprocessError):
                    pass
                while True:
                    try:
                        self._events.get_nowait()
                    except queue.Empty:
                        break
                self._events.put_nowait(error)

            def read() -> None:
                try:
                    while True:
                        line = process.stdout.readline(MAX_PROTOCOL_LINE_BYTES + 1)
                        if line == "":
                            publish(None)
                            return
                        if len(line.encode("utf-8")) > MAX_PROTOCOL_LINE_BYTES:
                            raise AdapterProcessError("adapter protocol line exceeds size bound")
                        publish(json.loads(line))
                except BaseException as error:
                    fail_reader(error)

            self._reader = threading.Thread(target=read, name="voice-agent-adapter-reader", daemon=True)
            self._reader.start()
            ready = self.receive(timeout_seconds)
            if ready.get("event") != "ready":
                raise AdapterProcessError("adapter did not become ready")
            return ready
        except BaseException:
            try:
                self.close()
            except (OSError, subprocess.SubprocessError):
                pass
            raise

    def send(self, value: dict) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise AdapterProcessError("adapter is not running")
        self.process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout_seconds: float) -> dict:
        try:
            value = self._events.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise AdapterProcessError("adapter timed out") from error
        if value is None:
            raise AdapterProcessError("adapter closed unexpectedly")
        if isinstance(value, BaseException):
            raise AdapterProcessError("adapter emitted invalid protocol output") from value
        if not isinstance(value, dict):
            raise AdapterProcessError("adapter emitted non-object protocol output")
        return value

    def request(self, value: dict, timeout_seconds: float) -> dict:
        self.send(value)
        while True:
            response = self.receive(timeout_seconds)
            if response.get("request_id") != value.get("request_id"):
                raise AdapterProcessError("adapter correlation mismatch")
            if response.get("event") == "error":
                raise AdapterProcessError(str(response.get("error_class", "adapter_error")))
            if response.get("event") == "final":
                return response

    def stream(self, value: dict, timeout_seconds: float) -> Iterator[dict]:
        deadline = time.monotonic() + timeout_seconds
        self.send(value)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdapterProcessError("adapter timed out")
            response = self.receive(remaining)
            if response.get("request_id") != value.get("request_id"):
                raise AdapterProcessError("adapter correlation mismatch")
            if response.get("event") == "error":
                raise AdapterProcessError(str(response.get("error_class", "adapter_error")))
            yield response
            if response.get("event") == "final":
                return

    def cancel(self, timeout_seconds: float = 0.25) -> float:
        self._cancel_requested.set()
        process = self.process
        if process is None or process.poll() is not None:
            return 0.0
        started = time.monotonic()
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=timeout_seconds)
        return (time.monotonic() - started) * 1000

    def close(self) -> None:
        process = self.process
        try:
            if process is not None and process.poll() is None:
                self.cancel()
            if self._reader is not None:
                self._reader.join(timeout=5)
        finally:
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
            if self._log is not None:
                self._log.close()
            self.process = None
            self._reader = None
            self._log = None

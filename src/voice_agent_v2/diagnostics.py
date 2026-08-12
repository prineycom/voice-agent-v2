"""Bounded content-free JSONL diagnostics for Slice 6 manual acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Mapping

MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_TRACE_RECORDS = 20_000
MAX_TRACE_DIRECTORY_BYTES = 64 * 1024 * 1024
MAX_TRACE_FILES = 32
MAX_TRACE_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_TRACE_FAILURES = 20_000
_DIRECTORY_LOCK = threading.Lock()
_ALLOWED_VALUE_TYPES = (bool, float, int, str, type(None))
_FORBIDDEN_KEYS = frozenset({
    "audio", "content", "pcm", "prompt", "response", "secret", "text", "token", "transcript"
})


def _safe_key(key: str) -> bool:
    lowered = key.lower()
    return not any(word in lowered for word in _FORBIDDEN_KEYS)


def _safe_value(value: object) -> object:
    if not isinstance(value, _ALLOWED_VALUE_TYPES):
        raise ValueError("diagnostic value type is forbidden")
    if isinstance(value, str) and (len(value) > 512 or "\n" in value or "\r" in value):
        raise ValueError("diagnostic string is outside bounds")
    return value


@dataclass(frozen=True)
class TraceIdentity:
    session_id: str
    turn_id: str = "session"
    stream_epoch: int = 1


class PrivacySafeTrace:
    def __init__(self, path: Path, identity: TraceIdentity) -> None:
        self.path = path.resolve()
        self.identity = identity
        self._started = time.monotonic()
        self._records = 0
        self._bytes = 0
        self._lock = threading.Lock()
        self._write_failed = False
        self._failure_counts = {"validation": 0, "write": 0, "limit": 0}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _DIRECTORY_LOCK:
                self._prune_directory(reserve_bytes=MAX_TRACE_BYTES, reserve_files=1)
            self._bytes = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            self._write_failed = True
            self._increment_failure("write")

    @property
    def failure_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._failure_counts)

    def _increment_failure(self, kind: str) -> None:
        self._failure_counts[kind] = min(
            self._failure_counts[kind] + 1, MAX_TRACE_FAILURES
        )

    def child(self, *, turn_id: str, stream_epoch: int) -> PrivacySafeTrace:
        child = object.__new__(PrivacySafeTrace)
        child.path = self.path
        child.identity = TraceIdentity(self.identity.session_id, turn_id, stream_epoch)
        child._started = self._started
        child._records = self._records
        child._bytes = self._bytes
        child._lock = self._lock
        child._write_failed = self._write_failed
        child._failure_counts = self._failure_counts
        return child

    def _trace_files(self) -> list[Path]:
        return [
            path for path in self.path.parent.glob("*.jsonl")
            if path.is_file() and not path.is_symlink()
        ]

    def _prune_directory(self, *, reserve_bytes: int, reserve_files: int) -> None:
        files = self._trace_files()
        now = time.time()
        retained: list[tuple[Path, int, float]] = []
        for path in files:
            stat = path.stat()
            if path != self.path and now - stat.st_mtime > MAX_TRACE_AGE_SECONDS:
                path.unlink(missing_ok=True)
            else:
                retained.append((path, stat.st_size, stat.st_mtime))
        retained.sort(key=lambda item: item[2])
        total = sum(size for _path, size, _mtime in retained)
        while retained and (
            len(retained) + reserve_files > MAX_TRACE_FILES
            or total + reserve_bytes > MAX_TRACE_DIRECTORY_BYTES
        ):
            index = next(
                (index for index, item in enumerate(retained) if item[0] != self.path),
                None,
            )
            if index is None:
                break
            path, size, _mtime = retained.pop(index)
            path.unlink(missing_ok=True)
            total -= size

    def emit(
        self,
        stage: str,
        event: str,
        fields: Mapping[str, object] | None = None,
        *,
        turn_id: str | None = None,
        stream_epoch: int | None = None,
    ) -> bool:
        try:
            values = dict(fields or {})
            if (
                not isinstance(stage, str)
                or not isinstance(event, str)
                or len(stage) > 64
                or len(event) > 64
                or not _safe_key(stage)
                or not _safe_key(event)
            ):
                raise ValueError("diagnostic stage/event is forbidden")
            normalized: dict[str, object] = {}
            for key, value in values.items():
                if not isinstance(key, str) or len(key) > 64 or not _safe_key(key):
                    raise ValueError("diagnostic key is forbidden")
                normalized[key] = _safe_value(value)
        except (TypeError, ValueError):
            with self._lock:
                self._increment_failure("validation")
            return False
        document = {
            "schema_version": "voice-agent.slice6-diagnostic.v1",
            "wall_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "monotonic_ms": round((time.monotonic() - self._started) * 1000, 3),
            "session_id": self.identity.session_id,
            "turn_id": turn_id or self.identity.turn_id,
            "stream_epoch": stream_epoch or self.identity.stream_epoch,
            "stage": stage,
            "event": event,
            "fields": normalized,
        }
        line = json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        with self._lock:
            if self._write_failed:
                return False
            if (
                self._records >= MAX_TRACE_RECORDS
                or self._bytes + len(encoded) > MAX_TRACE_BYTES
            ):
                self._increment_failure("limit")
                return False
            try:
                with _DIRECTORY_LOCK:
                    self._prune_directory(reserve_bytes=0, reserve_files=0)
                    files = self._trace_files()
                    total = sum(path.stat().st_size for path in files)
                    if self.path not in files and len(files) >= MAX_TRACE_FILES:
                        self._increment_failure("limit")
                        return False
                    if total + len(encoded) > MAX_TRACE_DIRECTORY_BYTES:
                        self._increment_failure("limit")
                        return False
                    with self.path.open("ab") as destination:
                        destination.write(encoded)
            except OSError:
                self._write_failed = True
                self._increment_failure("write")
                return False
            self._records += 1
            self._bytes += len(encoded)
            return True

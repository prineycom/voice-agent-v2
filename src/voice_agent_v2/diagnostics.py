"""Bounded privacy-safe metadata diagnostics and explicit content capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Callable, Mapping

from .contracts import valid_correlation_id
from .observability import (
    MAX_OBSERVATION_SEQUENCE,
    OBSERVATION_VERSION,
    safe_observation_key,
    safe_observation_scalar,
    validate_observation,
)

MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_TRACE_RECORDS = 20_000
MAX_TRACE_DIRECTORY_BYTES = 64 * 1024 * 1024
MAX_TRACE_FILES = 32
MAX_TRACE_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_TRACE_FAILURES = 20_000
MAX_CAPTURE_BYTES = 1 * 1024 * 1024
MAX_CAPTURE_FILES = 16
MIN_CAPTURE_TTL_SECONDS = 60
MAX_CAPTURE_TTL_SECONDS = 60 * 60
_DIRECTORY_LOCK = threading.Lock()
_CAPTURE_KINDS = frozenset({"raw-audio", "transcript", "prompt", "response"})


@dataclass(frozen=True)
class TraceIdentity:
    session_id: str
    turn_id: str = "session"
    stream_epoch: int = 1

    def __post_init__(self) -> None:
        if (
            not valid_correlation_id(self.session_id)
            or not valid_correlation_id(self.turn_id)
            or type(self.stream_epoch) is not int
            or not 1 <= self.stream_epoch <= MAX_OBSERVATION_SEQUENCE
        ):
            raise ValueError("invalid diagnostic correlation")


@dataclass
class _TraceState:
    started: float
    records: int
    byte_count: int
    lock: threading.Lock
    write_failed: bool
    failure_counts: dict[str, int]


class PrivacySafeTrace:
    """Append-only metadata JSONL. Content-like keys and values fail closed."""

    def __init__(self, path: Path, identity: TraceIdentity) -> None:
        self.path = path.resolve()
        self.identity = identity
        self._state = _TraceState(
            time.monotonic(), 0, 0, threading.Lock(), False,
            {"validation": 0, "write": 0, "limit": 0},
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _DIRECTORY_LOCK:
                self._prune_directory(reserve_bytes=MAX_TRACE_BYTES, reserve_files=1)
            self._state.byte_count = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            self._state.write_failed = True
            self._increment_failure("write")

    @property
    def failure_counts(self) -> dict[str, int]:
        with self._state.lock:
            return dict(self._state.failure_counts)

    def _increment_failure(self, kind: str) -> None:
        self._state.failure_counts[kind] = min(
            self._state.failure_counts[kind] + 1, MAX_TRACE_FAILURES
        )

    def child(self, *, turn_id: str, stream_epoch: int) -> "PrivacySafeTrace":
        identity = TraceIdentity(self.identity.session_id, turn_id, stream_epoch)
        child = object.__new__(PrivacySafeTrace)
        child.path = self.path
        child.identity = identity
        child._state = self._state
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
        effective_turn = self.identity.turn_id if turn_id is None else turn_id
        effective_epoch = self.identity.stream_epoch if stream_epoch is None else stream_epoch
        try:
            values = dict(fields or {})
            if (
                not isinstance(stage, str)
                or not safe_observation_key(stage)
                or not isinstance(event, str)
                or not safe_observation_key(event)
                or not valid_correlation_id(effective_turn)
                or type(effective_epoch) is not int
                or not 1 <= effective_epoch <= MAX_OBSERVATION_SEQUENCE
                or len(values) > 64
            ):
                raise ValueError("diagnostic identity is forbidden")
            normalized: dict[str, object] = {}
            for key, value in values.items():
                if (
                    not isinstance(key, str)
                    or not safe_observation_key(key)
                    or not safe_observation_scalar(value, key=key)
                ):
                    raise ValueError("diagnostic field is forbidden")
                normalized[key] = value
        except (TypeError, ValueError):
            with self._state.lock:
                self._increment_failure("validation")
            return False
        with self._state.lock:
            if self._state.write_failed:
                return False
            if self._state.records >= MAX_TRACE_RECORDS:
                self._increment_failure("limit")
                return False
            sequence = self._state.records + 1
            document = {
                "schema_version": OBSERVATION_VERSION,
                "record_sequence": sequence,
                "wall_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "monotonic_ms": round((time.monotonic() - self._state.started) * 1000, 3),
                "session_id": self.identity.session_id,
                "turn_id": effective_turn,
                "stream_epoch": effective_epoch,
                "stage": stage,
                "event": event,
                "fields": normalized,
            }
            try:
                validate_observation(document)
            except ValueError:
                self._increment_failure("validation")
                return False
            line = json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n"
            encoded = line.encode("utf-8")
            if self._state.byte_count + len(encoded) > MAX_TRACE_BYTES:
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
                self._state.write_failed = True
                self._increment_failure("write")
                return False
            self._state.records = sequence
            self._state.byte_count += len(encoded)
            return True

    def observe(
        self,
        stage: str,
        event: str,
        fields: Mapping[str, object],
        *,
        stream_epoch: int | None = None,
    ) -> bool:
        values = dict(fields)
        turn_id = values.pop("turn_id", self.identity.turn_id)
        correlated_epoch = values.pop("stream_epoch", stream_epoch)
        return self.emit(
            stage,
            event,
            values,
            turn_id=turn_id,
            stream_epoch=correlated_epoch,
        )


def _inside_git_worktree(path: Path) -> bool:
    current = path.resolve()
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.exists() or marker.is_symlink():
            return True
    return False


class DiagnosticContentCapture:
    """Explicit opt-in, short-lived content capture outside every Git worktree."""

    def __init__(
        self,
        root: Path,
        session_id: str,
        *,
        opt_in: bool,
        ttl_seconds: int = 15 * 60,
        now: Callable[[], float] = time.time,
        timer_factory: Callable[
            [float, Callable[[], None]], threading.Timer
        ] = threading.Timer,
    ) -> None:
        if not opt_in:
            raise ValueError("diagnostic content capture requires explicit opt-in")
        if not valid_correlation_id(session_id):
            raise ValueError("invalid capture session identity")
        if type(ttl_seconds) is not int or not MIN_CAPTURE_TTL_SECONDS <= ttl_seconds <= MAX_CAPTURE_TTL_SECONDS:
            raise ValueError("diagnostic capture TTL is outside bounds")
        resolved = root.expanduser().resolve()
        if not resolved.is_absolute() or _inside_git_worktree(resolved):
            raise ValueError("diagnostic content capture must remain outside Git")
        self.root = resolved
        self.session_id = session_id
        self.path = resolved / f"capture-{session_id}"
        self.ttl_seconds = ttl_seconds
        self._now = now
        self._created = float(now())
        self._files = 0
        self._bytes = 0
        self._lock = threading.RLock()
        self._expiry_timer: threading.Timer | None = None
        self.purge_expired(resolved, now=now)
        self.path.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            os.chmod(self.path, 0o700)
            manifest = {
                "schema_version": "voice-agent.diagnostic-content-capture.v1",
                "session_id": session_id,
                "created_unix_seconds": self._created,
                "expires_unix_seconds": self._created + ttl_seconds,
                "max_files": MAX_CAPTURE_FILES,
                "max_bytes": MAX_CAPTURE_BYTES,
                "explicit_opt_in": True,
            }
            self._write_file(
                "manifest.json",
                json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
            )
            expiry_timer = timer_factory(ttl_seconds, self._expire_now)
            expiry_timer.daemon = True
            self._expiry_timer = expiry_timer
            expiry_timer.start()
        except BaseException:
            shutil.rmtree(self.path, ignore_errors=True)
            raise
        self._files = 0
        self._bytes = 0

    def _write_file(self, name: str, payload: bytes) -> None:
        destination = self.path / name
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    @property
    def expired(self) -> bool:
        return self._now() >= self._created + self.ttl_seconds

    def capture(self, kind: str, payload: bytes | str) -> Path:
        if kind not in _CAPTURE_KINDS:
            raise ValueError("unsupported diagnostic content kind")
        encoded = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if not encoded:
            raise ValueError("empty diagnostic content is not captured")
        with self._lock:
            if self.expired:
                self.delete()
                raise RuntimeError("diagnostic content capture expired")
            if self._files >= MAX_CAPTURE_FILES or self._bytes + len(encoded) > MAX_CAPTURE_BYTES:
                raise RuntimeError("diagnostic content capture limit reached")
            sequence = self._files + 1
            suffix = "bin" if kind == "raw-audio" else "utf8"
            name = f"{sequence:03d}-{kind}.{suffix}"
            self._write_file(name, encoded)
            self._files = sequence
            self._bytes += len(encoded)
            return self.path / name

    def _expire_now(self) -> None:
        try:
            self.delete()
        except RuntimeError:
            pass

    def delete(self) -> bool:
        with self._lock:
            timer = self._expiry_timer
            self._expiry_timer = None
            if timer is not None:
                timer.cancel()
            if not self.path.exists():
                return False
            try:
                return self.delete_path(self.path)
            except ValueError as error:
                raise RuntimeError("diagnostic capture deletion guard failed") from error

    @staticmethod
    def delete_path(path: Path) -> bool:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise ValueError("path is not an owned outside-Git diagnostic capture")
        resolved = expanded.resolve()
        if (
            _inside_git_worktree(resolved)
            or not resolved.name.startswith("capture-")
            or not resolved.is_dir()
        ):
            raise ValueError("path is not an owned outside-Git diagnostic capture")
        try:
            document = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("diagnostic capture manifest is unavailable") from error
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != "voice-agent.diagnostic-content-capture.v1"
            or document.get("explicit_opt_in") is not True
            or resolved.name != f"capture-{document.get('session_id')}"
        ):
            raise ValueError("diagnostic capture manifest does not own this path")
        shutil.rmtree(resolved)
        return True

    @staticmethod
    def purge_expired(
        root: Path, *, now: Callable[[], float] = time.time
    ) -> int:
        resolved = root.expanduser().resolve()
        if _inside_git_worktree(resolved) or not resolved.exists():
            return 0
        removed = 0
        current_time = float(now())
        for directory in resolved.glob("capture-*"):
            if not directory.is_dir() or directory.is_symlink():
                continue
            manifest = directory / "manifest.json"
            try:
                document = json.loads(manifest.read_text(encoding="utf-8"))
                expires = float(document["expires_unix_seconds"])
                explicitly_enabled = document["explicit_opt_in"] is True
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if explicitly_enabled and expires <= current_time:
                try:
                    DiagnosticContentCapture.delete_path(directory)
                except (OSError, ValueError):
                    continue
                removed += 1
        return removed

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
        self._bytes = self.path.stat().st_size if self.path.exists() else 0
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def child(self, *, turn_id: str, stream_epoch: int) -> PrivacySafeTrace:
        child = object.__new__(PrivacySafeTrace)
        child.path = self.path
        child.identity = TraceIdentity(self.identity.session_id, turn_id, stream_epoch)
        child._started = self._started
        child._records = self._records
        child._bytes = self._bytes
        child._lock = self._lock
        return child

    def emit(
        self,
        stage: str,
        event: str,
        fields: Mapping[str, object] | None = None,
        *,
        turn_id: str | None = None,
        stream_epoch: int | None = None,
    ) -> None:
        values = dict(fields or {})
        if not _safe_key(stage) or not _safe_key(event):
            raise ValueError("diagnostic stage/event is forbidden")
        normalized: dict[str, object] = {}
        for key, value in values.items():
            if not isinstance(key, str) or len(key) > 64 or not _safe_key(key):
                raise ValueError("diagnostic key is forbidden")
            normalized[key] = _safe_value(value)
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
            if self._records >= MAX_TRACE_RECORDS or self._bytes + len(encoded) > MAX_TRACE_BYTES:
                return
            with self.path.open("ab") as destination:
                destination.write(encoded)
            self._records += 1
            self._bytes += len(encoded)

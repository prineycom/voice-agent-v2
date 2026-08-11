"""Cache-boundary and privacy guards for Slice 2 benchmark operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

AUTHORIZED_CACHE_ROOT = Path("/home/priney/.cache/voice-agent-v2/slice-2")

# Public fixture files may contain authored prompts and references. Committed result files may not.
FORBIDDEN_RESULT_KEYS = {
    "answer",
    "asset_url",
    "audio",
    "audio_path",
    "content",
    "credential",
    "environment",
    "hostname",
    "prompt",
    "response",
    "secret",
    "token",
    "transcript",
    "username",
    "uuid",
}


def cache_path(path: Path) -> Path:
    """Resolve a path and reject writes outside the task-specific authorized cache."""
    resolved = path.expanduser().resolve()
    root = AUTHORIZED_CACHE_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path is outside authorized Slice 2 cache: {resolved}")
    return resolved


def assert_privacy_safe_result(value: Any, path: str = "$") -> None:
    """Reject content-bearing or identifying fields in machine-readable result evidence."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            terms = set(normalized.split("_"))
            forbidden = FORBIDDEN_RESULT_KEYS.intersection(terms)
            if forbidden:
                raise ValueError(f"{path}.{key}: forbidden result key term {sorted(forbidden)}")
            assert_privacy_safe_result(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_privacy_safe_result(child, f"{path}[{index}]")

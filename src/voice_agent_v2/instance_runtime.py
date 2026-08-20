"""Explicit mutable-state and listener contract for a selected stand instance.

Legacy commands keep their historical defaults when no instance is selected.  Once
``VOICE_AGENT_INSTANCE_ROOT`` is present every mutable path and listener port is
required explicitly; falling back to the legacy user-wide locations is forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


INSTANCE_ROOT_NAME = "VOICE_AGENT_INSTANCE_ROOT"
SHARED_CACHE_ROOT_NAME = "VOICE_AGENT_SHARED_CACHE_ROOT"
PORT_NAMES = (
    "VOICE_AGENT_LLM_PORT",
    "VOICE_AGENT_LIVEKIT_PORT",
    "VOICE_AGENT_RTC_UDP_PORT",
    "VOICE_AGENT_GATEWAY_PORT",
)


class InstanceRuntimeError(ValueError):
    """The selected instance contract is absent, ambiguous, or unsafe."""


def _values(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def selected_instance_root(environment: Mapping[str, str] | None = None) -> Path | None:
    values = _values(environment)
    configured = values.get(INSTANCE_ROOT_NAME)
    if configured is None:
        return None
    if not configured or configured != configured.strip():
        raise InstanceRuntimeError("selected instance root is invalid")
    root = Path(configured)
    if not root.is_absolute() or root == Path(root.anchor):
        raise InstanceRuntimeError("selected instance root must be an absolute non-root path")
    return root


def mutable_path(
    relative: str,
    *,
    legacy: Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    root = selected_instance_root(environment)
    if root is None:
        return legacy
    candidate = root / relative
    if candidate == root or not candidate.is_relative_to(root):
        raise InstanceRuntimeError("selected instance mutable path is invalid")
    return candidate


def shared_cache_root(environment: Mapping[str, str] | None = None) -> Path:
    values = _values(environment)
    root = selected_instance_root(values)
    configured = values.get(SHARED_CACHE_ROOT_NAME)
    if root is not None and configured is None:
        raise InstanceRuntimeError("selected instance requires the explicit shared immutable cache root")
    if configured is None:
        return Path(values.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-agent-v2"
    cache = Path(configured)
    if not cache.is_absolute() or cache == root:
        raise InstanceRuntimeError("shared immutable cache root is invalid")
    return cache


def listener_port(
    name: str,
    default: int,
    environment: Mapping[str, str] | None = None,
) -> int:
    if name not in PORT_NAMES:
        raise InstanceRuntimeError("unknown instance listener port")
    values = _values(environment)
    selected = selected_instance_root(values) is not None
    configured = values.get(name)
    if configured is None:
        if selected:
            raise InstanceRuntimeError(f"selected instance requires explicit {name}")
        return default
    if not configured.isdecimal() or not 1 <= int(configured) <= 65535:
        raise InstanceRuntimeError(f"invalid explicit {name}")
    return int(configured)


@dataclass(frozen=True, slots=True)
class InstancePaths:
    root: Path
    config: Path
    data: Path
    cache: Path
    runtime: Path
    workspace: Path
    credentials: Path
    agent_environment: Path

    @classmethod
    def selected(cls, environment: Mapping[str, str] | None = None) -> "InstancePaths":
        root = selected_instance_root(environment)
        if root is None:
            raise InstanceRuntimeError("no stand instance is selected")
        return cls(
            root=root,
            config=root / "config",
            data=root / "data",
            cache=root / "cache",
            runtime=root / "runtime",
            workspace=root / "workspace",
            credentials=root / "credentials",
            agent_environment=root / "agent-environment",
        )

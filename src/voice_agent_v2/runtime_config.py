"""Closed launcher-generated production runtime path authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_SCHEMA = "voice-agent.runtime-config.v1"
_REQUIRED_MODELS = frozenset({"stt", "llm", "tts", "vad"})


def _clean_absolute(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise RuntimeError(f"runtime config {name} is not an absolute path")
    path = Path(value)
    if str(path) != value:
        raise RuntimeError(f"runtime config {name} is not canonical")
    return path


def load_production_runtime_config() -> dict[str, Any] | None:
    """Load the sole production path descriptor; development has no descriptor."""
    filename = os.environ.get("VOICE_AGENT_RUNTIME_CONFIG")
    release = os.environ.get("VOICE_AGENT_RELEASE_ROOT")
    if filename is None:
        if release is not None:
            raise RuntimeError("launcher runtime config is required for a production release")
        return None
    config_path = _clean_absolute(filename, "file")
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("launcher runtime config is unavailable") from error
    expected = {"schema", "application_protocol", "release_root", "executables", "models", "paths"}
    if not isinstance(document, dict) or set(document) != expected or document.get("schema") != _SCHEMA or document.get("application_protocol") != 1:
        raise RuntimeError("launcher runtime config has an unsupported contract")
    executables = document.get("executables")
    paths = document.get("paths")
    models = document.get("models")
    if not isinstance(executables, dict) or set(executables) != {"python", "livekit", "llama"}:
        raise RuntimeError("launcher runtime executable closure is invalid")
    if not isinstance(paths, dict) or set(paths) != {"agent_data", "logs", "state", "temp", "web"}:
        raise RuntimeError("launcher mutable path closure is invalid")
    if not isinstance(models, dict) or set(models) != _REQUIRED_MODELS:
        raise RuntimeError("launcher model view closure is invalid")
    release_root = _clean_absolute(document["release_root"], "release_root")
    document["release_root"] = release_root
    for name, value in tuple(executables.items()):
        candidate = _clean_absolute(value, f"executables.{name}")
        if not candidate.is_relative_to(release_root):
            raise RuntimeError("runtime executable escaped the immutable release")
        executables[name] = candidate
    for name, value in tuple(paths.items()):
        if name == "agent_data" and value is None:
            continue
        paths[name] = _clean_absolute(value, f"paths.{name}")
    for name, value in tuple(models.items()):
        models[name] = _clean_absolute(value, f"models.{name}")
    return document

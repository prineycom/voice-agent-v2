"""Explicit local STT backend selection; Whisper remains the default rollback."""

from __future__ import annotations

from .local_stt import WhisperSTT
from .parakeet_stt import ParakeetSTT

STT_BACKENDS = frozenset({"whisper", "parakeet"})


def create_stt_backend(name: str):
    if name == "whisper":
        return WhisperSTT()
    if name == "parakeet":
        return ParakeetSTT()
    raise ValueError("unsupported STT backend")

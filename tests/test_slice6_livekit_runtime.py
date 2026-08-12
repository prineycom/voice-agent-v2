from __future__ import annotations

from tests.test_checkpoint_ab import (
    CheckpointBLiveKitTests,
    CheckpointBWarmupTests,
)


class PersistentLiveKitTrackTests(CheckpointBLiveKitTests):
    """Checkpoint B persistent publication behavior."""


class ResidentQwenWarmupTests(CheckpointBWarmupTests):
    """Checkpoint B discard-only resident Qwen warm-up behavior."""

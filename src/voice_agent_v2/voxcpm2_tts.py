"""Optional accelerated VoxCPM2 backend behind the unchanged local TTS v1 contract."""

from __future__ import annotations

import os
from pathlib import Path

from .local_tts import Qwen3TTS

EXPERIMENT_CACHE = Path(
    "/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "config" / "voxcpm2-fast-tts-v1.json"
RUNNER = PROJECT_ROOT / "scripts" / "voxcpm2_fast_runner.py"
MODEL_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"
RUNTIME_REVISION = "0cc522ca22971213f5fda5d4b2c4457f294aab85"


class VoxCPM2FastTTS(Qwen3TTS):
    """Resident Nano-vLLM VoxCPM2 process using one pinned public clone reference."""

    identity = f"openbmb/VoxCPM2@{MODEL_REVISION}/nano-vllm-voxcpm@{RUNTIME_REVISION}"
    cache = EXPERIMENT_CACHE
    venv = EXPERIMENT_CACHE / "runtime"
    runner = RUNNER
    logs = EXPERIMENT_CACHE / "logs"
    log_prefix = "voxcpm2-fast-tts"
    voice = "ruls-public-reader-4471"
    language = "Russian"

    def _environment(self) -> dict[str, str]:
        environment = super()._environment()
        environment.update(
            {
                "VOICE_AGENT_VOXCPM2_MANIFEST": str(MANIFEST),
                "NANOVLLM_SERVERPOOL_NUM_KVCACHE_BLOCKS": "16",
                "TOKENIZERS_PARALLELISM": "false",
                "PYTHONHASHSEED": "0",
                "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
                "VOICE_AGENT_GPU_BAKEOFF_LOCK_FD": os.environ.get(
                    "VOICE_AGENT_GPU_BAKEOFF_LOCK_FD", ""
                ),
            }
        )
        return environment

    def _pass_fds(self) -> tuple[int, ...]:
        value = os.environ.get("VOICE_AGENT_GPU_BAKEOFF_LOCK_FD", "")
        if not value.isdigit():
            return ()
        descriptor = int(value)
        try:
            os.fstat(descriptor)
        except OSError:
            return ()
        return (descriptor,)

    def _validate_ready(self, metadata: dict) -> None:
        expected = {
            "runtime": "nano-vllm-voxcpm",
            "runtime_version": "2.0.4",
            "runtime_revision": RUNTIME_REVISION,
            "model": "openbmb/VoxCPM2",
            "model_revision": MODEL_REVISION,
            "sample_rate_hz": 48_000,
            "output_sample_rate_hz": 16_000,
            "voice_mode": "ultimate-cloning-public-reference",
            "reference_id": self.voice,
            "local_only": True,
            "reserve_mib": 1_536,
        }
        if any(metadata.get(name) != value for name, value in expected.items()):
            raise ValueError("accelerated VoxCPM2 runner readiness identity mismatch")

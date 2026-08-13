from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest

from voice_agent_v2.contracts import StageFailure
from voice_agent_v2.local_tts import Qwen3TTS, create_local_tts
from voice_agent_v2.slice6_config import Slice6ConfigurationError, Slice6Settings
from voice_agent_v2.tracer import CancellationToken
from voice_agent_v2.voxcpm2_tts import VoxCPM2FastTTS

ROOT = Path(__file__).resolve().parents[1]


def settings_environment() -> dict[str, str]:
    return {
        "LIVEKIT_API_KEY": "candidate-key",
        "LIVEKIT_API_SECRET": "candidate-secret-value",
        "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
        "LIVEKIT_PUBLIC_URL": "wss://host.example.ts.net:7443",
        "SLICE6_APP_PUBLIC_URL": "https://host.example.ts.net:8443",
    }


class FakeStreamingProcess:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.interrupts = 0
        self.process = types.SimpleNamespace(pid=4202, poll=lambda: None)

    def interrupt_request(self) -> None:
        self.interrupts += 1

    def stream(self, request: dict, _timeout: float):
        total = 0
        for sequence, chunk in enumerate(self.chunks, 1):
            total += len(chunk)
            yield {
                "event": "chunk", "request_id": request["request_id"],
                "sequence": sequence, "pcm_base64": base64.b64encode(chunk).decode(),
                "bytes": len(chunk),
            }
        yield {
            "event": "final", "request_id": request["request_id"],
            "audio_bytes": total, "chunk_count": len(self.chunks),
            "sample_rate_hz": 16_000, "channels": 1, "encoding": "pcm_s16le",
        }


class VoxCPM2BackendSelectionTests(unittest.TestCase):
    def test_qwen_remains_default_and_candidate_requires_explicit_selection(self) -> None:
        default = Slice6Settings.from_environment(settings_environment(), project_root=ROOT)
        self.assertEqual(default.tts_backend, "qwen")
        self.assertIsInstance(create_local_tts(default.tts_backend), Qwen3TTS)

        candidate_env = settings_environment()
        candidate_env["VOICE_AGENT_TTS_BACKEND"] = "voxcpm2-fast"
        candidate = Slice6Settings.from_environment(candidate_env, project_root=ROOT)
        self.assertEqual(candidate.tts_backend, "voxcpm2-fast")
        self.assertIsInstance(create_local_tts(candidate.tts_backend), VoxCPM2FastTTS)

    def test_unknown_backend_fails_instead_of_falling_back(self) -> None:
        environment = settings_environment()
        environment["VOICE_AGENT_TTS_BACKEND"] = "automatic"
        with self.assertRaises(Slice6ConfigurationError):
            Slice6Settings.from_environment(environment, project_root=ROOT)
        with self.assertRaises(ValueError):
            create_local_tts("automatic")

    def test_manifest_is_bounded_local_and_uses_public_ultimate_clone(self) -> None:
        manifest = json.loads(
            (ROOT / "config" / "voxcpm2-fast-tts-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "experimental-no-default")
        self.assertEqual(manifest["reference"]["mode"], "ultimate-cloning")
        self.assertEqual(manifest["reference"]["row_index"], 999)
        self.assertIn("public", manifest["reference"]["license"].lower())
        self.assertEqual(manifest["inference"]["max_num_seqs"], 1)
        self.assertLessEqual(manifest["inference"]["max_generate_length"], 1024)
        self.assertGreaterEqual(manifest["resource_gate"]["vram_reserve_mib"], 1536)
        self.assertEqual(
            Path(manifest["cache_root"]),
            Path("/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts"),
        )


class VoxCPM2AdapterContractTests(unittest.TestCase):
    def ready_adapter(self, chunks: tuple[bytes, ...]) -> tuple[VoxCPM2FastTTS, FakeStreamingProcess]:
        adapter = VoxCPM2FastTTS()
        process = FakeStreamingProcess(chunks)
        adapter._process = process
        adapter.ready_metadata = {"event": "ready"}
        return adapter, process

    def test_stream_preserves_tts_v1_format_correlation_bounds_and_privacy(self) -> None:
        adapter, _process = self.ready_adapter((b"\0\0" * 320, b"\1\0" * 320))
        chunks = tuple(adapter.stream_synthesize(
            session_id="session-test", turn_id="turn-test", text="Публичная проверка.",
            audio_format=adapter.output_format,
        ))
        self.assertEqual(tuple(map(len, chunks)), (640, 640))
        self.assertEqual(adapter.process_id, 4202)
        observation = adapter.observations[-1]
        self.assertEqual(observation["identity"], adapter.identity)
        self.assertEqual(observation["speaker"], "ruls-public-reader-4471")
        self.assertFalse(observation["retained_by_adapter"])
        self.assertNotIn("text", observation)

    def test_cancellation_drops_late_pcm_and_keeps_resident_process(self) -> None:
        adapter, process = self.ready_adapter((b"\0\0" * 320, b"\2\0" * 320))
        token = CancellationToken()
        stream = adapter.stream_synthesize(
            session_id="session-test", turn_id="turn-test", text="Публичная проверка.",
            audio_format=adapter.output_format, cancellation=token,
        )
        self.assertEqual(len(next(stream)), 640)
        token.cancel()
        with self.assertRaises(StageFailure) as raised:
            next(stream)
        self.assertEqual(raised.exception.code, "selected_tts_cancelled")
        self.assertEqual(process.interrupts, 1)
        self.assertIs(adapter._process, process)
        self.assertEqual(adapter.process_id, 4202)

    def test_runner_environment_is_offline_and_task_scoped(self) -> None:
        environment = VoxCPM2FastTTS()._environment()
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(environment["NANOVLLM_SERVERPOOL_NUM_KVCACHE_BLOCKS"], "16")
        self.assertNotIn("LITELLM_BASE_URL", environment)
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertTrue(environment["VOICE_AGENT_VOXCPM2_MANIFEST"].endswith(
            "config/voxcpm2-fast-tts-v1.json"
        ))

    def test_real_runner_refuses_to_touch_gpu_without_bakeoff_lock(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "voxcpm2_fast_runner.py")],
            input="", text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("requires the GPU bakeoff lock", result.stderr)


if __name__ == "__main__":
    unittest.main()

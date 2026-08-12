from __future__ import annotations

import math
from pathlib import Path
import struct
import unittest
import wave

from voice_agent_v2.local_vad import SileroOnnxModel, SileroSpeechEndpoint


class SequenceModel:
    def __init__(self, values):
        self.values = iter(values)
        self.reset_count = 0

    def infer(self, _pcm: bytes) -> float:
        return next(self.values)

    def reset(self) -> None:
        self.reset_count += 1


def frame(amplitude: int = 0) -> bytes:
    return struct.pack("<h", amplitude) * 320


class SileroSpeechEndpointTests(unittest.TestCase):
    def decisions(self, values, frames):
        telemetry = []
        endpoint = SileroSpeechEndpoint(SequenceModel(values), telemetry=telemetry.append)
        signals = []
        for item in frames:
            signals.extend(endpoint.feed(item))
        return signals, telemetry

    def test_silence_stationary_background_and_agc_spikes_do_not_admit_turn(self) -> None:
        values = [0.01] * 20 + [0.10, 0.76, 0.08] * 8 + [0.04] * 20
        signals, telemetry = self.decisions(values, [frame(900)] * 102)
        self.assertEqual(signals, [])
        self.assertIn("candidate_rejected", {row["decision"] for row in telemetry})
        self.assertNotIn("speech_started", {row["decision"] for row in telemetry})
        self.assertNotIn("payload", str(telemetry).lower())

    def test_confirmed_speech_starts_and_ends_with_bounded_pcm(self) -> None:
        values = [0.05] * 4 + [0.91] * 12 + [0.10] * 24
        signals, _telemetry = self.decisions(values, [frame(1200)] * 64)
        self.assertEqual([signal.kind for signal in signals], ["speech_started", "utterance"])
        utterance = signals[-1].payload
        self.assertIsNotNone(utterance)
        self.assertGreater(len(utterance or b""), 0)

    def test_rejected_noise_candidate_never_emits_public_start(self) -> None:
        values = [0.75, 0.74, 0.05] * 20
        signals, telemetry = self.decisions(values, [frame(1600)] * 96)
        self.assertEqual(signals, [])
        self.assertGreater(sum(row["decision"] == "candidate_rejected" for row in telemetry), 0)

    def test_cache_local_real_russian_fixture_is_detected_when_available(self) -> None:
        fixture = Path(
            "/home/priney/.cache/voice-agent-v2/slice-2/corpora/"
            "stt-ruls-v1/ruls-test-0000.wav"
        )
        model = Path.home() / ".cache/voice-agent-v2/slice-6/models/silero-vad-v6.onnx"
        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            self.skipTest("installed Slice 6 ONNX runtime is absent")
        if not fixture.is_file() or not model.is_file():
            self.skipTest("authorized public Russian fixture or pinned VAD cache is absent")
        endpoint = SileroSpeechEndpoint(SileroOnnxModel(model))
        signals = []
        with wave.open(str(fixture), "rb") as source:
            self.assertEqual(source.getparams()[:3], (1, 2, 16_000))
            while chunk := source.readframes(320):
                if len(chunk) < 640:
                    chunk += b"\0" * (640 - len(chunk))
                signals.extend(endpoint.feed(chunk))
        signals.extend(endpoint.flush())
        self.assertIn("speech_started", [signal.kind for signal in signals])
        self.assertIn("utterance", [signal.kind for signal in signals])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from voice_agent_v2.local_vad import (
    SILERO_MODEL_SIZE,
    SileroOnnxModel,
    SileroSpeechEndpoint,
)


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

    def test_confirmed_speech_keeps_decision_silence_out_of_stt_input(self) -> None:
        values = [0.05] * 4 + [0.91] * 12 + [0.10] * 24
        signals, telemetry = self.decisions(values, [frame(1200)] * 64)
        self.assertEqual([signal.kind for signal in signals], ["speech_started", "utterance"])
        utterance = signals[-1].payload or b""
        # Endpointing still waits for 640 ms, but Whisper receives only the
        # explicit 160 ms final-phoneme tail rather than the whole decision tail.
        self.assertEqual(len(utterance), (4 * 32 + 12 * 32 + 160) * 32)
        submitted = next(row for row in telemetry if row["decision"] == "utterance")
        self.assertEqual(submitted["silence_duration_ms"], 640)
        self.assertEqual(submitted["submitted_post_speech_ms"], 160)

    def test_pre_onset_custody_freezes_before_speech_confirmation(self) -> None:
        endpoint = SileroSpeechEndpoint(SequenceModel([0.05] * 9 + [0.9] * 20))
        signals = []
        pcm = b"".join(struct.pack("<h", sample) for sample in range(9_000))
        for offset in range(0, len(pcm), 640):
            item = pcm[offset:offset + 640]
            if len(item) == 640:
                signals.extend(endpoint.feed(item))
            if any(signal.kind == "speech_started" for signal in signals):
                break
        signals.extend(endpoint.flush())

        payload = signals[-1].payload or b""
        self.assertEqual(struct.unpack_from("<h", payload)[0], 512)
        self.assertEqual(
            struct.unpack_from("<h", payload, endpoint.pre_roll_bytes)[0],
            4_608,
        )
        self.assertEqual(endpoint.pre_roll_bytes, 256 * 32)

    def test_maximum_utterance_payload_never_exceeds_configured_bound(self) -> None:
        model = SequenceModel([0.9] * 30 + [0.05] * 30)
        endpoint = SileroSpeechEndpoint(model, max_utterance_ms=400)
        signals = []
        for item in [frame(1000)] * 40:
            signals.extend(endpoint.feed(item))
            if any(signal.kind == "utterance" for signal in signals):
                break

        payload = signals[-1].payload or b""
        self.assertEqual([signal.kind for signal in signals], ["speech_started", "utterance"])
        self.assertLessEqual(len(payload), 400 * 32)
        self.assertGreaterEqual(model.reset_count, 1)

    def test_same_size_substituted_model_is_rejected_before_runtime_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "silero.onnx"
            model.write_bytes(b"x" * SILERO_MODEL_SIZE)
            with self.assertRaisesRegex(RuntimeError, "model identity"):
                SileroOnnxModel(model)

    def test_rejected_noise_candidate_never_emits_public_start(self) -> None:
        values = [0.75, 0.74, 0.05] * 20
        signals, telemetry = self.decisions(values, [frame(1600)] * 96)
        self.assertEqual(signals, [])
        self.assertGreater(sum(row["decision"] == "candidate_rejected" for row in telemetry), 0)

    def test_candidate_shorter_than_minimum_speech_never_becomes_public(self) -> None:
        values = [0.85] * 3 + [0.05] * 8
        frames = [frame(1200)] * math.ceil(len(values) * 512 / 320)
        signals, telemetry = self.decisions(values, frames)
        self.assertEqual(signals, [])
        self.assertNotIn("speech_started", {row["decision"] for row in telemetry})

    def test_long_idle_does_not_consume_the_next_utterance_budget(self) -> None:
        values = [0.02] * 470 + [0.9] * 10 + [0.05] * 22
        frames = [frame(1200)] * math.ceil(len(values) * 512 / 320)
        signals, _telemetry = self.decisions(values, frames)
        self.assertEqual([signal.kind for signal in signals], ["speech_started", "utterance"])

    @unittest.skipUnless(
        os.environ.get("VOICE_AGENT_VERIFY_SLICE6_RUNTIME") == "1",
        "requires explicit Slice 6 runtime verification",
    )
    def test_cache_local_real_russian_fixture_is_detected(self) -> None:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        fixture = Path(os.environ.get(
            "VOICE_AGENT_VAD_SPEECH_FIXTURE",
            str(cache / "voice-agent-v2/slice-6/test-data/real-russian-speech.wav"),
        ))
        model = cache / "voice-agent-v2/slice-6/models/silero-vad-v6.onnx"
        self.assertTrue(fixture.is_file(), "setup-slice6 must acquire the pinned public speech fixture")
        self.assertTrue(model.is_file(), "setup-slice6 must acquire the pinned Silero model")
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

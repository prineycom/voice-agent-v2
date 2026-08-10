"""Integer-only PCM generation for cross-run deterministic fixtures."""

from __future__ import annotations

import struct

from .contracts import AudioFormat

DEFAULT_AUDIO_FORMAT = AudioFormat()
INPUT_FREQUENCY_HZ = 250
OUTPUT_FREQUENCY_HZ = 500
DURATION_SAMPLES = 3_200


def generate_square_tone_pcm(
    *, frequency_hz: int, amplitude: int, sample_count: int = DURATION_SAMPLES
) -> bytes:
    """Generate signed 16-bit little-endian mono square-wave PCM without libm."""
    if frequency_hz <= 0 or DEFAULT_AUDIO_FORMAT.sample_rate_hz % frequency_hz:
        raise ValueError("frequency must divide the sample rate exactly")
    if not 0 < amplitude <= 32_767:
        raise ValueError("amplitude must fit positive signed 16-bit PCM")
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")

    period = DEFAULT_AUDIO_FORMAT.sample_rate_hz // frequency_hz
    half_period = period // 2
    samples = (
        amplitude if sample_index % period < half_period else -amplitude
        for sample_index in range(sample_count)
    )
    return b"".join(struct.pack("<h", sample) for sample in samples)


def generated_input_pcm() -> bytes:
    return generate_square_tone_pcm(frequency_hz=INPUT_FREQUENCY_HZ, amplitude=4_000)


def generated_output_pcm() -> bytes:
    return generate_square_tone_pcm(frequency_hz=OUTPUT_FREQUENCY_HZ, amplitude=10_000)

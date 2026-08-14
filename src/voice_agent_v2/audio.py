"""Integer-only PCM generation for cross-run deterministic fixtures."""

from __future__ import annotations

import struct

from .contracts import AudioFormat

# The input and output domains are deliberately separate.  DEFAULT_AUDIO_FORMAT is
# retained only for the immutable Slice 1–5 fixtures; active realtime code must use
# INPUT_AUDIO_FORMAT or TTS_OUTPUT_AUDIO_FORMAT explicitly.
INPUT_AUDIO_FORMAT = AudioFormat(
    encoding="pcm_s16le", sample_rate_hz=16_000, channels=1, sample_width_bytes=2
)
TTS_V1_AUDIO_FORMAT = INPUT_AUDIO_FORMAT
TTS_OUTPUT_AUDIO_FORMAT = AudioFormat(
    encoding="pcm_s16le", sample_rate_hz=48_000, channels=1, sample_width_bytes=2
)
DEFAULT_AUDIO_FORMAT = INPUT_AUDIO_FORMAT
INPUT_FREQUENCY_HZ = 250
OUTPUT_FREQUENCY_HZ = 500
DURATION_SAMPLES = 3_200
INPUT_MEDIA_MAX_SECONDS = 30
INPUT_MEDIA_MAX_SAMPLES = INPUT_AUDIO_FORMAT.sample_rate_hz * INPUT_MEDIA_MAX_SECONDS
INPUT_MEDIA_MAX_BYTES = INPUT_MEDIA_MAX_SAMPLES * INPUT_AUDIO_FORMAT.sample_width_bytes
OUTPUT_MEDIA_MAX_SECONDS = 180
OUTPUT_MEDIA_MAX_SAMPLES = TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz * OUTPUT_MEDIA_MAX_SECONDS
OUTPUT_MEDIA_MAX_BYTES = OUTPUT_MEDIA_MAX_SAMPLES * TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes
TTS_V1_OUTPUT_MEDIA_MAX_SAMPLES = TTS_V1_AUDIO_FORMAT.sample_rate_hz * OUTPUT_MEDIA_MAX_SECONDS
TTS_V1_OUTPUT_MEDIA_MAX_BYTES = (
    TTS_V1_OUTPUT_MEDIA_MAX_SAMPLES * TTS_V1_AUDIO_FORMAT.sample_width_bytes
)
TTS_SEGMENT_MAX_SECONDS = 15
TTS_SEGMENT_MAX_SAMPLES = TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz * TTS_SEGMENT_MAX_SECONDS
TTS_SEGMENT_MAX_BYTES = TTS_SEGMENT_MAX_SAMPLES * TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes
OUTPUT_FRAME_MS = 20
OUTPUT_FRAME_SAMPLES = TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz * OUTPUT_FRAME_MS // 1_000
OUTPUT_FRAME_BYTES = OUTPUT_FRAME_SAMPLES * TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes
OUTPUT_DELIVERY_BLOCK_MS = 60
OUTPUT_DELIVERY_BLOCK_SAMPLES = (
    TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz * OUTPUT_DELIVERY_BLOCK_MS // 1_000
)
OUTPUT_DELIVERY_BLOCK_BYTES = (
    OUTPUT_DELIVERY_BLOCK_SAMPLES * TTS_OUTPUT_AUDIO_FORMAT.sample_width_bytes
)


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

#!/usr/bin/env python3
"""One host-local real voice turn for final microphone/listening acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.cloud_llm import LiteLLMProvider
from voice_agent_v2.local_stt import CACHE, WhisperSTT
from voice_agent_v2.local_tts import OUTPUT_FORMAT, Qwen3TTS
from voice_agent_v2.real_turn import RealTurnController

CAPTURE_RATE_HZ = 16_000
CAPTURE_CHANNELS = 1
CAPTURE_SAMPLE_WIDTH_BYTES = 2
CAPTURE_TIMEOUT_MARGIN_SECONDS = 5.0


class MicrophoneCaptureFailure(RuntimeError):
    """Content-free bounded failure before a voice turn is admitted."""

    def __init__(self, code: str, *, input_retained: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.input_retained = input_retained


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one real Whisper → LiteLLM → Qwen3 voice turn")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--microphone", action="store_true", help="capture the default PipeWire microphone")
    source.add_argument("--input-wav", type=Path, help="use a 16 kHz mono PCM WAV")
    parser.add_argument("--duration", type=float, default=8.0, help="microphone capture seconds (default 8)")
    parser.add_argument("--play", action="store_true", help="play Qwen3 PCM through the default PipeWire sink")
    return parser.parse_args()


def wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise SystemExit("input WAV must be pcm_s16le/16000Hz/mono")
        return source.readframes(source.getnframes())


def _cleanup_temporary_capture(temporary: Path) -> None:
    try:
        temporary.unlink(missing_ok=True)
        return
    except OSError as error:
        cleanup_error = error
    try:
        with temporary.open("r+b") as captured:
            captured.truncate(0)
    except OSError:
        pass
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        try:
            input_retained = temporary.stat().st_size > 0
        except OSError:
            input_retained = True
    else:
        input_retained = False
    raise MicrophoneCaptureFailure(
        "capture_cleanup_failed", input_retained=input_retained,
    ) from cleanup_error


def microphone_pcm(duration: float) -> bytes:
    if not 1.0 <= duration <= 30.0:
        raise MicrophoneCaptureFailure("duration_out_of_bounds")
    sample_count = int(duration * CAPTURE_RATE_HZ)
    expected_bytes = sample_count * CAPTURE_CHANNELS * CAPTURE_SAMPLE_WIDTH_BYTES
    temporary = CACHE / "runtime" / "turn-temp" / f"microphone-{time.monotonic_ns()}.pcm"
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MicrophoneCaptureFailure("capture_setup_failed") from error
    command = [
        "pw-record", "--rate", str(CAPTURE_RATE_HZ), "--channels", str(CAPTURE_CHANNELS),
        "--format", "s16", "--raw", "--sample-count", str(sample_count), str(temporary),
    ]
    print(f"Speak now ({duration:.1f} seconds)...", flush=True)
    try:
        try:
            completed = subprocess.run(
                command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=duration + CAPTURE_TIMEOUT_MARGIN_SECONDS,
            )
        except FileNotFoundError as error:
            raise MicrophoneCaptureFailure("recorder_unavailable") from error
        except subprocess.TimeoutExpired as error:
            raise MicrophoneCaptureFailure("recorder_timeout") from error
        except OSError as error:
            raise MicrophoneCaptureFailure("recorder_start_failed") from error
        # PipeWire 1.6.8 pw-record (pw-cat) returns 1 after a normal sample-count stop;
        # accept that status only when the bounded output is present at the exact expected size.
        if completed.returncode not in {0, 1}:
            raise MicrophoneCaptureFailure("recorder_nonzero")
        if not temporary.is_file():
            code = "recorder_nonzero" if completed.returncode else "capture_output_missing"
            raise MicrophoneCaptureFailure(code)
        try:
            size = temporary.stat().st_size
        except OSError as error:
            raise MicrophoneCaptureFailure("capture_output_unreadable") from error
        if size != expected_bytes:
            code = "recorder_nonzero" if completed.returncode else "capture_output_size_mismatch"
            raise MicrophoneCaptureFailure(code)
        try:
            pcm = temporary.read_bytes()
        except OSError as error:
            raise MicrophoneCaptureFailure("capture_output_unreadable") from error
        if len(pcm) != expected_bytes:
            raise MicrophoneCaptureFailure("capture_output_size_mismatch")
        return pcm
    finally:
        _cleanup_temporary_capture(temporary)


def _capture_failure(error: MicrophoneCaptureFailure) -> int:
    print("Terminal: turn.failed", file=sys.stderr)
    print(f"Failure: microphone_capture/{error.code}", file=sys.stderr)
    microphone_retained = str(error.input_retained).lower()
    print(
        f"Retention: microphone={microphone_retained}, "
        "synthesized_audio=false, conversation_content=false",
        file=sys.stderr,
    )
    evidence = {
        "schema_version": "voice-agent.slice5-human-command-run.v1",
        "terminal": "turn.failed", "stage": "microphone_capture", "error_class": error.code,
        "input_retained": error.input_retained,
        "output_retained": False, "conversation_content_retained": False,
    }
    try:
        output = CACHE / "evidence" / f"slice5-human-run-{time.monotonic_ns()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    except OSError:
        print("Evidence: content_free_evidence_write_failed", file=sys.stderr)
    return 2


def main() -> int:
    args = arguments()
    try:
        pcm = microphone_pcm(args.duration) if args.microphone else wav_pcm(args.input_wav.resolve())
    except MicrophoneCaptureFailure as error:
        return _capture_failure(error)
    stt = WhisperSTT()
    llm = LiteLLMProvider()
    tts = Qwen3TTS()
    session_id = f"session-local-{time.monotonic_ns()}"
    turn_id = f"turn-local-{time.monotonic_ns()}"
    try:
        llm.readiness()
        stt.start()
        tts.start()
        result = RealTurnController(stt, llm, tts).run_turn(
            session_id=session_id, turn_id=turn_id, input_pcm=pcm,
        )
    finally:
        stt.close()
        tts.close()
    transcript = next((event["payload"]["transcript"] for event in result.events if event["type"] == "stt.final"), None)
    response = next((event["payload"]["response"] for event in result.events if event["type"] == "llm.final"), None)
    print(f"Transcript: {transcript or '<none>'}")
    print(f"Response: {response or '<none>'}")
    print(f"Terminal: {result.terminal_event['type']}")
    print(f"Output: pcm_s16le/{OUTPUT_FORMAT.sample_rate_hz}Hz/mono, {len(result.output_pcm)} bytes")
    if args.play and result.output_pcm:
        subprocess.run([
            "pw-play", "--rate", str(OUTPUT_FORMAT.sample_rate_hz), "--channels", "1",
            "--format", "s16", "--raw", "-",
        ], input=result.output_pcm, check=True)
    evidence = {
        "schema_version": "voice-agent.slice5-human-command-run.v1",
        "terminal": result.terminal_event["type"],
        "stt_identity": stt.identity,
        "provider_identity": llm.provider_identity,
        "tts_identity": tts.identity,
        "output_format": OUTPUT_FORMAT.as_dict(),
        "input_bytes": len(pcm), "output_bytes": len(result.output_pcm),
        "input_retained": False, "output_retained": False,
        "provider_observation": llm.observations[-1] if llm.observations else None,
        "tts_observation_count": len(tts.observations),
    }
    output = CACHE / "evidence" / f"slice5-human-run-{time.monotonic_ns()}.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("Retention: microphone=false, synthesized_audio=false, conversation_content=false")
    print("Human acceptance: judge microphone transcript and Qwen3 intelligibility/naturalness now; not auto-claimed.")
    return 0 if result.terminal_event["type"] == "turn.completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

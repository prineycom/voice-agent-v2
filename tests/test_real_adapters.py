from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from voice_agent_v2.cloud_llm import ALIAS, LiteLLMProvider
from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.local_stt import TemporaryAudioFailure, WhisperSTT
from voice_agent_v2.local_tts import OUTPUT_FORMAT, Qwen3TTS
from voice_agent_v2.process_adapter import AdapterProcess, AdapterProcessError
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.tracer import CancellationToken


class FailingProcess:
    def request(self, value: dict, timeout: float) -> dict:
        del value, timeout
        raise OSError("injected process loss")

    def close(self) -> None:
        return None


class StubProcess:
    def request(self, value: dict, timeout: float) -> dict:
        del timeout
        self.last_path = Path(value["audio_path"])
        if not self.last_path.exists():
            raise AssertionError("temporary WAV was not created")
        return {"event": "final", "request_id": value["request_id"], "hypothesis": "Публичный тест."}

    def cancel(self) -> float:
        return 12.0

    def close(self) -> None:
        return None


class StubStreamingProcess:
    def stream(self, value: dict, timeout: float):
        del timeout
        self.request = value
        yield {"event": "chunk", "request_id": value["request_id"], "sequence": 1, "pcm_base64": "AAE=", "bytes": 2}
        yield {
            "event": "final", "request_id": value["request_id"], "audio_bytes": 2, "chunk_count": 1,
            "sample_rate_hz": 16000, "channels": 1, "encoding": "pcm_s16le",
        }

    def cancel(self) -> float:
        return 10.0

    def close(self) -> None:
        return None


class EmptyStreamingProcess(StubStreamingProcess):
    def stream(self, value: dict, timeout: float):
        del timeout
        yield {
            "event": "final", "request_id": value["request_id"], "audio_bytes": 0, "chunk_count": 0,
            "sample_rate_hz": 16000, "channels": 1, "encoding": "pcm_s16le",
        }


class StartupFailingProcess:
    instances = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    def start(self, _timeout: float) -> dict:
        raise AdapterProcessError("injected startup failure")

    def close(self) -> None:
        self.closed = True


class StubHTTPResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.status = status
        self._body = body
        self.fp = BytesIO(body)

    def read(self, _limit: int | None = None) -> bytes:
        return self._body


class StubHTTPConnection:
    def __init__(self, response: StubHTTPResponse) -> None:
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method: str, path: str, body=None, headers=None) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> StubHTTPResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class LocalTTSContractTests(unittest.TestCase):
    def test_streaming_pcm_is_ordered_and_not_retained(self) -> None:
        tts = Qwen3TTS()
        process = StubStreamingProcess()
        tts._process = process
        tts.ready_metadata = {"event": "ready"}
        chunks = tuple(tts.stream_synthesize(
            session_id="session-test-0001", turn_id="turn-test-0001",
            text="Публичный ответ.", audio_format=OUTPUT_FORMAT,
        ))
        self.assertEqual(chunks, (b"\x00\x01",))
        self.assertTrue(process.request["emit_pcm"])
        self.assertEqual(process.request["output_sample_rate_hz"], 16000)
        self.assertNotIn("output_path", process.request)
        self.assertFalse(tts.observations[-1]["retained_by_adapter"])
        self.assertNotIn("text", tts.observations[-1])

    def test_tts_format_text_and_correlation_bounds_fail_before_start(self) -> None:
        tts = Qwen3TTS()
        with self.assertRaises(StageFailure) as invalid_id:
            tuple(tts.stream_synthesize(
                session_id="../escape", turn_id="turn-test-0001",
                text="Публичный ответ.", audio_format=OUTPUT_FORMAT,
            ))
        self.assertEqual(invalid_id.exception.code, "invalid_correlation_id")
        with self.assertRaises(StageFailure):
            tuple(tts.stream_synthesize(
                session_id="session-test-0001", turn_id="turn-test-0001",
                text="Публичный ответ.", audio_format=AudioFormat(sample_rate_hz=24_000),
            ))
        self.assertIsNone(tts._process)

    def test_empty_terminal_result_is_an_explicit_tts_failure(self) -> None:
        tts = Qwen3TTS()
        tts._process = EmptyStreamingProcess()
        tts.ready_metadata = {"event": "ready"}
        with self.assertRaises(StageFailure) as raised:
            tuple(tts.stream_synthesize(
                session_id="session-test-0001", turn_id="turn-test-0001",
                text="Публичный ответ.", audio_format=OUTPUT_FORMAT,
            ))
        self.assertEqual(raised.exception.code, "selected_tts_unavailable")
        self.assertFalse(tts.observations)


class LiteLLMProviderContractTests(unittest.TestCase):
    def test_exact_alias_context_isolation_and_safe_observation(self) -> None:
        payloads = []

        def execute(payload: dict) -> dict:
            payloads.append(payload)
            return {
                "text": "Публичный ответ.", "http_acceptance_ms": 1.0,
                "raw_first_delta_ms": 2.0, "visible_first_content_ms": 3.0,
                "completion_ms": 4.0, "usage": {"total_tokens": 5},
                "response_models": [ALIAS],
            }

        provider = LiteLLMProvider(executor=execute)
        provider.respond(session_id="session-a", turn_id="turn-a", transcript="Публичный запрос А")
        provider.respond(session_id="session-b", turn_id="turn-b", transcript="Публичный запрос Б")
        self.assertEqual(payloads[0]["model"], ALIAS)
        self.assertEqual(payloads[1]["model"], ALIAS)
        self.assertNotIn("Публичный запрос А", json.dumps(payloads[1], ensure_ascii=False))
        self.assertEqual(set(payloads[0]), {"model", "messages", "temperature", "top_p", "max_tokens", "stream", "stream_options"})
        self.assertFalse({"text", "transcript", "response", "messages"}.intersection(provider.observations[-1]))
        provider.reset_session("session-a")
        self.assertNotIn("session-a", provider._contexts)

    def test_failure_is_explicit_and_never_changes_alias(self) -> None:
        def fail(_payload: dict) -> dict:
            raise StageFailure("llm_provider", "selected_provider_http_503")

        provider = LiteLLMProvider(executor=fail)
        with self.assertRaises(StageFailure) as raised:
            provider.respond(session_id="session-a", turn_id="turn-a", transcript="Публичный запрос")
        self.assertEqual(raised.exception.code, "selected_provider_http_503")
        self.assertEqual(provider.provider_identity, "litellm/deepseek-v4-flash")
        self.assertFalse(provider.observations[-1]["success"])

    def test_transcript_and_correlation_bounds_are_checked_before_executor(self) -> None:
        provider = LiteLLMProvider(executor=lambda _payload: self.fail("executor must not run"))
        with self.assertRaises(StageFailure) as invalid_id:
            provider.respond(session_id="../escape", turn_id="turn-a", transcript="Публичный запрос")
        self.assertEqual(invalid_id.exception.code, "invalid_correlation_id")
        with self.assertRaises(StageFailure) as raised:
            provider.respond(session_id="session-a", turn_id="turn-a", transcript="x" * 4097)
        self.assertEqual(raised.exception.code, "transcript_out_of_bounds")

    def test_readiness_checks_authenticated_alias_without_runtime_network_proof(self) -> None:
        body = json.dumps({"data": [{"id": ALIAS}]}).encode()
        connection = StubHTTPConnection(StubHTTPResponse(body))
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch(
                "voice_agent_v2.cloud_llm.http.client.HTTPConnection", return_value=connection,
            ) as http_connection,
        ):
            readiness = LiteLLMProvider().readiness()
        self.assertTrue(readiness["authenticated_alias_capability"])
        self.assertEqual(readiness["selected_alias"], ALIAS)
        self.assertEqual(readiness["endpoint"], "http://rpi:4000")
        self.assertFalse(readiness["redirects_followed"])
        self.assertFalse(readiness["runtime_network_proof_enforced"])
        self.assertEqual(readiness["transport_security"], "temporary-operator-accepted-http")
        self.assertNotIn("wireguard_proven", readiness)
        self.assertNotIn("route_interface", readiness)
        self.assertNotIn("address_class", readiness)
        http_connection.assert_called_once_with("rpi", 4000, timeout=10)
        method, path, request_body, headers = connection.requests[0]
        self.assertEqual((method, path, request_body), ("GET", "/v1/models", None))
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertTrue(connection.closed)

    def test_readiness_rejects_redirect_without_following_it(self) -> None:
        connection = StubHTTPConnection(StubHTTPResponse(b"", status=302))
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch("voice_agent_v2.cloud_llm.http.client.HTTPConnection", return_value=connection),
        ):
            with self.assertRaises(StageFailure) as raised:
                LiteLLMProvider().readiness()
        self.assertEqual(raised.exception.code, "capability_http_302")
        self.assertEqual(len(connection.requests), 1)
        self.assertTrue(connection.closed)

    def test_completion_rejects_redirect_without_fallback(self) -> None:
        connection = StubHTTPConnection(StubHTTPResponse(b"", status=307))
        provider = LiteLLMProvider()
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch(
                "voice_agent_v2.cloud_llm.http.client.HTTPConnection", return_value=connection,
            ) as http_connection,
        ):
            with self.assertRaises(StageFailure) as raised:
                provider.respond(
                    session_id="session-a", turn_id="turn-a", transcript="Публичный запрос",
                )
        self.assertEqual(raised.exception.code, "selected_provider_http_307")
        self.assertEqual(provider.provider_identity, "litellm/deepseek-v4-flash")
        self.assertFalse(provider.observations[-1]["success"])
        self.assertEqual(len(connection.requests), 1)
        http_connection.assert_called_once_with("rpi", 4000, timeout=40)

    def test_stream_output_type_and_size_are_enforced_locally(self) -> None:
        cases = (
            ({"choices": [{"delta": {"content": 7}}]}, "selected_provider_protocol_error"),
            ({"choices": [{"delta": {"content": "x" * 8193}}]}, "selected_provider_output_out_of_bounds"),
        )
        for event, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                stream = b"data: " + json.dumps(event).encode() + b"\ndata: [DONE]\n"
                connection = StubHTTPConnection(StubHTTPResponse(stream))
                provider = LiteLLMProvider()
                with (
                    patch.object(LiteLLMProvider, "_token", return_value="test-token"),
                    patch("voice_agent_v2.cloud_llm.http.client.HTTPConnection", return_value=connection),
                ):
                    with self.assertRaises(StageFailure) as raised:
                        provider.respond(
                            session_id="session-a", turn_id="turn-a", transcript="Публичный запрос",
                        )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(provider.observations[-1]["error_class"], expected_code)
                self.assertNotIn("text", provider.observations[-1])

    def test_stream_rejects_missing_or_alternate_provider_identity_before_tts_handoff(self) -> None:
        cases = (
            {"model": "alternate-provider", "choices": [{"delta": {"content": "Ответ."}}]},
            {"choices": [{"delta": {"content": "Ответ."}}]},
        )
        for event in cases:
            with self.subTest(event=event):
                stream = b"data: " + json.dumps(event).encode() + b"\ndata: [DONE]\n"
                connection = StubHTTPConnection(StubHTTPResponse(stream))
                provider = LiteLLMProvider()
                handed_off = []
                with (
                    patch.object(LiteLLMProvider, "_token", return_value="test-token"),
                    patch("voice_agent_v2.cloud_llm.http.client.HTTPConnection", return_value=connection),
                ):
                    with self.assertRaises(StageFailure) as raised:
                        provider.respond_with_handoff(
                            session_id="session-a", turn_id="turn-a", transcript="Публичный запрос",
                            on_sentence=handed_off.append,
                        )
                self.assertEqual(raised.exception.code, "selected_provider_identity_mismatch")
                self.assertEqual(handed_off, [])
                self.assertEqual(provider.observations[-1]["error_class"], "selected_provider_identity_mismatch")

    def test_executor_output_is_bounded_and_protocol_failures_are_explicit(self) -> None:
        for result, expected_code in (
            ({"text": 7}, "selected_provider_protocol_error"),
            ({"text": "x" * 8193}, "selected_provider_output_out_of_bounds"),
        ):
            with self.subTest(expected_code=expected_code):
                provider = LiteLLMProvider(executor=lambda _payload, result=result: result)
                with self.assertRaises(StageFailure) as raised:
                    provider.respond(session_id="session-a", turn_id="turn-a", transcript="Публичный запрос")
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(provider.observations[-1]["error_class"], expected_code)


class FakeSTT:
    version = "voice-agent.stt.v1"

    def transcribe(self, **_kwargs) -> str:
        return "Публичный запрос"


class FakeLLM:
    version = "voice-agent.llm-provider.v1"
    provider_mode = "cloud"
    provider_identity = "litellm/deepseek-v4-flash"

    def respond_with_handoff(self, *, on_sentence, **_kwargs) -> str:
        on_sentence("Первое предложение.")
        on_sentence("Второе предложение.")
        return "Первое предложение. Второе предложение."

    def cancel(self) -> None:
        return None


class BlockingLLM(FakeLLM):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_count = 0

    def respond_with_handoff(self, **_kwargs) -> str:
        self.entered.set()
        if not self.release.wait(1):
            raise AssertionError("LLM cancellation was not delivered")
        raise StageFailure("llm_provider", "selected_provider_cancelled")

    def cancel(self) -> None:
        self.cancel_count += 1
        self.release.set()


class FakeTTS:
    version = "voice-agent.tts.v1"
    output_format = OUTPUT_FORMAT

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.cancel_count = 0

    def stream_synthesize(self, **_kwargs):
        if self.fail:
            raise StageFailure("tts", "injected_real_tts_failure")
        yield b"\0\0" * 10

    def cancel(self) -> float:
        self.cancel_count += 1
        return 0.0


class BlockingTTS(FakeTTS):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def stream_synthesize(self, **_kwargs):
        self.entered.set()
        if not self.release.wait(1):
            raise AssertionError("TTS cancellation was not delivered")
        raise StageFailure("tts", "selected_tts_unavailable")
        yield b""

    def cancel(self) -> float:
        self.release.set()
        return super().cancel()


class RealTurnControllerTests(unittest.TestCase):
    def test_real_turn_keeps_lifecycle_order_while_inference_overlaps(self) -> None:
        result = RealTurnController(FakeSTT(), FakeLLM(), FakeTTS()).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
        )
        types = [event["type"] for event in result.events]
        self.assertEqual(result.terminal_event["type"], "turn.completed")
        self.assertLess(types.index("llm.final"), types.index("tts.audio"))
        self.assertEqual(types.count("tts.audio"), 2)

    def test_tts_failure_preserves_llm_text_event_but_fails_spoken_turn(self) -> None:
        result = RealTurnController(FakeSTT(), FakeLLM(), FakeTTS(fail=True)).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
        )
        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertIn("llm.final", [event["type"] for event in result.events])
        self.assertNotIn("tts.audio", [event["type"] for event in result.events])

    def test_stt_cleanup_retention_status_reaches_terminal_failure(self) -> None:
        class CleanupFailingSTT(FakeSTT):
            def transcribe(self, **_kwargs) -> str:
                raise TemporaryAudioFailure(input_retained=True)

        result = RealTurnController(CleanupFailingSTT(), FakeLLM(), FakeTTS()).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
        )
        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertEqual(result.terminal_event["payload"]["stage"], "stt")
        self.assertEqual(result.terminal_event["payload"]["code"], "temporary_audio_cleanup_failed")
        self.assertTrue(result.terminal_event["payload"]["input_retained"])

    def test_invalid_correlation_id_is_rejected_before_events(self) -> None:
        controller = RealTurnController(FakeSTT(), FakeLLM(), FakeTTS())
        with self.assertRaises(ValueError):
            controller.run_turn(session_id="../escape", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160)

    def test_incompatible_real_adapter_version_is_rejected(self) -> None:
        tts = FakeTTS()
        tts.version = "voice-agent.tts.v999"
        with self.assertRaises(ValueError):
            RealTurnController(FakeSTT(), FakeLLM(), tts)

    def test_cancellation_emits_no_chunks_after_interrupted_terminal(self) -> None:
        result = RealTurnController(FakeSTT(), FakeLLM(), FakeTTS()).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
            cancel_after_output_chunks=1,
        )
        self.assertEqual(result.terminal_event["type"], "turn.interrupted")
        terminal_index = [event["terminal"] for event in result.events].index(True)
        self.assertEqual(terminal_index, len(result.events) - 1)
        self.assertEqual(sum(event["type"] == "tts.audio" for event in result.events), 1)

    def test_mid_generation_cancellation_stops_adapters_and_interrupts_once(self) -> None:
        llm = BlockingLLM()
        tts = FakeTTS()
        token = CancellationToken()
        canceller = threading.Thread(target=lambda: (llm.entered.wait(1), token.cancel()))
        canceller.start()
        result = RealTurnController(FakeSTT(), llm, tts).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
            cancellation=token,
        )
        canceller.join()
        self.assertEqual(result.terminal_event["type"], "turn.interrupted")
        self.assertEqual(sum(event["terminal"] for event in result.events), 1)
        self.assertGreaterEqual(llm.cancel_count, 1)
        self.assertGreaterEqual(tts.cancel_count, 1)

    def test_mid_synthesis_cancellation_normalizes_adapter_failure_to_interruption(self) -> None:
        tts = BlockingTTS()
        token = CancellationToken()
        canceller = threading.Thread(target=lambda: (tts.entered.wait(1), token.cancel()))
        canceller.start()
        result = RealTurnController(FakeSTT(), FakeLLM(), tts).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
            cancellation=token,
        )
        canceller.join()
        self.assertEqual(result.terminal_event["type"], "turn.interrupted")
        self.assertNotIn("turn.failed", [event["type"] for event in result.events])
        self.assertGreaterEqual(tts.cancel_count, 1)

    def test_empty_tts_stream_cannot_complete_spoken_turn(self) -> None:
        class EmptyTTS(FakeTTS):
            def stream_synthesize(self, **_kwargs):
                return iter(())

        result = RealTurnController(FakeSTT(), FakeLLM(), EmptyTTS()).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
        )
        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertEqual(result.terminal_event["payload"]["code"], "empty_tts_output")


class LocalSTTContractTests(unittest.TestCase):
    def test_format_and_correlation_mismatch_fail_before_model_start(self) -> None:
        stt = WhisperSTT()
        with self.assertRaises(StageFailure) as invalid_id:
            stt.transcribe(
                session_id="../escape", turn_id="turn-test-0001", pcm=b"\0\0", audio_format=AudioFormat(),
            )
        self.assertEqual(invalid_id.exception.code, "invalid_correlation_id")
        with self.assertRaises(StageFailure) as raised:
            stt.transcribe(
                session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0",
                audio_format=AudioFormat(sample_rate_hz=24_000),
            )
        self.assertEqual(raised.exception.code, "unsupported_audio_format")
        self.assertIsNone(stt._process)

    def test_temporary_wav_is_deleted_and_observation_omits_transcript(self) -> None:
        stt = WhisperSTT()
        process = StubProcess()
        stt._process = process  # dependency seam; no GPU/process in unit tests
        stt.ready_metadata = {"event": "ready"}
        with tempfile.TemporaryDirectory() as directory, patch("voice_agent_v2.local_stt.TEMP", Path(directory)):
            transcript = stt.transcribe(
                session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                audio_format=AudioFormat(),
            )
            self.assertEqual(transcript, "Публичный тест.")
            self.assertFalse(list(Path(directory).iterdir()))
        self.assertNotIn("transcript", stt.observations[-1])
        self.assertFalse(stt.observations[-1]["temporary_audio_retained"])

    def test_process_loss_has_explicit_stage_failure_and_no_retained_audio(self) -> None:
        stt = WhisperSTT()
        stt._process = FailingProcess()
        stt.ready_metadata = {"event": "ready"}
        with tempfile.TemporaryDirectory() as directory, patch("voice_agent_v2.local_stt.TEMP", Path(directory)):
            with self.assertRaises(StageFailure) as raised:
                stt.transcribe(
                    session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                    audio_format=AudioFormat(),
                )
            self.assertEqual(raised.exception.code, "selected_stt_unavailable")
            self.assertFalse(list(Path(directory).iterdir()))

    def test_temporary_directory_failure_is_stage_specific_before_inference(self) -> None:
        stt = WhisperSTT()
        process = StubProcess()
        stt._process = process
        stt.ready_metadata = {"event": "ready"}
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "not-a-directory"
            blocked.write_bytes(b"")
            with patch("voice_agent_v2.local_stt.TEMP", blocked / "turn-temp"):
                with self.assertRaises(StageFailure) as raised:
                    stt.transcribe(
                        session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                        audio_format=AudioFormat(),
                    )
        self.assertEqual(raised.exception.code, "temporary_audio_setup_failed")
        self.assertFalse(hasattr(process, "last_path"))

    def test_temporary_cleanup_failure_scrubs_audio_and_refuses_transcript(self) -> None:
        stt = WhisperSTT()
        stt._process = StubProcess()
        stt.ready_metadata = {"event": "ready"}
        original_unlink = Path.unlink

        def reject_wav_unlink(path, *, missing_ok=False):
            if path.suffix == ".wav":
                raise PermissionError("temporary cleanup blocked")
            return original_unlink(path, missing_ok=missing_ok)

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("voice_agent_v2.local_stt.TEMP", Path(directory)),
                patch.object(Path, "unlink", new=reject_wav_unlink),
            ):
                with self.assertRaises(StageFailure) as raised:
                    stt.transcribe(
                        session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                        audio_format=AudioFormat(),
                    )
                retained_path = next(Path(directory).glob("*.wav"))
                self.assertEqual(retained_path.read_bytes(), b"")
                self.assertFalse(raised.exception.input_retained)
        self.assertEqual(raised.exception.code, "temporary_audio_cleanup_failed")
        self.assertFalse(stt.observations)

    def test_cancel_stops_selected_process(self) -> None:
        stt = WhisperSTT()
        stt._process = StubProcess()
        self.assertEqual(stt.cancel(), 12.0)
        self.assertIsNone(stt._process)

    def test_stt_and_tts_startup_failures_are_stage_specific_and_cleaned_up(self) -> None:
        StartupFailingProcess.instances.clear()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("voice_agent_v2.local_stt.TEMP", Path(directory)),
            patch("voice_agent_v2.local_stt.AdapterProcess", StartupFailingProcess),
        ):
            stt = WhisperSTT()
            with self.assertRaises(StageFailure) as stt_failure:
                stt.transcribe(
                    session_id="session-test-0001", turn_id="turn-test-0001", pcm=b"\0\0" * 160,
                    audio_format=AudioFormat(),
                )
        with patch("voice_agent_v2.local_tts.AdapterProcess", StartupFailingProcess):
            tts = Qwen3TTS()
            with self.assertRaises(StageFailure) as tts_failure:
                tuple(tts.stream_synthesize(
                    session_id="session-test-0001", turn_id="turn-test-0001",
                    text="Публичный ответ.", audio_format=OUTPUT_FORMAT,
                ))
        self.assertEqual(stt_failure.exception.code, "selected_stt_unavailable")
        self.assertEqual(tts_failure.exception.code, "selected_tts_unavailable")
        self.assertIsNone(stt._process)
        self.assertIsNone(tts._process)
        self.assertTrue(all(process.closed for process in StartupFailingProcess.instances))


class AdapterProcessTests(unittest.TestCase):
    def test_invalid_startup_event_terminates_child_and_resets_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = AdapterProcess(
                [sys.executable, "-c", "import time; print('{}', flush=True); time.sleep(30)"],
                Path(directory) / "adapter.log", dict(os.environ),
            )
            with self.assertRaises(AdapterProcessError):
                adapter.start(1)
            self.assertIsNone(adapter.process)
            self.assertIsNone(adapter._reader)
            self.assertIsNone(adapter._log)

    def test_non_object_startup_event_is_an_explicit_protocol_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = AdapterProcess(
                [sys.executable, "-c", "print('[]', flush=True)"],
                Path(directory) / "adapter.log", dict(os.environ),
            )
            with self.assertRaisesRegex(AdapterProcessError, "non-object protocol output"):
                adapter.start(1)
            self.assertIsNone(adapter.process)
            self.assertIsNone(adapter._reader)
            self.assertIsNone(adapter._log)


if __name__ == "__main__":
    unittest.main()

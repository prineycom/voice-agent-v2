from __future__ import annotations

import base64
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from voice_agent_v2.cloud_llm import ALIAS, LiteLLMProvider, parse_provider_endpoint
from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.local_stt import TemporaryAudioFailure, WhisperSTT
from voice_agent_v2.local_tts import OUTPUT_FORMAT, Qwen3TTS
from voice_agent_v2.process_adapter import AdapterProcess, AdapterProcessError
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.tracer import CancellationToken


def loopback_resolver(_host: str, port: int, sender) -> None:
    sender.send((True, [(2, 1, 6, "", ("127.0.0.1", port))]))
    sender.close()


def blocked_resolver(_host: str, _port: int, sender) -> None:
    del sender
    time.sleep(10)


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


class CancellationBlockingProcess:
    instances = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.entered = threading.Event()
        self.cancelled = threading.Event()
        self.closed = False
        self.process = None
        self.__class__.instances.append(self)

    def start(self, _timeout: float) -> dict:
        self.entered.set()
        self.cancelled.wait(1)
        raise AdapterProcessError("startup cancelled")

    def cancel(self) -> float:
        self.cancelled.set()
        return 0.0

    def close(self) -> None:
        self.closed = True


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


class SlowLineReader:
    def __init__(self, lines: list[bytes], delay_seconds: float) -> None:
        self.lines = iter(lines)
        self.delay_seconds = delay_seconds

    def readline(self, _limit: int = -1) -> bytes:
        time.sleep(self.delay_seconds)
        return next(self.lines, b"")


class TrickleReadinessResponse:
    status = 200

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.fp = BytesIO()

    def read(self, _limit: int | None = None) -> bytes:
        while not self.cancelled.wait(0.01):
            pass
        raise OSError("readiness body transport closed")

    def cancel(self) -> None:
        self.cancelled.set()


class BlockingConnectSocket:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.closed = threading.Event()

    def settimeout(self, _timeout: float) -> None:
        return None

    def bind(self, _source) -> None:
        return None

    def connect(self, _address) -> None:
        self.entered.set()
        self.closed.wait(2)
        raise OSError("connect cancelled")

    def setsockopt(self, *_args) -> None:
        return None

    def close(self) -> None:
        self.closed.set()


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
        cancel = getattr(self.response, "cancel", None)
        if cancel is not None:
            cancel()


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

    def test_concurrent_tts_cancellation_joins_single_process_cleanup(self) -> None:
        class CleanupProcess:
            def __init__(self) -> None:
                self.process = object()
                self.entered = threading.Event()
                self.release = threading.Event()
                self.cancel_count = 0
                self.close_count = 0

            def cancel(self) -> float:
                self.cancel_count += 1
                self.entered.set()
                self.release.wait(1)
                return 7.0

            def close(self) -> None:
                self.close_count += 1
                self.process = None

        tts = Qwen3TTS()
        process = CleanupProcess()
        tts._process = process
        results: list[float] = []
        first = threading.Thread(target=lambda: results.append(tts.cancel()))
        second = threading.Thread(target=lambda: results.append(tts.cancel()))

        first.start()
        self.assertTrue(process.entered.wait(0.5))
        second.start()
        time.sleep(0.02)
        self.assertEqual(process.cancel_count, 1)
        self.assertTrue(second.is_alive())
        process.release.set()
        first.join(0.5)
        second.join(0.5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(process.cancel_count, 1)
        self.assertEqual(process.close_count, 1)
        self.assertCountEqual(results, [0.0, 7.0])
        self.assertIsNone(tts._process)

    def test_turn_tts_budget_spans_every_sentence_handoff(self) -> None:
        class SentenceProcess:
            def __init__(self) -> None:
                self.cancelled = False

            def stream(self, value: dict, _timeout: float):
                data = b"\0\0"
                yield {
                    "event": "chunk",
                    "request_id": value["request_id"],
                    "sequence": 1,
                    "pcm_base64": base64.b64encode(data).decode(),
                    "bytes": len(data),
                }
                yield {
                    "event": "final",
                    "request_id": value["request_id"],
                    "audio_bytes": len(data),
                    "chunk_count": 1,
                    "sample_rate_hz": 16000,
                    "channels": 1,
                    "encoding": "pcm_s16le",
                }

            def cancel(self) -> float:
                self.cancelled = True
                return 0.0

        class ManySentenceLLM(FakeLLM):
            def respond_with_handoff(self, *, on_sentence, **_kwargs) -> str:
                for index in range(4):
                    on_sentence(f"Предложение {index}.")
                return "Ответ из четырёх предложений."

        process = SentenceProcess()
        tts = Qwen3TTS()
        tts._process = process
        tts.ready_metadata = {"event": "ready"}
        with patch("voice_agent_v2.local_tts.MAX_TTS_CHUNKS", 2):
            result = RealTurnController(FakeSTT(), ManySentenceLLM(), tts).run_turn(
                session_id="session-test-0001",
                turn_id="turn-test-0001",
                input_pcm=b"\0\0" * 160,
            )

        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertEqual(
            result.terminal_event["payload"]["code"],
            "selected_tts_output_out_of_bounds",
        )
        self.assertTrue(process.cancelled)
        self.assertIsNone(tts._process)

    def test_tts_chunk_and_audio_duration_bounds_cancel_malformed_adapter(self) -> None:
        class MalformedStreamingProcess:
            def __init__(self, chunks: tuple[bytes, ...]) -> None:
                self.chunks = chunks
                self.cancelled = False

            def stream(self, value: dict, _timeout: float):
                for sequence, chunk in enumerate(self.chunks, 1):
                    yield {
                        "event": "chunk",
                        "request_id": value["request_id"],
                        "sequence": sequence,
                        "pcm_base64": base64.b64encode(chunk).decode(),
                        "bytes": len(chunk),
                    }

            def cancel(self) -> float:
                self.cancelled = True
                return 0.0

        cases = (
            ("MAX_TTS_CHUNKS", 2, (b"\0\0", b"\0\0", b"\0\0")),
            ("MAX_TTS_OUTPUT_BYTES", 2, (b"\0\0\0\0",)),
        )
        for bound, limit, chunks in cases:
            with self.subTest(bound=bound):
                process = MalformedStreamingProcess(chunks)
                tts = Qwen3TTS()
                tts._process = process
                tts.ready_metadata = {"event": "ready"}
                with (
                    patch(f"voice_agent_v2.local_tts.{bound}", limit),
                    self.assertRaises(StageFailure) as raised,
                ):
                    tuple(tts.stream_synthesize(
                        session_id="session-test-0001",
                        turn_id="turn-test-0001",
                        text="Публичный ответ.",
                        audio_format=OUTPUT_FORMAT,
                    ))
                self.assertEqual(raised.exception.code, "selected_tts_output_out_of_bounds")
                self.assertTrue(process.cancelled)
                self.assertIsNone(tts._process)


class LiteLLMProviderContractTests(unittest.TestCase):
    TEST_BASE_URL = "http://rpi:4000"

    def test_endpoint_is_required_from_the_one_named_environment_variable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(StageFailure) as missing:
                LiteLLMProvider().readiness()
        self.assertEqual(missing.exception.code, "endpoint_configuration_invalid")
        with patch.dict(os.environ, {"LITELLM_ENDPOINT": self.TEST_BASE_URL}, clear=True):
            with self.assertRaises(StageFailure) as alias:
                LiteLLMProvider().readiness()
        self.assertEqual(alias.exception.code, "endpoint_configuration_invalid")

    def test_endpoint_parser_allows_configuration_only_http_to_https_migration(self) -> None:
        http = parse_provider_endpoint(self.TEST_BASE_URL)
        https = parse_provider_endpoint("https://llm.example.test")
        self.assertEqual((http.scheme, http.host, http.port), ("http", "rpi", 4000))
        self.assertEqual((https.scheme, https.host, https.port), ("https", "llm.example.test", 443))
        for invalid in ("", "rpi:4000", "http://rpi:4000/v1", "http://user@rpi:4000", "http://rpi:4000?next=x"):
            with self.subTest(invalid=invalid), self.assertRaises(StageFailure):
                parse_provider_endpoint(invalid)

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

    def test_session_context_can_rollback_an_undelivered_playout_turn(self) -> None:
        provider = LiteLLMProvider(executor=lambda _payload: {"text": "Публичный ответ."})
        snapshot = provider.snapshot_session("session-a")
        provider.respond(session_id="session-a", turn_id="turn-a", transcript="Публичный запрос")
        self.assertIn("session-a", provider._contexts)
        provider.restore_session("session-a", snapshot)
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
                "voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection,
            ) as http_connection,
        ):
            readiness = LiteLLMProvider(base_url=self.TEST_BASE_URL).readiness()
        self.assertTrue(readiness["authenticated_alias_capability"])
        self.assertEqual(readiness["selected_alias"], ALIAS)
        self.assertEqual(readiness["endpoint"], "http://rpi:4000")
        self.assertFalse(readiness["redirects_followed"])
        self.assertFalse(readiness["runtime_network_proof_enforced"])
        self.assertEqual(readiness["transport_security"], "temporary-operator-accepted-http")
        self.assertNotIn("wireguard_proven", readiness)
        self.assertNotIn("route_interface", readiness)
        self.assertNotIn("address_class", readiness)
        http_connection.assert_called_once()
        args, kwargs = http_connection.call_args
        self.assertEqual(args, ("rpi", 4000))
        self.assertEqual(kwargs["timeout"], 10)
        self.assertTrue(callable(kwargs["cancelled"]))
        method, path, request_body, headers = connection.requests[0]
        self.assertEqual((method, path, request_body), ("GET", "/v1/models", None))
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertTrue(connection.closed)

    def test_readiness_rejects_redirect_without_following_it(self) -> None:
        connection = StubHTTPConnection(StubHTTPResponse(b"", status=302))
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch("voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection),
        ):
            with self.assertRaises(StageFailure) as raised:
                LiteLLMProvider(base_url=self.TEST_BASE_URL).readiness()
        self.assertEqual(raised.exception.code, "capability_http_302")
        self.assertEqual(len(connection.requests), 1)
        self.assertTrue(connection.closed)

    def test_readiness_uses_one_deadline_across_trickled_body(self) -> None:
        response = TrickleReadinessResponse()
        connection = StubHTTPConnection(response)
        provider = LiteLLMProvider(
            base_url=self.TEST_BASE_URL,
            transport_start_timeout_seconds=0.08,
        )
        started = time.monotonic()
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch(
                "voice_agent_v2.cloud_llm._RegisteredHTTPConnection",
                return_value=connection,
            ) as http_connection,
            self.assertRaises(StageFailure) as raised,
        ):
            provider.readiness()
        elapsed = time.monotonic() - started

        self.assertEqual(raised.exception.code, "capability_probe_unavailable")
        self.assertLess(elapsed, 0.25)
        self.assertTrue(connection.closed)
        self.assertIsNotNone(http_connection.call_args.kwargs["operation_deadline"])
        self.assertTrue(callable(http_connection.call_args.kwargs["cancelled"]))

    def test_blocked_connection_start_is_cancelled_by_registered_socket(self) -> None:
        provider = LiteLLMProvider(
            base_url=self.TEST_BASE_URL,
            resolver_target=loopback_resolver,
        )
        connection_socket = BlockingConnectSocket()
        failures = []

        def respond() -> None:
            try:
                provider.respond(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                )
            except StageFailure as error:
                failures.append(error)

        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch("voice_agent_v2.cloud_llm.socket.socket", return_value=connection_socket),
        ):
            worker = threading.Thread(target=respond)
            worker.start()
            self.assertTrue(connection_socket.entered.wait(1))
            provider.cancel()
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(connection_socket.closed.is_set())
        self.assertEqual(failures[0].code, "selected_provider_cancelled")

    def test_blocked_dns_start_is_cancelled_before_transport_creation(self) -> None:
        provider = LiteLLMProvider(
            base_url=self.TEST_BASE_URL,
            transport_start_timeout_seconds=0.2,
            resolver_target=blocked_resolver,
        )
        failures = []

        def respond() -> None:
            try:
                provider.respond(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                )
            except StageFailure as error:
                failures.append(error)

        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch("voice_agent_v2.cloud_llm.socket.socket") as socket_factory,
        ):
            worker = threading.Thread(target=respond)
            worker.start()
            deadline = time.monotonic() + 1
            while not any(
                child.name == "voice-provider-resolver" and child.is_alive()
                for child in multiprocessing.active_children()
            ):
                if time.monotonic() >= deadline:
                    self.fail("resolver process did not start")
                time.sleep(0.005)
            provider.cancel()
            worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertFalse(socket_factory.called)
        self.assertFalse(any(
            child.name == "voice-provider-resolver" and child.is_alive()
            for child in multiprocessing.active_children()
        ))
        self.assertEqual(failures[0].code, "selected_provider_cancelled")

    def test_completion_rejects_redirect_without_fallback(self) -> None:
        connection = StubHTTPConnection(StubHTTPResponse(b"", status=307))
        provider = LiteLLMProvider(base_url=self.TEST_BASE_URL)
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch(
                "voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection,
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
        http_connection.assert_called_once()
        args, kwargs = http_connection.call_args
        self.assertEqual(args, ("rpi", 4000))
        self.assertEqual(kwargs["timeout"], 40)
        self.assertTrue(callable(kwargs["cancelled"]))

    def test_stream_output_type_and_size_are_enforced_locally(self) -> None:
        cases = (
            ({"choices": [{"delta": {"content": 7}}]}, "selected_provider_protocol_error"),
            ({"choices": [{"delta": {"content": "x" * 8193}}]}, "selected_provider_output_out_of_bounds"),
        )
        for event, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                stream = b"data: " + json.dumps(event).encode() + b"\ndata: [DONE]\n"
                connection = StubHTTPConnection(StubHTTPResponse(stream))
                provider = LiteLLMProvider(base_url=self.TEST_BASE_URL)
                with (
                    patch.object(LiteLLMProvider, "_token", return_value="test-token"),
                    patch("voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection),
                ):
                    with self.assertRaises(StageFailure) as raised:
                        provider.respond(
                            session_id="session-a", turn_id="turn-a", transcript="Публичный запрос",
                        )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(provider.observations[-1]["error_class"], expected_code)
                self.assertNotIn("text", provider.observations[-1])

    def test_stream_uses_one_wall_clock_deadline_across_slow_events(self) -> None:
        first = json.dumps({
            "model": ALIAS,
            "choices": [{"delta": {"content": "Ответ."}}],
        }).encode()
        response = StubHTTPResponse(b"")
        response.fp = SlowLineReader(
            [b"data: " + first + b"\n", b"data: [DONE]\n"],
            0.06,
        )
        connection = StubHTTPConnection(response)
        provider = LiteLLMProvider(
            base_url=self.TEST_BASE_URL,
            request_timeout_seconds=0.1,
        )
        started = time.monotonic()
        with (
            patch.object(LiteLLMProvider, "_token", return_value="test-token"),
            patch("voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection),
            self.assertRaises(StageFailure) as raised,
        ):
            provider.respond(
                session_id="session-a",
                turn_id="turn-a",
                transcript="Публичный запрос",
            )
        elapsed = time.monotonic() - started

        self.assertEqual(raised.exception.code, "selected_provider_request_timeout")
        self.assertLess(elapsed, 0.2)
        self.assertTrue(connection.closed)

    def test_stream_rejects_missing_or_alternate_provider_identity_before_tts_handoff(self) -> None:
        cases = (
            {"model": "alternate-provider", "choices": [{"delta": {"content": "Ответ."}}]},
            {"choices": [{"delta": {"content": "Ответ."}}]},
        )
        for event in cases:
            with self.subTest(event=event):
                stream = b"data: " + json.dumps(event).encode() + b"\ndata: [DONE]\n"
                connection = StubHTTPConnection(StubHTTPResponse(stream))
                provider = LiteLLMProvider(base_url=self.TEST_BASE_URL)
                handed_off = []
                with (
                    patch.object(LiteLLMProvider, "_token", return_value="test-token"),
                    patch("voice_agent_v2.cloud_llm._RegisteredHTTPConnection", return_value=connection),
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


class GenerationBlockingLLM(FakeLLM):
    def __init__(self) -> None:
        self.calls = 0
        self.first_entered = threading.Event()
        self.first_released = threading.Event()
        self.cancel_entered = threading.Event()
        self.allow_cancel = threading.Event()

    def respond_with_handoff(self, *, on_sentence, **_kwargs) -> str:
        self.calls += 1
        if self.calls == 1:
            self.first_entered.set()
            self.first_released.wait(2)
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        on_sentence("Следующий ответ.")
        return "Следующий ответ."

    def cancel(self) -> None:
        self.cancel_entered.set()
        self.allow_cancel.wait(2)
        self.first_released.set()


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
        self.assertLess(types.index("llm.visible"), types.index("tts.audio"))
        self.assertLess(types.index("tts.audio"), types.index("llm.final"))
        self.assertEqual(types.count("tts.audio"), 2)

    def test_late_llm_failure_discards_streamed_tts_buffer(self) -> None:
        class LateFailingLLM(FakeLLM):
            supports_handoff_abort = True

            def respond_with_handoff(self, *, on_sentence, on_handoff_abort, **_kwargs) -> str:
                on_sentence("Буферизованный ответ.")
                on_handoff_abort()
                raise StageFailure("llm_provider", "selected_provider_identity_mismatch")

        class RecordingTTS(FakeTTS):
            def __init__(self) -> None:
                super().__init__()
                self.synthesized = 0

            def stream_synthesize(self, **kwargs):
                self.synthesized += 1
                yield from super().stream_synthesize(**kwargs)

        tts = RecordingTTS()
        result = RealTurnController(FakeSTT(), LateFailingLLM(), tts).run_turn(
            session_id="session-test-0001", turn_id="turn-test-0001", input_pcm=b"\0\0" * 160,
        )

        self.assertEqual(tts.synthesized, 1)
        self.assertEqual(tts.cancel_count, 1)
        self.assertEqual(result.output_pcm, b"")
        self.assertEqual(result.terminal_event["type"], "turn.failed")
        event_types = [event["type"] for event in result.events]
        self.assertIn("llm.visible", event_types)
        self.assertIn("turn.speaking", event_types)
        self.assertIn("tts.audio", event_types)
        self.assertNotIn("llm.final", event_types)

    def test_late_handoff_cleanup_finishes_before_next_turn_generation(self) -> None:
        class LateCleanupLLM(FakeLLM):
            supports_handoff_abort = True

            def __init__(self) -> None:
                self.calls = 0

            def respond_with_handoff(
                self, *, on_sentence, on_handoff_abort, **_kwargs
            ) -> str:
                self.calls += 1
                on_sentence("Буферизованный ответ.")
                if self.calls == 1:
                    on_handoff_abort()
                    raise StageFailure(
                        "llm_provider", "selected_provider_identity_mismatch"
                    )
                return "Буферизованный ответ."

        class CleanupBlockingTTS(FakeTTS):
            def __init__(self) -> None:
                super().__init__()
                self.cleanup_entered = threading.Event()
                self.cleanup_release = threading.Event()

            def cancel(self) -> float:
                if self.cancel_count == 0:
                    self.cleanup_entered.set()
                    self.cleanup_release.wait(1)
                return super().cancel()

        llm = LateCleanupLLM()
        tts = CleanupBlockingTTS()
        controller = RealTurnController(FakeSTT(), llm, tts)
        results = {}
        first = threading.Thread(target=lambda: results.__setitem__("first", controller.run_turn(
            session_id="session-test-0001",
            turn_id="turn-test-0001",
            input_pcm=b"\0\0" * 160,
        )))
        second = threading.Thread(target=lambda: results.__setitem__("second", controller.run_turn(
            session_id="session-test-0001",
            turn_id="turn-test-0002",
            input_pcm=b"\0\0" * 160,
        )))

        first.start()
        self.assertTrue(tts.cleanup_entered.wait(0.5))
        second.start()
        time.sleep(0.03)
        self.assertEqual(llm.calls, 1)
        self.assertTrue(second.is_alive())
        tts.cleanup_release.set()
        first.join(0.5)
        second.join(0.5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(llm.calls, 2)
        self.assertEqual(results["first"].terminal_event["type"], "turn.failed")
        self.assertEqual(results["first"].output_pcm, b"")
        self.assertEqual(results["second"].terminal_event["type"], "turn.completed")

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

    def test_cancellation_during_adapter_startup_is_persistent(self) -> None:
        CancellationBlockingProcess.instances.clear()
        token = CancellationToken()
        stt = WhisperSTT()
        controller = RealTurnController(stt, FakeLLM(), FakeTTS())
        result = []

        def run() -> None:
            result.append(controller.run_turn(
                session_id="session-test-0001",
                turn_id="turn-test-0001",
                input_pcm=b"\0\0" * 160,
                cancellation=token,
            ))

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("voice_agent_v2.local_stt.TEMP", Path(directory)),
            patch("voice_agent_v2.local_stt.AdapterProcess", CancellationBlockingProcess),
        ):
            worker = threading.Thread(target=run)
            worker.start()
            while not CancellationBlockingProcess.instances:
                threading.Event().wait(0.001)
            process = CancellationBlockingProcess.instances[0]
            self.assertTrue(process.entered.wait(1))
            token.cancel()
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(process.cancelled.is_set())
        self.assertEqual(result[0].terminal_event["type"], "turn.interrupted")
        self.assertIsNone(stt._process)

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

    def test_late_cancellation_finishes_before_the_next_turn_generation(self) -> None:
        llm = GenerationBlockingLLM()
        controller = RealTurnController(FakeSTT(), llm, FakeTTS())
        token = CancellationToken()
        results = []

        first = threading.Thread(target=lambda: results.append(controller.run_turn(
            session_id="session-test-0001",
            turn_id="turn-test-0001",
            input_pcm=b"\0\0" * 160,
            cancellation=token,
        )))
        second = threading.Thread(target=lambda: results.append(controller.run_turn(
            session_id="session-test-0001",
            turn_id="turn-test-0002",
            input_pcm=b"\0\0" * 160,
        )))
        first.start()
        self.assertTrue(llm.first_entered.wait(1))
        canceller = threading.Thread(target=token.cancel)
        canceller.start()
        self.assertTrue(llm.cancel_entered.wait(1))
        second.start()
        threading.Event().wait(0.05)
        self.assertTrue(canceller.is_alive())
        self.assertEqual(llm.calls, 1)
        llm.allow_cancel.set()
        canceller.join(1)
        first.join(1)
        second.join(1)

        self.assertFalse(canceller.is_alive())
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(llm.calls, 2)
        self.assertEqual(results[0].terminal_event["type"], "turn.interrupted")
        self.assertEqual(results[1].terminal_event["type"], "turn.completed")

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
    def test_request_timeout_is_one_deadline_and_terminates_slow_child(self) -> None:
        script = (
            "import json,sys,time; "
            "print(json.dumps({'event':'ready'}), flush=True); "
            "request=json.loads(sys.stdin.readline()); "
            "[(time.sleep(0.04), print(json.dumps({'request_id':request['request_id'],"
            "'event':'partial'}), flush=True)) for _ in range(10)]"
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = AdapterProcess(
                [sys.executable, "-c", script],
                Path(directory) / "adapter.log",
                dict(os.environ),
            )
            adapter.start(1)
            started = time.monotonic()
            with self.assertRaisesRegex(AdapterProcessError, "timed out"):
                adapter.request({"request_id": "request"}, 0.1)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.2)
            self.assertIsNotNone(adapter.process)
            self.assertIsNotNone(adapter.process.poll())
            adapter.close()

    def test_request_event_limit_terminates_flooding_child(self) -> None:
        script = (
            "import json,sys,time; "
            "print(json.dumps({'event':'ready'}), flush=True); "
            "request=json.loads(sys.stdin.readline()); "
            "[(time.sleep(0.01), print(json.dumps({'request_id':request['request_id'],"
            "'event':'partial'}), flush=True)) for _ in range(10)]"
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = AdapterProcess(
                [sys.executable, "-c", script],
                Path(directory) / "adapter.log",
                dict(os.environ),
            )
            adapter.start(1)
            with (
                patch("voice_agent_v2.process_adapter.MAX_REQUEST_EVENTS", 2),
                self.assertRaisesRegex(AdapterProcessError, "event bound exceeded"),
            ):
                adapter.request({"request_id": "request"}, 1)

            self.assertIsNotNone(adapter.process)
            self.assertIsNotNone(adapter.process.poll())
            adapter.close()

    def test_stream_timeout_is_one_wall_clock_deadline(self) -> None:
        adapter = AdapterProcess([], Path("unused"), {})
        adapter.send = lambda _value: None

        def emit_slow_events() -> None:
            time.sleep(0.06)
            adapter._events.put({"request_id": "request", "event": "chunk"})
            time.sleep(0.06)
            adapter._events.put({"request_id": "request", "event": "final"})

        producer = threading.Thread(target=emit_slow_events)
        producer.start()
        started = time.monotonic()
        with self.assertRaisesRegex(AdapterProcessError, "timed out"):
            tuple(adapter.stream({"request_id": "request"}, 0.1))
        elapsed = time.monotonic() - started
        producer.join(1)

        self.assertLess(elapsed, 0.14)

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

    def test_protocol_line_and_event_queue_are_bounded_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            line_adapter = AdapterProcess(
                [sys.executable, "-c", "print('x' * 256, flush=True)"],
                Path(directory) / "line.log",
                dict(os.environ),
            )
            with (
                patch("voice_agent_v2.process_adapter.MAX_PROTOCOL_LINE_BYTES", 64),
                self.assertRaisesRegex(AdapterProcessError, "invalid protocol output") as raised,
            ):
                line_adapter.start(1)
            self.assertIn("line exceeds size bound", str(raised.exception.__cause__))

            queue_adapter = AdapterProcess(
                [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'event':'ready'}), flush=True); "
                    "[print(json.dumps({'event':'chunk','sequence':i}), flush=True) for i in range(100)]",
                ],
                Path(directory) / "queue.log",
                dict(os.environ),
            )
            with patch("voice_agent_v2.process_adapter.MAX_PROTOCOL_QUEUE_EVENTS", 2):
                try:
                    queue_adapter.start(1)
                except AdapterProcessError as error:
                    self.assertIn("queue overflow", str(error.__cause__ or error))
                else:
                    time.sleep(0.05)
                    with self.assertRaisesRegex(AdapterProcessError, "invalid protocol output") as raised:
                        queue_adapter.receive(1)
                    self.assertIn("queue overflow", str(raised.exception.__cause__))
                finally:
                    queue_adapter.close()

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

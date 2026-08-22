from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import threading
import time
import unittest

from voice_agent_v2.agent_run import AGENT_TOOLS, AgentDecision
from voice_agent_v2.contracts import StageFailure
from voice_agent_v2.local_lfm import (
    AGENT_DECISION_ALLOWED_PAYLOAD_FIELDS,
    ALLOWED_PAYLOAD_FIELDS,
    LLAMA_ENDPOINT,
    MAX_TOKENS,
    MODEL_ALIAS,
    PROVIDER_IDENTITY,
    REASONING_BUDGET,
    LocalLFMProvider,
)
from voice_agent_v2.tracer import CancellationToken


class StubResponse:
    def __init__(self, events: list[dict], *, status: int = 200) -> None:
        self.status = status
        body = b"".join(
            b"data: " + json.dumps(event).encode() + b"\n" for event in events
        ) + b"data: [DONE]\n"
        self.fp = BytesIO(body)
        self._body = body

    def read(self, limit: int | None = None) -> bytes:
        return self._body if limit is None else self._body[:limit]


class StubConnection:
    def __init__(self, _host: str, _port: int, *, timeout: float, responses: list[StubResponse]) -> None:
        self.timeout = timeout
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None, dict | None]] = []
        self.closed = False
        self.sock = None

    def request(self, method: str, path: str, body=None, headers=None) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> StubResponse:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class DeadlineConnection(StubConnection):
    def __init__(self, *args, block_headers: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.block_headers = block_headers
        self.closed_event = threading.Event()
        for response in self.responses:
            if isinstance(response, BlockingReadResponse):
                response.closed_event = self.closed_event

    def getresponse(self) -> StubResponse:
        if self.block_headers:
            self.closed_event.wait(1)
            raise OSError("connection closed while awaiting headers")
        return super().getresponse()

    def close(self) -> None:
        super().close()
        self.closed_event.set()


class BlockingReadResponse(StubResponse):
    closed_event: threading.Event | None = None

    def read(self, limit: int | None = None) -> bytes:
        if self.closed_event is None:
            raise AssertionError("response is not bound to its connection")
        self.closed_event.wait(1)
        raise OSError("connection closed while reading error body")


class CallbackGatedResponse:
    status = 200

    def __init__(self, lines: list[bytes], callback_seen: threading.Event) -> None:
        self.fp = self
        self.lines = lines
        self.callback_seen = callback_seen
        self.index = 0

    def readline(self, _limit: int) -> bytes:
        if self.index == 1 and not self.callback_seen.wait(0.5):
            raise AssertionError("sentence callback did not run during SSE streaming")
        if self.index >= len(self.lines):
            return b""
        line = self.lines[self.index]
        self.index += 1
        return line

    def read(self, _limit: int | None = None) -> bytes:
        return b""


def semantic_difference_paths(
    left: object, right: object, path: tuple[object, ...] = (),
) -> set[tuple[object, ...]]:
    if isinstance(left, dict) and isinstance(right, dict):
        result = {path + (key,) for key in set(left) ^ set(right)}
        for key in set(left) & set(right):
            result.update(semantic_difference_paths(left[key], right[key], path + (key,)))
        return result
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {path + ("length",)}
        result: set[tuple[object, ...]] = set()
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            result.update(semantic_difference_paths(left_item, right_item, path + (index,)))
        return result
    return set() if left == right else {path}


def stream_event(*, reasoning: str = "", content: str = "", finish=None, model=MODEL_ALIAS):
    delta = {}
    if reasoning:
        delta["reasoning_content"] = reasoning
    if content:
        delta["content"] = content
    return {
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "delta": delta}],
    }


class LocalLFMProviderTests(unittest.TestCase):
    def factory(self, responses: list[StubResponse]):
        created: list[StubConnection] = []

        def build(host: str, port: int, *, timeout: float):
            self.assertEqual((host, port), ("127.0.0.1", 18080))
            connection = StubConnection(host, port, timeout=timeout, responses=responses)
            created.append(connection)
            return connection

        return build, created

    def test_identity_endpoint_and_readiness_are_fixed_local_without_credentials(self) -> None:
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        factory, created_response = self.factory([health])
        provider = LocalLFMProvider(connection_factory=factory)
        readiness = provider.readiness()

        self.assertEqual(LLAMA_ENDPOINT, "http://127.0.0.1:18080")
        self.assertEqual(provider.provider_mode, "local")
        self.assertEqual(provider.provider_identity, PROVIDER_IDENTITY)
        self.assertFalse(readiness["external_transfer"])
        self.assertFalse(readiness["automatic_fallback"])
        self.assertFalse(readiness["credentials_required"])
        self.assertEqual(readiness["parallel_slots"], 2)
        self.assertEqual(readiness["context_tokens_per_slot"], 32768)
        self.assertTrue(provider.runtime_live)
        self.assertTrue(provider.runtime_health["ready"])
        self.assertTrue(provider.runtime_health["compatible"])
        self.assertEqual(created_response[0].requests[0][:2], ("GET", "/health"))

    def test_identity_failure_preserves_liveness_but_drops_readiness_and_compatibility(self) -> None:
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        wrong_identity = StubResponse([
            stream_event(content="Ответ.", model="wrong-model"),
            stream_event(finish="stop", model="wrong-model"),
        ])
        factory, _created = self.factory([health, wrong_identity])
        provider = LocalLFMProvider(connection_factory=factory)

        provider.readiness()
        self.assertTrue(provider.runtime_live)
        with self.assertRaises(StageFailure):
            provider.respond(
                session_id="session-liveness",
                turn_id="turn-liveness",
                transcript="Проверка.",
            )
        self.assertTrue(provider.runtime_live)
        self.assertEqual(provider.runtime_health, {
            "live": True,
            "ready": False,
            "compatible": False,
            "reason_code": "selected_provider_identity_mismatch",
        })

    def test_protocol_failure_preserves_liveness_but_drops_compatibility(self) -> None:
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        malformed = StubResponse([
            stream_event(reasoning="Скрытый токен."),
            {"model": MODEL_ALIAS, "choices": "invalid"},
        ])
        factory, _created = self.factory([health, malformed])
        provider = LocalLFMProvider(connection_factory=factory)

        provider.readiness()
        with self.assertRaises(StageFailure) as raised:
            provider.respond(
                session_id="session-protocol",
                turn_id="turn-protocol",
                transcript="Проверка.",
            )

        self.assertEqual(raised.exception.code, "selected_provider_protocol_error")
        self.assertEqual(provider.runtime_health, {
            "live": True,
            "ready": False,
            "compatible": False,
            "reason_code": "selected_provider_protocol_error",
        })
        self.assertIsInstance(
            provider.observations[-1]["provider_first_token_ms"], float
        )
        self.assertNotIn("visible_first_content_ms", provider.observations[-1])

    def test_health_body_transport_loss_is_dead_not_a_contract_mismatch(self) -> None:
        health = StubResponse([])

        def fail_read(_limit=None):
            raise OSError("truncated health transfer")

        health.read = fail_read
        factory, _created = self.factory([health])
        provider = LocalLFMProvider(connection_factory=factory)

        with self.assertRaises(StageFailure) as raised:
            provider.readiness()

        self.assertEqual(raised.exception.code, "local_lfm_unavailable")
        self.assertEqual(provider.runtime_health, {
            "live": False,
            "ready": False,
            "compatible": True,
            "reason_code": "local_lfm_unavailable",
        })

    def test_invalid_health_document_is_alive_but_incompatible(self) -> None:
        health = StubResponse([])
        health._body = b'{invalid'
        health.read = lambda limit=None: health._body
        factory, _created = self.factory([health])
        provider = LocalLFMProvider(connection_factory=factory)

        with self.assertRaises(StageFailure) as raised:
            provider.readiness()

        self.assertEqual(raised.exception.code, "local_lfm_health_failed")
        self.assertEqual(provider.runtime_health, {
            "live": True,
            "ready": False,
            "compatible": False,
            "reason_code": "local_lfm_health_failed",
        })

    def test_payload_freezes_voice_sampling_reasoning_and_visible_bounds(self) -> None:
        provider = LocalLFMProvider(connection_factory=lambda *_a, **_k: None)
        payload = provider._payload("session-a", "Почему летом день длиннее?")
        self.assertEqual(set(payload), ALLOWED_PAYLOAD_FIELDS)
        self.assertEqual(payload["model"], MODEL_ALIAS)
        self.assertEqual(payload["max_tokens"], MAX_TOKENS)
        self.assertEqual(payload["reasoning_budget"], REASONING_BUDGET)
        self.assertEqual(payload["reasoning_format"], "deepseek")
        self.assertFalse(payload["cache_prompt"])
        self.assertNotIn("endpoint", json.dumps(payload))

    def test_agent_decision_request_uses_one_field_generation_clone_and_valid_documents_parse_unchanged(self) -> None:
        operation = {
            "kind": "operation",
            "tool": "shell.exec",
            "arguments": {"command": "printf safe"},
        }
        final = {"kind": "final", "answer": "Готово."}
        responses = [
            StubResponse([
                stream_event(content=json.dumps(operation)),
                stream_event(finish="stop"),
            ]),
            StubResponse([
                stream_event(content=json.dumps(final, ensure_ascii=False)),
                stream_event(finish="stop"),
            ]),
        ]
        factory, created = self.factory(responses)
        provider = LocalLFMProvider(connection_factory=factory)

        self.assertEqual(provider.agent_decision(request="first"), operation)
        self.assertEqual(provider.agent_decision(request="second"), final)
        parsed_operation = AgentDecision.parse(operation)
        parsed_final = AgentDecision.parse(final)
        self.assertEqual(parsed_operation.tool, operation["tool"])
        self.assertEqual(parsed_operation.arguments, operation["arguments"])
        self.assertEqual(parsed_final.answer, final["answer"])

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts/agent-decision.v4.schema.json").read_bytes()
        )
        generation_schema = json.loads(json.dumps(schema))
        del generation_schema["oneOf"][1]["properties"]["answer"]["maxLength"]
        for connection in created:
            payload = json.loads(connection.requests[0][2])
            self.assertEqual(set(payload), AGENT_DECISION_ALLOWED_PAYLOAD_FIELDS)
            self.assertEqual(payload["response_format"], {
                "type": "json_schema",
                "json_schema": {
                    "name": "voice_agent_agent_decision_v4",
                    "strict": True,
                    "schema": generation_schema,
                },
            })
        generated = json.loads(
            created[0].requests[0][2]
        )["response_format"]["json_schema"]["schema"]
        self.assertEqual(
            semantic_difference_paths(schema, generated),
            {("oneOf", 1, "properties", "answer", "maxLength")},
        )
        self.assertEqual(schema["oneOf"][1]["properties"]["answer"]["maxLength"], 2_000)
        self.assertEqual(generated.get("$schema"), schema["$schema"])
        self.assertEqual(generated.get("$id"), schema["$id"])
        self.assertEqual(generated["oneOf"][0], schema["oneOf"][0])
        self.assertEqual(
            generated["oneOf"][1]["properties"]["citations"],
            schema["oneOf"][1]["properties"]["citations"],
        )
        variants = generated["oneOf"]
        self.assertEqual({variant["properties"]["kind"]["const"] for variant in variants}, {"operation", "final"})
        self.assertTrue(all(variant["additionalProperties"] is False for variant in variants))
        self.assertEqual(tuple(variants[0]["properties"]["tool"]["enum"]), AGENT_TOOLS)

    def test_malformed_agent_decision_is_turn_local_and_next_request_stays_ready(self) -> None:
        marker = "PRIVATE_MALFORMED_DECISION_7391"
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        malformed = StubResponse([
            stream_event(content=marker),
            stream_event(finish="stop"),
        ])
        final = {"kind": "final", "answer": "Следующий ход готов."}
        recovered = StubResponse([
            stream_event(content=json.dumps(final, ensure_ascii=False)),
            stream_event(finish="stop"),
        ])
        factory, created = self.factory([health, malformed, recovered])
        provider = LocalLFMProvider(connection_factory=factory)
        provider.readiness()
        ready_health = provider.runtime_health

        with self.assertRaises(StageFailure) as raised:
            provider.agent_decision(request="malformed-request")

        self.assertEqual(raised.exception.code, "agent_decision_invalid")
        self.assertEqual(str(raised.exception), "agent_decision_invalid")
        self.assertNotIn(marker, str(raised.exception))
        self.assertEqual(provider.runtime_health, ready_health)
        self.assertEqual(
            provider.agent_decision(request="fresh-independent-request"), final
        )
        self.assertEqual(provider.runtime_health, {
            "live": True,
            "ready": True,
            "compatible": True,
            "reason_code": None,
        })
        self.assertEqual(len(created), 3)

    def test_empty_and_incomplete_decision_streams_are_turn_local_and_ready(self) -> None:
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        responses = (
            StubResponse([stream_event(finish="stop")]),
            StubResponse([
                stream_event(content='{"kind":"final","answer":"partial"}'),
                stream_event(finish="length"),
            ]),
        )
        factory, _created = self.factory([health, *responses])
        provider = LocalLFMProvider(connection_factory=factory)
        provider.readiness()
        ready_health = provider.runtime_health

        for request in ("empty-decision", "incomplete-decision"):
            with self.subTest(request=request), self.assertRaises(StageFailure) as raised:
                provider.agent_decision(request=request)
            self.assertEqual(raised.exception.code, "agent_decision_invalid")
            self.assertEqual(provider.runtime_health, ready_health)

    def test_semantically_invalid_agent_decisions_remain_turn_local_and_ready(self) -> None:
        cases = (
            ("empty-answer", {"kind": "final", "answer": ""}, "agent_decision_invalid"),
            ("oversized-answer", {"kind": "final", "answer": "я" * 1_001}, "agent_decision_invalid"),
            ("unknown-field", {"kind": "final", "answer": "ok", "extra": True}, "agent_decision_invalid"),
            ("unknown-tool", {"kind": "operation", "tool": "unknown", "arguments": {}}, "agent_decision_invalid"),
            (
                "invalid-citation",
                {
                    "kind": "final",
                    "answer": "ok",
                    "citations": [{"receipt_id": "bad", "claims": ["ok"], "spans": ["ok"]}],
                },
                "research_citation_invalid",
            ),
        )
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        responses = [health, *(
            StubResponse([
                stream_event(content=json.dumps(document, ensure_ascii=False)),
                stream_event(finish="stop"),
            ])
            for _name, document, _code in cases
        )]
        factory, _created = self.factory(responses)
        provider = LocalLFMProvider(connection_factory=factory)
        provider.readiness()
        ready_health = provider.runtime_health

        for name, _document, code in cases:
            with self.subTest(name=name), self.assertRaises(StageFailure) as raised:
                AgentDecision.parse(provider.agent_decision(request=f"case-{name}"))
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(provider.runtime_health, ready_health)

    def test_agent_decision_identity_and_protocol_failures_remain_global(self) -> None:
        cases = (
            (
                StubResponse([
                    stream_event(content='{"kind":"final","answer":"x"}', model="wrong-model"),
                    stream_event(finish="stop", model="wrong-model"),
                ]),
                "selected_provider_identity_mismatch",
            ),
            (
                StubResponse([
                    {"model": MODEL_ALIAS, "choices": "invalid"},
                ]),
                "selected_provider_protocol_error",
            ),
        )
        for response, code in cases:
            with self.subTest(code=code):
                factory, _created = self.factory([response])
                provider = LocalLFMProvider(connection_factory=factory)
                provider._set_runtime_health(
                    live=True, ready=True, compatible=True, reason_code=None,
                )

                with self.assertRaises(StageFailure) as raised:
                    provider.agent_decision(request="global-failure")

                self.assertEqual(raised.exception.code, code)
                self.assertEqual(provider.runtime_health, {
                    "live": True,
                    "ready": False,
                    "compatible": False,
                    "reason_code": code,
                })

    def test_agent_decision_transport_failure_remains_global(self) -> None:
        class TransportFailureConnection(StubConnection):
            def request(self, method: str, path: str, body=None, headers=None) -> None:
                raise OSError("private transport detail")

        def factory(host: str, port: int, *, timeout: float):
            return TransportFailureConnection(
                host, port, timeout=timeout, responses=[],
            )

        provider = LocalLFMProvider(connection_factory=factory)
        provider._set_runtime_health(
            live=True, ready=True, compatible=True, reason_code=None,
        )

        with self.assertRaises(StageFailure) as raised:
            provider.agent_decision(request="transport-failure")

        self.assertEqual(raised.exception.code, "local_lfm_transport_error")
        self.assertEqual(str(raised.exception), "local_lfm_transport_error")
        self.assertEqual(provider.runtime_health, {
            "live": False,
            "ready": False,
            "compatible": True,
            "reason_code": "local_lfm_transport_error",
        })

    def test_structured_output_rejection_is_stable_global_compatibility_failure(self) -> None:
        health = StubResponse([])
        health._body = b'{"status":"ok"}'
        health.read = lambda limit=None: health._body
        factory, _created = self.factory([health, StubResponse([], status=400)])
        provider = LocalLFMProvider(connection_factory=factory)
        provider.readiness()

        with self.assertRaises(StageFailure) as raised:
            provider.agent_decision(request="requires-structured-output")

        self.assertEqual(
            raised.exception.code, "agent_decision_structured_output_unsupported"
        )
        self.assertEqual(provider.runtime_health, {
            "live": True,
            "ready": False,
            "compatible": False,
            "reason_code": "agent_decision_structured_output_unsupported",
        })

    def test_hidden_reasoning_never_reaches_handoff_or_response(self) -> None:
        response = StubResponse([
            stream_event(reasoning="Скрытое рассуждение."),
            stream_event(content="Первый видимый ответ. "),
            stream_event(content="Второй видимый ответ!"),
            stream_event(finish="stop"),
            {"model": MODEL_ALIAS, "choices": [], "usage": {"total_tokens": 12}},
        ])
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        handed_off: list[str] = []
        text = provider.respond_with_handoff(
            session_id="session-a",
            turn_id="turn-a",
            transcript="Публичный запрос",
            on_sentence=handed_off.append,
        )
        self.assertEqual(text, "Первый видимый ответ. Второй видимый ответ!")
        self.assertEqual(
            handed_off, ["Первый видимый ответ.", "Второй видимый ответ!"]
        )
        self.assertNotIn("Скрытое", text + "".join(handed_off))
        self.assertFalse(provider.observations[-1]["external_transfer"])
        self.assertGreater(provider.observations[-1]["reasoning_chars"], 0)
        self.assertLessEqual(
            provider.observations[-1]["provider_first_token_ms"],
            provider.observations[-1]["visible_first_content_ms"],
        )

    def test_decimal_split_across_stream_events_stays_exact_for_visibility_and_handoff(self) -> None:
        response = StubResponse([
            stream_event(content="Значение равно 3."),
            stream_event(content="14 и не меняется."),
            stream_event(finish="stop"),
        ])
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        handed_off: list[str] = []
        visible: list[str] = []

        text = provider.respond_with_handoff(
            session_id="session-a",
            turn_id="turn-a",
            transcript="Публичный запрос",
            on_sentence=handed_off.append,
            on_visible_sentence=visible.append,
        )

        self.assertEqual(text, "Значение равно 3.14 и не меняется.")
        self.assertEqual(handed_off, [text])
        self.assertEqual(visible[-1], text)
        self.assertTrue(all(text.startswith(prefix) for prefix in visible))

    def test_visible_stream_and_final_preserve_original_boundary_whitespace(self) -> None:
        response = StubResponse([
            stream_event(content=" Привет."),
            stream_event(content=" "),
            stream_event(finish="stop"),
        ])
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        visible: list[str] = []
        handed_off: list[str] = []

        text = provider.respond_with_handoff(
            session_id="session-a",
            turn_id="turn-a",
            transcript="Публичный запрос",
            on_sentence=handed_off.append,
            on_visible_sentence=visible.append,
        )

        self.assertEqual(text, " Привет. ")
        self.assertEqual(visible[-1], text)
        self.assertTrue(all(text.startswith(prefix) for prefix in visible))
        self.assertEqual(handed_off, ["Привет."])
        self.assertEqual(provider.snapshot_session("session-a")[-1]["content"], text)

    def test_handoff_uses_its_own_deadline_after_stream_completion(self) -> None:
        response = StubResponse([
            stream_event(content="Готовый ответ."),
            stream_event(finish="stop"),
        ])
        factory, created = self.factory([response])
        provider = LocalLFMProvider(
            connection_factory=factory, request_timeout_seconds=0.05
        )
        callback_started = threading.Event()

        def handoff(sentence: str) -> None:
            callback_started.set()
            time.sleep(0.08)
            self.assertEqual(sentence, "Готовый ответ.")

        started = time.monotonic()
        text = provider.respond_with_handoff(
            session_id="session-a",
            turn_id="turn-a",
            transcript="Публичный запрос",
            on_sentence=handoff,
        )

        self.assertEqual(text, "Готовый ответ.")
        self.assertGreaterEqual(time.monotonic() - started, 0.08)
        self.assertTrue(callback_started.is_set())
        self.assertTrue(created[0].closed)
        self.assertTrue(provider.observations[-1]["success"])
        self.assertLess(provider.observations[-1]["completion_ms"], 50)

    def test_cancel_after_stream_completion_detaches_blocked_final_handoff(self) -> None:
        response = StubResponse([
            stream_event(content="Готовый ответ."),
            stream_event(finish="stop"),
        ])
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        cancellation = CancellationToken()
        callback_started = threading.Event()
        callback_release = threading.Event()
        abort_seen = threading.Event()
        failures: list[BaseException] = []

        def handoff(_sentence: str) -> None:
            callback_started.set()
            callback_release.wait(2)

        def respond() -> None:
            try:
                provider.respond_with_handoff(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                    on_sentence=handoff,
                    on_handoff_abort=lambda: (abort_seen.set(), True)[1],
                    cancellation=cancellation,
                )
            except BaseException as error:
                failures.append(error)

        request = threading.Thread(target=respond)
        request.start()
        try:
            self.assertTrue(callback_started.wait(0.5))
            started = time.monotonic()
            cancellation.cancel()
            request.join(0.5)
            self.assertFalse(request.is_alive())
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(abort_seen.is_set())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], StageFailure)
            self.assertEqual(failures[0].code, "selected_provider_cancelled")
        finally:
            callback_release.set()
            request.join(1)

    def test_complete_sentence_handoff_occurs_before_stream_return(self) -> None:
        callback_seen = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Первое предложение."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(finish="stop"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: [DONE]\n",
        ]
        response = CallbackGatedResponse(lines, callback_seen)
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        handed_off: list[str] = []

        text = provider.respond_with_handoff(
            session_id="session-a",
            turn_id="turn-a",
            transcript="Публичный запрос",
            on_sentence=lambda sentence: (handed_off.append(sentence), callback_seen.set()),
        )

        self.assertEqual(text, "Первое предложение.")
        self.assertEqual(handed_off, ["Первое предложение."])
        self.assertTrue(callback_seen.is_set())

    def test_late_identity_failure_follows_streaming_handoff(self) -> None:
        callback_seen = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Буферизовать."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(content="Ошибка.", model="other"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
        ]
        response = CallbackGatedResponse(lines, callback_seen)
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        handed_off: list[str] = []

        with self.assertRaises(StageFailure) as raised:
            provider.respond_with_handoff(
                session_id="session-a",
                turn_id="turn-a",
                transcript="Публичный запрос",
                on_sentence=lambda sentence: (handed_off.append(sentence), callback_seen.set()),
            )

        self.assertEqual(raised.exception.code, "selected_provider_identity_mismatch")
        self.assertEqual(handed_off, ["Буферизовать."])

    def test_late_failure_cancels_blocked_handoff_before_bounded_join(self) -> None:
        callback_seen = threading.Event()
        callback_release = threading.Event()
        abort_seen = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Буферизовать."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(content="Ошибка.", model="other"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
        ]
        response = CallbackGatedResponse(lines, callback_seen)
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)

        def handoff(_sentence: str) -> None:
            callback_seen.set()
            callback_release.wait(2)

        def abort() -> None:
            abort_seen.set()
            callback_release.set()

        started = time.monotonic()
        with self.assertRaises(StageFailure) as raised:
            provider.respond_with_handoff(
                session_id="session-a",
                turn_id="turn-a",
                transcript="Публичный запрос",
                on_sentence=handoff,
                on_handoff_abort=abort,
            )

        self.assertEqual(raised.exception.code, "selected_provider_identity_mismatch")
        self.assertTrue(abort_seen.is_set())
        self.assertLess(time.monotonic() - started, 0.5)

    def test_noncooperative_handoff_drain_does_not_block_lfm_replacement(self) -> None:
        callback_seen = threading.Event()
        callback_release = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Буферизовать."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(content="Ошибка.", model="other"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
        ]
        responses = [
            CallbackGatedResponse(lines, callback_seen),
            StubResponse([
                stream_event(content="Новый ответ."),
                stream_event(finish="stop"),
            ]),
        ]
        factory, _created = self.factory(responses)
        provider = LocalLFMProvider(connection_factory=factory)

        def blocked_handoff(_sentence: str) -> None:
            callback_seen.set()
            callback_release.wait(1)

        try:
            started = time.monotonic()
            with self.assertRaises(StageFailure) as failure:
                provider.respond_with_handoff(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                    on_sentence=blocked_handoff,
                    on_handoff_abort=lambda: True,
                )
            self.assertEqual(failure.exception.code, "selected_provider_identity_mismatch")
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(
                provider.respond(
                    session_id="session-b", turn_id="turn-b", transcript="Запрос"
                ),
                "Новый ответ.",
            )
            self.assertEqual(provider.handoff_capacity_state(), "available")
        finally:
            callback_release.set()

    def test_handoff_cleanup_allows_replacement_generation_to_use_second_slot(self) -> None:
        callback_seen = threading.Event()
        cleanup_entered = threading.Event()
        cleanup_release = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Буферизовать."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(content="Ошибка.", model="other"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
        ]
        responses = [
            CallbackGatedResponse(lines, callback_seen),
            StubResponse([
                stream_event(content="Восстановлено."),
                stream_event(finish="stop"),
            ]),
        ]
        factory, _created = self.factory(responses)
        provider = LocalLFMProvider(
            connection_factory=factory, handoff_cleanup_timeout_seconds=0.5
        )
        failures: list[str] = []

        def handoff(_sentence: str) -> None:
            callback_seen.set()
            cleanup_release.wait(1)

        def abort() -> None:
            cleanup_entered.set()
            cleanup_release.wait(1)

        def first_response() -> None:
            try:
                provider.respond_with_handoff(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                    on_sentence=handoff,
                    on_handoff_abort=abort,
                )
            except StageFailure as error:
                failures.append(error.code)

        worker = threading.Thread(target=first_response)
        worker.start()
        self.assertTrue(cleanup_entered.wait(0.5))
        self.assertEqual(provider.handoff_capacity_state(), "cleaning")
        self.assertEqual(
            provider.respond(
                session_id="session-b", turn_id="turn-b", transcript="Запрос"
            ),
            "Восстановлено.",
        )

        cleanup_release.set()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ["selected_provider_identity_mismatch"])
        self.assertEqual(provider.handoff_capacity_state(), "available")

    def test_handoff_cleanup_timeout_closes_provider_capacity(self) -> None:
        callback_seen = threading.Event()
        cleanup_release = threading.Event()
        lines = [
            b"data: " + json.dumps(
                stream_event(content="Буферизовать."), ensure_ascii=False
            ).encode("utf-8") + b"\n",
            b"data: " + json.dumps(
                stream_event(content="Ошибка.", model="other"), ensure_ascii=False
            ).encode("utf-8") + b"\n",
        ]
        response = CallbackGatedResponse(lines, callback_seen)
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(
            connection_factory=factory, handoff_cleanup_timeout_seconds=0.05
        )

        def handoff(_sentence: str) -> None:
            callback_seen.set()
            cleanup_release.wait(1)

        try:
            with self.assertRaises(StageFailure) as late_failure:
                provider.respond_with_handoff(
                    session_id="session-a",
                    turn_id="turn-a",
                    transcript="Публичный запрос",
                    on_sentence=handoff,
                    on_handoff_abort=lambda: cleanup_release.wait(1),
                )
            self.assertEqual(
                late_failure.exception.code,
                "local_lfm_handoff_cleanup_failed",
            )
            self.assertEqual(provider.handoff_capacity_state(), "unavailable")
            with self.assertRaises(StageFailure) as unavailable:
                provider.respond(
                    session_id="session-b", turn_id="turn-b", transcript="Запрос"
                )
            self.assertEqual(
                unavailable.exception.code, "local_lfm_handoff_cleanup_failed"
            )
        finally:
            cleanup_release.set()
        time.sleep(0.02)
        self.assertEqual(provider.handoff_capacity_state(), "unavailable")

    def test_handoff_waits_for_complete_identity_validation(self) -> None:
        response = StubResponse([
            stream_event(content="Не выдавать заранее."),
            stream_event(content="Ошибка.", model="other"),
            stream_event(finish="stop"),
        ])
        factory, _created = self.factory([response])
        provider = LocalLFMProvider(connection_factory=factory)
        handed_off: list[str] = []

        with self.assertRaises(StageFailure) as raised:
            provider.respond_with_handoff(
                session_id="session-a",
                turn_id="turn-a",
                transcript="Публичный запрос",
                on_sentence=handed_off.append,
            )

        self.assertEqual(raised.exception.code, "selected_provider_identity_mismatch")
        self.assertTrue(set(handed_off).issubset({"Не выдавать заранее.", "Ошибка."}))

    def test_callback_failure_remains_owned_by_downstream_stage(self) -> None:
        response = StubResponse([
            stream_event(content="Готовый ответ."),
            stream_event(finish="stop"),
        ])
        factory, created = self.factory([response])
        provider = LocalLFMProvider(
            connection_factory=factory, request_timeout_seconds=0.05
        )
        downstream = StageFailure("tts", "selected_tts_output_out_of_bounds")

        with self.assertRaises(StageFailure) as raised:
            provider.respond_with_handoff(
                session_id="session-a",
                turn_id="turn-a",
                transcript="Публичный запрос",
                on_sentence=lambda _sentence: (_ for _ in ()).throw(downstream),
            )

        self.assertIs(raised.exception, downstream)
        self.assertEqual(raised.exception.stage, "tts")
        self.assertTrue(created[0].closed)

    def test_context_is_bounded_isolated_and_rollbackable(self) -> None:
        responses = []
        for answer in ("Ответ А.", "Ответ Б.", "Ответ А2."):
            responses.append(StubResponse([
                stream_event(content=answer),
                stream_event(finish="stop"),
            ]))
        factory, created = self.factory(responses)
        provider = LocalLFMProvider(connection_factory=factory)
        provider.respond(session_id="session-a", turn_id="turn-a", transcript="Запрос А")
        snapshot = provider.snapshot_session("session-a")
        provider.respond(session_id="session-b", turn_id="turn-b", transcript="Запрос Б")
        provider.respond(session_id="session-a", turn_id="turn-a2", transcript="Запрос А2")

        payload_b = json.loads(created[1].requests[0][2])
        payload_a2 = json.loads(created[2].requests[0][2])
        self.assertNotIn("Запрос А", json.dumps(payload_b, ensure_ascii=False))
        self.assertIn("Запрос А", json.dumps(payload_a2, ensure_ascii=False))
        provider.restore_session("session-a", snapshot)
        self.assertEqual(provider.snapshot_session("session-a"), snapshot)

    def test_identity_finish_empty_and_visible_limit_fail_closed_without_fallback(self) -> None:
        cases = (
            ([stream_event(content="Ответ.", model="other"), stream_event(finish="stop")], "selected_provider_identity_mismatch"),
            ([stream_event(reasoning="только скрыто"), stream_event(finish="stop")], "empty_selected_provider_response"),
            ([stream_event(content="x" * 501), stream_event(finish="stop")], "selected_provider_output_out_of_bounds"),
            ([stream_event(content="Ответ."), stream_event(finish="length")], "local_lfm_incomplete_response"),
        )
        for events, code in cases:
            with self.subTest(code=code):
                factory, _created = self.factory([StubResponse(events)])
                provider = LocalLFMProvider(connection_factory=factory)
                with self.assertRaises(StageFailure) as raised:
                    provider.respond(
                        session_id="session-a", turn_id="turn-a", transcript="Запрос"
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(provider.provider_identity, PROVIDER_IDENTITY)
                self.assertFalse(provider.observations[-1]["success"])

    def test_pre_cancelled_request_never_opens_transport(self) -> None:
        opened = False

        def factory(*_args, **_kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("transport must not open")

        token = CancellationToken()
        token.cancel()
        provider = LocalLFMProvider(connection_factory=factory)
        with self.assertRaises(StageFailure) as raised:
            provider.respond(
                session_id="session-a", turn_id="turn-a", transcript="Запрос",
                cancellation=token,
            )
        self.assertEqual(raised.exception.code, "selected_provider_cancelled")
        self.assertFalse(opened)

    def test_active_request_wait_is_synchronized_with_cancellation(self) -> None:
        created: list[DeadlineConnection] = []

        def factory(host: str, port: int, *, timeout: float):
            connection = DeadlineConnection(
                host, port, timeout=timeout, responses=[], block_headers=True
            )
            created.append(connection)
            return connection

        provider = LocalLFMProvider(
            connection_factory=factory, request_timeout_seconds=1
        )
        cancellation = CancellationToken()
        failures: list[str] = []

        def respond() -> None:
            try:
                provider.respond(
                    session_id="session-a", turn_id="turn-a", transcript="Запрос",
                    cancellation=cancellation,
                )
            except StageFailure as error:
                failures.append(error.code)

        worker = threading.Thread(target=respond)
        worker.start()
        self.assertTrue(provider.wait_for_active_request(0.5))
        cancellation.cancel()
        worker.join(0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ["selected_provider_cancelled"])
        self.assertTrue(created[0].closed)
        self.assertFalse(provider.wait_for_active_request(0))

    def test_whole_request_deadline_closes_delayed_headers(self) -> None:
        created: list[DeadlineConnection] = []

        def factory(host: str, port: int, *, timeout: float):
            connection = DeadlineConnection(
                host, port, timeout=timeout, responses=[], block_headers=True
            )
            created.append(connection)
            return connection

        provider = LocalLFMProvider(
            connection_factory=factory, request_timeout_seconds=0.05
        )
        started = time.monotonic()
        with self.assertRaises(StageFailure) as raised:
            provider.respond(
                session_id="session-a", turn_id="turn-a", transcript="Запрос"
            )

        self.assertEqual(raised.exception.code, "local_lfm_request_timeout")
        self.assertFalse(provider.runtime_live)
        self.assertFalse(provider.runtime_health["ready"])
        self.assertTrue(provider.runtime_health["compatible"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(created[0].closed)

    def test_whole_request_deadline_closes_delayed_error_body(self) -> None:
        response = BlockingReadResponse([], status=503)
        created: list[DeadlineConnection] = []

        def factory(host: str, port: int, *, timeout: float):
            connection = DeadlineConnection(
                host, port, timeout=timeout, responses=[response]
            )
            created.append(connection)
            return connection

        provider = LocalLFMProvider(
            connection_factory=factory, request_timeout_seconds=0.05
        )
        started = time.monotonic()
        with self.assertRaises(StageFailure) as raised:
            provider.respond(
                session_id="session-a", turn_id="turn-a", transcript="Запрос"
            )

        self.assertEqual(raised.exception.code, "local_lfm_request_timeout")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()

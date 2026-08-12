from __future__ import annotations

from io import BytesIO
import json
import threading
import time
import unittest

from voice_agent_v2.contracts import StageFailure
from voice_agent_v2.local_lfm import (
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
        self.assertEqual(created_response[0].requests[0][:2], ("GET", "/health"))

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

    def test_handoff_cleanup_blocks_overlapping_generation_until_confirmed(self) -> None:
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
        with self.assertRaises(StageFailure) as pending:
            provider.respond(
                session_id="session-b", turn_id="turn-b", transcript="Запрос"
            )
        self.assertEqual(pending.exception.code, "local_lfm_handoff_cleanup_pending")

        cleanup_release.set()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ["selected_provider_identity_mismatch"])
        self.assertEqual(provider.handoff_capacity_state(), "available")
        self.assertEqual(
            provider.respond(
                session_id="session-b", turn_id="turn-b", transcript="Запрос"
            ),
            "Восстановлено.",
        )

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
                "selected_provider_identity_mismatch",
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

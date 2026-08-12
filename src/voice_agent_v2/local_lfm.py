"""Pinned loopback-only llama.cpp adapter for local LFM2.5 Q4_K_M."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import queue
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .contracts import LLM_VERSION, StageFailure, valid_correlation_id
from .tracer import CancellationToken

LLAMA_ENDPOINT = "http://127.0.0.1:18080"
MODEL_ALIAS = "lfm2.5-2.6b-q4-k-m"
PROVIDER_IDENTITY = "LiquidAI/LFM2.5-2.6B-GGUF@b421ad1d549afeda6a0fb2ad3a697cb5a7879adc#Q4_K_M"
SYSTEM_PROMPT = (
    "Ты голосовой помощник. Отвечай по-русски, естественно и полезно. "
    "Дай один короткий ответ: обычно 1–3 предложения и не более 500 видимых символов. "
    "Сначала кратко обдумай ответ скрыто, затем обязательно дай видимый ответ. "
    "Не раскрывай рассуждения, не используй Markdown, списки или служебные маркеры."
)
MAX_CONTEXT_MESSAGES = 4
MAX_VISIBLE_CHARS = 500
MAX_VISIBLE_BYTES = 2_000
MAX_REASONING_CHARS = 16_384
MAX_STREAM_EVENTS = 1_024
MAX_STREAM_LINE_BYTES = 65_536
REQUEST_TIMEOUT_SECONDS = 20.0
READINESS_TIMEOUT_SECONDS = 3.0
MAX_TOKENS = 768
REASONING_BUDGET = 384
HANDOFF_CLEANUP_TIMEOUT_SECONDS = 6.0
ALLOWED_PAYLOAD_FIELDS = frozenset({
    "model", "messages", "stream", "stream_options", "temperature", "top_p",
    "top_k", "repeat_penalty", "max_tokens", "reasoning_format", "reasoning_budget",
    "cache_prompt", "timings",
})


@dataclass(frozen=True)
class LocalLFMObservation:
    success: bool
    error_class: str | None
    visible_first_content_ms: float | None
    completion_ms: float
    reasoning_chars: int
    visible_chars: int


def _complete_visible_sentences(text: str, start: int) -> tuple[tuple[str, ...], int]:
    chunks: list[str] = []
    cursor = start
    for index in range(start, len(text)):
        if text[index] not in ".!?。！？":
            continue
        chunk = text[cursor:index + 1].strip()
        if chunk:
            chunks.append(chunk)
        cursor = index + 1
    return tuple(chunks), cursor


class LocalLFMProvider:
    """One fixed local provider; no credentials, endpoint choice, alias, or fallback."""

    version = LLM_VERSION
    provider_mode = "local"
    provider_identity = PROVIDER_IDENTITY
    supports_handoff_abort = True

    def __init__(
        self,
        *,
        connection_factory: Callable[..., http.client.HTTPConnection] = http.client.HTTPConnection,
        request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        handoff_cleanup_timeout_seconds: float = HANDOFF_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if handoff_cleanup_timeout_seconds <= 0:
            raise ValueError("handoff_cleanup_timeout_seconds must be positive")
        parsed = urlsplit(LLAMA_ENDPOINT)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 18080:
            raise AssertionError("local LFM endpoint invariant changed")
        self._host = parsed.hostname
        self._port = parsed.port
        self._connection_factory = connection_factory
        self._request_timeout_seconds = request_timeout_seconds
        self._handoff_cleanup_timeout_seconds = handoff_cleanup_timeout_seconds
        self._contexts: dict[str, list[dict[str, str]]] = {}
        self._context_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._operation_changed = threading.Condition(self._operation_lock)
        self._operation_generation = 0
        self._cancelled_generations: set[int] = set()
        self._connections: dict[int, http.client.HTTPConnection] = {}
        self._handoff_cleanup_generations: set[int] = set()
        self._handoff_capacity_error: str | None = None
        self.observations: list[dict[str, object]] = []

    def _begin_operation(self, cancellation: CancellationToken | None) -> int:
        with self._operation_lock:
            if self._handoff_capacity_error is not None:
                raise StageFailure("llm_provider", self._handoff_capacity_error)
            if self._handoff_cleanup_generations:
                raise StageFailure("llm_provider", "local_lfm_handoff_cleanup_pending")
            self._operation_generation += 1
            generation = self._operation_generation
            if cancellation is not None and cancellation.cancelled:
                self._cancelled_generations.add(generation)
            return generation

    def _cancelled(self, generation: int) -> bool:
        with self._operation_lock:
            return generation in self._cancelled_generations

    def _register_connection(self, generation: int, timeout: float) -> http.client.HTTPConnection:
        if self._cancelled(generation):
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        connection = self._connection_factory(self._host, self._port, timeout=timeout)
        with self._operation_changed:
            self._connections[generation] = connection
            cancelled = generation in self._cancelled_generations
            self._operation_changed.notify_all()
        if cancelled:
            connection.close()
            with self._operation_lock:
                if self._connections.get(generation) is connection:
                    self._connections.pop(generation, None)
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        return connection

    def _release_connection(self, generation: int, connection: http.client.HTTPConnection) -> None:
        connection.close()
        with self._operation_changed:
            if self._connections.get(generation) is connection:
                self._connections.pop(generation, None)
                self._operation_changed.notify_all()

    def _cancel_operation(self, generation: int) -> None:
        with self._operation_lock:
            self._cancelled_generations.add(generation)
            connection = self._connections.get(generation)
        if connection is not None:
            connection.close()

    def readiness(self, cancellation: CancellationToken | None = None) -> dict[str, object]:
        generation = self._begin_operation(cancellation)
        connection = self._register_connection(generation, READINESS_TIMEOUT_SECONDS)
        try:
            connection.request("GET", "/health", headers={"Connection": "close"})
            response = connection.getresponse()
            body = response.read(4_097)
            if self._cancelled(generation):
                raise StageFailure("llm_provider", "selected_provider_cancelled")
            if response.status != 200 or len(body) > 4_096:
                raise StageFailure("llm_provider", "local_lfm_health_failed")
            document = json.loads(body)
            if not isinstance(document, dict) or document.get("status") != "ok":
                raise StageFailure("llm_provider", "local_lfm_health_failed")
        except StageFailure:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            code = "selected_provider_cancelled" if self._cancelled(generation) else "local_lfm_unavailable"
            raise StageFailure("llm_provider", code) from error
        finally:
            self._release_connection(generation, connection)
        return {
            "ready": True,
            "provider_mode": self.provider_mode,
            "provider_identity": self.provider_identity,
            "selected_alias": MODEL_ALIAS,
            "endpoint_scope": "loopback-only",
            "external_transfer": False,
            "automatic_fallback": False,
            "credentials_required": False,
            "parallel_slots": 2,
            "context_tokens_per_slot": 32_768,
        }

    def _payload(self, session_id: str, transcript: str) -> dict[str, object]:
        if not transcript.strip() or len(transcript) > 4_096:
            raise StageFailure("llm_provider", "transcript_out_of_bounds")
        with self._context_lock:
            history = list(self._contexts.get(session_id, ())) [-MAX_CONTEXT_MESSAGES:]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": transcript.strip()},
        ]
        payload: dict[str, object] = {
            "model": MODEL_ALIAS,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 40,
            "repeat_penalty": 1.05,
            "max_tokens": MAX_TOKENS,
            "reasoning_format": "deepseek",
            "reasoning_budget": REASONING_BUDGET,
            "cache_prompt": False,
            "timings": True,
        }
        if set(payload) != ALLOWED_PAYLOAD_FIELDS:
            raise StageFailure("llm_provider", "forbidden_request_field")
        return payload

    def _raise_if_operation_stopped(
        self,
        generation: int,
        deadline: float,
        deadline_expired: threading.Event,
    ) -> None:
        if self._cancelled(generation):
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        if deadline_expired.is_set() or time.monotonic() >= deadline:
            raise StageFailure("llm_provider", "local_lfm_request_timeout")

    def _apply_deadline(
        self,
        connection: http.client.HTTPConnection,
        response: object | None,
        generation: int,
        deadline: float,
        deadline_expired: threading.Event,
    ) -> None:
        self._raise_if_operation_stopped(generation, deadline, deadline_expired)
        remaining = deadline - time.monotonic()
        connection.timeout = remaining
        sockets = [getattr(connection, "sock", None)]
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sockets.append(getattr(raw, "_sock", None))
        for sock in sockets:
            if sock is not None:
                sock.settimeout(remaining)

    def _execute(
        self,
        payload: dict[str, object],
        generation: int,
        started: float,
        deadline: float,
        handoff: Callable[[str], None] | None,
    ) -> dict[str, object]:
        connection = self._register_connection(
            generation, max(0.001, deadline - time.monotonic())
        )
        deadline_expired = threading.Event()

        def expire() -> None:
            deadline_expired.set()
            try:
                connection.close()
            except Exception:
                pass

        deadline_guard = threading.Timer(
            max(0.001, deadline - time.monotonic()), expire
        )
        deadline_guard.daemon = True
        visible: list[str] = []
        handoff_offset = 0
        visible_chars = visible_bytes = reasoning_chars = stream_events = 0
        visible_first: float | None = None
        finish_reason: str | None = None
        response_models: set[str] = set()
        usage: dict[str, int] = {}
        result: dict[str, object]
        deadline_guard.start()
        try:
            self._apply_deadline(
                connection, None, generation, deadline, deadline_expired
            )
            request_body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self._apply_deadline(
                connection, None, generation, deadline, deadline_expired
            )
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Connection": "close",
                },
            )
            self._apply_deadline(
                connection, None, generation, deadline, deadline_expired
            )
            response = connection.getresponse()
            self._apply_deadline(
                connection, response, generation, deadline, deadline_expired
            )
            if response.status != 200:
                response.read(MAX_STREAM_LINE_BYTES + 1)
                self._raise_if_operation_stopped(
                    generation, deadline, deadline_expired
                )
                raise StageFailure(
                    "llm_provider", f"local_lfm_http_{response.status}"
                )
            while True:
                self._apply_deadline(
                    connection, response, generation, deadline, deadline_expired
                )
                line = response.fp.readline(MAX_STREAM_LINE_BYTES + 1)
                self._raise_if_operation_stopped(
                    generation, deadline, deadline_expired
                )
                if not line:
                    break
                if len(line) > MAX_STREAM_LINE_BYTES:
                    raise StageFailure(
                        "llm_provider", "selected_provider_output_out_of_bounds"
                    )
                text = line.decode("utf-8").strip()
                self._raise_if_operation_stopped(
                    generation, deadline, deadline_expired
                )
                if not text.startswith("data: "):
                    continue
                if text == "data: [DONE]":
                    break
                stream_events += 1
                if stream_events > MAX_STREAM_EVENTS:
                    raise StageFailure(
                        "llm_provider", "selected_provider_output_out_of_bounds"
                    )
                event = json.loads(text[6:])
                self._raise_if_operation_stopped(
                    generation, deadline, deadline_expired
                )
                if not isinstance(event, dict):
                    raise StageFailure(
                        "llm_provider", "selected_provider_protocol_error"
                    )
                model = event.get("model")
                if model is not None:
                    if model != MODEL_ALIAS:
                        raise StageFailure(
                            "llm_provider", "selected_provider_identity_mismatch"
                        )
                    response_models.add(model)
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = raw_usage.get(key)
                        if isinstance(value, int) and value >= 0:
                            usage[key] = value
                choices = event.get("choices")
                if choices in (None, []):
                    if handoff is not None and MODEL_ALIAS in response_models:
                        sentences, handoff_offset = _complete_visible_sentences(
                            "".join(visible), handoff_offset
                        )
                        for sentence in sentences:
                            handoff(sentence)
                    continue
                if not isinstance(choices, list) or not isinstance(choices[0], dict):
                    raise StageFailure(
                        "llm_provider", "selected_provider_protocol_error"
                    )
                choice = choices[0]
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    raise StageFailure(
                        "llm_provider", "selected_provider_protocol_error"
                    )
                reasoning = delta.get("reasoning_content") or ""
                content = delta.get("content") or ""
                if not isinstance(reasoning, str) or not isinstance(content, str):
                    raise StageFailure(
                        "llm_provider", "selected_provider_protocol_error"
                    )
                reasoning_chars += len(reasoning)
                if reasoning_chars > MAX_REASONING_CHARS:
                    raise StageFailure(
                        "llm_provider", "selected_provider_output_out_of_bounds"
                    )
                if content:
                    visible_first = visible_first or time.monotonic()
                    visible_chars += len(content)
                    visible_bytes += len(content.encode("utf-8"))
                    if visible_chars > MAX_VISIBLE_CHARS or visible_bytes > MAX_VISIBLE_BYTES:
                        raise StageFailure(
                            "llm_provider", "selected_provider_output_out_of_bounds"
                        )
                    visible.append(content)
                if handoff is not None and MODEL_ALIAS in response_models:
                    sentences, handoff_offset = _complete_visible_sentences(
                        "".join(visible), handoff_offset
                    )
                    for sentence in sentences:
                        handoff(sentence)
                raw_finish = choice.get("finish_reason")
                if raw_finish is not None:
                    if raw_finish not in {"stop", "eos_token"}:
                        raise StageFailure(
                            "llm_provider", "local_lfm_incomplete_response"
                        )
                    finish_reason = raw_finish
                self._raise_if_operation_stopped(
                    generation, deadline, deadline_expired
                )
            output = "".join(visible).strip()
            if response_models != {MODEL_ALIAS}:
                raise StageFailure(
                    "llm_provider", "selected_provider_identity_mismatch"
                )
            if finish_reason not in {"stop", "eos_token"}:
                raise StageFailure("llm_provider", "local_lfm_incomplete_response")
            if not output:
                raise StageFailure(
                    "llm_provider", "empty_selected_provider_response"
                )
            self._raise_if_operation_stopped(
                generation, deadline, deadline_expired
            )
            if handoff is not None:
                remaining = "".join(visible)[handoff_offset:].strip()
                if remaining:
                    handoff(remaining)
            completed = time.monotonic()
            result = {
                "text": output,
                "visible_first_content_ms": (
                    (visible_first or completed) - started
                ) * 1_000,
                "completion_ms": (completed - started) * 1_000,
                "reasoning_chars": reasoning_chars,
                "usage": usage,
            }
        except StageFailure:
            self._raise_if_operation_stopped(
                generation, deadline, deadline_expired
            )
            raise
        except (
            OSError,
            TimeoutError,
            http.client.HTTPException,
            UnicodeError,
            json.JSONDecodeError,
        ) as error:
            if self._cancelled(generation):
                code = "selected_provider_cancelled"
            elif deadline_expired.is_set() or time.monotonic() >= deadline:
                code = "local_lfm_request_timeout"
            else:
                code = "local_lfm_transport_error"
            raise StageFailure("llm_provider", code) from error
        finally:
            deadline_guard.cancel()
            deadline_guard.join()
            self._release_connection(generation, connection)
        return result

    def _respond(
        self,
        *,
        session_id: str,
        turn_id: str,
        transcript: str,
        on_sentence: Callable[[str], None] | None,
        on_handoff_abort: Callable[[], None] | None,
        cancellation: CancellationToken | None,
    ) -> str:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("llm_provider", "invalid_correlation_id")
        generation = self._begin_operation(cancellation)
        started = time.monotonic()
        deadline = started + self._request_timeout_seconds
        unregister = (
            cancellation.register(lambda: self._cancel_operation(generation))
            if cancellation is not None
            else lambda: None
        )
        def record_failure(error: StageFailure) -> None:
            self.observations.append({
                "provider_mode": self.provider_mode,
                "provider_identity": self.provider_identity,
                "external_transfer": False,
                "success": False,
                "error_class": error.code,
                "completion_ms": (time.monotonic() - started) * 1_000,
            })

        handoff_queue: queue.Queue[str | None] | None = None
        handoff_thread: threading.Thread | None = None
        handoff_errors: list[BaseException] = []
        handoff_aborted = threading.Event()
        if on_sentence is not None:
            handoff_queue = queue.Queue(maxsize=MAX_STREAM_EVENTS)

            def deliver_handoffs() -> None:
                assert handoff_queue is not None
                while not handoff_aborted.is_set():
                    try:
                        sentence = handoff_queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if sentence is None:
                        return
                    try:
                        on_sentence(sentence)
                    except BaseException as error:
                        handoff_errors.append(error)
                        handoff_aborted.set()

            handoff_thread = threading.Thread(
                target=deliver_handoffs,
                name=f"local-lfm-handoff-{generation}",
                daemon=True,
            )
            handoff_thread.start()

        def enqueue_handoff(sentence: str) -> None:
            if handoff_queue is not None and not handoff_aborted.is_set():
                handoff_queue.put_nowait(sentence)

        handoff_finished = False

        def finish_handoffs() -> None:
            nonlocal handoff_finished
            if handoff_finished:
                return
            if handoff_aborted.is_set():
                abort_handoffs()
                return
            handoff_finished = True
            if handoff_queue is not None:
                handoff_queue.put(None)
            if handoff_thread is not None:
                handoff_thread.join()

        def abort_handoffs() -> None:
            nonlocal handoff_finished
            if handoff_finished:
                return
            handoff_finished = True
            with self._operation_lock:
                self._handoff_cleanup_generations.add(generation)
            handoff_aborted.set()
            if handoff_queue is not None:
                try:
                    handoff_queue.put_nowait(None)
                except queue.Full:
                    pass

            cleanup_errors: list[BaseException] = []

            def cleanup() -> None:
                try:
                    if on_handoff_abort is not None:
                        on_handoff_abort()
                    if handoff_thread is not None:
                        handoff_thread.join()
                except BaseException as error:
                    cleanup_errors.append(error)

            cleanup_thread = threading.Thread(
                target=cleanup,
                name=f"local-lfm-handoff-cleanup-{generation}",
                daemon=True,
            )
            cleanup_thread.start()
            cleanup_thread.join(self._handoff_cleanup_timeout_seconds)
            with self._operation_lock:
                if cleanup_thread.is_alive() or cleanup_errors:
                    self._handoff_capacity_error = "local_lfm_handoff_cleanup_failed"
                self._handoff_cleanup_generations.discard(generation)

        try:
            try:
                payload = self._payload(session_id, transcript)
                if self._cancelled(generation):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                if time.monotonic() >= deadline:
                    raise StageFailure("llm_provider", "local_lfm_request_timeout")
                result = self._execute(
                    payload, generation, started, deadline,
                    enqueue_handoff if on_sentence is not None else None,
                )
                text = result["text"]
                if not isinstance(text, str):
                    raise StageFailure(
                        "llm_provider", "selected_provider_protocol_error"
                    )
            except StageFailure as error:
                abort_handoffs()
                record_failure(error)
                raise
            except (
                OSError,
                TimeoutError,
                http.client.HTTPException,
                UnicodeError,
                json.JSONDecodeError,
            ) as cause:
                abort_handoffs()
                if self._cancelled(generation):
                    code = "selected_provider_cancelled"
                elif time.monotonic() >= deadline:
                    code = "local_lfm_request_timeout"
                else:
                    code = "local_lfm_transport_error"
                error = StageFailure("llm_provider", code)
                record_failure(error)
                raise error from cause
            finish_handoffs()
            if handoff_errors:
                raise handoff_errors[0]
            if self._cancelled(generation):
                error = StageFailure("llm_provider", "selected_provider_cancelled")
                record_failure(error)
                raise error
            with self._context_lock:
                context = list(self._contexts.get(session_id, ()))
                context.extend([
                    {"role": "user", "content": transcript.strip()},
                    {"role": "assistant", "content": text},
                ])
                self._contexts[session_id] = context[-MAX_CONTEXT_MESSAGES:]
            self.observations.append({
                "provider_mode": self.provider_mode,
                "provider_identity": self.provider_identity,
                "external_transfer": False,
                "success": True,
                "error_class": None,
                "visible_first_content_ms": result.get("visible_first_content_ms"),
                "completion_ms": result.get("completion_ms"),
                "reasoning_chars": result.get("reasoning_chars"),
                "visible_chars": len(text),
                "usage": result.get("usage", {}),
            })
            return text
        finally:
            if not handoff_finished:
                abort_handoffs()
            unregister()
            with self._operation_lock:
                self._cancelled_generations.discard(generation)

    def respond(
        self,
        *,
        session_id: str,
        turn_id: str,
        transcript: str,
        cancellation: CancellationToken | None = None,
    ) -> str:
        return self._respond(
            session_id=session_id,
            turn_id=turn_id,
            transcript=transcript,
            on_sentence=None,
            on_handoff_abort=None,
            cancellation=cancellation,
        )

    def respond_with_handoff(
        self,
        *,
        session_id: str,
        turn_id: str,
        transcript: str,
        on_sentence: Callable[[str], None],
        on_handoff_abort: Callable[[], None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> str:
        return self._respond(
            session_id=session_id,
            turn_id=turn_id,
            transcript=transcript,
            on_sentence=on_sentence,
            on_handoff_abort=on_handoff_abort,
            cancellation=cancellation,
        )

    def cancel(self) -> None:
        with self._operation_lock:
            generation = self._operation_generation
        self._cancel_operation(generation)

    def handoff_capacity_state(self) -> str:
        with self._operation_lock:
            if self._handoff_capacity_error is not None:
                return "unavailable"
            if self._handoff_cleanup_generations:
                return "cleaning"
            return "available"

    def wait_for_active_request(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        deadline = time.monotonic() + timeout_seconds
        with self._operation_changed:
            while not self._connections:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._operation_changed.wait(remaining)
            return True

    def snapshot_session(self, session_id: str) -> tuple[dict[str, str], ...]:
        with self._context_lock:
            return tuple(dict(message) for message in self._contexts.get(session_id, ()))

    def restore_session(self, session_id: str, snapshot: tuple[dict[str, str], ...]) -> None:
        with self._context_lock:
            if snapshot:
                self._contexts[session_id] = [dict(message) for message in snapshot]
            else:
                self._contexts.pop(session_id, None)

    def reset_session(self, session_id: str) -> None:
        with self._context_lock:
            self._contexts.pop(session_id, None)

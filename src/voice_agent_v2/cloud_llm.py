"""Single-endpoint, single-alias LiteLLM adapter with fail-closed content guards."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable

from .contracts import LLM_VERSION, StageFailure, valid_correlation_id

ENDPOINT = "http://rpi:4000"
HOST = "rpi"
PORT = 4000
ALIAS = "deepseek-v4-flash"
TOKEN_PATH = Path("/home/priney/.cache/voice-agent-v2/slice-2/secrets/litellm.token")
SYSTEM_PROMPT = "Отвечай по-русски, кратко, полезно и безопасно. Не раскрывай скрытые рассуждения."
ALLOWED_BODY_FIELDS = frozenset({"model", "messages", "temperature", "top_p", "max_tokens", "stream", "stream_options"})
ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
MAX_STREAM_EVENTS = 2048
MAX_STREAM_LINE_BYTES = 65_536
MAX_VISIBLE_CHARS = 8192
MAX_VISIBLE_BYTES = 32_768


class LiteLLMProvider:
    version = LLM_VERSION
    provider_mode = "cloud"
    provider_identity = "litellm/deepseek-v4-flash"

    def __init__(self, executor: Callable[[dict], dict] | None = None) -> None:
        self._executor = executor
        self._contexts: dict[str, list[dict[str, str]]] = {}
        self._cancelled = threading.Event()
        self._connection: http.client.HTTPConnection | None = None
        self.observations: list[dict] = []

    @staticmethod
    def _token() -> str:
        try:
            info = TOKEN_PATH.stat()
            if not TOKEN_PATH.is_file() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise StageFailure("llm_provider", "credential_file_invalid")
            value = TOKEN_PATH.read_text(encoding="utf-8").strip()
        except StageFailure:
            raise
        except (OSError, UnicodeError) as error:
            raise StageFailure("llm_provider", "credential_file_invalid") from error
        if not value:
            raise StageFailure("llm_provider", "credential_file_empty")
        return value

    @staticmethod
    def _capability_gate(token: str) -> None:
        connection = http.client.HTTPConnection(HOST, PORT, timeout=10)
        try:
            connection.request(
                "GET", "/v1/models",
                headers={"Authorization": "Bearer " + token, "Connection": "close", "Host": HOST},
            )
            response = connection.getresponse()
            body = response.read(MAX_STREAM_LINE_BYTES + 1)
            if response.status != 200:
                raise StageFailure("llm_provider", f"capability_http_{response.status}")
            if len(body) > MAX_STREAM_LINE_BYTES:
                raise StageFailure("llm_provider", "capability_response_out_of_bounds")
            document = json.loads(body)
            models = document.get("data") if isinstance(document, dict) else None
            if not isinstance(models, list) or not any(
                isinstance(model, dict) and model.get("id") == ALIAS for model in models
            ):
                raise StageFailure("llm_provider", "selected_alias_unavailable")
        except StageFailure:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            raise StageFailure("llm_provider", "capability_probe_unavailable") from error
        finally:
            connection.close()

    def readiness(self) -> dict:
        token = self._token()
        try:
            self._capability_gate(token)
        finally:
            del token
        return {
            "ready": True, "provider_mode": self.provider_mode,
            "provider_identity": self.provider_identity, "endpoint": ENDPOINT,
            "selected_alias": ALIAS, "external_transfer": True,
            "automatic_fallback": False, "redirects_followed": False,
            "authenticated_alias_capability": True,
            "runtime_network_proof_enforced": False,
            "transport_security": "temporary-operator-accepted-http",
        }

    def _payload(self, session_id: str, transcript: str) -> dict:
        if not transcript.strip() or len(transcript) > 4096:
            raise StageFailure("llm_provider", "transcript_out_of_bounds")
        history = list(self._contexts.get(session_id, []))[-4:]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": transcript}]
        payload = {
            "model": ALIAS, "messages": messages, "temperature": 0.1, "top_p": 0.9,
            "max_tokens": 512, "stream": True, "stream_options": {"include_usage": True},
        }
        if set(payload) != ALLOWED_BODY_FIELDS:
            raise StageFailure("llm_provider", "forbidden_request_field")
        if any(set(message) != {"role", "content"} or message["role"] not in ALLOWED_ROLES for message in messages):
            raise StageFailure("llm_provider", "forbidden_context_field")
        return payload

    def _execute(self, payload: dict, on_sentence: Callable[[str], None] | None = None) -> dict:
        token = self._token()
        connection = http.client.HTTPConnection(HOST, PORT, timeout=40)
        self._connection = connection
        submitted = time.monotonic()
        accepted = None
        visible: list[str] = []
        reasoning_events = 0
        raw_first = visible_first = None
        usage: dict[str, int] = {}
        response_models: set[str] = set()
        handoff_offset = 0
        stream_events = 0
        visible_chars = 0
        visible_bytes = 0
        try:
            connection.request(
                "POST", "/v1/chat/completions", body=json.dumps(payload).encode(),
                headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json",
                    "Accept": "text/event-stream", "Connection": "close", "Host": HOST,
                },
            )
            response = connection.getresponse()
            accepted = time.monotonic()
            if response.status != 200:
                response.read(65536)
                raise StageFailure("llm_provider", f"selected_provider_http_{response.status}")
            while True:
                if self._cancelled.is_set():
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                line = response.fp.readline(MAX_STREAM_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_STREAM_LINE_BYTES:
                    raise StageFailure("llm_provider", "selected_provider_output_out_of_bounds")
                stream_events += 1
                if stream_events > MAX_STREAM_EVENTS:
                    raise StageFailure("llm_provider", "selected_provider_output_out_of_bounds")
                text = line.decode("utf-8").strip()
                if not text.startswith("data: "):
                    continue
                if text == "data: [DONE]":
                    break
                event = json.loads(text[6:])
                if not isinstance(event, dict):
                    raise StageFailure("llm_provider", "selected_provider_protocol_error")
                now = time.monotonic()
                model = event.get("model")
                if model is not None:
                    if not isinstance(model, str) or len(model) > 512:
                        raise StageFailure("llm_provider", "selected_provider_protocol_error")
                    if model != ALIAS:
                        raise StageFailure("llm_provider", "selected_provider_identity_mismatch")
                    response_models.add(model)
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if isinstance(raw_usage.get(key), int) and raw_usage[key] >= 0:
                            usage[key] = raw_usage[key]
                choices = event.get("choices")
                if choices in (None, []):
                    continue
                if not isinstance(choices, list) or not isinstance(choices[0], dict):
                    raise StageFailure("llm_provider", "selected_provider_protocol_error")
                delta = choices[0].get("delta")
                if delta is None:
                    delta = {}
                if not isinstance(delta, dict):
                    raise StageFailure("llm_provider", "selected_provider_protocol_error")
                reasoning_piece = delta.get("reasoning", delta.get("reasoning_content"))
                content_piece = delta.get("content")
                if reasoning_piece is None:
                    reasoning_piece = ""
                if content_piece is None:
                    content_piece = ""
                if not isinstance(reasoning_piece, str) or not isinstance(content_piece, str):
                    raise StageFailure("llm_provider", "selected_provider_protocol_error")
                if reasoning_piece or content_piece:
                    raw_first = raw_first or now
                if reasoning_piece:
                    reasoning_events += 1
                if content_piece:
                    visible_chars += len(content_piece)
                    visible_bytes += len(content_piece.encode("utf-8"))
                    if visible_chars > MAX_VISIBLE_CHARS or visible_bytes > MAX_VISIBLE_BYTES:
                        raise StageFailure("llm_provider", "selected_provider_output_out_of_bounds")
                    visible_first = visible_first or now
                    visible.append(content_piece)
                    if on_sentence is not None and response_models == {ALIAS}:
                        current = "".join(visible)
                        punctuation = max((current.rfind(mark) for mark in (".", "!", "?", "。", "！", "？")), default=-1)
                        if punctuation >= handoff_offset:
                            sentence = current[handoff_offset:punctuation + 1].strip()
                            handoff_offset = punctuation + 1
                            if sentence:
                                on_sentence(sentence)
        except StageFailure:
            raise
        except (UnicodeError, json.JSONDecodeError, AttributeError, TypeError, IndexError) as error:
            raise StageFailure("llm_provider", "selected_provider_protocol_error") from error
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            code = "selected_provider_cancelled" if self._cancelled.is_set() else "selected_provider_transport_error"
            raise StageFailure("llm_provider", code) from error
        finally:
            completed = time.monotonic()
            connection.close()
            self._connection = None
            del token
        if response_models != {ALIAS}:
            raise StageFailure("llm_provider", "selected_provider_identity_mismatch")
        output = "".join(visible).strip()
        if on_sentence is not None and handoff_offset < len("".join(visible)):
            remaining = "".join(visible)[handoff_offset:].strip()
            if remaining:
                on_sentence(remaining)
        if not output:
            raise StageFailure("llm_provider", "empty_selected_provider_response")
        return {
            "text": output,
            "http_acceptance_ms": ((accepted or completed) - submitted) * 1000,
            "raw_first_delta_ms": ((raw_first or completed) - submitted) * 1000,
            "visible_first_content_ms": ((visible_first or completed) - submitted) * 1000,
            "completion_ms": (completed - submitted) * 1000,
            "usage": usage,
            "response_models": sorted(response_models),
            "reasoning_event_count": reasoning_events,
        }

    def _respond(
        self, *, session_id: str, turn_id: str, transcript: str,
        on_sentence: Callable[[str], None] | None,
    ) -> str:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("llm_provider", "invalid_correlation_id")
        self._cancelled.clear()
        payload = self._payload(session_id, transcript)
        started = time.monotonic()
        try:
            if self._executor is None:
                result = self._execute(payload, on_sentence)
            else:
                result = self._executor(payload)
            if not isinstance(result, dict):
                raise StageFailure("llm_provider", "selected_provider_protocol_error")
            text = result.get("text")
            if not isinstance(text, str):
                raise StageFailure("llm_provider", "selected_provider_protocol_error")
            if not text.strip():
                raise StageFailure("llm_provider", "empty_selected_provider_response")
            if len(text) > MAX_VISIBLE_CHARS or len(text.encode("utf-8")) > MAX_VISIBLE_BYTES:
                raise StageFailure("llm_provider", "selected_provider_output_out_of_bounds")
            if self._executor is not None and on_sentence is not None:
                on_sentence(text.strip())
        except StageFailure as error:
            self.observations.append({
                "provider_mode": self.provider_mode, "provider_identity": self.provider_identity,
                "external_transfer": True, "success": False, "error_class": error.code,
                "latency_ms": (time.monotonic() - started) * 1000,
            })
            raise
        context = self._contexts.setdefault(session_id, [])
        context.extend([{"role": "user", "content": transcript}, {"role": "assistant", "content": text}])
        self._contexts[session_id] = context[-4:]
        self.observations.append({
            "provider_mode": self.provider_mode, "provider_identity": self.provider_identity,
            "external_transfer": True, "success": True, "error_class": None,
            "http_acceptance_ms": result.get("http_acceptance_ms"),
            "raw_first_delta_ms": result.get("raw_first_delta_ms"),
            "visible_first_content_ms": result.get("visible_first_content_ms"),
            "completion_ms": result.get("completion_ms"),
            "usage": result.get("usage", {}), "response_models": result.get("response_models", []),
        })
        return text.strip()

    def respond(self, *, session_id: str, turn_id: str, transcript: str) -> str:
        return self._respond(session_id=session_id, turn_id=turn_id, transcript=transcript, on_sentence=None)

    def respond_with_handoff(
        self, *, session_id: str, turn_id: str, transcript: str, on_sentence: Callable[[str], None]
    ) -> str:
        return self._respond(session_id=session_id, turn_id=turn_id, transcript=transcript, on_sentence=on_sentence)

    def cancel(self) -> None:
        self._cancelled.set()
        if self._connection is not None:
            self._connection.close()

    def reset_session(self, session_id: str) -> None:
        self._contexts.pop(session_id, None)

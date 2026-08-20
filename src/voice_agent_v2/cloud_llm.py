"""Single-endpoint, single-alias LiteLLM adapter with fail-closed content guards."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import http.client
import json
import multiprocessing
import os
from pathlib import Path
import socket
import stat
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .contracts import LLM_VERSION, StageFailure, valid_correlation_id
from .tracer import CancellationToken

BASE_URL_ENV = "LITELLM_BASE_URL"
ALIAS = "deepseek-v4-flash"
TOKEN_PATH = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "voice-agent" / "private" / "litellm.token"
SYSTEM_PROMPT = "Отвечай по-русски, кратко, полезно и безопасно. Не раскрывай скрытые рассуждения."
ALLOWED_BODY_FIELDS = frozenset({"model", "messages", "temperature", "top_p", "max_tokens", "stream", "stream_options"})
ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
MAX_STREAM_EVENTS = 2048
MAX_STREAM_LINE_BYTES = 65_536
MAX_VISIBLE_CHARS = 8192
MAX_VISIBLE_BYTES = 32_768
PROVIDER_STARTUP_TIMEOUT_SECONDS = 10.0
PROVIDER_REQUEST_TIMEOUT_SECONDS = 40.0
RESOLVER_SHUTDOWN_TIMEOUT_SECONDS = 0.25


def _resolve_provider_host(host: str, port: int, sender) -> None:
    try:
        sender.send((True, socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)))
    except BaseException as error:
        try:
            sender.send((False, type(error).__name__, str(error)))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


@dataclass(frozen=True)
class ProviderEndpoint:
    base_url: str
    scheme: str
    host: str
    port: int
    authority: str


def parse_provider_endpoint(value: str | None) -> ProviderEndpoint:
    """Parse the one required endpoint without defaults, aliases, paths, or redirects."""
    if value is None or not value or value != value.strip():
        raise StageFailure("llm_provider", "endpoint_configuration_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise StageFailure("llm_provider", "endpoint_configuration_invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise StageFailure("llm_provider", "endpoint_configuration_invalid")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    default_port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname
    display_host = f"[{host}]" if ":" in host else host
    authority = display_host if port == default_port else f"{display_host}:{port}"
    return ProviderEndpoint(
        base_url=f"{parsed.scheme}://{authority}",
        scheme=parsed.scheme,
        host=host,
        port=port,
        authority=authority,
    )


class _RegisteredTCPConnection:
    def __init__(
        self,
        *args,
        cancelled: Callable[[], bool],
        startup_timeout: float,
        resolver_target: Callable = _resolve_provider_host,
        operation_deadline: float | None = None,
        **kwargs,
    ) -> None:
        self._cancelled = cancelled
        self._startup_timeout = startup_timeout
        self._resolver_target = resolver_target
        self._operation_deadline = operation_deadline
        super().__init__(*args, **kwargs)

    def _startup_failure(self, deadline: float) -> OSError | None:
        if self._cancelled():
            return OSError(errno.ECANCELED, "provider connection cancelled")
        if time.monotonic() >= deadline:
            return OSError(errno.ETIMEDOUT, "provider connection startup timed out")
        return None

    def _resolve_addresses(self, deadline: float) -> list[tuple]:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        resolver = context.Process(
            target=self._resolver_target,
            args=(self.host, self.port, sender),
            name="voice-provider-resolver",
            daemon=True,
        )
        try:
            resolver.start()
            sender.close()
            while True:
                failure = self._startup_failure(deadline)
                if failure is not None:
                    raise failure
                if not receiver.poll(max(0.001, min(0.01, deadline - time.monotonic()))):
                    continue
                try:
                    result = receiver.recv()
                except EOFError as error:
                    raise OSError("provider endpoint resolution failed") from error
                if not isinstance(result, tuple) or not result or result[0] is not True:
                    detail = result[2] if isinstance(result, tuple) and len(result) > 2 else "resolver failed"
                    raise socket.gaierror(str(detail))
                addresses = result[1]
                if not isinstance(addresses, list):
                    raise OSError("provider endpoint resolver returned invalid addresses")
                return addresses
        finally:
            receiver.close()
            sender.close()
            if resolver.pid is not None:
                resolver.join(0)
                if resolver.is_alive():
                    resolver.terminate()
                    resolver.join(RESOLVER_SHUTDOWN_TIMEOUT_SECONDS)
                if resolver.is_alive():
                    resolver.kill()
                    resolver.join(RESOLVER_SHUTDOWN_TIMEOUT_SECONDS)
                if resolver.is_alive():
                    raise OSError("provider resolver could not be terminated")
                resolver.close()

    def _connect_registered_socket(self) -> float:
        sys.audit("http.client.connect", self, self.host, self.port)
        deadline = time.monotonic() + self._startup_timeout
        if self._operation_deadline is not None:
            deadline = min(deadline, self._operation_deadline)
        addresses = self._resolve_addresses(deadline)
        last_error: OSError | None = None
        for family, socktype, protocol, _canonical_name, address in addresses:
            failure = self._startup_failure(deadline)
            if failure is not None:
                raise failure
            candidate = socket.socket(family, socktype, protocol)
            self.sock = candidate
            try:
                candidate.settimeout(max(0.001, deadline - time.monotonic()))
                if self.source_address:
                    candidate.bind(self.source_address)
                failure = self._startup_failure(deadline)
                if failure is not None:
                    raise failure
                candidate.connect(address)
                failure = self._startup_failure(deadline)
                if failure is not None:
                    raise failure
                try:
                    candidate.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError as error:
                    if error.errno != errno.ENOPROTOOPT:
                        raise
                return deadline
            except OSError as error:
                last_error = error
                candidate.close()
                if self.sock is candidate:
                    self.sock = None
                failure = self._startup_failure(deadline)
                if failure is not None:
                    raise failure from error
        if last_error is not None:
            raise last_error
        raise OSError("provider endpoint did not resolve")


    def _remaining_operation_timeout(self) -> float:
        if self._operation_deadline is None:
            return self.timeout
        return max(0.001, min(self.timeout, self._operation_deadline - time.monotonic()))


class _RegisteredHTTPConnection(_RegisteredTCPConnection, http.client.HTTPConnection):
    def connect(self) -> None:
        self._connect_registered_socket()
        self.sock.settimeout(self._remaining_operation_timeout())


class _RegisteredHTTPSConnection(_RegisteredTCPConnection, http.client.HTTPSConnection):
    def connect(self) -> None:
        deadline = self._connect_registered_socket()
        server_hostname = self._tunnel_host or self.host
        if self._tunnel_host:
            self._tunnel()
        failure = self._startup_failure(deadline)
        if failure is not None:
            raise failure
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=server_hostname
        )
        failure = self._startup_failure(deadline)
        if failure is not None:
            raise failure
        self.sock.settimeout(self._remaining_operation_timeout())


class LiteLLMProvider:
    version = LLM_VERSION
    provider_mode = "cloud"
    provider_identity = "litellm/deepseek-v4-flash"

    def __init__(
        self,
        executor: Callable[[dict], dict] | None = None,
        *,
        base_url: str | None = None,
        token_path: Path = TOKEN_PATH,
        transport_start_timeout_seconds: float = PROVIDER_STARTUP_TIMEOUT_SECONDS,
        request_timeout_seconds: float = PROVIDER_REQUEST_TIMEOUT_SECONDS,
        resolver_target: Callable = _resolve_provider_host,
    ) -> None:
        if transport_start_timeout_seconds <= 0:
            raise ValueError("transport_start_timeout_seconds must be positive")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self._executor = executor
        self._configured_base_url = base_url
        self._token_path = token_path
        self._transport_start_timeout_seconds = transport_start_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._resolver_target = resolver_target
        self._contexts: dict[str, list[dict[str, str]]] = {}
        self._operation_lock = threading.Lock()
        self._operation_generation = 0
        self._cancelled_generation = 0
        self._connection_generation = 0
        self._connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None
        self.observations: list[dict] = []

    def _endpoint(self) -> ProviderEndpoint:
        value = self._configured_base_url
        if value is None:
            value = os.environ.get(BASE_URL_ENV)
        return parse_provider_endpoint(value)

    def _token(self) -> str:
        try:
            info = self._token_path.stat()
            if not self._token_path.is_file() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise StageFailure("llm_provider", "credential_file_invalid")
            value = self._token_path.read_text(encoding="utf-8").strip()
        except StageFailure:
            raise
        except (OSError, UnicodeError) as error:
            raise StageFailure("llm_provider", "credential_file_invalid") from error
        if not value:
            raise StageFailure("llm_provider", "credential_file_empty")
        return value

    def _connection_for(
        self,
        endpoint: ProviderEndpoint,
        timeout: float,
        cancelled: Callable[[], bool] = lambda: False,
        operation_deadline: float | None = None,
    ):
        connection_type = (
            _RegisteredHTTPSConnection
            if endpoint.scheme == "https"
            else _RegisteredHTTPConnection
        )
        return connection_type(
            endpoint.host,
            endpoint.port,
            timeout=timeout,
            cancelled=cancelled,
            startup_timeout=min(timeout, self._transport_start_timeout_seconds),
            resolver_target=self._resolver_target,
            operation_deadline=operation_deadline,
        )

    def _capability_gate(
        self, token: str, endpoint: ProviderEndpoint, generation: int
    ) -> None:
        timeout = self._transport_start_timeout_seconds
        deadline = time.monotonic() + timeout
        connection = self._connection_for(
            endpoint,
            timeout,
            cancelled=lambda: self._operation_cancelled(generation),
            operation_deadline=deadline,
        )
        with self._operation_lock:
            self._connection = connection
            self._connection_generation = generation
            cancelled = self._cancelled_generation == generation
        if cancelled:
            connection.close()
            with self._operation_lock:
                if self._connection_generation == generation:
                    self._connection = None
                    self._connection_generation = 0
            raise StageFailure("llm_provider", "capability_probe_cancelled")
        deadline_guard = threading.Timer(
            max(0.001, deadline - time.monotonic()), connection.close
        )
        deadline_guard.daemon = True
        deadline_guard.start()
        try:
            connection.request(
                "GET", "/v1/models",
                headers={"Authorization": "Bearer " + token, "Connection": "close", "Host": endpoint.authority},
            )
            if self._operation_cancelled(generation):
                raise StageFailure("llm_provider", "capability_probe_cancelled")
            response = connection.getresponse()
            if time.monotonic() >= deadline:
                raise StageFailure("llm_provider", "capability_probe_timeout")
            self._apply_response_deadline(response, deadline)
            body = response.read(MAX_STREAM_LINE_BYTES + 1)
            if time.monotonic() >= deadline:
                raise StageFailure("llm_provider", "capability_probe_timeout")
            if self._operation_cancelled(generation):
                raise StageFailure("llm_provider", "capability_probe_cancelled")
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
            code = (
                "capability_probe_cancelled"
                if self._operation_cancelled(generation)
                else "capability_probe_unavailable"
            )
            raise StageFailure("llm_provider", code) from error
        finally:
            deadline_guard.cancel()
            deadline_guard.join()
            connection.close()
            with self._operation_lock:
                if self._connection_generation == generation:
                    self._connection = None
                    self._connection_generation = 0

    def readiness(self, cancellation: CancellationToken | None = None) -> dict:
        endpoint = self._endpoint()
        token = self._token()
        generation = self._begin_operation(cancellation)
        try:
            self._capability_gate(token, endpoint, generation)
        finally:
            del token
        return {
            "ready": True, "provider_mode": self.provider_mode,
            "provider_identity": self.provider_identity, "endpoint": endpoint.base_url,
            "selected_alias": ALIAS, "external_transfer": True,
            "automatic_fallback": False, "redirects_followed": False,
            "authenticated_alias_capability": True,
            "runtime_network_proof_enforced": False,
            "transport_security": (
                "https" if endpoint.scheme == "https" else "temporary-operator-accepted-http"
            ),
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

    def _begin_operation(self, cancellation: CancellationToken | None) -> int:
        with self._operation_lock:
            self._operation_generation += 1
            generation = self._operation_generation
            if cancellation is not None and cancellation.cancelled:
                self._cancelled_generation = generation
            return generation

    def _operation_cancelled(self, generation: int) -> bool:
        with self._operation_lock:
            return self._cancelled_generation == generation

    def _apply_response_deadline(
        self, response: http.client.HTTPResponse, deadline: float
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StageFailure("llm_provider", "selected_provider_request_timeout")
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)

    def _execute(
        self, payload: dict, on_sentence: Callable[[str], None] | None, generation: int
    ) -> dict:
        endpoint = self._endpoint()
        token = self._token()
        submitted = time.monotonic()
        request_deadline = submitted + self._request_timeout_seconds
        connection = self._connection_for(
            endpoint,
            self._request_timeout_seconds,
            cancelled=lambda: self._operation_cancelled(generation),
            operation_deadline=request_deadline,
        )
        with self._operation_lock:
            self._connection = connection
            self._connection_generation = generation
            cancelled = self._cancelled_generation == generation
        if cancelled:
            connection.close()
            with self._operation_lock:
                if self._connection_generation == generation:
                    self._connection = None
                    self._connection_generation = 0
            del token
            raise StageFailure("llm_provider", "selected_provider_cancelled")
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
                    "Accept": "text/event-stream", "Connection": "close", "Host": endpoint.authority,
                },
            )
            response = connection.getresponse()
            accepted = time.monotonic()
            self._apply_response_deadline(response, request_deadline)
            if response.status != 200:
                response.read(65536)
                raise StageFailure("llm_provider", f"selected_provider_http_{response.status}")
            while True:
                if self._operation_cancelled(generation):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                self._apply_response_deadline(response, request_deadline)
                line = response.fp.readline(MAX_STREAM_LINE_BYTES + 1)
                if time.monotonic() >= request_deadline:
                    raise StageFailure("llm_provider", "selected_provider_request_timeout")
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
                    if (
                        on_sentence is not None
                        and response_models == {ALIAS}
                        and not self._operation_cancelled(generation)
                    ):
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
            code = (
                "selected_provider_cancelled"
                if self._operation_cancelled(generation)
                else "selected_provider_transport_error"
            )
            raise StageFailure("llm_provider", code) from error
        finally:
            completed = time.monotonic()
            connection.close()
            with self._operation_lock:
                if self._connection_generation == generation:
                    self._connection = None
                    self._connection_generation = 0
            del token
        if self._operation_cancelled(generation):
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        if response_models != {ALIAS}:
            raise StageFailure("llm_provider", "selected_provider_identity_mismatch")
        output = "".join(visible).strip()
        if on_sentence is not None and handoff_offset < len("".join(visible)):
            remaining = "".join(visible)[handoff_offset:].strip()
            if remaining:
                if self._operation_cancelled(generation):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
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
        cancellation: CancellationToken | None,
    ) -> str:
        if not valid_correlation_id(session_id) or not valid_correlation_id(turn_id):
            raise StageFailure("llm_provider", "invalid_correlation_id")
        generation = self._begin_operation(cancellation)
        if self._operation_cancelled(generation):
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        payload = self._payload(session_id, transcript)
        started = time.monotonic()
        try:
            if self._executor is None:
                result = self._execute(payload, on_sentence, generation)
            else:
                result = self._executor(payload)
            if self._operation_cancelled(generation):
                raise StageFailure("llm_provider", "selected_provider_cancelled")
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
                if self._operation_cancelled(generation):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
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

    def respond(
        self, *, session_id: str, turn_id: str, transcript: str,
        cancellation: CancellationToken | None = None,
    ) -> str:
        return self._respond(
            session_id=session_id, turn_id=turn_id, transcript=transcript,
            on_sentence=None, cancellation=cancellation,
        )

    def respond_with_handoff(
        self, *, session_id: str, turn_id: str, transcript: str,
        on_sentence: Callable[[str], None], cancellation: CancellationToken | None = None,
    ) -> str:
        return self._respond(
            session_id=session_id, turn_id=turn_id, transcript=transcript,
            on_sentence=on_sentence, cancellation=cancellation,
        )

    def cancel(self) -> None:
        with self._operation_lock:
            self._cancelled_generation = self._operation_generation
            connection = (
                self._connection
                if self._connection_generation == self._operation_generation
                else None
            )
        if connection is not None:
            connection.close()

    def snapshot_session(self, session_id: str) -> tuple[dict[str, str], ...]:
        return tuple(dict(message) for message in self._contexts.get(session_id, ()))

    def restore_session(self, session_id: str, snapshot: tuple[dict[str, str], ...]) -> None:
        if snapshot:
            self._contexts[session_id] = [dict(message) for message in snapshot]
        else:
            self._contexts.pop(session_id, None)

    def reset_session(self, session_id: str) -> None:
        self._contexts.pop(session_id, None)

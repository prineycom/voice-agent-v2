"""Single-alias LiteLLM provider adapter with fail-closed privacy and routing guards."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import threading
import time
from typing import Callable

from .contracts import LLM_VERSION, StageFailure

ENDPOINT = "http://rpi:4000"
HOST = "rpi"
PORT = 4000
ALIAS = "deepseek-v4-flash"
TOKEN_PATH = Path("/home/priney/.cache/voice-agent-v2/slice-2/secrets/litellm.token")
SYSTEM_PROMPT = "Отвечай по-русски, кратко, полезно и безопасно. Не раскрывай скрытые рассуждения."
ALLOWED_BODY_FIELDS = frozenset({"model", "messages", "temperature", "top_p", "max_tokens", "stream", "stream_options"})
ALLOWED_ROLES = frozenset({"system", "user", "assistant"})


class LiteLLMProvider:
    version = LLM_VERSION
    provider_mode = "cloud"
    provider_identity = "litellm/deepseek-v4-flash"

    def __init__(self, executor: Callable[[dict], dict] | None = None) -> None:
        self._executor = executor or self._execute
        self._contexts: dict[str, list[dict[str, str]]] = {}
        self._cancelled = threading.Event()
        self._connection: http.client.HTTPConnection | None = None
        self.observations: list[dict] = []

    @staticmethod
    def _transport_gate() -> dict[str, str]:
        addresses = {item[4][0] for item in socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)}
        network = ipaddress.ip_network("100.64.0.0/10")
        if not addresses or any(ipaddress.ip_address(address) not in network for address in addresses):
            raise StageFailure("llm_provider", "endpoint_not_tailscale")
        address = sorted(addresses)[0]
        route = subprocess.run(["ip", "route", "get", address], check=True, capture_output=True, text=True).stdout
        if " dev tailscale0 " not in f" {route.strip()} ":
            raise StageFailure("llm_provider", "endpoint_route_not_tailscale")
        return {"address_class": "tailscale-cgnat-ipv4", "route_interface": "tailscale0"}

    @staticmethod
    def _token() -> str:
        info = TOKEN_PATH.stat()
        if not TOKEN_PATH.is_file() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise StageFailure("llm_provider", "credential_file_invalid")
        value = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if not value:
            raise StageFailure("llm_provider", "credential_file_empty")
        return value

    def readiness(self) -> dict:
        transport = self._transport_gate()
        self._token()
        return {
            "ready": True, "provider_mode": self.provider_mode,
            "provider_identity": self.provider_identity, "endpoint": ENDPOINT,
            "selected_alias": ALIAS, "external_transfer": True,
            "automatic_fallback": False, **transport,
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

    def _execute(self, payload: dict) -> dict:
        self._transport_gate()
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
        try:
            connection.request(
                "POST", "/v1/chat/completions", body=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "text/event-stream", "Connection": "close"},
            )
            response = connection.getresponse()
            accepted = time.monotonic()
            if response.status != 200:
                response.read(65536)
                raise StageFailure("llm_provider", f"selected_provider_http_{response.status}")
            while True:
                if self._cancelled.is_set():
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                line = response.fp.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if not text.startswith("data: "):
                    continue
                if text == "data: [DONE]":
                    break
                event = json.loads(text[6:])
                now = time.monotonic()
                if isinstance(event.get("model"), str):
                    response_models.add(event["model"])
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if isinstance(raw_usage.get(key), int) and raw_usage[key] >= 0:
                            usage[key] = raw_usage[key]
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content") or ""
                content_piece = delta.get("content") or ""
                if reasoning_piece or content_piece:
                    raw_first = raw_first or now
                if reasoning_piece:
                    reasoning_events += 1
                if content_piece:
                    visible_first = visible_first or now
                    visible.append(content_piece)
        except StageFailure:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            code = "selected_provider_cancelled" if self._cancelled.is_set() else "selected_provider_transport_error"
            raise StageFailure("llm_provider", code) from error
        finally:
            completed = time.monotonic()
            connection.close()
            self._connection = None
            del token
        output = "".join(visible).strip()
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

    def respond(self, *, session_id: str, turn_id: str, transcript: str) -> str:
        del turn_id
        self._cancelled.clear()
        payload = self._payload(session_id, transcript)
        started = time.monotonic()
        try:
            result = self._executor(payload)
        except StageFailure as error:
            self.observations.append({
                "provider_mode": self.provider_mode, "provider_identity": self.provider_identity,
                "external_transfer": True, "success": False, "error_class": error.code,
                "latency_ms": (time.monotonic() - started) * 1000,
            })
            raise
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            raise StageFailure("llm_provider", "empty_selected_provider_response")
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

    def cancel(self) -> None:
        self._cancelled.set()
        if self._connection is not None:
            self._connection.close()

    def reset_session(self, session_id: str) -> None:
        self._contexts.pop(session_id, None)

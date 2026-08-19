"""Bounded cited-Web research receipts for the single AgentEnvironment.

Convenience operations are compiled to one fixed Python program executed by the
existing Docker-only shell helper.  Model values are data in a base64 envelope,
never shell source.  Ordinary container curl/scripts remain available.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Mapping
from urllib.parse import urlsplit

from .contracts import StageFailure

RESEARCH_CITATION_VERSION = "voice-agent.research-citation.v1"
RESEARCH_RECEIPT_VERSION = "voice-agent.web-receipt.v1"
WEB_TOOLS = ("web.search", "web.fetch", "web.extract")
MAX_WEB_BYTES = 1024 * 1024
MAX_WEB_PREVIEW_BYTES = 48 * 1024
MAX_WEB_TIMEOUT_SECONDS = 120

# This source executes only inside the selected container. It deliberately uses
# the standard library: no provider, browser, host service, or alternate runtime.
_CONTAINER_PROGRAM = r'''
import base64, hashlib, html.parser, json, os, pathlib, re, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

request = json.loads(base64.b64decode(os.environ["VOICE_AGENT_RESEARCH_REQUEST_B64"]))
operation = request["operation"]
maximum = request.get("maximum_bytes", 262144)
preview_maximum = min(maximum, 49152)
now = int(time.time())

SENSITIVE = re.compile(r"(?i)(?:authorization|api[-_]?key|access[-_]?token|token|secret|password|signature|sig|credential)")
def display_url(value):
    parts = urllib.parse.urlsplit(value)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    if parts.port is not None:
        host += ":" + str(parts.port)
    query = []
    for key, item in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if SENSITIVE.search(key) else item))
    return urllib.parse.urlunsplit((parts.scheme.lower(), host.lower(), parts.path or "/", urllib.parse.urlencode(query), ""))

def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass

def title_from_html(content):
    class Title(html.parser.HTMLParser):
        def __init__(self): super().__init__(); self.inside = False; self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag.lower() == "title": self.inside = True
        def handle_endtag(self, tag):
            if tag.lower() == "title": self.inside = False
        def handle_data(self, data):
            if self.inside and sum(map(len, self.parts)) < 512: self.parts.append(data)
    parser = Title(); parser.feed(content.decode("utf-8", "replace"))
    return " ".join(" ".join(parser.parts).split())[:512] or None

def emit(outcome, details, preview=b"", error=None):
    print(json.dumps({"outcome": outcome, "preview_base64": base64.b64encode(preview).decode(), "error": error, "details": details}, sort_keys=True, separators=(",", ":")))

if operation == "web.extract":
    path = pathlib.Path(request["path"])
    try:
        content = path.read_bytes()
        if len(content) > maximum: raise ValueError("extract_input_out_of_bounds")
        class Text(html.parser.HTMLParser):
            def __init__(self): super().__init__(); self.skip = 0; self.parts = []; self.title = None; self.in_title = False
            def handle_starttag(self, tag, attrs):
                tag = tag.lower(); self.skip += tag in {"script", "style"}; self.in_title = tag == "title"
            def handle_endtag(self, tag):
                tag = tag.lower(); self.skip = max(0, self.skip - (tag in {"script", "style"})); self.in_title = False if tag == "title" else self.in_title
            def handle_data(self, data):
                if not self.skip: self.parts.append(data)
        parser = Text(); parser.feed(content.decode("utf-8", "replace"))
        extracted = "\n".join(item for item in (" ".join(part.split()) for part in parser.parts) if item).encode()
        truncated = len(extracted) > preview_maximum
        preview = extracted[:preview_maximum]
        details = {"schema_version":"voice-agent.web-receipt.v1", "kind":"web_extract", "artifact_path":str(path), "artifact_bytes":len(content), "artifact_sha256":hashlib.sha256(content).hexdigest(), "extracted_bytes":len(extracted), "extracted_sha256":hashlib.sha256(extracted).hexdigest(), "truncated":truncated, "extraction_error":None, "storage":"cache" if str(path).startswith("/cache/") else "workspace" if str(path).startswith("/workspace/") else "rootfs_or_mount"}
        emit("completed", details, preview)
    except Exception as error:
        details = {"schema_version":"voice-agent.web-receipt.v1", "kind":"web_extract", "artifact_path":str(path), "truncated":False, "extraction_error":type(error).__name__}
        emit("failed", details, error="web_extraction_failed")
    raise SystemExit(0)

url = request["url"]
if operation == "web.search":
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append((request.get("query_parameter", "q"), request["query"]))
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))
path = pathlib.Path(request["save_path"])
sidecar = pathlib.Path(str(path) + ".receipt.json")
url_hash = hashlib.sha256(url.encode()).hexdigest()
cache_mode = request.get("cache_mode", "network")
maximum_age = request.get("maximum_cache_age_seconds", 86400)

def cached(allow_stale):
    try:
        content = path.read_bytes(); prior = json.loads(sidecar.read_text())
        age = max(0, now - int(prior["retrieved_epoch_seconds"]))
        if prior.get("request_url_sha256") != url_hash or hashlib.sha256(content).hexdigest() != prior.get("artifact_sha256"): return None
        if age > maximum_age and not allow_stale: return None
        details = dict(prior); details.update({"cache_used":True, "cache_stale":age > maximum_age, "cache_age_seconds":age, "observed_epoch_seconds":now, "network_error":None})
        return content, details
    except Exception: return None

candidate = cached(cache_mode in {"only", "fallback"}) if cache_mode in {"prefer", "only"} else None
if candidate is not None:
    content, details = candidate; emit("completed", details, content[:preview_maximum]); raise SystemExit(0)
if cache_mode == "only":
    emit("failed", {"schema_version":"voice-agent.web-receipt.v1", "kind":"web_search" if operation == "web.search" else "web_fetch", "display_url":display_url(url), "cache_used":False, "cache_stale":False, "truncated":False, "network_error":"cache_unavailable", "extraction_error":None}, error="web_cache_unavailable"); raise SystemExit(0)

class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self): super().__init__(); self.values = []
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if len(self.values) >= 5: raise urllib.error.HTTPError(req.full_url, code, "redirect_limit", headers, fp)
        self.values.append(newurl); return super().redirect_request(req, fp, code, msg, headers, newurl)
redirects = Redirects()
try:
    opener = urllib.request.build_opener(redirects)
    incoming = urllib.request.Request(url, headers={"User-Agent":"voice-agent-v2-web-research/1", "Accept":"*/*"})
    started = time.monotonic()
    with opener.open(incoming, timeout=request.get("timeout_seconds", 30)) as response:
        observed = response.read(maximum + 1); effective = response.geturl(); status = response.status; content_type = response.headers.get("Content-Type")
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    truncated = len(observed) > maximum; content = observed[:maximum]
    artifact_hash = hashlib.sha256(content).hexdigest()
    details = {"schema_version":"voice-agent.web-receipt.v1", "kind":"web_search" if operation == "web.search" else "web_fetch", "display_url":display_url(effective), "requested_display_url":display_url(url), "redirects":[display_url(item) for item in redirects.values], "http_status":status, "content_type":content_type, "retrieved_epoch_seconds":now, "observed_epoch_seconds":now, "retrieval_duration_ms":elapsed_ms, "artifact_path":str(path), "artifact_bytes":len(content), "artifact_sha256":artifact_hash, "observed_response_bytes_at_least":len(observed), "truncated":truncated, "cache_used":False, "cache_stale":False, "cache_age_seconds":0, "network_error":None, "extraction_error":None, "title":title_from_html(content), "request_url_sha256":url_hash, "storage":"cache" if str(path).startswith("/cache/") else "workspace", "query_sha256":hashlib.sha256(request.get("query", "").encode()).hexdigest() if operation == "web.search" else None}
    atomic(path, content); atomic(sidecar, json.dumps(details, sort_keys=True, separators=(",", ":")).encode())
    emit("completed", details, content[:preview_maximum])
except Exception as error:
    fallback = cached(True) if cache_mode == "fallback" else None
    if fallback is not None:
        content, details = fallback; details["network_error"] = type(error).__name__; emit("completed", details, content[:preview_maximum])
    else:
        code = getattr(error, "code", None)
        details = {"schema_version":"voice-agent.web-receipt.v1", "kind":"web_search" if operation == "web.search" else "web_fetch", "display_url":display_url(getattr(error, "url", url)), "requested_display_url":display_url(url), "redirects":[display_url(item) for item in redirects.values], "http_status":code if isinstance(code, int) else None, "retrieved_epoch_seconds":now, "observed_epoch_seconds":now, "artifact_path":str(path), "artifact_bytes":0, "artifact_sha256":hashlib.sha256(b"").hexdigest(), "truncated":False, "cache_used":False, "cache_stale":False, "network_error":type(error).__name__, "extraction_error":None, "title":None, "storage":"cache" if str(path).startswith("/cache/") else "workspace", "query_sha256":hashlib.sha256(request.get("query", "").encode()).hexdigest() if operation == "web.search" else None}
        emit("failed", details, error="web_request_failed")
'''


def _bounded_string(value: object, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name}_invalid")
    return value


def _url(value: object) -> str:
    result = _bounded_string(value, "url", maximum=8192)
    parsed = urlsplit(result)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url_invalid")
    return result


def _persistent_path(value: object, name: str) -> str:
    result = _bounded_string(value, name)
    path = PurePosixPath(result)
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError(f"{name}_invalid")
    if not (result.startswith("/workspace/") or result.startswith("/cache/")):
        raise ValueError(f"{name}_not_persistent")
    return result


def research_command(tool_id: str, arguments: Mapping[str, object]) -> str:
    """Validate one closed convenience operation and return fixed shell source."""
    if tool_id not in WEB_TOOLS or not isinstance(arguments, Mapping):
        raise ValueError("research_operation_invalid")
    data = dict(arguments)
    if any(not isinstance(key, str) or key.startswith("_") for key in data):
        raise ValueError("research_operation_invalid")
    allowed_common = {"maximum_bytes", "timeout_seconds", "cache_mode", "maximum_cache_age_seconds"}
    if tool_id == "web.extract":
        if set(data) - {"path", "maximum_bytes"}:
            raise ValueError("research_operation_invalid")
        request: dict[str, object] = {"operation": tool_id, "path": _persistent_path(data.get("path"), "path")}
    else:
        allowed = allowed_common | {"url", "save_path"}
        if tool_id == "web.search":
            allowed |= {"query", "query_parameter"}
        if set(data) - allowed:
            raise ValueError("research_operation_invalid")
        request = {
            "operation": tool_id,
            "url": _url(data.get("url")),
            "save_path": _persistent_path(data.get("save_path"), "save_path"),
        }
        if tool_id == "web.search":
            request["query"] = _bounded_string(data.get("query"), "query")
            parameter = data.get("query_parameter", "q")
            if not isinstance(parameter, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", parameter) is None:
                raise ValueError("query_parameter_invalid")
            request["query_parameter"] = parameter
        timeout = data.get("timeout_seconds", 30)
        cache_mode = data.get("cache_mode", "network")
        age = data.get("maximum_cache_age_seconds", 86_400)
        if type(timeout) is not int or not 1 <= timeout <= MAX_WEB_TIMEOUT_SECONDS:
            raise ValueError("web_timeout_invalid")
        if cache_mode not in {"network", "prefer", "only", "fallback"}:
            raise ValueError("cache_mode_invalid")
        if type(age) is not int or not 0 <= age <= 31_536_000:
            raise ValueError("cache_age_invalid")
        request.update(timeout_seconds=timeout, cache_mode=cache_mode, maximum_cache_age_seconds=age)
    maximum = data.get("maximum_bytes", 262_144)
    if type(maximum) is not int or not 1024 <= maximum <= MAX_WEB_BYTES:
        raise ValueError("maximum_bytes_invalid")
    request["maximum_bytes"] = maximum
    envelope = base64.b64encode(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).decode("ascii")
    program = base64.b64encode(_CONTAINER_PROGRAM.encode()).decode("ascii")
    return (
        f"VOICE_AGENT_RESEARCH_REQUEST_B64={envelope} "
        f"python3 -c 'import base64;exec(base64.b64decode(\"{program}\"))'"
    )


def research_request_from_command(command: str) -> dict[str, object] | None:
    """Test-fixture decoder for the inert request envelope."""
    match = re.match(r"VOICE_AGENT_RESEARCH_REQUEST_B64=([A-Za-z0-9+/=]+) ", command)
    if match is None:
        return None
    return json.loads(base64.b64decode(match.group(1)))


def postprocess_research_output(tool_id: str, raw: bytes) -> tuple[str, int, bytes, bytes, dict[str, object]]:
    try:
        document = json.loads(raw)
        if set(document) != {"outcome", "preview_base64", "error", "details"}:
            raise ValueError
        details = document["details"]
        preview = base64.b64decode(document["preview_base64"], validate=True)
        if not isinstance(details, dict) or details.get("schema_version") != RESEARCH_RECEIPT_VERSION:
            raise ValueError
        expected_kind = tool_id.replace(".", "_")
        if details.get("kind") != expected_kind or len(json.dumps(details).encode()) > 8192:
            raise ValueError
        outcome = document["outcome"]
        if outcome not in {"completed", "failed"}:
            raise ValueError
        error = document["error"]
        if error is not None and (not isinstance(error, str) or re.fullmatch(r"[a-z0-9_]{1,64}", error) is None):
            raise ValueError
        total = details.get("artifact_bytes", details.get("extracted_bytes", len(preview)))
        digest = details.get("artifact_sha256", details.get("extracted_sha256", hashlib.sha256(preview).hexdigest()))
        if type(total) is not int or total < len(preview) or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise ValueError
        metadata = {
            "stdout": {"byte_count": total, "sha256": digest, "truncated": bool(details.get("truncated")) or total > len(preview)},
            "stderr": {"byte_count": len(error.encode()) if error else 0, "sha256": hashlib.sha256(error.encode() if error else b"").hexdigest(), "truncated": False},
            "details": details,
        }
        return outcome, 0 if outcome == "completed" else 2, preview, error.encode() if error else b"", metadata
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise ValueError("research_receipt_invalid") from None


@dataclass(frozen=True, slots=True)
class CitationRequest:
    receipt_id: str
    claims: tuple[str, ...]
    spans: tuple[str, ...]

    @classmethod
    def parse_many(cls, value: object) -> tuple["CitationRequest", ...]:
        if not isinstance(value, list) or len(value) > 16:
            raise StageFailure("llm_provider", "research_citation_invalid")
        result = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"receipt_id", "claims", "spans"}:
                raise StageFailure("llm_provider", "research_citation_invalid")
            receipt_id = item.get("receipt_id")
            claims, spans = item.get("claims"), item.get("spans")
            if not isinstance(receipt_id, str) or re.fullmatch(r"[a-f0-9]{32}", receipt_id) is None:
                raise StageFailure("llm_provider", "research_citation_invalid")
            if not isinstance(claims, list) or not isinstance(spans, list) or not claims or not spans or len(claims) > 16 or len(spans) > 16:
                raise StageFailure("llm_provider", "research_citation_invalid")
            if any(not isinstance(text, str) or not text.strip() or len(text.encode()) > 512 for text in (*claims, *spans)):
                raise StageFailure("llm_provider", "research_citation_invalid")
            result.append(cls(receipt_id, tuple(claims), tuple(spans)))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class CitationRecord:
    receipt_id: str
    displayed_url: str
    title: str | None
    retrieved_epoch_seconds: int
    byte_count: int
    sha256: str
    truncated: bool
    redirects: tuple[str, ...]
    cache_used: bool
    cache_stale: bool
    network_error: str | None
    extraction_error: str | None
    claims: tuple[str, ...]
    spans: tuple[str, ...]

    def document(self) -> dict[str, object]:
        return {
            "schema_version": RESEARCH_CITATION_VERSION, "receipt_id": self.receipt_id,
            "displayed_url": self.displayed_url, "title": self.title,
            "retrieved_epoch_seconds": self.retrieved_epoch_seconds,
            "byte_count": self.byte_count, "sha256": self.sha256,
            "truncated": self.truncated, "redirects": list(self.redirects),
            "cache_used": self.cache_used, "cache_stale": self.cache_stale,
            "network_error": self.network_error, "extraction_error": self.extraction_error,
            "claims": list(self.claims), "spans": list(self.spans),
        }


def bind_citations(answer: str, requests: tuple[CitationRequest, ...], receipts: Mapping[str, Mapping[str, object]]) -> tuple[CitationRecord, ...]:
    if receipts and not requests:
        raise StageFailure("llm_provider", "research_citations_required")
    result = []
    for request in requests:
        details = receipts.get(request.receipt_id)
        if details is None or details.get("kind") not in {"web_fetch", "web_search"} or details.get("network_error") is not None and not details.get("cache_used"):
            raise StageFailure("llm_provider", "research_citation_invalid")
        if any(span not in answer for span in request.spans):
            raise StageFailure("llm_provider", "research_citation_span_invalid")
        try:
            result.append(CitationRecord(
                request.receipt_id, str(details["display_url"]),
                str(details["title"]) if details.get("title") is not None else None,
                int(details["retrieved_epoch_seconds"]), int(details["artifact_bytes"]),
                str(details["artifact_sha256"]), bool(details["truncated"]),
                tuple(map(str, details.get("redirects", ()))), bool(details.get("cache_used")),
                bool(details.get("cache_stale")), details.get("network_error") if isinstance(details.get("network_error"), str) else None,
                details.get("extraction_error") if isinstance(details.get("extraction_error"), str) else None,
                request.claims, request.spans,
            ))
        except (KeyError, TypeError, ValueError):
            raise StageFailure("llm_provider", "research_citation_invalid") from None
    return tuple(result)

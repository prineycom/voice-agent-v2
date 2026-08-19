"""Atomic saved-report custody and exact-target Telegram delivery.

The controller owns the target, credential lookup, artifact/delivery identities,
and durable at-most-once state.  Model/content bytes can choose only the report,
summary, persistent relative path, media type, and text/document product shape.
They never become recipient, credential, endpoint, or host-path authority.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import threading
import time
from typing import Callable, Mapping, Protocol
import uuid

from .agent_environment import AgentEnvironment, AgentEnvironmentError

DELIVERY_CONFIG_SCHEMA = "voice-agent.telegram-delivery-config.v1"
ARTIFACT_SCHEMA = "voice-agent.saved-report.v1"
DELIVERY_SCHEMA = "voice-agent.telegram-delivery.v1"
DELIVERY_STATUS_SCHEMA = "voice-agent.telegram-delivery-status.v1"
TELEGRAM_API_HOST = "api.telegram.org"
TELEGRAM_API_ENDPOINT = "https://api.telegram.org"
TELEGRAM_CREDENTIAL_NAME = "TELEGRAM_BOT_TOKEN"
MAX_REPORT_BYTES = 262_144
MAX_SUMMARY_BYTES = 12_000
MAX_TEXT_CHUNK_BYTES = 4_096
MAX_TEXT_CHUNKS = 4
MAX_CAPTION_BYTES = 1_024
MAX_TELEGRAM_RESPONSE_BYTES = 64 * 1024
TELEGRAM_TIMEOUT_SECONDS = 30.0
IDENTITY = re.compile(r"^[a-f0-9]{32}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+/-]{0,126}$")
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class ReportDeliveryError(RuntimeError):
    """Stable, content-free delivery-plane failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TelegramTarget:
    chat_id: int
    user_id: int
    message_thread_id: None = None

    def document(self) -> dict[str, object]:
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "message_thread_id": self.message_thread_id,
            "chat_type": "private",
        }


@dataclass(frozen=True, slots=True)
class DeliveryCitation:
    receipt_id: str
    displayed_url: str
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not IDENTITY.fullmatch(self.receipt_id)
            or not self.displayed_url.startswith(("https://", "http://"))
            or type(self.byte_count) is not int
            or self.byte_count < 0
            or not SHA256.fullmatch(self.sha256)
        ):
            raise ValueError("delivery citation is invalid")

    def custody_document(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "source_byte_count": self.byte_count,
            "source_sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ReportDeliveryRequest:
    report_bytes: bytes
    relative_path: str
    media_type: str
    mode: str
    summary: str
    citations: tuple[DeliveryCitation, ...]

    @classmethod
    def parse(
        cls,
        arguments: Mapping[str, object],
        successful_research: Mapping[str, Mapping[str, object]],
    ) -> "ReportDeliveryRequest":
        if not isinstance(arguments, Mapping):
            raise ReportDeliveryError("report_request_invalid")
        keys = set(arguments)
        common = {"relative_path", "media_type", "mode", "summary", "citation_receipt_ids"}
        if keys == common | {"report_text"} and isinstance(arguments.get("report_text"), str):
            report = str(arguments["report_text"]).encode("utf-8")
        elif keys == common | {"report_base64"} and isinstance(arguments.get("report_base64"), str):
            try:
                report = base64.b64decode(str(arguments["report_base64"]), validate=True)
            except ValueError as error:
                raise ReportDeliveryError("report_request_invalid") from error
        else:
            raise ReportDeliveryError("report_request_invalid")
        if not report or len(report) > MAX_REPORT_BYTES:
            raise ReportDeliveryError("report_bytes_out_of_bounds")
        relative_path = arguments.get("relative_path")
        candidate = Path(relative_path) if isinstance(relative_path, str) else Path("/")
        if (
            not isinstance(relative_path, str)
            or not relative_path.startswith("reports/")
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or len(relative_path.encode("utf-8")) > 512
        ):
            raise ReportDeliveryError("report_path_invalid")
        media_type = arguments.get("media_type")
        mode = arguments.get("mode")
        summary = arguments.get("summary")
        identifiers = arguments.get("citation_receipt_ids")
        if (
            not isinstance(media_type, str)
            or MEDIA_TYPE.fullmatch(media_type) is None
            or mode not in {"text", "document", "both"}
            or not isinstance(summary, str)
            or len(summary.encode("utf-8")) > MAX_SUMMARY_BYTES
            or (mode in {"text", "both"} and not summary.strip())
            or not isinstance(identifiers, list)
            or not 1 <= len(identifiers) <= 16
            or len(identifiers) != len(set(map(str, identifiers)))
        ):
            raise ReportDeliveryError("report_request_invalid")
        citations: list[DeliveryCitation] = []
        for identifier in identifiers:
            if not isinstance(identifier, str) or IDENTITY.fullmatch(identifier) is None:
                raise ReportDeliveryError("report_citation_invalid")
            details = successful_research.get(identifier)
            if (
                details is None
                or details.get("kind") != "web_fetch"
                or (details.get("network_error") is not None and not details.get("cache_used"))
            ):
                raise ReportDeliveryError("report_citation_invalid")
            try:
                citations.append(DeliveryCitation(
                    identifier,
                    str(details["display_url"]),
                    int(details["artifact_bytes"]),
                    str(details["artifact_sha256"]),
                ))
            except (KeyError, TypeError, ValueError) as error:
                raise ReportDeliveryError("report_citation_invalid") from error
        return cls(report, relative_path, media_type, str(mode), summary.strip(), tuple(citations))


@dataclass(frozen=True, slots=True)
class TelegramOperation:
    operation_id: str
    kind: str
    target: TelegramTarget
    method: str
    request_body: bytes
    content_type: str
    payload_byte_count: int
    payload_sha256: str
    network_byte_count: int
    network_sha256: str
    document_byte_count: int | None = None
    document_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramAcknowledgement:
    outcome: str
    target: TelegramTarget | None = None
    operation_kind: str | None = None
    payload_byte_count: int | None = None
    payload_sha256: str | None = None
    remote_receipt: str | None = None
    reason_code: str | None = None


class TelegramTransport(Protocol):
    def send(self, credential: bytes, operation: TelegramOperation) -> TelegramAcknowledgement: ...


class TelegramBotAPITransport:
    """One-shot HTTPS transport to the release-fixed Telegram Bot API host."""

    @staticmethod
    def _credential_path_value(value: bytes) -> str:
        try:
            token = value.decode("ascii")
        except UnicodeError as error:
            raise ReportDeliveryError("telegram_credential_invalid") from error
        if not token or len(token) > 256 or re.fullmatch(r"[A-Za-z0-9:_-]+", token) is None:
            raise ReportDeliveryError("telegram_credential_invalid")
        return token

    def send(self, credential: bytes, operation: TelegramOperation) -> TelegramAcknowledgement:
        connection: http.client.HTTPSConnection | None = None
        try:
            token = self._credential_path_value(credential)
            connection = http.client.HTTPSConnection(
                TELEGRAM_API_HOST, 443, timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            connection.request(
                "POST", f"/bot{token}/{operation.method}", body=operation.request_body,
                headers={
                    "Content-Type": operation.content_type,
                    "Content-Length": str(len(operation.request_body)),
                    "Accept": "application/json",
                    "User-Agent": "voice-agent-v2/e4.2",
                },
            )
            response = connection.getresponse()
            body = response.read(MAX_TELEGRAM_RESPONSE_BYTES + 1)
            if len(body) > MAX_TELEGRAM_RESPONSE_BYTES:
                return TelegramAcknowledgement("unknown", reason_code="telegram_response_out_of_bounds")
            if response.status < 200 or response.status >= 300:
                return TelegramAcknowledgement("rejected", reason_code="telegram_rejected")
            document = json.loads(body)
            result = document.get("result") if isinstance(document, dict) and document.get("ok") is True else None
            chat = result.get("chat") if isinstance(result, dict) else None
            if (
                not isinstance(chat, dict)
                or chat.get("id") != operation.target.chat_id
                or chat.get("type") != "private"
                or result.get("message_thread_id") is not operation.target.message_thread_id
                or type(result.get("message_id")) is not int
            ):
                return TelegramAcknowledgement("unknown", reason_code="telegram_acknowledgement_mismatch")
            remote = hashlib.sha256(
                f"{operation.target.chat_id}:{result['message_id']}".encode("ascii")
            ).hexdigest()[:24]
            return TelegramAcknowledgement(
                "acknowledged", operation.target, operation.kind,
                operation.payload_byte_count, operation.payload_sha256, remote,
                "telegram_acknowledged",
            )
        except ReportDeliveryError:
            raise
        except (OSError, socket.timeout, http.client.HTTPException, ValueError, TypeError, json.JSONDecodeError):
            return TelegramAcknowledgement("unknown", reason_code="telegram_delivery_uncertain")
        finally:
            if connection is not None:
                connection.close()


def _secure_private_document(path: Path) -> dict[str, object]:
    descriptor = -1
    try:
        root = path.parent.lstat()
        if (
            not stat.S_ISDIR(root.st_mode)
            or root.st_uid != os.geteuid()
            or stat.S_IMODE(root.st_mode) != 0o700
        ):
            raise ReportDeliveryError("telegram_target_config_unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 16 * 1024
        ):
            raise ReportDeliveryError("telegram_target_config_unsafe")
        raw = os.read(descriptor, 16 * 1024 + 1)
        document = json.loads(raw)
    except ReportDeliveryError:
        raise
    except FileNotFoundError as error:
        raise ReportDeliveryError("telegram_target_config_unavailable") from error
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ReportDeliveryError("telegram_target_config_invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(document, dict):
        raise ReportDeliveryError("telegram_target_config_invalid")
    return document


def load_telegram_target(path: Path) -> TelegramTarget:
    document = _secure_private_document(path)
    if set(document) != {"schema_version", "target"} or document.get("schema_version") != DELIVERY_CONFIG_SCHEMA:
        raise ReportDeliveryError("telegram_target_config_invalid")
    target = document.get("target")
    if not isinstance(target, dict) or set(target) != {"chat_id", "user_id", "message_thread_id"}:
        raise ReportDeliveryError("telegram_target_config_invalid")
    chat_id, user_id, thread_id = target.get("chat_id"), target.get("user_id"), target.get("message_thread_id")
    if (
        type(chat_id) is not int
        or type(user_id) is not int
        or chat_id <= 0
        or user_id <= 0
        or chat_id != user_id
        or thread_id is not None
    ):
        raise ReportDeliveryError("telegram_target_config_invalid")
    return TelegramTarget(chat_id, user_id)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _chunk_utf8(value: str, maximum: int) -> tuple[bytes, ...]:
    remaining = value.strip()
    chunks: list[bytes] = []
    while remaining:
        encoded = remaining.encode("utf-8")
        if len(encoded) <= maximum:
            chunks.append(encoded)
            break
        boundary = 0
        used = 0
        for index, character in enumerate(remaining):
            width = len(character.encode("utf-8"))
            if used + width > maximum:
                break
            used += width
            if character.isspace():
                boundary = index + 1
        if boundary == 0:
            boundary = index
        chunk = remaining[:boundary].rstrip()
        if not chunk:
            raise ReportDeliveryError("telegram_text_out_of_bounds")
        chunks.append(chunk.encode("utf-8"))
        remaining = remaining[boundary:].lstrip()
        if len(chunks) >= MAX_TEXT_CHUNKS and remaining:
            raise ReportDeliveryError("telegram_text_out_of_bounds")
    return tuple(chunks)


def _source_lines(citations: tuple[DeliveryCitation, ...]) -> str:
    return "\n".join(
        f"[{index}] {item.displayed_url} (receipt {item.receipt_id})"
        for index, item in enumerate(citations, 1)
    )


def _filename(relative_path: str) -> str:
    result = SAFE_FILENAME.sub("_", Path(relative_path).name).strip("._")
    return (result or "report.bin")[:128]


def _json_body(target: TelegramTarget, text: bytes) -> bytes:
    value = {
        "chat_id": target.chat_id,
        "text": text.decode("utf-8"),
        "disable_web_page_preview": True,
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _multipart_body(
    *, boundary: str, target: TelegramTarget, filename: str, media_type: str,
    document: bytes, caption: bytes,
) -> bytes:
    marker = boundary.encode("ascii")
    fields = [
        b"--" + marker + b'\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n' + str(target.chat_id).encode("ascii") + b"\r\n",
        b"--" + marker + b'\r\nContent-Disposition: form-data; name="caption"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n' + caption + b"\r\n",
        b"--" + marker + b'\r\nContent-Disposition: form-data; name="document"; filename="' + filename.encode("ascii") + b'"\r\nContent-Type: ' + media_type.encode("ascii") + b"\r\n\r\n" + document + b"\r\n",
        b"--" + marker + b"--\r\n",
    ]
    return b"".join(fields)


class ReportDeliveryController:
    """Durable report/artifact ledger plus at-most-once Telegram dispatch."""

    def __init__(
        self,
        environment: AgentEnvironment,
        *,
        transport: TelegramTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.environment = environment
        self.transport = transport or TelegramBotAPITransport()
        self.clock = clock
        self.state_root = environment.state_root
        self.ledger_path = self.state_root / "report-deliveries.json"
        self.lock_path = self.state_root / "report-deliveries.lock"
        self._thread_lock = threading.RLock()
        self.target: TelegramTarget | None = None
        self.target_reason: str | None = None
        try:
            self.target = load_telegram_target(self.state_root / "telegram-delivery.json")
        except ReportDeliveryError as error:
            self.target_reason = error.code

    def _ledger(self) -> dict[str, object]:
        if not self.ledger_path.exists():
            return {"schema_version": "voice-agent.report-delivery-ledger.v1", "artifacts": {}, "deliveries": {}}
        try:
            value = json.loads(self.ledger_path.read_bytes())
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ReportDeliveryError("delivery_ledger_unavailable") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "artifacts", "deliveries"}
            or value.get("schema_version") != "voice-agent.report-delivery-ledger.v1"
            or not isinstance(value.get("artifacts"), dict)
            or not isinstance(value.get("deliveries"), dict)
        ):
            raise ReportDeliveryError("delivery_ledger_invalid")
        return value

    def _locked(self):
        controller = self
        class Lock:
            def __enter__(self_nonlocal):
                controller._thread_lock.acquire()
                controller.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(controller.state_root, 0o700)
                self_nonlocal.handle = controller.lock_path.open("a+b")
                os.chmod(controller.lock_path, 0o600)
                fcntl.flock(self_nonlocal.handle.fileno(), fcntl.LOCK_EX)
                return self_nonlocal
            def __exit__(self_nonlocal, *_args):
                fcntl.flock(self_nonlocal.handle.fileno(), fcntl.LOCK_UN)
                self_nonlocal.handle.close()
                controller._thread_lock.release()
        return Lock()

    @staticmethod
    def _safe_delivery(document: Mapping[str, object]) -> dict[str, object]:
        keys = (
            "schema_version", "delivery_id", "artifact_id", "container_path", "artifact_byte_count",
            "artifact_sha256", "media_type", "citation_receipt_ids", "target", "mode", "outcome",
            "acknowledgement_state", "acknowledged_operation_count", "operation_count", "duration_ms",
            "reason_code", "automatic_resend", "remote_effects_retracted", "research_rerun",
        )
        result = {key: document.get(key) for key in keys}
        receipts = document.get("operation_receipts", [])
        result["operations"] = [
            {
                key: item.get(key)
                for key in (
                    "kind", "payload_byte_count", "payload_sha256", "network_byte_count",
                    "network_sha256", "document_byte_count", "document_sha256",
                    "acknowledged", "remote_receipt",
                )
            }
            for item in receipts if isinstance(item, dict)
        ]
        return result

    def _save_ledger(self, ledger: Mapping[str, object]) -> None:
        _atomic_json(self.ledger_path, ledger)

    def _credential(self) -> bytes:
        declarations = self.environment.config.model.agent_environment.credentials
        if declarations.exec_environment_names.count(TELEGRAM_CREDENTIAL_NAME) != 1:
            raise ReportDeliveryError("telegram_credential_not_configured")
        try:
            return self.environment.credential_store.resolve("environment", TELEGRAM_CREDENTIAL_NAME)
        except Exception as error:
            raise ReportDeliveryError("telegram_credential_unavailable") from error

    @staticmethod
    def _sidecar(request: ReportDeliveryRequest) -> bytes:
        text = request.summary
        if request.mode in {"text", "both"}:
            text += "\n\nSources:\n" + _source_lines(request.citations)
        text_chunks = _chunk_utf8(text, MAX_TEXT_CHUNK_BYTES) if request.mode in {"text", "both"} else ()
        caption_text = "Sources:\n" + _source_lines(request.citations)
        caption = caption_text.encode("utf-8")
        if len(caption) > MAX_CAPTION_BYTES:
            raise ReportDeliveryError("telegram_caption_out_of_bounds")
        return json.dumps({
            "schema_version": "voice-agent.report-delivery-sidecar.v1",
            "relative_path": request.relative_path,
            "media_type": request.media_type,
            "mode": request.mode,
            "filename": _filename(request.relative_path),
            "text_chunks_base64": [base64.b64encode(item).decode("ascii") for item in text_chunks],
            "caption_base64": base64.b64encode(caption).decode("ascii"),
            "citations": [item.custody_document() | {"displayed_url": item.displayed_url} for item in request.citations],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def save_and_deliver(self, request: ReportDeliveryRequest, *, cancellation: object | None = None) -> dict[str, object]:
        artifact_id = uuid.uuid4().hex
        report_receipt = self.environment.stream_inbound(
            request.report_bytes, root="workspace", relative_path=request.relative_path,
            transfer_id=artifact_id,
        )
        # The report is now fsynced and atomically renamed before target,
        # credential, Telegram readiness, or network dispatch is admitted.
        try:
            sidecar = self._sidecar(request)
            sidecar_path = f"reports/.delivery/{artifact_id}.json"
            sidecar_receipt = self.environment.stream_inbound(
                sidecar, root="workspace", relative_path=sidecar_path,
            )
        except (AgentEnvironmentError, ReportDeliveryError) as error:
            reason = error.code if hasattr(error, "code") else "delivery_sidecar_failed"
            return self._record_saved_failure(artifact_id, report_receipt, request, reason)
        artifact = {
            "schema_version": ARTIFACT_SCHEMA,
            "artifact_id": artifact_id,
            "root": "workspace",
            "relative_path": request.relative_path,
            "container_path": report_receipt.container_path,
            "byte_count": report_receipt.byte_count,
            "sha256": report_receipt.sha256,
            "media_type": request.media_type,
            "citation_receipt_ids": [item.receipt_id for item in request.citations],
            "sidecar_relative_path": sidecar_path,
            "sidecar_byte_count": sidecar_receipt.byte_count,
            "sidecar_sha256": sidecar_receipt.sha256,
        }
        with self._locked():
            ledger = self._ledger()
            ledger["artifacts"][artifact_id] = artifact
            self._save_ledger(ledger)
        return self._new_attempt(artifact_id, cancellation=cancellation)

    def _record_saved_failure(self, artifact_id, receipt, request, reason: str) -> dict[str, object]:
        delivery_id = uuid.uuid4().hex
        document = {
            "schema_version": DELIVERY_SCHEMA, "delivery_id": delivery_id,
            "artifact_id": artifact_id, "container_path": receipt.container_path,
            "artifact_byte_count": receipt.byte_count, "artifact_sha256": receipt.sha256,
            "media_type": request.media_type,
            "citation_receipt_ids": [item.receipt_id for item in request.citations],
            "target": self.target.document() if self.target else None, "mode": request.mode,
            "outcome": "failed", "acknowledgement_state": "failed",
            "acknowledged_operation_count": 0, "operation_count": 0, "duration_ms": 0,
            "reason_code": reason, "automatic_resend": False,
            "remote_effects_retracted": False, "research_rerun": False,
        }
        with self._locked():
            ledger = self._ledger(); ledger["deliveries"][delivery_id] = document; self._save_ledger(ledger)
        return self._safe_delivery(document)

    def _read_artifact(self, artifact: Mapping[str, object]) -> tuple[bytes, dict[str, object]]:
        try:
            report, report_receipt = self.environment.stream_outbound(
                root="workspace", relative_path=str(artifact["relative_path"]),
            )
            sidecar, sidecar_receipt = self.environment.stream_outbound(
                root="workspace", relative_path=str(artifact["sidecar_relative_path"]),
            )
        except AgentEnvironmentError as error:
            raise ReportDeliveryError("saved_artifact_unavailable") from error
        if (
            report_receipt.byte_count != artifact.get("byte_count")
            or report_receipt.sha256 != artifact.get("sha256")
            or sidecar_receipt.byte_count != artifact.get("sidecar_byte_count")
            or sidecar_receipt.sha256 != artifact.get("sidecar_sha256")
        ):
            raise ReportDeliveryError("saved_artifact_changed")
        try:
            plan = json.loads(sidecar)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ReportDeliveryError("saved_artifact_changed") from error
        return report, plan

    @staticmethod
    def _operations(
        delivery_id: str, target: TelegramTarget, report: bytes, plan: Mapping[str, object],
    ) -> tuple[TelegramOperation, ...]:
        operations: list[TelegramOperation] = []
        mode = plan.get("mode")
        encoded_chunks = plan.get("text_chunks_base64")
        if mode not in {"text", "document", "both"} or not isinstance(encoded_chunks, list):
            raise ReportDeliveryError("saved_artifact_changed")
        if mode in {"text", "both"}:
            for index, encoded in enumerate(encoded_chunks):
                try:
                    text = base64.b64decode(encoded, validate=True)
                    text.decode("utf-8")
                except (TypeError, ValueError, UnicodeError) as error:
                    raise ReportDeliveryError("saved_artifact_changed") from error
                body = _json_body(target, text)
                operations.append(TelegramOperation(
                    f"{delivery_id}:text:{index}", "text", target, "sendMessage", body,
                    "application/json; charset=utf-8", len(text), hashlib.sha256(text).hexdigest(),
                    len(body), hashlib.sha256(body).hexdigest(),
                ))
        if mode in {"document", "both"}:
            try:
                caption = base64.b64decode(plan["caption_base64"], validate=True)
                filename = str(plan["filename"])
                media_type = str(plan["media_type"])
            except (KeyError, TypeError, ValueError) as error:
                raise ReportDeliveryError("saved_artifact_changed") from error
            boundary = "voice-agent-" + hashlib.sha256(delivery_id.encode("ascii")).hexdigest()[:32]
            body = _multipart_body(
                boundary=boundary, target=target, filename=filename, media_type=media_type,
                document=report, caption=caption,
            )
            operations.append(TelegramOperation(
                f"{delivery_id}:document", "document", target, "sendDocument", body,
                f"multipart/form-data; boundary={boundary}", len(report), hashlib.sha256(report).hexdigest(),
                len(body), hashlib.sha256(body).hexdigest(), len(report), hashlib.sha256(report).hexdigest(),
            ))
        if not operations or len(operations) > MAX_TEXT_CHUNKS + 1:
            raise ReportDeliveryError("saved_artifact_changed")
        return tuple(operations)

    def _new_attempt(self, artifact_id: str, *, cancellation: object | None = None) -> dict[str, object]:
        if not IDENTITY.fullmatch(artifact_id):
            raise ReportDeliveryError("artifact_identity_invalid")
        delivery_id = uuid.uuid4().hex
        started = self.clock()
        with self._locked():
            ledger = self._ledger()
            artifact = ledger["artifacts"].get(artifact_id)
            if not isinstance(artifact, dict):
                raise ReportDeliveryError("saved_artifact_unknown")
        base = {
            "schema_version": DELIVERY_SCHEMA, "delivery_id": delivery_id,
            "artifact_id": artifact_id, "container_path": artifact["container_path"],
            "artifact_byte_count": artifact["byte_count"], "artifact_sha256": artifact["sha256"],
            "media_type": artifact["media_type"],
            "citation_receipt_ids": artifact["citation_receipt_ids"],
            "target": self.target.document() if self.target else None,
            "mode": None, "outcome": "failed", "acknowledgement_state": "failed",
            "acknowledged_operation_count": 0, "operation_count": 0, "duration_ms": 0,
            "reason_code": self.target_reason, "automatic_resend": False,
            "remote_effects_retracted": False, "research_rerun": False,
            "operation_receipts": [],
        }
        if self.target is None:
            return self._finish_attempt(base, started)
        try:
            report, plan = self._read_artifact(artifact)
            operations = self._operations(delivery_id, self.target, report, plan)
            base["mode"] = plan["mode"]
            base["operation_count"] = len(operations)
            credential = self._credential()
        except ReportDeliveryError as error:
            base["reason_code"] = error.code
            return self._finish_attempt(base, started)
        if getattr(cancellation, "cancelled", False):
            base["reason_code"] = "delivery_cancelled"
            return self._finish_attempt(base, started)
        base["outcome"] = "dispatching"
        base["acknowledgement_state"] = "dispatching"
        base["reason_code"] = None
        with self._locked():
            ledger = self._ledger(); ledger["deliveries"][delivery_id] = base; self._save_ledger(ledger)
        acknowledged = 0
        receipts: list[dict[str, object]] = []
        outcome, reason = "sent", "telegram_acknowledged"
        for operation in operations:
            if getattr(cancellation, "cancelled", False):
                outcome, reason = "failed", "delivery_cancelled"
                break
            try:
                acknowledgement = self.transport.send(credential, operation)
            except ReportDeliveryError as error:
                acknowledgement = TelegramAcknowledgement("rejected", reason_code=error.code)
            except Exception:
                acknowledgement = TelegramAcknowledgement("unknown", reason_code="telegram_delivery_uncertain")
            exact = (
                acknowledgement.outcome == "acknowledged"
                and acknowledgement.target == self.target
                and acknowledgement.operation_kind == operation.kind
                and acknowledgement.payload_byte_count == operation.payload_byte_count
                and acknowledgement.payload_sha256 == operation.payload_sha256
            )
            receipts.append({
                "kind": operation.kind,
                "payload_byte_count": operation.payload_byte_count,
                "payload_sha256": operation.payload_sha256,
                "network_byte_count": operation.network_byte_count,
                "network_sha256": operation.network_sha256,
                "document_byte_count": operation.document_byte_count,
                "document_sha256": operation.document_sha256,
                "acknowledged": exact,
                "remote_receipt": acknowledgement.remote_receipt if exact else None,
            })
            if exact:
                acknowledged += 1
                with self._locked():
                    ledger = self._ledger(); current = ledger["deliveries"][delivery_id]
                    current["acknowledged_operation_count"] = acknowledged
                    current["operation_receipts"] = receipts
                    self._save_ledger(ledger)
                continue
            if acknowledgement.outcome == "rejected":
                outcome, reason = "failed", acknowledgement.reason_code or "telegram_rejected"
            elif acknowledgement.outcome == "acknowledged":
                outcome, reason = "delivery_outcome_unknown", "telegram_acknowledgement_mismatch"
            else:
                outcome, reason = "delivery_outcome_unknown", acknowledgement.reason_code or "telegram_delivery_uncertain"
            break
        base.update(
            outcome=outcome,
            acknowledgement_state="acknowledged" if outcome == "sent" else "unknown" if outcome == "delivery_outcome_unknown" else "failed",
            acknowledged_operation_count=acknowledged,
            reason_code=reason,
            operation_receipts=receipts,
        )
        return self._finish_attempt(base, started)

    def _finish_attempt(self, document: dict[str, object], started: float) -> dict[str, object]:
        document["duration_ms"] = max(0, int((self.clock() - started) * 1000))
        with self._locked():
            ledger = self._ledger(); ledger["deliveries"][str(document["delivery_id"])] = document; self._save_ledger(ledger)
        return self._safe_delivery(document)

    def reconcile_delivery(self, delivery_id: str) -> dict[str, object]:
        """Return durable truth for one ID; never dispatch or retry it."""
        if not IDENTITY.fullmatch(delivery_id):
            raise ReportDeliveryError("delivery_identity_invalid")
        with self._locked():
            ledger = self._ledger(); item = ledger["deliveries"].get(delivery_id)
            if not isinstance(item, dict):
                raise ReportDeliveryError("delivery_identity_unknown")
            if item.get("outcome") == "dispatching":
                item["outcome"] = "delivery_outcome_unknown"
                item["acknowledgement_state"] = "unknown"
                item["reason_code"] = "controller_restart_after_dispatch"
                self._save_ledger(ledger)
            return self._safe_delivery(item)

    def resend_artifact(self, artifact_id: str, *, cancellation: object | None = None) -> dict[str, object]:
        """Explicitly create one new attempt for the same pinned artifact bytes."""
        return self._new_attempt(artifact_id, cancellation=cancellation)

    def status(self) -> dict[str, object]:
        try:
            with self._locked():
                ledger = self._ledger()
                artifacts = ledger["artifacts"]
                deliveries = ledger["deliveries"]
                unknown = sum(1 for item in deliveries.values() if isinstance(item, dict) and item.get("outcome") in {"dispatching", "delivery_outcome_unknown"})
        except ReportDeliveryError as error:
            return {
                "schema_version": DELIVERY_STATUS_SCHEMA, "state": "unavailable", "reason_code": error.code,
                "target": self.target.document() if self.target else None, "artifact_count": 0,
                "delivery_count": 0, "unknown_delivery_count": 0,
                "credential_name": TELEGRAM_CREDENTIAL_NAME,
                "credential_value_exposed": False, "endpoint_model_selectable": False,
                "target_model_selectable": False, "automatic_resend": False,
            }
        credential_declared = (
            self.environment.config.model.agent_environment.credentials.exec_environment_names.count(
                TELEGRAM_CREDENTIAL_NAME
            ) == 1
        )
        return {
            "schema_version": DELIVERY_STATUS_SCHEMA,
            "state": "ready" if self.target is not None and credential_declared else "unavailable",
            "reason_code": self.target_reason if self.target is None else None if credential_declared else "telegram_credential_not_configured",
            "target": self.target.document() if self.target else None,
            "artifact_count": len(artifacts), "delivery_count": len(deliveries),
            "unknown_delivery_count": unknown,
            "credential_name": TELEGRAM_CREDENTIAL_NAME,
            "credential_value_exposed": False, "endpoint_model_selectable": False,
            "target_model_selectable": False, "automatic_resend": False,
        }

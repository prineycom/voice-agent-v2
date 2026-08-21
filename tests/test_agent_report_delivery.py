from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from voice_agent_v2.agent_environment import AgentEnvironment, DockerResult
from voice_agent_v2.agent_environment_config import DEFAULT_DOCUMENT, parse_agent_config_v2
from voice_agent_v2.contracts import StageFailure
from voice_agent_v2.agent_report_delivery import (
    DELIVERY_CONFIG_SCHEMA, MAX_TEXT_CHUNK_BYTES, DeliveryCitation,
    ReportArtifactRequest, ReportDeliveryController, ReportDeliveryError,
    ReportDeliveryRequest, TelegramAcknowledgement, TelegramTarget,
)
from voice_agent_v2.agent_run import AgentRealtimeIdentity, AgentRun
from voice_agent_v2.local_lfm import PROVIDER_IDENTITY
from voice_agent_v2.schema import validate as validate_schema
from voice_agent_v2.tracer import CancellationToken
from tests.test_agent_environment import Disk, TEST_PREPARED_IMAGE
from tests.test_agent_research import ResearchDocker

TARGET = TelegramTarget(424242, 424242)
TOKEN = b"424242:synthetic-e42-token"
REPORT = b"# Synthetic report\n\nExact bytes \x00 and credential-looking 424242:synthetic-e42-token.\n"
SOURCE_HASH = hashlib.sha256(b"launch 2026 limit 42").hexdigest()


class DeliveryCredentialStore:
    def resolve(self, kind: str, name: str) -> bytes:
        if (kind, name) != ("environment", "TELEGRAM_BOT_TOKEN"):
            raise KeyError(name)
        return TOKEN

    def fingerprint(self, kind: str, name: str) -> str:
        return hashlib.sha256(kind.encode() + name.encode() + TOKEN).hexdigest()


class DeliveryDocker(ResearchDocker):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, bytes] = {}
        self.stream_events: list[tuple[str, str]] = []
        self.before_atomic_update = None

    @staticmethod
    def _argument(arguments: tuple[str, ...], name: str) -> str:
        return arguments[arguments.index(name) + 1]

    @staticmethod
    def _file_receipt(status: str, stderr: bytes = b"", details: dict[str, object] | None = None) -> DockerResult:
        document = {
            "status": status, "exit_code": 0 if status == "completed" else 2,
            "stdout_base64": "", "stderr_base64": base64.b64encode(stderr).decode(),
            "stdout_bytes": 0, "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stdout_truncated": False, "stderr_bytes": len(stderr),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(), "stderr_truncated": False,
            "cwd": "/workspace", "replayed": False, "details": details or {},
        }
        return DockerResult(0, json.dumps(document).encode(), accepted=True)

    def run(self, arguments: tuple[str, ...], *, stdin: bytes = b"", timeout: float = 15) -> DockerResult:
        if arguments[:2] == ("container", "exec") and "/usr/local/lib/voice-agent/agent-helper" in arguments:
            helper = arguments.index("/usr/local/lib/voice-agent/agent-helper")
            command = arguments[helper + 1]
            if command == "claim-execute" and self._argument(arguments, "--tool") == "file-write":
                request = json.loads(stdin)
                path = str(request["path"])
                if self.before_atomic_update is not None:
                    callback, self.before_atomic_update = self.before_atomic_update, None
                    callback(self)
                current = self.files.get(path)
                if current is None or hashlib.sha256(current).hexdigest() != request["expected_current_sha256"]:
                    return self._file_receipt("failed", b"expected_current_sha256_mismatch")
                content = base64.b64decode(request["data_base64"])
                self.files[path] = content
                return self._file_receipt("completed", details={
                    "kind": "file_write", "atomic": True, "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(), "mode": "0600",
                })
            if command == "stream-in":
                root = self._argument(arguments, "--root")
                relative = self._argument(arguments, "--path")
                transfer_id = self._argument(arguments, "--transfer-id")
                path = f"/{root}/{relative}"
                digest = hashlib.sha256(stdin).hexdigest()
                self.files[path] = stdin
                self.stream_events.append(("stored", path))
                return DockerResult(0, json.dumps({
                    "atomic": True, "bytes": len(stdin), "mode": "0600", "path": path,
                    "sha256": digest, "status": "stored", "transfer_id": transfer_id,
                }).encode(), accepted=True)
            if command == "stream-stat":
                path = f"/{self._argument(arguments, '--root')}/{self._argument(arguments, '--path')}"
                if path not in self.files:
                    return DockerResult(2, b"{}")
                content = self.files[path]
                self.stream_events.append(("stat", path))
                return DockerResult(0, json.dumps({
                    "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                }).encode())
            if command == "stream-out":
                path = f"/{self._argument(arguments, '--root')}/{self._argument(arguments, '--path')}"
                self.stream_events.append(("read", path))
                return DockerResult(0, self.files[path], accepted=True)
        return super().run(arguments, stdin=stdin, timeout=timeout)


class FakeTelegram:
    def __init__(self, outcomes: list[str] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.operations = []
        self.fixture: Fixture | None = None

    def send(self, credential: bytes, operation):
        assert credential == TOKEN
        assert operation.target == TARGET
        assert operation.target.chat_id == operation.target.user_id == 424242
        assert operation.target.message_thread_id is None
        if self.fixture is not None:
            assert "/workspace/reports/e4-2.md" in self.fixture.docker.files
            assert any(path.startswith("/workspace/reports/.delivery/") for path in self.fixture.docker.files)
        self.operations.append(operation)
        outcome = self.outcomes.pop(0) if self.outcomes else "acknowledged"
        if outcome == "acknowledged":
            return TelegramAcknowledgement(
                outcome, TARGET, operation.kind, operation.payload_byte_count,
                operation.payload_sha256, "opaque-synthetic-receipt", "telegram_acknowledged",
            )
        if outcome == "rejected":
            return TelegramAcknowledgement(outcome, reason_code="telegram_rejected")
        if outcome == "mismatch":
            alternate = TelegramTarget(999999, 999999)
            return TelegramAcknowledgement(
                "acknowledged", alternate, operation.kind, operation.payload_byte_count,
                operation.payload_sha256, "wrong-target-receipt", "telegram_acknowledged",
            )
        return TelegramAcknowledgement("unknown", reason_code="telegram_delivery_uncertain")


class Fixture:
    def __init__(self, *, target: bool = True, credential: bool = True, transport: FakeTelegram | None = None) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agent-report-delivery-")
        root = Path(self.temp.name)
        self.state = root / "private"
        self.state.mkdir(mode=0o700)
        if target:
            target_path = self.state / "telegram-delivery.json"
            target_path.write_text(json.dumps({
                "schema_version": DELIVERY_CONFIG_SCHEMA,
                "target": {"chat_id": 424242, "user_id": 424242, "message_thread_id": None},
            }))
            target_path.chmod(0o600)
        document = json.loads(json.dumps(DEFAULT_DOCUMENT))
        if credential:
            document["agent_environment"]["credentials"]["exec_environment_names"] = ["TELEGRAM_BOT_TOKEN"]
        config = parse_agent_config_v2(json.dumps(document).encode())
        self.docker = DeliveryDocker()
        self.environment = AgentEnvironment(
            config, state_root=self.state, workspace=root / "workspace", cache=root / "cache",
            runner=self.docker, credential_store=DeliveryCredentialStore(),
            prepared_image=TEST_PREPARED_IMAGE,
            disk_usage=lambda _path: Disk(100 << 30, 1, 99 << 30),
        )
        self.transport = transport or FakeTelegram()
        self.transport.fixture = self
        self.controller = ReportDeliveryController(self.environment, transport=self.transport)

    def close(self) -> None:
        self.temp.cleanup()


def citation(identifier: str = "a" * 32) -> DeliveryCitation:
    return DeliveryCitation(identifier, "https://source.synthetic.test/report", 20, SOURCE_HASH)


def request(*, mode: str = "both", report: bytes = REPORT) -> ReportDeliveryRequest:
    return ReportDeliveryRequest(
        report, "reports/e4-2.md", "text/markdown", mode,
        "Launch is planned for 2026 and the limit is 42.", (citation(),),
    )


class NaturalResearchDeliveryModel:
    provider_mode = "local"
    provider_identity = PROVIDER_IDENTITY

    def __init__(self) -> None:
        self.step = 0
        self.fetch_id = ""

    def decide(self, raw: str, _cancellation) -> dict[str, object]:
        document = json.loads(raw)
        self.step += 1
        if self.step == 1:
            return {
                "kind": "operation", "tool": "web.fetch",
                "arguments": {"url": "https://source-a.synthetic.test/report", "save_path": "/workspace/research/source.html"},
            }
        if self.step == 2:
            self.fetch_id = document["history"][-1]["operation"]["call_id"]
            return {
                "kind": "operation", "tool": "report.deliver",
                "arguments": {
                    "report_base64": base64.b64encode(REPORT).decode(),
                    "relative_path": "reports/e4-2.md", "media_type": "text/markdown",
                    "mode": "both", "summary": "Launch is 2026; limit 42.",
                    "citation_receipt_ids": [self.fetch_id],
                },
            }
        return {
            "kind": "final", "answer": "Saved and sent the 2026 report with limit 42.",
            "citations": [{
                "receipt_id": self.fetch_id, "claims": ["launch 2026", "limit 42"],
                "spans": ["2026 report", "limit 42"],
            }],
        }

    def cancel(self) -> None:
        return None


class ReportDeliverySliceTests(unittest.TestCase):
    def test_natural_research_saves_before_exact_text_and_document_ack(self) -> None:
        fixture = Fixture()
        try:
            result = AgentRun(
                fixture.environment, model=NaturalResearchDeliveryModel(), delivery=fixture.controller,
            ).run(
                transcript="Research current facts, save a report, and send text and document to me.",
                identity=AgentRealtimeIdentity("session-e42", 1, "turn-e42", "request-e42", 1),
            )
            self.assertEqual((result.terminal, result.operations, len(result.deliveries)), ("completed", 2, 1))
            delivery = result.deliveries[0]
            self.assertEqual(delivery["outcome"], "sent")
            self.assertEqual(delivery["acknowledgement_state"], "acknowledged")
            self.assertEqual(delivery["target"], TARGET.document())
            self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], REPORT)
            self.assertEqual(delivery["artifact_byte_count"], len(REPORT))
            self.assertEqual(delivery["artifact_sha256"], hashlib.sha256(REPORT).hexdigest())
            self.assertNotIn(TOKEN.decode(), json.dumps(delivery))
            self.assertNotIn(str(Path(fixture.temp.name)), json.dumps(delivery))
            self.assertEqual([item.kind for item in fixture.transport.operations], ["text", "document"])
            text, document = fixture.transport.operations
            self.assertLessEqual(text.payload_byte_count, MAX_TEXT_CHUNK_BYTES)
            self.assertIn(result.citations[0].receipt_id.encode(), text.request_body)
            self.assertEqual((document.document_byte_count, document.document_sha256), (len(REPORT), hashlib.sha256(REPORT).hexdigest()))
            self.assertIn(REPORT, document.request_body)
            self.assertEqual(fixture.environment.redact_display(REPORT), REPORT.replace(TOKEN, b"[REDACTED]"))
            root = Path(__file__).resolve().parents[1]
            validate_schema(result.document(), json.loads((root / "contracts/agent-run.v4.schema.json").read_text()))
            validate_schema(delivery, json.loads((root / "contracts/telegram-delivery.v2.schema.json").read_text()))
            ledger = json.loads(fixture.controller.ledger_path.read_text())
            validate_schema(
                ledger["artifacts"][delivery["artifact_id"]],
                json.loads((root / "contracts/saved-report.v2.schema.json").read_text()),
            )
        finally:
            fixture.close()

    def test_utf8_text_chunking_is_fixed_bounded_and_receipt_cited(self) -> None:
        fixture = Fixture()
        try:
            summary = "Паша, проверенный факт 2026. " * 250
            result = fixture.controller.save_and_deliver(ReportDeliveryRequest(
                REPORT, "reports/e4-2.md", "text/markdown", "text", summary, (citation(),),
            ))
            self.assertEqual(result["outcome"], "sent")
            self.assertGreater(len(fixture.transport.operations), 1)
            self.assertLessEqual(len(fixture.transport.operations), 4)
            for operation in fixture.transport.operations:
                self.assertEqual(operation.kind, "text")
                self.assertLessEqual(operation.payload_byte_count, MAX_TEXT_CHUNK_BYTES)
                text = json.loads(operation.request_body)["text"]
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), operation.payload_sha256)
            rendered = b"\n".join(item.request_body for item in fixture.transport.operations)
            self.assertIn(citation().receipt_id.encode(), rendered)
        finally:
            fixture.close()

    def test_target_is_restart_pinned_and_model_content_cannot_select_authority(self) -> None:
        fixture = Fixture()
        try:
            target_path = fixture.state / "telegram-delivery.json"
            target_path.write_text(json.dumps({
                "schema_version": DELIVERY_CONFIG_SCHEMA,
                "target": {"chat_id": 999999, "user_id": 999999, "message_thread_id": None},
            }))
            target_path.chmod(0o600)
            hostile = ReportDeliveryRequest(
                b'{"chat_id":999999,"endpoint":"https://evil.invalid","token":"ambient"}',
                "reports/e4-2.md", "application/json", "document", "", (citation(),),
            )
            delivery = fixture.controller.save_and_deliver(hostile)
            self.assertEqual(delivery["target"], TARGET.document())
            self.assertEqual(fixture.transport.operations[0].target, TARGET)
            body = fixture.transport.operations[0].request_body
            self.assertIn(hostile.report_bytes, body)
            self.assertNotIn(b'name="chat_id"\r\n\r\n999999', body)
            restarted_transport = FakeTelegram()
            restarted_transport.fixture = fixture
            restarted = ReportDeliveryController(fixture.environment, transport=restarted_transport)
            self.assertEqual(restarted.status()["target"]["chat_id"], 999999)
        finally:
            fixture.close()

    def test_unknown_is_never_automatic_retried_and_explicit_restart_resend_is_same_bytes(self) -> None:
        first_transport = FakeTelegram(["unknown"])
        fixture = Fixture(transport=first_transport)
        try:
            unknown = fixture.controller.save_and_deliver(request(mode="document"))
            self.assertEqual(unknown["outcome"], "delivery_outcome_unknown")
            self.assertEqual(len(first_transport.operations), 1)
            artifact_id, delivery_id = unknown["artifact_id"], unknown["delivery_id"]
            restarted_transport = FakeTelegram()
            restarted_transport.fixture = fixture
            restarted = ReportDeliveryController(fixture.environment, transport=restarted_transport)
            reconciled = restarted.reconcile_delivery(delivery_id)
            self.assertEqual(reconciled["outcome"], "delivery_outcome_unknown")
            self.assertEqual(restarted_transport.operations, [])
            class ResendModel:
                provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
                def __init__(self): self.step = 0
                def decide(self, _request, _cancellation):
                    self.step += 1
                    if self.step == 1:
                        return {"kind": "operation", "tool": "report.deliver", "arguments": {"action": "resend", "artifact_id": artifact_id}}
                    return {"kind": "final", "answer": "Explicit same-byte resend acknowledged."}
                def cancel(self): return None

            run = AgentRun(fixture.environment, model=ResendModel(), delivery=restarted).run(
                transcript="Explicitly resend the known saved report without new research.",
                identity=AgentRealtimeIdentity("session-e42-resend", 1, "turn-e42-resend", "request-e42-resend", 1),
            )
            resent = run.deliveries[0]
            self.assertNotEqual(resent["delivery_id"], delivery_id)
            self.assertEqual(resent["outcome"], "sent")
            self.assertEqual(restarted_transport.operations[0].document_sha256, hashlib.sha256(REPORT).hexdigest())
            self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], REPORT)
            self.assertEqual(fixture.docker.research_requests, [])
        finally:
            fixture.close()

    def test_known_ack_reconciles_without_duplicate_and_changed_artifact_fails_closed(self) -> None:
        fixture = Fixture()
        try:
            sent = fixture.controller.save_and_deliver(request(mode="document"))
            calls = len(fixture.transport.operations)
            restarted = ReportDeliveryController(fixture.environment, transport=fixture.transport)
            self.assertEqual(restarted.reconcile_delivery(sent["delivery_id"])["outcome"], "sent")
            self.assertEqual(len(fixture.transport.operations), calls)
            fixture.docker.files["/workspace/reports/e4-2.md"] += b"edited"
            failed = restarted.resend_artifact(sent["artifact_id"])
            self.assertEqual((failed["outcome"], failed["reason_code"]), ("failed", "saved_artifact_changed"))
            self.assertEqual(len(fixture.transport.operations), calls)
        finally:
            fixture.close()

    def test_outage_rejection_cancellation_and_missing_readiness_preserve_report(self) -> None:
        for outcome, expected in (("unknown", "delivery_outcome_unknown"), ("mismatch", "delivery_outcome_unknown"), ("rejected", "failed")):
            with self.subTest(outcome=outcome):
                fixture = Fixture(transport=FakeTelegram([outcome]))
                try:
                    result = fixture.controller.save_and_deliver(request(mode="document"))
                    self.assertEqual(result["outcome"], expected)
                    self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], REPORT)
                    self.assertEqual(fixture.environment.status()["state"], "running")
                finally:
                    fixture.close()
        fixture = Fixture()
        try:
            token = CancellationToken(); token.cancel()
            cancelled = fixture.controller.save_and_deliver(request(mode="document"), cancellation=token)
            self.assertEqual((cancelled["outcome"], cancelled["reason_code"]), ("failed", "delivery_cancelled"))
            self.assertEqual(fixture.transport.operations, [])
            self.assertFalse(cancelled["remote_effects_retracted"])
        finally:
            fixture.close()
        for target, credential, reason in (
            (False, True, "telegram_target_config_unavailable"),
            (True, False, "telegram_credential_not_configured"),
        ):
            with self.subTest(reason=reason):
                fixture = Fixture(target=target, credential=credential)
                try:
                    failed = fixture.controller.save_and_deliver(request(mode="document"))
                    self.assertEqual((failed["outcome"], failed["reason_code"]), ("failed", reason))
                    self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], REPORT)
                    self.assertEqual(fixture.transport.operations, [])
                finally:
                    fixture.close()

    def test_later_ru_summary_resolves_trusted_path_with_zero_network_or_host_read(self) -> None:
        fixture = Fixture()
        try:
            saved = fixture.controller.save_and_deliver(request(mode="document"))
            research_count = len(fixture.docker.research_requests)
            restarted = ReportDeliveryController(fixture.environment, transport=fixture.transport)

            class Model:
                provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
                def __init__(self): self.step = 0
                def decide(self, raw, _cancellation):
                    self.step += 1
                    if self.step == 1:
                        return {"kind":"operation", "tool":"report.artifact", "arguments":{
                            "action":"local_summary", "relative_path":"reports/e4-2.md",
                        }}
                    history = json.loads(raw)["history"][-1]
                    self.test.assertIn(b"Synthetic report", base64.b64decode(history["result"]["receipt"]["stdout"]["data_base64"]))
                    details = history["result"]["receipt"]["details"]
                    return {"kind":"final", "answer":f"Найден отчёт revision {details['revision']} hash {details['sha256']}."}
                def cancel(self): return None

            model = Model(); model.test = self
            result = AgentRun(fixture.environment, model=model, delivery=restarted).run(
                transcript="Найди прошлый отчёт и кратко перескажи локально, без Интернета.",
                identity=AgentRealtimeIdentity("session-e43-later", 1, "turn-e43-summary", "request-e43-summary", 1),
            )
            operation = result.artifact_operations[0]
            self.assertEqual((operation["outcome"], operation["revision"]), ("resolved", 1))
            self.assertEqual(operation["artifact_id"], saved["artifact_id"])
            self.assertEqual(operation["external_call_count"], 0)
            self.assertFalse(operation["host_path_read"])
            self.assertEqual(len(fixture.docker.research_requests), research_count)
            self.assertNotIn(str(Path(fixture.temp.name)), json.dumps(result.document()))
        finally:
            fixture.close()

    def test_atomic_local_update_links_revision_and_resends_current_unredacted_bytes(self) -> None:
        fixture = Fixture()
        updated = b"# Updated report\n\nCurrent exact token " + TOKEN + b".\n"
        try:
            saved = fixture.controller.save_and_deliver(request(mode="document"))
            prior_calls = len(fixture.transport.operations)

            class Model:
                provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
                def __init__(self): self.step = 0
                def decide(self, _raw, _cancellation):
                    self.step += 1
                    if self.step == 1:
                        return {"kind":"operation", "tool":"report.artifact", "arguments":{
                            "action":"local_update", "artifact_id":saved["artifact_id"],
                            "expected_revision":1, "expected_sha256":saved["artifact_sha256"],
                            "report_base64":base64.b64encode(updated).decode(), "mode":"document",
                            "summary":"Updated locally.",
                        }}
                    if self.step == 2:
                        return {"kind":"operation", "tool":"report.deliver", "arguments":{
                            "action":"resend", "artifact_id":saved["artifact_id"],
                        }}
                    return {"kind":"final", "answer":"Updated revision 2 and resent current bytes."}
                def cancel(self): return None

            result = AgentRun(fixture.environment, model=Model(), delivery=fixture.controller).run(
                transcript="Update the saved report locally and resend its current bytes without research.",
                identity=AgentRealtimeIdentity("session-e43-update", 1, "turn-e43-update", "request-e43-update", 1),
            )
            operation, resent = result.artifact_operations[0], result.deliveries[0]
            self.assertEqual((operation["outcome"], operation["revision"], operation["atomic_replacement"]), ("updated", 2, True))
            self.assertEqual(operation["prior_receipt_id"], saved["artifact_receipt_id"])
            self.assertEqual(resent["artifact_receipt_id"], operation["artifact_receipt_id"])
            self.assertEqual((resent["artifact_revision"], resent["artifact_byte_count"], resent["artifact_sha256"]), (2, len(updated), hashlib.sha256(updated).hexdigest()))
            self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], updated)
            self.assertEqual(len(fixture.docker.research_requests), 0)
            resent_operation = fixture.transport.operations[prior_calls]
            self.assertIn(updated, resent_operation.request_body)
            self.assertIn(TOKEN, resent_operation.request_body)
            self.assertNotEqual(fixture.environment.redact_display(updated), updated)
            root = Path(__file__).resolve().parents[1]
            validate_schema(operation, json.loads((root / "contracts/report-artifact-operation.v1.schema.json").read_text()))
            validate_schema(resent, json.loads((root / "contracts/telegram-delivery.v2.schema.json").read_text()))
            ledger = json.loads(fixture.controller.ledger_path.read_text())
            validate_schema(ledger["revisions"][operation["artifact_receipt_id"]], json.loads((root / "contracts/report-artifact-revision.v1.schema.json").read_text()))
        finally:
            fixture.close()

    def test_concurrent_writer_conflict_is_visible_and_unexpected_bytes_are_not_overwritten(self) -> None:
        fixture = Fixture()
        concurrent = b"background writer won\n"
        try:
            saved = fixture.controller.save_and_deliver(request(mode="document"))
            fixture.docker.before_atomic_update = lambda docker: docker.files.__setitem__(
                "/workspace/reports/e4-2.md", concurrent,
            )
            operation, raw = fixture.controller.manage_artifact(ReportArtifactRequest.parse({
                "action":"local_update", "artifact_id":saved["artifact_id"],
                "expected_revision":1, "expected_sha256":saved["artifact_sha256"],
                "report_text":"unexpected overwrite", "mode":"document", "summary":"local",
            }), successful_research={})
            self.assertEqual(raw, b"")
            self.assertEqual((operation["outcome"], operation["conflict"], operation["atomic_replacement"]), ("conflict", True, False))
            self.assertEqual(operation["reason_code"], "artifact_changed_concurrently")
            self.assertEqual(fixture.docker.files["/workspace/reports/e4-2.md"], concurrent)
            ledger = json.loads(fixture.controller.ledger_path.read_text())
            self.assertEqual(ledger["artifacts"][saved["artifact_id"]]["revision"], 1)
        finally:
            fixture.close()

    def test_explicit_refresh_uses_new_fetch_receipt_then_updates_only_once(self) -> None:
        fixture = Fixture()
        refreshed = b"# Refreshed\n\nLaunch 2027 selected claim.\n"
        try:
            saved = fixture.controller.save_and_deliver(request(mode="document"))

            class Model:
                provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
                def __init__(self): self.step = 0; self.fetch = None
                def decide(self, raw, _cancellation):
                    self.step += 1
                    if self.step == 1:
                        return {"kind":"operation", "tool":"web.fetch", "arguments":{
                            "url":"https://source-a.synthetic.test/refresh", "save_path":"/workspace/research/e43-refresh.html",
                        }}
                    if self.step == 2:
                        self.fetch = json.loads(raw)["history"][-1]["operation"]["call_id"]
                        return {"kind":"operation", "tool":"report.artifact", "arguments":{
                            "action":"bounded_refresh", "artifact_id":saved["artifact_id"],
                            "expected_revision":1, "expected_sha256":saved["artifact_sha256"],
                            "report_base64":base64.b64encode(refreshed).decode(), "mode":"document",
                            "summary":"Selected launch claim refreshed.", "citation_receipt_ids":[self.fetch],
                        }}
                    return {"kind":"final", "answer":"The refreshed source supports the selected launch claim.", "citations":[{
                        "receipt_id":self.fetch, "claims":["selected launch claim"], "spans":["selected launch claim"],
                    }]}
                def cancel(self): return None

            result = AgentRun(fixture.environment, model=Model(), delivery=fixture.controller).run(
                transcript="Refresh only the selected launch claim, update citations, but do not resend.",
                identity=AgentRealtimeIdentity("session-e43-refresh", 1, "turn-e43-refresh", "request-e43-refresh", 1),
            )
            operation = result.artifact_operations[0]
            self.assertEqual((operation["action"], operation["outcome"], operation["external_call_count"]), ("bounded_refresh", "updated", 1))
            self.assertEqual(operation["research_receipt_ids"], [result.research_receipts[0]["receipt_id"]])
            self.assertEqual(len(fixture.docker.research_requests), 1)
            self.assertEqual(fixture.transport.operations[-1].document_sha256, hashlib.sha256(REPORT).hexdigest())
        finally:
            fixture.close()

    def test_artifact_admission_rejects_selection_lifecycle_and_host_authority(self) -> None:
        for arguments in (
            {"action":"local_summary", "artifact_id":"a" * 32, "environment":"other"},
            {"action":"local_summary", "artifact_id":"a" * 32, "container_id":"b" * 64},
            {"action":"local_summary", "relative_path":"/home/pasha/sentinel"},
            {"action":"local_update", "artifact_id":"a" * 32, "expected_revision":1, "expected_sha256":"0" * 64, "report_text":"x", "mode":"document", "summary":"x", "runtime":"host"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ReportDeliveryError):
                ReportArtifactRequest.parse(arguments)
        self.assertNotIn("environment.lifecycle", __import__("voice_agent_v2.agent_run", fromlist=["AGENT_TOOLS"]).AGENT_TOOLS)

    def test_artifact_failure_emits_one_terminal_failure_and_next_voice_run_is_clean(self) -> None:
        fixture = Fixture()
        class FailureModel:
            provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
            def decide(self, _raw, _cancellation):
                return {"kind":"operation", "tool":"report.artifact", "arguments":{
                    "action":"local_summary", "artifact_id":"f" * 32,
                }}
            def cancel(self): return None
        class HealthyModel:
            provider_mode = "local"; provider_identity = PROVIDER_IDENTITY
            def decide(self, _raw, _cancellation): return {"kind":"final", "answer":"Обычный голосовой ответ здоров."}
            def cancel(self): return None
        try:
            with self.assertRaises(StageFailure) as failure:
                AgentRun(fixture.environment, model=FailureModel(), delivery=fixture.controller).run(
                    transcript="Find a missing report.",
                    identity=AgentRealtimeIdentity("session-e43-fail", 1, "turn-e43-fail", "request-e43-fail", 1),
                )
            self.assertEqual(failure.exception.code, "saved_artifact_unknown")
            following = AgentRun(fixture.environment, model=HealthyModel(), delivery=fixture.controller).run(
                transcript="Скажи обычную короткую фразу.",
                identity=AgentRealtimeIdentity("session-e43-next", 2, "turn-e43-next", "request-e43-next", 1),
            )
            self.assertEqual((following.terminal, following.operations, following.answer), ("completed", 0, "Обычный голосовой ответ здоров."))
            self.assertEqual((following.artifact_operations, following.deliveries), ((), ()))
        finally:
            fixture.close()

    def test_status_is_content_minimal_and_contract_valid(self) -> None:
        fixture = Fixture(transport=FakeTelegram(["unknown"]))
        try:
            fixture.controller.save_and_deliver(request(mode="document"))
            status = fixture.controller.status()
            self.assertEqual(status["target"], TARGET.document())
            self.assertEqual(status["unknown_delivery_count"], 1)
            self.assertFalse(status["credential_value_exposed"])
            self.assertFalse(status["target_model_selectable"])
            self.assertFalse(status["endpoint_model_selectable"])
            self.assertFalse(status["automatic_resend"])
            rendered = json.dumps(status)
            self.assertNotIn(TOKEN.decode(), rendered)
            self.assertNotIn(REPORT.decode("utf-8", "ignore"), rendered)
            root = Path(__file__).resolve().parents[1]
            self.assertFalse(status["prompt_injection_prevented"])
            self.assertTrue(status["open_sandbox_authority_accepted"])
            self.assertEqual(status["display_redaction_scope"], "display_only")
            validate_schema(status, json.loads((root / "contracts/telegram-delivery-status.v2.schema.json").read_text()))
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()

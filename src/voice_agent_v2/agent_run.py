"""Versioned production multi-step AgentRun over the exact local LFM and Docker.

The loop owns decision budgets, full realtime identity, opaque call IDs and late
result custody.  It never owns AgentEnvironment lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import threading
import time
from typing import Callable, Mapping, Protocol
import uuid

from .agent_environment import AgentEnvironment, AgentEnvironmentError, CallReceipt, HELPERS
from .agent_report_delivery import (
    ReportDeliveryController, ReportDeliveryError, ReportDeliveryRequest,
)
from .agent_research import CitationRecord, CitationRequest, WEB_TOOLS, bind_citations
from .contracts import StageFailure, valid_correlation_id
from .local_lfm import LocalLFMProvider, MODEL_ALIAS, PROVIDER_IDENTITY
from .tracer import CancellationToken

AGENT_RUN_VERSION = "voice-agent.agent-run.v3"
DECISION_VERSION = "voice-agent.agent-decision.v3"
OPERATION_VERSION = "voice-agent.agent-operation.v1"
RESULT_VERSION = "voice-agent.agent-operation-result.v1"
BUDGET_VERSION = "voice-agent.agent-budget.v1"
IDENTITY_VERSION = "voice-agent.agent-realtime-identity.v1"
CANCELLATION_VERSION = "voice-agent.agent-cancellation.v1"
MAX_DECISION_INPUT_BYTES = 16_384
REPORT_DELIVERY_TOOL = "report.deliver"
AGENT_TOOLS = (*HELPERS, REPORT_DELIVERY_TOOL)


@dataclass(frozen=True, slots=True)
class AgentRealtimeIdentity:
    session_id: str
    stream_epoch: int
    turn_id: str
    request_id: str
    turn_generation: int

    def __post_init__(self) -> None:
        if (
            not all(valid_correlation_id(value) for value in (self.session_id, self.turn_id, self.request_id))
            or any(type(value) is not int or not 1 <= value <= 1_000_000_000 for value in (self.stream_epoch, self.turn_generation))
        ):
            raise ValueError("invalid AgentRun realtime identity")

    def document(self) -> dict[str, object]:
        return {
            "schema_version": IDENTITY_VERSION,
            "session_id": self.session_id,
            "stream_epoch": self.stream_epoch,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "turn_generation": self.turn_generation,
        }


@dataclass(frozen=True, slots=True)
class AgentBudget:
    maximum_decisions: int
    active_deadline_seconds: int
    maximum_operation_seconds: int

    def document(self) -> dict[str, object]:
        return {
            "schema_version": BUDGET_VERSION,
            "maximum_decisions": self.maximum_decisions,
            "active_deadline_seconds": self.active_deadline_seconds,
            "maximum_operation_seconds": self.maximum_operation_seconds,
        }


@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: str
    tool: str | None = None
    arguments: Mapping[str, object] | None = None
    answer: str | None = None
    citations: tuple[CitationRequest, ...] = ()

    @classmethod
    def parse(cls, raw: object) -> "AgentDecision":
        if not isinstance(raw, dict) or raw.get("kind") not in {"operation", "final"}:
            raise StageFailure("llm_provider", "agent_decision_invalid")
        if raw["kind"] == "final":
            if set(raw) not in ({"kind", "answer"}, {"kind", "answer", "citations"}) or not isinstance(raw.get("answer"), str):
                raise StageFailure("llm_provider", "agent_decision_invalid")
            answer = raw["answer"].strip()
            if not answer or len(answer.encode("utf-8")) > 2_000:
                raise StageFailure("llm_provider", "agent_decision_invalid")
            citations = CitationRequest.parse_many(raw.get("citations", []))
            return cls("final", answer=answer, citations=citations)
        if (
            set(raw) != {"kind", "tool", "arguments"}
            or raw.get("tool") not in AGENT_TOOLS
            or not isinstance(raw.get("arguments"), dict)
        ):
            raise StageFailure("llm_provider", "agent_decision_invalid")
        try:
            copied = json.loads(json.dumps(raw["arguments"], ensure_ascii=False))
        except (TypeError, ValueError, RecursionError) as error:
            raise StageFailure("llm_provider", "agent_decision_invalid") from error
        if len(json.dumps(copied, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            raise StageFailure("llm_provider", "agent_decision_invalid")
        return cls("operation", tool=str(raw["tool"]), arguments=copied)


class DecisionModel(Protocol):
    provider_mode: str
    provider_identity: str
    def decide(self, request: str, cancellation: CancellationToken) -> object: ...
    def cancel(self) -> None: ...


class ExactLocalLFMAgentAdapter:
    """The single production decision adapter; exact identity and zero fallback."""

    provider_mode = "local"
    provider_identity = PROVIDER_IDENTITY
    model_alias = MODEL_ALIAS
    automatic_fallback = False

    def __init__(self, provider: LocalLFMProvider | None = None) -> None:
        self.provider = provider or LocalLFMProvider(request_timeout_seconds=120)
        if (
            self.provider.provider_mode != "local"
            or self.provider.provider_identity != PROVIDER_IDENTITY
        ):
            raise ValueError("exact local LFM identity is required")

    def decide(self, request: str, cancellation: CancellationToken) -> object:
        return self.provider.agent_decision(request=request, cancellation=cancellation)

    def cancel(self) -> None:
        self.provider.cancel_request()


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: str
    identity: AgentRealtimeIdentity
    answer: str
    decisions: int
    operations: int
    terminal: str
    citations: tuple[CitationRecord, ...] = ()
    research_receipts: tuple[Mapping[str, object], ...] = ()
    deliveries: tuple[Mapping[str, object], ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "schema_version": AGENT_RUN_VERSION,
            "run_id": self.run_id,
            "identity": self.identity.document(),
            "answer": self.answer,
            "decisions": self.decisions,
            "operations": self.operations,
            "terminal": self.terminal,
            "provider_mode": "local",
            "provider_identity": PROVIDER_IDENTITY,
            "automatic_fallback": False,
            "citations": [item.document() for item in self.citations],
            "research_receipts": [dict(item) for item in self.research_receipts],
            "deliveries": [dict(item) for item in self.deliveries],
        }


class AgentRun:
    def __init__(
        self,
        environment: AgentEnvironment,
        *,
        model: DecisionModel | None = None,
        observation: Callable[[dict[str, object]], None] | None = None,
        delivery: ReportDeliveryController | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.environment = environment
        self.model = model or ExactLocalLFMAgentAdapter()
        if self.model.provider_mode != "local" or self.model.provider_identity != PROVIDER_IDENTITY:
            raise ValueError("AgentRun cannot use a fallback model")
        self.observation = observation
        self.delivery = delivery or ReportDeliveryController(environment)
        self.clock = clock
        config = environment.config.model
        self.budget = AgentBudget(
            config.agent.max_decisions,
            config.agent.active_deadline_seconds,
            config.agent_environment.lifecycle.command_timeout_seconds,
        )
        self._lock = threading.Lock()
        self._active_identity: AgentRealtimeIdentity | None = None
        self._active_call: tuple[str, str] | None = None
        self._closed: set[AgentRealtimeIdentity] = set()

    def _live(self, identity: AgentRealtimeIdentity, cancellation: CancellationToken) -> bool:
        with self._lock:
            return self._active_identity == identity and identity not in self._closed and not cancellation.cancelled

    def cancel(self) -> None:
        with self._lock:
            identity = self._active_identity
            active_call = self._active_call
            if identity is not None:
                self._closed.add(identity)
                self._active_identity = None
        self.model.cancel()
        if active_call is not None:
            call_id, container_id = active_call
            self.environment.cancel_call(call_id, container_id)

    def _request(
        self,
        transcript: str,
        history: list[dict[str, object]],
        identity: AgentRealtimeIdentity,
        run_id: str,
    ) -> str:
        document = {
            "schema_version": AGENT_RUN_VERSION,
            "run_id": run_id,
            "identity": identity.document(),
            "budget": self.budget.document(),
            "user_request": transcript,
            "allowed_tools": list(AGENT_TOOLS),
            "history": history,
        }
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_DECISION_INPUT_BYTES:
            raise StageFailure("llm_provider", "agent_context_out_of_bounds")
        return encoded

    def run(
        self,
        *,
        transcript: str,
        identity: AgentRealtimeIdentity,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        if not transcript.strip() or len(transcript.encode("utf-8")) > 4_096:
            raise StageFailure("llm_provider", "transcript_out_of_bounds")
        token = cancellation or CancellationToken()
        with self._lock:
            if self._active_identity is not None:
                raise StageFailure("llm_provider", "agent_run_capacity_unavailable")
            self._active_identity = identity
            self._closed.discard(identity)
        unregister = token.register(self.cancel)
        deadline = self.clock() + self.budget.active_deadline_seconds
        history: list[dict[str, object]] = []
        run_id = uuid.uuid4().hex
        operations = 0
        successful_research: dict[str, Mapping[str, object]] = {}
        research_receipts: list[Mapping[str, object]] = []
        deliveries: list[Mapping[str, object]] = []
        try:
            for decision_number in range(1, self.budget.maximum_decisions + 1):
                if not self._live(identity, token):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                if self.clock() >= deadline:
                    raise StageFailure("llm_provider", "agent_run_timeout")
                request = self._request(transcript.strip(), history, identity, run_id)
                decision = AgentDecision.parse(self.model.decide(request, token))
                if not self._live(identity, token):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                if decision.kind == "final":
                    assert decision.answer is not None
                    citations = bind_citations(decision.answer, decision.citations, successful_research)
                    if self.observation is not None:
                        self.observation({"transition": "agent_run_completed", "decision_count": decision_number, "operation_count": operations, "citation_count": len(citations), "research_receipt_count": len(research_receipts)})
                    display_answer = self.environment.redact_display(
                        decision.answer.encode("utf-8")
                    ).decode("utf-8", "replace")
                    display_citations = tuple(CitationRecord(
                        item.receipt_id, item.displayed_url,
                        self.environment.redact_display(item.title.encode()).decode("utf-8", "replace") if item.title else None,
                        item.retrieved_epoch_seconds, item.byte_count, item.sha256, item.truncated,
                        item.redirects, item.cache_used, item.cache_stale, item.network_error,
                        item.extraction_error,
                        tuple(self.environment.redact_display(value.encode()).decode("utf-8", "replace") for value in item.claims),
                        tuple(self.environment.redact_display(value.encode()).decode("utf-8", "replace") for value in item.spans),
                    ) for item in citations)
                    return AgentRunResult(
                        run_id, identity, display_answer, decision_number, operations, "completed",
                        display_citations, tuple(research_receipts), tuple(deliveries),
                    )
                assert decision.tool is not None and decision.arguments is not None
                call_id = uuid.uuid4().hex
                facts = self.environment.ensure_running()
                with self._lock:
                    if (
                        self._active_identity != identity
                        or identity in self._closed
                        or token.cancelled
                    ):
                        raise StageFailure("llm_provider", "selected_provider_cancelled")
                    self._active_call = (call_id, facts.container_id)
                try:
                    if decision.tool == REPORT_DELIVERY_TOOL:
                        try:
                            if set(decision.arguments) == {"action", "artifact_id"} and decision.arguments.get("action") == "resend":
                                artifact_id = decision.arguments.get("artifact_id")
                                if not isinstance(artifact_id, str):
                                    raise ReportDeliveryError("artifact_identity_invalid")
                                delivery_result = self.delivery.resend_artifact(
                                    artifact_id, cancellation=token,
                                )
                            elif set(decision.arguments) == {"action", "delivery_id"} and decision.arguments.get("action") == "reconcile":
                                delivery_id = decision.arguments.get("delivery_id")
                                if not isinstance(delivery_id, str):
                                    raise ReportDeliveryError("delivery_identity_invalid")
                                delivery_result = self.delivery.reconcile_delivery(delivery_id)
                            else:
                                delivery_request = ReportDeliveryRequest.parse(
                                    decision.arguments, successful_research,
                                )
                                delivery_result = self.delivery.save_and_deliver(
                                    delivery_request, cancellation=token,
                                )
                        except ReportDeliveryError as error:
                            raise StageFailure("agent_environment", error.code) from None
                        deliveries.append(delivery_result)
                        safe_output = json.dumps(
                            delivery_result, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                        receipt = CallReceipt(
                            call_id, facts.container_id, facts.generation, "completed", 0,
                            safe_output, b"", "/workspace", metadata={
                                "stdout": {"byte_count": len(safe_output), "sha256": hashlib.sha256(safe_output).hexdigest(), "truncated": False},
                                "stderr": {"byte_count": 0, "sha256": hashlib.sha256(b"").hexdigest(), "truncated": False},
                                "details": delivery_result,
                            },
                        )
                    else:
                        receipt = self.environment.execute(
                            decision.tool, decision.arguments, call_id=call_id,
                            timeout_seconds=min(self.budget.maximum_operation_seconds, max(0.001, deadline - self.clock())),
                        )
                except AgentEnvironmentError as error:
                    raise StageFailure("agent_environment", error.code) from None
                finally:
                    with self._lock:
                        if self._active_call and self._active_call[0] == call_id:
                            self._active_call = None
                if not self._live(identity, token):
                    raise StageFailure("llm_provider", "selected_provider_cancelled")
                operations += 1
                if decision.tool in WEB_TOOLS:
                    details = receipt.metadata.get("details", {})
                    if isinstance(details, dict):
                        safe_details = dict(details)
                        if isinstance(safe_details.get("title"), str):
                            safe_details["title"] = self.environment.redact_display(
                                safe_details["title"].encode()
                            ).decode("utf-8", "replace")
                        research_receipts.append({
                            "receipt_id": receipt.call_id, "outcome": receipt.status,
                            **safe_details,
                        })
                        if receipt.status == "completed" and details.get("kind") == "web_fetch":
                            successful_research[receipt.call_id] = details
                def bounded_result(data: bytes, stream: str) -> dict[str, object]:
                    visible = data[:1024]
                    accounting = receipt.metadata.get(stream, {})
                    return {
                        "data_base64": base64.b64encode(visible).decode("ascii"),
                        "byte_count": accounting.get("byte_count", len(data)),
                        "sha256": accounting.get("sha256", hashlib.sha256(data).hexdigest()),
                        "truncated": bool(accounting.get("truncated", False)) or len(data) > len(visible),
                    }

                history.append({
                    "decision": {"schema_version": DECISION_VERSION, "kind": "operation", "tool": decision.tool},
                    "operation": {"schema_version": OPERATION_VERSION, "call_id": call_id, "tool": decision.tool},
                    "result": {
                        "schema_version": RESULT_VERSION,
                        "receipt": {
                            "schema_version": "voice-agent.agent-call-receipt.v2",
                            "call_id": receipt.call_id,
                            "status": receipt.status,
                            "exit_code": receipt.exit_code,
                            "cwd": receipt.cwd,
                            "replayed": receipt.replayed,
                            "stdout": bounded_result(receipt.stdout, "stdout"),
                            "stderr": bounded_result(receipt.stderr, "stderr"),
                            "details": receipt.metadata.get("details", {}),
                        },
                    },
                })
                history = history[-4:]
            raise StageFailure("llm_provider", "agent_decision_budget_exhausted")
        finally:
            unregister()
            with self._lock:
                self._closed.add(identity)
                if self._active_identity == identity:
                    self._active_identity = None
                self._active_call = None

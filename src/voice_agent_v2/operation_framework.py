"""Closed, deny-by-default operation admission seam.

This module owns contracts and admission only.  It contains no capability ID,
production registry, configuration loader, model prompt, or fallback dispatcher.
A release composition may inject one typed definition for a hermetic tracer.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol

from .contracts import valid_correlation_id
from .schema import SchemaViolation, validate


PROPOSAL_VERSION = "voice-agent.operation-proposal.v1"
ADMITTED_VERSION = "voice-agent.admitted-operation.v1"
RESULT_VERSION = "voice-agent.operation-result.v1"
ROUND_VERSION = "voice-agent.operation-proposal-round.v1"
EXTERNAL_TOOL_DATA = "external_tool_data"
MAX_ARGUMENT_BYTES = 512
MAX_ARGUMENT_DEPTH = 4
MAX_RESULT_BYTES = 4096

_ROOT = Path(__file__).resolve().parents[2]


class OperationDenied(Exception):
    """A stable, content-free failure emitted before a handler may run."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ReleaseOperationDefinition(Protocol):
    """The only dispatch surface: a release-owned, typed definition object."""

    capability_id: str
    release_definition_id: str
    maximum_result_bytes: int

    def validate_arguments(self, arguments: Mapping[str, object]) -> object: ...

    def invoke(self, arguments: object) -> bytes: ...


@dataclass(frozen=True, slots=True)
class OperationPolicySnapshot:
    """An immutable policy input supplied by startup composition, never a model."""

    effective_policy_revision: str
    enabled_capability_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not _valid_revision(self.effective_policy_revision)
            or len(self.enabled_capability_ids) != len(set(self.enabled_capability_ids))
            or any(not _valid_capability_id(value) for value in self.enabled_capability_ids)
        ):
            raise ValueError("invalid immutable operation policy")


@dataclass(frozen=True, slots=True)
class OperationProposal:
    proposal_id: str
    session_id: str
    turn_id: str
    turn_generation: int
    capability_id: str
    arguments: Mapping[str, object]

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": PROPOSAL_VERSION,
            "proposal_id": self.proposal_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "turn_generation": self.turn_generation,
            "capability_id": self.capability_id,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class AdmittedOperation:
    proposal: OperationProposal
    effective_policy_revision: str
    release_definition_id: str
    maximum_result_bytes: int

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": ADMITTED_VERSION,
            "proposal_id": self.proposal.proposal_id,
            "session_id": self.proposal.session_id,
            "turn_id": self.proposal.turn_id,
            "turn_generation": self.proposal.turn_generation,
            "capability_id": self.proposal.capability_id,
            "effective_policy_revision": self.effective_policy_revision,
            "release_definition_id": self.release_definition_id,
            "maximum_result_bytes": self.maximum_result_bytes,
        }


@dataclass(frozen=True, slots=True)
class OperationResult:
    admitted: AdmittedOperation
    data: bytes

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": RESULT_VERSION,
            "proposal_id": self.admitted.proposal.proposal_id,
            "capability_id": self.admitted.proposal.capability_id,
            "effective_policy_revision": self.admitted.effective_policy_revision,
            "release_definition_id": self.admitted.release_definition_id,
            "data_classification": EXTERNAL_TOOL_DATA,
            "data_base64": base64.b64encode(self.data).decode("ascii"),
            "byte_count": len(self.data),
        }


@dataclass(frozen=True, slots=True)
class _RegisteredDefinition:
    handler: ReleaseOperationDefinition
    capability_id: str
    release_definition_id: str
    maximum_result_bytes: int


class ReleaseOperationRegistry:
    """Immutable release definitions; it is deliberately not config-driven."""

    def __init__(
        self,
        definitions: tuple[ReleaseOperationDefinition, ...],
        *,
        declared_capability_ids: tuple[str, ...],
    ) -> None:
        definition_ids = tuple(definition.capability_id for definition in definitions)
        if (
            len(definition_ids) != len(set(definition_ids))
            or len(declared_capability_ids) != len(set(declared_capability_ids))
            or any(not _valid_capability_id(value) for value in (*definition_ids, *declared_capability_ids))
            or any(value not in declared_capability_ids for value in definition_ids)
            or any(
                not _valid_capability_id(definition.release_definition_id)
                or type(definition.maximum_result_bytes) is not int
                or not 1 <= definition.maximum_result_bytes <= MAX_RESULT_BYTES
                for definition in definitions
            )
        ):
            raise ValueError("invalid release operation registry")
        self._definitions = {
            definition.capability_id: _RegisteredDefinition(
                handler=definition,
                capability_id=definition.capability_id,
                release_definition_id=definition.release_definition_id,
                maximum_result_bytes=definition.maximum_result_bytes,
            )
            for definition in definitions
        }
        self._declared_ids = frozenset(declared_capability_ids)

    def definition_for(self, capability_id: str) -> _RegisteredDefinition:
        if capability_id not in self._declared_ids:
            known_bases = {value.rsplit(".v", 1)[0] for value in self._declared_ids}
            if capability_id.rsplit(".v", 1)[0] in known_bases:
                raise OperationDenied("capability_unsupported_version")
            raise OperationDenied("capability_unknown")
        definition = self._definitions.get(capability_id)
        if definition is None:
            raise OperationDenied("capability_unregistered")
        return definition


def _schema(name: str) -> dict[str, object]:
    return json.loads((_ROOT / "contracts" / name).read_text(encoding="utf-8"))


def _valid_revision(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_capability_id(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 96:
        return False
    parts = value.split(".")
    if len(parts) < 2 or not parts[-1].startswith("v") or not parts[-1][1:].isdigit() or int(parts[-1][1:]) < 1:
        return False
    return all(
        part
        and part[0].islower()
        and all(character.islower() or character.isdigit() or character == "-" for character in part)
        for part in parts
    )


def _bounded_json(value: object, *, depth: int = 0) -> bool:
    if depth > MAX_ARGUMENT_DEPTH:
        return False
    if value is None or type(value) in {bool, int, float, str}:
        return not isinstance(value, float) or value == value and abs(value) != float("inf")
    if isinstance(value, list):
        return len(value) <= 8 and all(_bounded_json(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return (
            len(value) <= 8
            and all(isinstance(key, str) and len(key) <= 64 for key in value)
            and all(_bounded_json(item, depth=depth + 1) for item in value.values())
        )
    return False


def parse_proposal_round(raw: object) -> OperationProposal:
    """Reject mixed content, multiple proposals, authority fields and malformed JSON."""
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "proposals"}:
        raise OperationDenied("proposal_round_invalid")
    if raw.get("schema_version") != ROUND_VERSION or not isinstance(raw.get("proposals"), list):
        raise OperationDenied("proposal_round_invalid")
    proposals = raw["proposals"]
    if len(proposals) != 1 or not isinstance(proposals[0], dict):
        raise OperationDenied("operation_limit_exceeded")
    document = proposals[0]
    try:
        validate(document, _schema("operation-proposal.v1.schema.json"))
    except (SchemaViolation, TypeError, ValueError):
        raise OperationDenied("proposal_schema_invalid") from None
    arguments = document.get("arguments")
    if not isinstance(arguments, dict) or not _bounded_json(arguments):
        raise OperationDenied("arguments_out_of_bounds")
    try:
        serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise OperationDenied("arguments_invalid") from None
    if len(serialized) > MAX_ARGUMENT_BYTES:
        raise OperationDenied("arguments_out_of_bounds")
    return OperationProposal(
        proposal_id=str(document["proposal_id"]),
        session_id=str(document["session_id"]),
        turn_id=str(document["turn_id"]),
        turn_generation=int(document["turn_generation"]),
        capability_id=str(document["capability_id"]),
        arguments=arguments,
    )


class OperationTurn:
    """One proposal, one admitted operation, one opaque-data final-answer handoff."""

    def __init__(
        self,
        registry: ReleaseOperationRegistry,
        policy: OperationPolicySnapshot,
        *,
        observation: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._observation = observation
        self._proposal_used = False
        self._final_answer_used = False

    def _observe(
        self, *, capability_id: str | None, transition: str, count: int,
        duration_ms: float | None = None, reason_code: str | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "effective_policy_revision": self._policy.effective_policy_revision,
            "transition": transition,
            "operation_count": count,
        }
        if capability_id is not None:
            fields["capability_id"] = capability_id
        if duration_ms is not None:
            fields["duration_ms"] = round(max(0.0, duration_ms), 3)
        if reason_code is not None:
            fields["reason_code"] = reason_code
        if self._observation is not None:
            self._observation(fields)

    def execute(
        self,
        raw_proposal_round: object,
        *,
        session_id: str,
        turn_id: str,
        turn_generation: int,
        expected_policy_revision: str,
    ) -> OperationResult:
        if self._proposal_used:
            raise OperationDenied("operation_limit_exceeded")
        self._proposal_used = True
        started = time.monotonic()
        try:
            proposal = parse_proposal_round(raw_proposal_round)
            if (
                not all(valid_correlation_id(value) for value in (session_id, turn_id))
                or type(turn_generation) is not int
                or not 1 <= turn_generation <= 1_000_000_000
                or (proposal.session_id, proposal.turn_id, proposal.turn_generation)
                != (session_id, turn_id, turn_generation)
            ):
                raise OperationDenied("proposal_correlation_invalid")
            if expected_policy_revision != self._policy.effective_policy_revision:
                raise OperationDenied("policy_revision_mismatch")
            definition = self._registry.definition_for(proposal.capability_id)
            if proposal.capability_id not in self._policy.enabled_capability_ids:
                raise OperationDenied("capability_disabled")
            try:
                typed_arguments = definition.handler.validate_arguments(proposal.arguments)
            except (TypeError, ValueError, OverflowError):
                raise OperationDenied("arguments_invalid") from None
            admitted = AdmittedOperation(
                proposal=proposal,
                effective_policy_revision=self._policy.effective_policy_revision,
                release_definition_id=definition.release_definition_id,
                maximum_result_bytes=definition.maximum_result_bytes,
            )
            validate(admitted.as_document(), _schema("admitted-operation.v1.schema.json"))
            self._observe(capability_id=proposal.capability_id, transition="admitted", count=1)
            try:
                data = definition.handler.invoke(typed_arguments)
            except Exception:
                raise OperationDenied("operation_handler_failed") from None
            if not isinstance(data, bytes) or len(data) > admitted.maximum_result_bytes:
                raise OperationDenied("result_out_of_bounds")
            result = OperationResult(admitted=admitted, data=data)
            document = result.as_document()
            validate(document, _schema("operation-result.v1.schema.json"))
            if base64.b64decode(str(document["data_base64"]), validate=True) != data:
                raise OperationDenied("result_invalid")
            self._observe(
                capability_id=proposal.capability_id,
                transition="completed",
                count=1,
                duration_ms=(time.monotonic() - started) * 1000,
            )
            return result
        except OperationDenied as error:
            self._observe(
                capability_id=None,
                transition="denied",
                count=0,
                duration_ms=(time.monotonic() - started) * 1000,
                reason_code=error.code,
            )
            raise

    def consume_for_final_answer(self, result: OperationResult) -> OperationResult:
        """Permit the opaque external/tool data in exactly one final-answer round."""
        if self._final_answer_used:
            raise OperationDenied("final_answer_limit_exceeded")
        if (
            result.admitted.effective_policy_revision != self._policy.effective_policy_revision
            or result.admitted.proposal.capability_id not in self._policy.enabled_capability_ids
            or result.as_document().get("data_classification") != EXTERNAL_TOOL_DATA
        ):
            raise OperationDenied("result_invalid")
        self._final_answer_used = True
        return result

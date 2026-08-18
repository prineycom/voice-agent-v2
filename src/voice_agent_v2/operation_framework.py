"""Closed, deny-by-default operation admission seam.

This module owns contracts, immutable release bounds, and admission only. It has
no capability ID, production registry, configuration loader, model prompt, or
fallback dispatcher. A release composition may inject one typed definition for
a hermetic tracer.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable, Mapping, Protocol
import unicodedata

from .contracts import valid_correlation_id
from .schema import SchemaViolation, validate


PROPOSAL_VERSION = "voice-agent.operation-proposal.v2"
ADMITTED_VERSION = "voice-agent.admitted-operation.v2"
RESULT_VERSION = "voice-agent.operation-result.v2"
ROUND_VERSION = "voice-agent.operation-proposal-round.v2"
EXTERNAL_TOOL_DATA = "external_tool_data"

# Immutable release-owned envelope and data limits. Capability definitions may
# narrow the result byte limit, never widen any of these limits.
MAX_PROPOSAL_BYTES = 2_048
MAX_PROPOSAL_DEPTH = 6
MAX_PROPOSAL_FIELDS = 32
MAX_PROPOSAL_ITEMS = 12
MAX_ARGUMENT_BYTES = 512
MAX_ARGUMENT_DEPTH = 4
MAX_ARGUMENT_FIELDS = 8
MAX_RESULT_BYTES = 4_096
MAX_RESULT_DEPTH = 6
MAX_RESULT_FIELDS = 64
MAX_RESULT_ITEMS = 32
MAX_RESULT_STRING_BYTES = 2_048

_ROOT = Path(__file__).resolve().parents[2]


class OperationDenied(Exception):
    """A stable, content-free failure emitted before visible output."""

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
class OperationIdentity:
    """The complete realtime identity that owns one operation task."""

    session_id: str
    stream_epoch: int
    turn_id: str
    request_id: str
    turn_generation: int

    def __post_init__(self) -> None:
        if (
            not all(
                valid_correlation_id(value)
                for value in (self.session_id, self.turn_id, self.request_id)
            )
            or any(
                type(value) is not int or not 1 <= value <= 1_000_000_000
                for value in (self.stream_epoch, self.turn_generation)
            )
        ):
            raise ValueError("invalid operation identity")

    @property
    def tuple(self) -> tuple[str, int, str, str, int]:
        return (
            self.session_id,
            self.stream_epoch,
            self.turn_id,
            self.request_id,
            self.turn_generation,
        )


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
    identity: OperationIdentity
    capability_id: str
    arguments: Mapping[str, object]

    @property
    def session_id(self) -> str:
        return self.identity.session_id

    @property
    def stream_epoch(self) -> int:
        return self.identity.stream_epoch

    @property
    def turn_id(self) -> str:
        return self.identity.turn_id

    @property
    def request_id(self) -> str:
        return self.identity.request_id

    @property
    def turn_generation(self) -> int:
        return self.identity.turn_generation

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": PROPOSAL_VERSION,
            "proposal_id": self.proposal_id,
            "session_id": self.session_id,
            "stream_epoch": self.stream_epoch,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
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

    @property
    def identity(self) -> OperationIdentity:
        return self.proposal.identity

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": ADMITTED_VERSION,
            "proposal_id": self.proposal.proposal_id,
            "session_id": self.identity.session_id,
            "stream_epoch": self.identity.stream_epoch,
            "turn_id": self.identity.turn_id,
            "request_id": self.identity.request_id,
            "turn_generation": self.identity.turn_generation,
            "capability_id": self.proposal.capability_id,
            "effective_policy_revision": self.effective_policy_revision,
            "release_definition_id": self.release_definition_id,
            "maximum_result_bytes": self.maximum_result_bytes,
        }


@dataclass(frozen=True, slots=True)
class OperationResult:
    admitted: AdmittedOperation
    data: bytes

    @property
    def identity(self) -> OperationIdentity:
        return self.admitted.identity

    def as_document(self) -> dict[str, object]:
        return {
            "schema_version": RESULT_VERSION,
            "proposal_id": self.admitted.proposal.proposal_id,
            "session_id": self.identity.session_id,
            "stream_epoch": self.identity.stream_epoch,
            "turn_id": self.identity.turn_id,
            "request_id": self.identity.request_id,
            "turn_generation": self.identity.turn_generation,
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


@dataclass(frozen=True, slots=True)
class AdmittedInvocation:
    """Private-authority dispatch input produced only by successful admission."""

    admitted: AdmittedOperation
    definition: _RegisteredDefinition
    typed_arguments: object


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
            or any(
                not _valid_capability_id(value)
                for value in (*definition_ids, *declared_capability_ids)
            )
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

    @property
    def handlers(self) -> tuple[ReleaseOperationDefinition, ...]:
        return tuple(definition.handler for definition in self._definitions.values())

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
    if (
        len(parts) < 2
        or not parts[-1].startswith("v")
        or not parts[-1][1:].isdigit()
        or int(parts[-1][1:]) < 1
    ):
        return False
    return all(
        part
        and part[0].islower()
        and all(
            character.islower() or character.isdigit() or character == "-"
            for character in part
        )
        for part in parts
    )


def _has_unicode_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)


def _bounded_json(
    value: object,
    *,
    maximum_depth: int,
    maximum_fields: int,
    maximum_items: int,
    maximum_string_bytes: int,
    depth: int = 0,
    counter: list[int] | None = None,
    ancestors: set[int] | None = None,
) -> bool:
    if depth > maximum_depth:
        return False
    counter = [0] if counter is None else counter
    ancestors = set() if ancestors is None else ancestors
    counter[0] += 1
    if counter[0] > maximum_fields:
        return False
    if value is None or type(value) in {bool, int, float, str}:
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            return False
        if isinstance(value, str):
            return (
                len(value.encode("utf-8")) <= maximum_string_bytes
                and not _has_unicode_control(value)
            )
        return True
    if not isinstance(value, (list, dict)) or id(value) in ancestors:
        return False
    next_ancestors = {*ancestors, id(value)}
    if isinstance(value, list):
        return len(value) <= maximum_items and all(
            _bounded_json(
                item,
                maximum_depth=maximum_depth,
                maximum_fields=maximum_fields,
                maximum_items=maximum_items,
                maximum_string_bytes=maximum_string_bytes,
                depth=depth + 1,
                counter=counter,
                ancestors=next_ancestors,
            )
            for item in value
        )
    return (
        len(value) <= maximum_items
        and all(
            isinstance(key, str)
            and len(key.encode("utf-8")) <= 64
            and not _has_unicode_control(key)
            for key in value
        )
        and all(
            _bounded_json(
                item,
                maximum_depth=maximum_depth,
                maximum_fields=maximum_fields,
                maximum_items=maximum_items,
                maximum_string_bytes=maximum_string_bytes,
                depth=depth + 1,
                counter=counter,
                ancestors=next_ancestors,
            )
            for item in value.values()
        )
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_proposal_round(raw: object) -> OperationProposal:
    """Reject mixed content, multiple proposals, authority fields and unsafe data."""
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "proposals"}:
        raise OperationDenied("proposal_round_invalid")
    if raw.get("schema_version") != ROUND_VERSION or not isinstance(raw.get("proposals"), list):
        raise OperationDenied("proposal_round_invalid")
    proposals = raw["proposals"]
    if len(proposals) != 1 or not isinstance(proposals[0], dict):
        raise OperationDenied("operation_limit_exceeded")
    if not _bounded_json(
        raw,
        maximum_depth=MAX_PROPOSAL_DEPTH,
        maximum_fields=MAX_PROPOSAL_FIELDS,
        maximum_items=MAX_PROPOSAL_ITEMS,
        maximum_string_bytes=MAX_PROPOSAL_BYTES,
    ):
        raise OperationDenied("proposal_out_of_bounds")
    try:
        if len(_canonical_json(raw)) > MAX_PROPOSAL_BYTES:
            raise OperationDenied("proposal_out_of_bounds")
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise OperationDenied("proposal_out_of_bounds") from None
    document = proposals[0]
    try:
        validate(document, _schema("operation-proposal.v2.schema.json"))
    except (SchemaViolation, TypeError, ValueError):
        raise OperationDenied("proposal_schema_invalid") from None
    arguments = document.get("arguments")
    if not isinstance(arguments, dict) or not _bounded_json(
        arguments,
        maximum_depth=MAX_ARGUMENT_DEPTH,
        maximum_fields=MAX_ARGUMENT_FIELDS,
        maximum_items=MAX_ARGUMENT_FIELDS,
        maximum_string_bytes=MAX_ARGUMENT_BYTES,
    ):
        raise OperationDenied("arguments_out_of_bounds")
    try:
        serialized = _canonical_json(arguments)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise OperationDenied("arguments_invalid") from None
    if len(serialized) > MAX_ARGUMENT_BYTES:
        raise OperationDenied("arguments_out_of_bounds")
    # Canonical JSON round-trip severs model-owned mutable references before admission.
    immutable_arguments = json.loads(serialized)
    try:
        identity = OperationIdentity(
            session_id=str(document["session_id"]),
            stream_epoch=int(document["stream_epoch"]),
            turn_id=str(document["turn_id"]),
            request_id=str(document["request_id"]),
            turn_generation=int(document["turn_generation"]),
        )
    except (TypeError, ValueError):
        raise OperationDenied("proposal_correlation_invalid") from None
    return OperationProposal(
        proposal_id=str(document["proposal_id"]),
        identity=identity,
        capability_id=str(document["capability_id"]),
        arguments=immutable_arguments,
    )


def validate_result_data(data: object, maximum_bytes: int) -> bytes:
    """Validate a handler result as bounded inert UTF-8/JSON data, never authority."""
    if not isinstance(data, bytes):
        raise OperationDenied("result_invalid")
    if len(data) > maximum_bytes or len(data) > MAX_RESULT_BYTES:
        raise OperationDenied("result_out_of_bounds")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise OperationDenied("result_invalid") from None
    if _has_unicode_control(text):
        raise OperationDenied("result_out_of_bounds")
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            structured = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            raise OperationDenied("result_invalid") from None
        if not _bounded_json(
            structured,
            maximum_depth=MAX_RESULT_DEPTH,
            maximum_fields=MAX_RESULT_FIELDS,
            maximum_items=MAX_RESULT_ITEMS,
            maximum_string_bytes=MAX_RESULT_STRING_BYTES,
        ):
            raise OperationDenied("result_out_of_bounds")
    elif len(data) > MAX_RESULT_STRING_BYTES:
        raise OperationDenied("result_out_of_bounds")
    return data


class OperationTurn:
    """One proposal, one admitted operation, one opaque-data final-answer handoff."""

    def __init__(
        self,
        registry: ReleaseOperationRegistry,
        policy: OperationPolicySnapshot,
        *,
        observation: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._observation = observation
        self._clock = clock
        self._proposal_used = False
        self._dispatch_used = False
        self._final_answer_used = False
        self._admitted_invocation: AdmittedInvocation | None = None

    def _observe(
        self,
        *,
        capability_id: str | None,
        transition: str,
        count: int,
        started: float | None = None,
        reason_code: str | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "effective_policy_revision": self._policy.effective_policy_revision,
            "transition": transition,
            "operation_count": count,
        }
        if capability_id is not None:
            fields["capability_id"] = capability_id
        if started is not None:
            fields["duration_ms"] = round(max(0.0, (self._clock() - started) * 1000), 3)
        if reason_code is not None:
            fields["reason_code"] = reason_code
        if self._observation is not None:
            self._observation(fields)

    def admit(
        self,
        raw_proposal_round: object,
        *,
        identity: OperationIdentity,
        expected_policy_revision: str,
    ) -> AdmittedInvocation:
        if self._proposal_used:
            raise OperationDenied("operation_limit_exceeded")
        self._proposal_used = True
        started = self._clock()
        try:
            proposal = parse_proposal_round(raw_proposal_round)
            if proposal.identity != identity:
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
            validate(admitted.as_document(), _schema("admitted-operation.v2.schema.json"))
            invocation = AdmittedInvocation(admitted, definition, typed_arguments)
            self._admitted_invocation = invocation
            self._observe(
                capability_id=proposal.capability_id,
                transition="admitted",
                count=1,
            )
            return invocation
        except OperationDenied as error:
            self._observe(
                capability_id=None,
                transition="denied",
                count=0,
                started=started,
                reason_code=error.code,
            )
            raise

    def dispatch(
        self,
        invocation: AdmittedInvocation,
        *,
        invoke: Callable[[ReleaseOperationDefinition, object], object] | None = None,
    ) -> OperationResult:
        if self._dispatch_used or invocation is not self._admitted_invocation:
            raise OperationDenied("operation_limit_exceeded")
        self._dispatch_used = True
        started = self._clock()
        try:
            try:
                data = (
                    invocation.definition.handler.invoke(invocation.typed_arguments)
                    if invoke is None
                    else invoke(
                        invocation.definition.handler,
                        invocation.typed_arguments,
                    )
                )
            except Exception:
                raise OperationDenied("operation_handler_failed") from None
            bounded = validate_result_data(
                data, invocation.admitted.maximum_result_bytes
            )
            result = OperationResult(admitted=invocation.admitted, data=bounded)
            self._validate_result(result, expected_identity=invocation.admitted.identity)
            self._observe(
                capability_id=invocation.admitted.proposal.capability_id,
                transition="completed",
                count=1,
                started=started,
            )
            return result
        except OperationDenied as error:
            self._observe(
                capability_id=None,
                transition="denied",
                count=0,
                started=started,
                reason_code=error.code,
            )
            raise

    def execute(
        self,
        raw_proposal_round: object,
        *,
        session_id: str,
        turn_id: str,
        turn_generation: int,
        expected_policy_revision: str,
        stream_epoch: int = 1,
        request_id: str | None = None,
    ) -> OperationResult:
        identity = OperationIdentity(
            session_id,
            stream_epoch,
            turn_id,
            request_id or f"request-{turn_id.removeprefix('turn-')}",
            turn_generation,
        )
        invocation = self.admit(
            raw_proposal_round,
            identity=identity,
            expected_policy_revision=expected_policy_revision,
        )
        return self.dispatch(invocation)

    def _validate_result(
        self, result: object, *, expected_identity: OperationIdentity | None = None
    ) -> OperationResult:
        if not isinstance(result, OperationResult):
            raise OperationDenied("result_invalid")
        if expected_identity is not None and result.identity != expected_identity:
            raise OperationDenied("result_correlation_invalid")
        try:
            document = result.as_document()
            validate(document, _schema("operation-result.v2.schema.json"))
            decoded = base64.b64decode(str(document["data_base64"]), validate=True)
        except (SchemaViolation, KeyError, TypeError, ValueError):
            raise OperationDenied("result_invalid") from None
        if decoded != result.data or document.get("byte_count") != len(result.data):
            raise OperationDenied("result_invalid")
        validate_result_data(result.data, result.admitted.maximum_result_bytes)
        return result

    def consume_for_final_answer(
        self,
        result: object,
        *,
        expected_identity: OperationIdentity | None = None,
    ) -> OperationResult:
        """Permit opaque external/tool data in exactly one final-answer round."""
        if self._final_answer_used:
            raise OperationDenied("final_answer_limit_exceeded")
        validated = self._validate_result(result, expected_identity=expected_identity)
        if (
            validated.admitted.effective_policy_revision
            != self._policy.effective_policy_revision
            or validated.admitted.proposal.capability_id
            not in self._policy.enabled_capability_ids
            or validated.as_document().get("data_classification") != EXTERNAL_TOOL_DATA
        ):
            raise OperationDenied("result_invalid")
        self._final_answer_used = True
        return validated

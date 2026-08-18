"""Request-scoped custody for the dependency-injected E2 operation tracer.

Nothing composes this provider in production. It supplies the generic ownership
loop used by the hermetic realtime tracer while the production LocalLFMProvider
remains unchanged and tool-unaware.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import threading
import time
from typing import Callable

from .contracts import LLM_VERSION, StageFailure
from .operation_framework import (
    OperationDenied,
    OperationIdentity,
    OperationPolicySnapshot,
    OperationResult,
    OperationTurn,
    ReleaseOperationDefinition,
    ReleaseOperationRegistry,
)
from .tracer import CancellationToken


# One deadline covers proposal, admission, dispatch, result injection, and the
# final answer. The handler gets one narrower sub-deadline without resetting the
# whole-loop deadline.
OPERATION_WHOLE_LOOP_SECONDS = 0.750
OPERATION_HANDLER_SECONDS = 0.250
OPERATION_CANCEL_GRACE_SECONDS = 0.025
OPERATION_TASK_POLL_SECONDS = 0.002


class _OperationCancelled(Exception):
    pass


@dataclass(slots=True)
class _Outcome:
    value: object | None = None
    error: BaseException | None = None


def _supported_call(callable_object, /, *arguments: object, **keywords: object) -> object:
    """Pass custody metadata only to explicitly compatible injected fakes."""
    parameters = inspect.signature(callable_object).parameters
    accepts_extra = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    filtered = (
        keywords
        if accepts_extra
        else {name: value for name, value in keywords.items() if name in parameters}
    )
    return callable_object(*arguments, **filtered)


class CustodiedOperationProvider:
    """One-operation deterministic provider with exact realtime task custody."""

    version = LLM_VERSION

    def __init__(
        self,
        registry: ReleaseOperationRegistry,
        policy: OperationPolicySnapshot,
        proposer: object,
        selected_provider: object,
        *,
        expected_policy_revision: str,
        observation: Callable[[dict[str, object]], None] | None = None,
        checkpoint: Callable[[str, OperationIdentity, CancellationToken], None]
        | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            getattr(selected_provider, "version", None) != LLM_VERSION
            or getattr(selected_provider, "provider_mode", None) != "local"
            or not isinstance(getattr(selected_provider, "provider_identity", None), str)
        ):
            raise ValueError("invalid selected operation final-answer provider")
        self._registry = registry
        self._policy = policy
        self._proposer = proposer
        self._selected = selected_provider
        self._expected_policy_revision = expected_policy_revision
        self._observation = observation
        self._checkpoint_observer = checkpoint
        self._clock = clock
        self._lock = threading.Lock()
        self._active_identity: OperationIdentity | None = None
        self._closed_identities: set[OperationIdentity] = set()
        self._tasks: set[threading.Thread] = set()
        self._late_drop_count = 0
        self._cancel_request_count = 0
        self.provider_mode = selected_provider.provider_mode
        self.provider_identity = selected_provider.provider_identity
        self.supports_visible_handoff = bool(
            getattr(selected_provider, "supports_visible_handoff", False)
        )
        self.visible_handoff_is_cumulative = bool(
            getattr(selected_provider, "visible_handoff_is_cumulative", False)
        )

    @property
    def late_drop_count(self) -> int:
        with self._lock:
            return self._late_drop_count

    @property
    def active_task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    @property
    def cancel_request_count(self) -> int:
        with self._lock:
            return self._cancel_request_count

    def _begin(self, identity: OperationIdentity) -> None:
        with self._lock:
            if self._active_identity is not None:
                raise StageFailure("llm_provider", "operation_custody_unavailable")
            self._closed_identities.discard(identity)
            self._active_identity = identity

    def _live(self, identity: OperationIdentity, cancellation: CancellationToken) -> bool:
        with self._lock:
            owned = (
                self._active_identity == identity
                and identity not in self._closed_identities
            )
        return owned and not cancellation.cancelled

    def _ensure_live(
        self,
        identity: OperationIdentity,
        cancellation: CancellationToken,
        deadline: float,
    ) -> None:
        if cancellation.cancelled or not self._live(identity, cancellation):
            raise _OperationCancelled
        if self._clock() >= deadline:
            self._cancel_identity(identity)
            raise StageFailure("llm_provider", "operation_loop_timeout")

    def _checkpoint(
        self,
        name: str,
        identity: OperationIdentity,
        cancellation: CancellationToken,
        deadline: float,
    ) -> None:
        self._ensure_live(identity, cancellation, deadline)
        if self._checkpoint_observer is not None:
            self._checkpoint_observer(name, identity, cancellation)
        self._ensure_live(identity, cancellation, deadline)

    def _component_cancel(self, component: object, identity: OperationIdentity) -> None:
        cancel = getattr(component, "cancel_operation", None)
        if cancel is None:
            cancel = getattr(component, "cancel_request", None)
        if cancel is None:
            cancel = getattr(component, "cancel", None)
        if cancel is None:
            return
        try:
            _supported_call(
                cancel,
                identity=identity,
                session_id=identity.session_id,
                stream_epoch=identity.stream_epoch,
                turn_id=identity.turn_id,
                request_id=identity.request_id,
                turn_generation=identity.turn_generation,
            )
        except Exception:
            pass

    def _cancel_identity(self, identity: OperationIdentity) -> None:
        with self._lock:
            already_closed = identity in self._closed_identities
            self._closed_identities.add(identity)
            if self._active_identity == identity:
                self._active_identity = None
            if not already_closed:
                self._cancel_request_count += 1
        if already_closed:
            return
        for component in (
            self._proposer,
            *self._registry.handlers,
            self._selected,
        ):
            self._component_cancel(component, identity)

    def cancel_request(self) -> None:
        """Cancel only the currently owned identity; never a later replacement."""
        with self._lock:
            identity = self._active_identity
        if identity is not None:
            self._cancel_identity(identity)

    def cancel(self) -> None:
        self.cancel_request()

    def _complete(self, identity: OperationIdentity) -> None:
        with self._lock:
            self._closed_identities.add(identity)
            if self._active_identity == identity:
                self._active_identity = None

    def _run_owned(
        self,
        *,
        identity: OperationIdentity,
        cancellation: CancellationToken,
        deadline: float,
        operation: Callable[[], object],
    ) -> object:
        outcome = _Outcome()
        finished = threading.Event()

        def run() -> None:
            try:
                outcome.value = operation()
            except BaseException as error:
                outcome.error = error
            finally:
                with self._lock:
                    if identity in self._closed_identities:
                        self._late_drop_count += 1
                    self._tasks.discard(threading.current_thread())
                finished.set()

        worker = threading.Thread(
            target=run,
            name=(
                "operation-task-"
                f"{identity.stream_epoch}-{identity.turn_generation}-{identity.request_id}"
            ),
            daemon=True,
        )
        with self._lock:
            self._tasks.add(worker)
        worker.start()
        while not finished.wait(OPERATION_TASK_POLL_SECONDS):
            if cancellation.cancelled or not self._live(identity, cancellation):
                self._cancel_identity(identity)
                finished.wait(OPERATION_CANCEL_GRACE_SECONDS)
                raise _OperationCancelled
            if self._clock() >= deadline:
                self._cancel_identity(identity)
                finished.wait(OPERATION_CANCEL_GRACE_SECONDS)
                raise StageFailure("llm_provider", "operation_loop_timeout")
        if not self._live(identity, cancellation):
            raise _OperationCancelled
        if self._clock() >= deadline:
            self._cancel_identity(identity)
            raise StageFailure("llm_provider", "operation_loop_timeout")
        if outcome.error is not None:
            raise outcome.error
        return outcome.value

    def wait_for_drains(self, timeout_seconds: float) -> bool:
        """Test cleanup probe for deliberately non-cooperative injected tasks."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._lock:
                tasks = tuple(self._tasks)
            if not tasks:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for task in tasks:
                task.join(min(remaining, OPERATION_TASK_POLL_SECONDS))

    def _invoke_handler(
        self,
        definition: ReleaseOperationDefinition,
        arguments: object,
        *,
        identity: OperationIdentity,
        cancellation: CancellationToken,
        handler_deadline: float,
    ) -> object:
        return _supported_call(
            definition.invoke,
            arguments,
            identity=identity,
            cancellation=cancellation,
            deadline=handler_deadline,
        )

    def _selected_call(
        self,
        *,
        ordinary: bool,
        identity: OperationIdentity,
        transcript: str,
        cancellation: CancellationToken,
        deadline: float,
        on_sentence: Callable[[str], None],
        on_visible_sentence: Callable[[str], None] | None,
        on_handoff_abort: Callable[[], bool | None] | None,
        tool_data: OperationResult | None = None,
    ) -> object:
        if ordinary:
            call = getattr(self._selected, "respond_with_handoff")
        else:
            call = getattr(
                self._selected,
                "respond_with_operation",
                getattr(self._selected, "respond_with_handoff"),
            )
        keywords: dict[str, object] = {
            "identity": identity,
            "session_id": identity.session_id,
            "stream_epoch": identity.stream_epoch,
            "turn_id": identity.turn_id,
            "request_id": identity.request_id,
            "turn_generation": identity.turn_generation,
            "transcript": transcript,
            "on_sentence": on_sentence,
            "on_visible_sentence": on_visible_sentence,
            "on_handoff_abort": on_handoff_abort,
            "cancellation": cancellation,
            "deadline": deadline,
        }
        if tool_data is not None:
            keywords["tool_data"] = tool_data
        return _supported_call(call, **keywords)

    def respond_with_handoff(
        self,
        *,
        session_id: str,
        stream_epoch: int,
        turn_id: str,
        request_id: str,
        turn_generation: int,
        transcript: str,
        on_sentence: Callable[[str], None],
        on_visible_sentence: Callable[[str], None] | None = None,
        on_handoff_abort: Callable[[], bool | None] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> str:
        token = cancellation or CancellationToken()
        try:
            identity = OperationIdentity(
                session_id, stream_epoch, turn_id, request_id, turn_generation
            )
        except ValueError:
            raise StageFailure("llm_provider", "invalid_correlation_id") from None
        self._begin(identity)
        whole_deadline = self._clock() + OPERATION_WHOLE_LOOP_SECONDS
        turn = OperationTurn(
            self._registry,
            self._policy,
            observation=self._observation,
            clock=self._clock,
        )
        unregister = token.register(lambda: self._cancel_identity(identity))

        def guarded(callback):
            if callback is None:
                return None

            def deliver(value: str) -> None:
                if self._live(identity, token):
                    callback(value)

            return deliver

        safe_sentence = guarded(on_sentence)
        safe_visible = guarded(on_visible_sentence)
        assert safe_sentence is not None
        try:
            self._checkpoint(
                "proposal_request", identity, token, whole_deadline
            )
            try:
                raw = self._run_owned(
                    identity=identity,
                    cancellation=token,
                    deadline=whole_deadline,
                    operation=lambda: _supported_call(
                        getattr(self._proposer, "propose"),
                        identity=identity,
                        session_id=identity.session_id,
                        stream_epoch=identity.stream_epoch,
                        turn_id=identity.turn_id,
                        request_id=identity.request_id,
                        turn_generation=identity.turn_generation,
                        transcript=transcript,
                        cancellation=token,
                        deadline=whole_deadline,
                    ),
                )
                self._ensure_live(identity, token, whole_deadline)
                if raw is None:
                    answer = self._run_owned(
                        identity=identity,
                        cancellation=token,
                        deadline=whole_deadline,
                        operation=lambda: self._selected_call(
                            ordinary=True,
                            identity=identity,
                            transcript=transcript,
                            cancellation=token,
                            deadline=whole_deadline,
                            on_sentence=safe_sentence,
                            on_visible_sentence=safe_visible,
                            on_handoff_abort=on_handoff_abort,
                        ),
                    )
                else:
                    invocation = turn.admit(
                        raw,
                        identity=identity,
                        expected_policy_revision=self._expected_policy_revision,
                    )
                    self._checkpoint(
                        "admitted_before_dispatch", identity, token, whole_deadline
                    )
                    handler_deadline = min(
                        whole_deadline, self._clock() + OPERATION_HANDLER_SECONDS
                    )
                    result = self._run_owned(
                        identity=identity,
                        cancellation=token,
                        deadline=handler_deadline,
                        operation=lambda: turn.dispatch(
                            invocation,
                            invoke=lambda definition, arguments: self._invoke_handler(
                                definition,
                                arguments,
                                identity=identity,
                                cancellation=token,
                                handler_deadline=handler_deadline,
                            ),
                        ),
                    )
                    assert isinstance(result, OperationResult)
                    self._checkpoint(
                        "result_before_injection", identity, token, whole_deadline
                    )
                    injected = turn.consume_for_final_answer(
                        result, expected_identity=identity
                    )
                    self._checkpoint(
                        "final_answer_generation", identity, token, whole_deadline
                    )
                    answer = self._run_owned(
                        identity=identity,
                        cancellation=token,
                        deadline=whole_deadline,
                        operation=lambda: self._selected_call(
                            ordinary=False,
                            identity=identity,
                            transcript=transcript,
                            cancellation=token,
                            deadline=whole_deadline,
                            on_sentence=safe_sentence,
                            on_visible_sentence=safe_visible,
                            on_handoff_abort=on_handoff_abort,
                            tool_data=injected,
                        ),
                    )
                    if isinstance(answer, dict):
                        # Model output cannot start a second operation. Re-entering
                        # admission deterministically hits this turn's one-call limit.
                        turn.admit(
                            answer,
                            identity=identity,
                            expected_policy_revision=self._expected_policy_revision,
                        )
                if not isinstance(answer, str) or not answer:
                    raise StageFailure("llm_provider", "operation_final_answer_invalid")
                self._ensure_live(identity, token, whole_deadline)
                self._complete(identity)
                return answer
            except OperationDenied as error:
                raise StageFailure("operation_admission", error.code) from None
            except _OperationCancelled:
                raise StageFailure("llm_provider", "selected_provider_cancelled") from None
            except StageFailure:
                raise
            except Exception:
                raise StageFailure("llm_provider", "operation_loop_failed") from None
        finally:
            unregister()
            with self._lock:
                still_active = self._active_identity == identity
            if still_active:
                self._complete(identity)

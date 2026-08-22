"""Realtime-provider composition for the production AgentRun path."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Callable

from .agent_environment import AgentEnvironment, AgentEnvironmentError
from .agent_environment_config import AgentConfigV2Snapshot
from .agent_environment_credentials import InstallationCredentialStore
from .agent_run import (
    MAX_CONVERSATION_BYTES, MAX_CONVERSATION_MESSAGES,
    AgentRealtimeIdentity, AgentRun, ExactLocalLFMAgentAdapter,
    validate_conversation,
)
from .contracts import LLM_VERSION, StageFailure
from .local_lfm import LocalLFMProvider, MODEL_ALIAS, PROVIDER_IDENTITY
from .tracer import CancellationToken


class AgentRunProvider:
    """Expose AgentRun through the existing selected-LLM voice-turn seam."""

    version = LLM_VERSION
    provider_mode = "local"
    provider_identity = PROVIDER_IDENTITY
    supports_visible_handoff = True
    visible_handoff_is_cumulative = True
    supports_handoff_abort = True

    def __init__(
        self,
        config: AgentConfigV2Snapshot,
        *,
        installation_root: Path,
        credential_root: Path | None = None,
        workspace_root: Path | None = None,
        cache_root: Path | None = None,
        image_state_root: Path | None = None,
        provider: LocalLFMProvider | None = None,
    ) -> None:
        selected = provider or LocalLFMProvider(request_timeout_seconds=120)
        self.selected = selected
        self.environment = AgentEnvironment(
            config,
            state_root=installation_root / "private",
            workspace=workspace_root or (installation_root / "workspace"),
            cache=cache_root or (installation_root / "cache"),
            credential_store=(
                InstallationCredentialStore(credential_root)
                if credential_root is not None else None
            ),
            image_state_root=image_state_root,
        )
        self._context_lock = threading.Lock()
        self._contexts: dict[str, list[dict[str, object]]] = {}
        self._turn_records: dict[tuple[str, str], dict[str, object]] = {}
        self._turn_provenance: dict[tuple[str, int, str, int, str], dict[str, object]] = {}
        self._observations = selected.observations
        self.agent_run = AgentRun(
            self.environment,
            model=ExactLocalLFMAgentAdapter(selected),
            observation=lambda item: self._observations.append(dict(item)),
        )

    @property
    def observations(self):
        return self._observations

    @property
    def runtime_health(self):
        return self.selected.runtime_health

    @property
    def runtime_live(self):
        return self.selected.runtime_live

    def readiness(self, cancellation: CancellationToken | None = None):
        """Gate full admission on the persistent environment's exact running truth."""

        readiness = dict(self.selected.readiness(cancellation))
        try:
            facts = self.environment.ensure_running()
        except AgentEnvironmentError as error:
            readiness["agent_environment_ready"] = False
            readiness["agent_environment_state"] = "unavailable"
            readiness["agent_environment_reason_code"] = error.code
        else:
            readiness["agent_environment_ready"] = True
            readiness["agent_environment_state"] = "running"
            readiness["agent_environment_reason_code"] = None
            if facts.state != "running":  # defensive: ensure_running owns this invariant
                readiness["agent_environment_ready"] = False
                readiness["agent_environment_state"] = "unavailable"
                readiness["agent_environment_reason_code"] = "environment_unhealthy"
        return readiness

    def environment_status(self) -> dict[str, object]:
        return self.environment.status()

    def warmup(self, cancellation: CancellationToken | None = None):
        return self.selected.warmup(cancellation)

    @staticmethod
    def _bounded_user(value: str) -> str:
        encoded = value.strip().encode("utf-8")
        if len(encoded) <= 2_048:
            return encoded.decode("utf-8")
        return encoded[:2_048].decode("utf-8", "ignore").strip()

    def _snapshot(self, session_id: str) -> tuple[dict[str, object], ...]:
        with self._context_lock:
            return tuple(dict(item) for item in self._contexts.get(session_id, ()))

    def _commit_record(self, record: dict[str, object], terminal_status: str) -> None:
        pair = [
            {"role": "user", "content": self._bounded_user(str(record["transcript"]))},
            {
                "role": "assistant", "content": str(record["answer"]),
                "terminal_status": terminal_status,
                "operation_count": int(record["operation_count"]),
                "action_outcome": str(record["action_outcome"]),
            },
        ]
        session_id = str(record["session_id"])
        with self._context_lock:
            context = list(self._contexts.get(session_id, ())) + pair
            context = context[-MAX_CONVERSATION_MESSAGES:]
            while context and len(json.dumps(
                context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")) > MAX_CONVERSATION_BYTES:
                context = context[2:]
            validate_conversation(tuple(context))
            self._contexts[session_id] = context

    def _cache_provenance(
        self, identity: AgentRealtimeIdentity, provenance: dict[str, object]
    ) -> None:
        key = (
            identity.session_id, identity.stream_epoch, identity.turn_id,
            identity.turn_generation, identity.request_id,
        )
        safe = {
            "operation_count": int(provenance.get("operation_count", 0)),
            "action_outcome": str(provenance.get("action_outcome", "no_operation")),
        }
        if (
            not 0 <= safe["operation_count"] <= 24
            or safe["action_outcome"] not in {"no_operation", "completed", "failed"}
        ):
            raise StageFailure("llm_provider", "agent_provenance_invalid")
        if (safe["operation_count"] == 0) != (
            safe["action_outcome"] == "no_operation"
        ):
            raise StageFailure("llm_provider", "agent_provenance_invalid")
        with self._context_lock:
            self._turn_provenance[key] = safe
            session_keys = [
                existing for existing in self._turn_provenance
                if existing[0] == identity.session_id
            ]
            for stale in session_keys[:-8]:
                self._turn_provenance.pop(stale, None)

    def turn_provenance(
        self, session_id: str, stream_epoch: int, turn_id: str,
        turn_generation: int, request_id: str,
    ) -> dict[str, object]:
        key = (session_id, stream_epoch, turn_id, turn_generation, request_id)
        with self._context_lock:
            cached = self._turn_provenance.get(key)
        if cached is not None:
            return dict(cached)
        identity = AgentRealtimeIdentity(
            session_id, stream_epoch, turn_id, request_id, turn_generation
        )
        provenance = self.agent_run.provenance(identity)
        self._cache_provenance(identity, provenance)
        return provenance

    def _store_turn_record(self, record: dict[str, object]) -> None:
        key = (str(record["session_id"]), str(record["turn_id"]))
        with self._context_lock:
            for stale in tuple(self._turn_records):
                if stale[0] == key[0] and stale != key:
                    self._turn_records.pop(stale, None)
            self._turn_records[key] = dict(record)

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
        del on_handoff_abort
        identity = AgentRealtimeIdentity(
            session_id, stream_epoch, turn_id, request_id, turn_generation
        )
        try:
            result = self.agent_run.run(
                transcript=transcript,
                identity=identity,
                cancellation=cancellation,
                conversation=self._snapshot(session_id),
            )
        except StageFailure:
            self._cache_provenance(identity, self.agent_run.provenance(identity))
            raise
        provenance = {
            "operation_count": result.operations,
            "action_outcome": result.action_outcome,
        }
        self._cache_provenance(identity, provenance)
        record = {
            "session_id": session_id, "turn_id": turn_id,
            "transcript": transcript, "answer": result.answer, **provenance,
        }
        self._store_turn_record(record)
        self._commit_record(record, "completed")
        if cancellation is not None and cancellation.cancelled:
            raise StageFailure("llm_provider", "selected_provider_cancelled")
        if on_visible_sentence is not None:
            on_visible_sentence(result.answer)
        on_sentence(result.answer)
        return result.answer

    def respond(
        self,
        *,
        session_id: str,
        turn_id: str,
        transcript: str,
        cancellation: CancellationToken | None = None,
    ) -> str:
        identity = AgentRealtimeIdentity(
            session_id, 1, turn_id, f"request-{turn_id}"[:64], 1
        )
        try:
            result = self.agent_run.run(
                transcript=transcript, identity=identity, cancellation=cancellation,
                conversation=self._snapshot(session_id),
            )
        except StageFailure:
            self._cache_provenance(identity, self.agent_run.provenance(identity))
            raise
        provenance = {
            "operation_count": result.operations,
            "action_outcome": result.action_outcome,
        }
        self._cache_provenance(identity, provenance)
        record = {
            "session_id": session_id, "turn_id": turn_id,
            "transcript": transcript, "answer": result.answer, **provenance,
        }
        self._store_turn_record(record)
        self._commit_record(record, "completed")
        return result.answer

    def cancel_request(self) -> None:
        self.agent_run.cancel()

    def cancel(self) -> None:
        self.cancel_request()

    def snapshot_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        return self._snapshot(session_id)

    def restore_session(
        self, session_id: str, snapshot: tuple[dict[str, object], ...]
    ) -> None:
        validated = validate_conversation(snapshot)
        with self._context_lock:
            if validated:
                self._contexts[session_id] = [dict(message) for message in validated]
            else:
                self._contexts.pop(session_id, None)

    def retain_visible_failed_turn(self, session_id: str, turn_id: str) -> None:
        with self._context_lock:
            record = self._turn_records.get((session_id, turn_id))
        if record is None:
            raise StageFailure("llm_provider", "visible_failed_context_unavailable")
        self._commit_record(record, "visible_tts_failed")

    def finish_turn(self, session_id: str, turn_id: str) -> None:
        """Release content-bearing transaction scratch after terminalization."""
        with self._context_lock:
            self._turn_records.pop((session_id, turn_id), None)

    def reset_session(self, session_id: str) -> None:
        with self._context_lock:
            self._contexts.pop(session_id, None)
            for key in tuple(self._turn_records):
                if key[0] == session_id:
                    self._turn_records.pop(key, None)
            for key in tuple(self._turn_provenance):
                if key[0] == session_id:
                    self._turn_provenance.pop(key, None)

    def close(self) -> None:
        # Deliberately no environment stop/remove. Clear every memory-only
        # conversation while leaving installation-owned AgentEnvironment intact.
        with self._context_lock:
            self._contexts.clear()
            self._turn_records.clear()
            self._turn_provenance.clear()

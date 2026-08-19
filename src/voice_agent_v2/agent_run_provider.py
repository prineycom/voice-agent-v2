"""Realtime-provider composition for the production AgentRun path."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .agent_environment import AgentEnvironment
from .agent_environment_config import AgentConfigV2Snapshot
from .agent_run import AgentRealtimeIdentity, AgentRun, ExactLocalLFMAgentAdapter
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
        provider: LocalLFMProvider | None = None,
    ) -> None:
        selected = provider or LocalLFMProvider(request_timeout_seconds=120)
        self.selected = selected
        self.environment = AgentEnvironment(
            config,
            state_root=installation_root / "private",
            workspace=installation_root / "workspace",
            cache=installation_root / "cache",
        )
        self.agent_run = AgentRun(
            self.environment,
            model=ExactLocalLFMAgentAdapter(selected),
        )

    @property
    def observations(self):
        return self.selected.observations

    @property
    def runtime_health(self):
        return self.selected.runtime_health

    @property
    def runtime_live(self):
        return self.selected.runtime_live

    def readiness(self, cancellation: CancellationToken | None = None):
        # Docker remains lazy: ordinary voice readiness probes only the exact LFM.
        return self.selected.readiness(cancellation)

    def warmup(self, cancellation: CancellationToken | None = None):
        return self.selected.warmup(cancellation)

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
        result = self.agent_run.run(
            transcript=transcript,
            identity=AgentRealtimeIdentity(
                session_id, stream_epoch, turn_id, request_id, turn_generation
            ),
            cancellation=cancellation,
        )
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
        return self.agent_run.run(
            transcript=transcript,
            identity=AgentRealtimeIdentity(
                session_id, 1, turn_id, f"request-{turn_id}"[:64], 1
            ),
            cancellation=cancellation,
        ).answer

    def cancel_request(self) -> None:
        self.agent_run.cancel()

    def cancel(self) -> None:
        self.cancel_request()

    def snapshot_session(self, _session_id: str) -> tuple[dict[str, str], ...]:
        return ()

    def restore_session(self, _session_id: str, _snapshot: tuple[dict[str, str], ...]) -> None:
        return None

    def reset_session(self, _session_id: str) -> None:
        return None

    def close(self) -> None:
        # Deliberately no environment stop/remove. The selected HTTP adapter owns
        # no persistent local process and has no close requirement.
        return None

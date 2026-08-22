"""Restart-pinned V2 agent configuration and ordinary-voice-safe status."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_config import AgentConfigError, AgentUserContext
from .agent_environment_config import AgentConfigV2Snapshot, load_agent_config_v2

RUNTIME_STATUS_SCHEMA = "voice-agent.agent-runtime-status.v2"


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    config: AgentConfigV2Snapshot | None
    status: str
    reason_code: str

    @classmethod
    def startup(cls, path: Path | None = None) -> "AgentRuntime":
        context = AgentUserContext.effective()
        config_path = path or (context.profile_root / "config.yaml")
        try:
            config = load_agent_config_v2(
                config_path,
                context=context if path is None else None,
            )
        except AgentConfigError as error:
            return cls(None, "degraded", error.code)
        except Exception:
            return cls(None, "degraded", "config_load_failed")
        return cls(config, "active", "agent_config_active")

    def status_document(
        self, agent_environment: dict[str, object] | None = None,
    ) -> dict[str, object]:
        enabled = bool(self.config and self.config.model.agent.enabled)
        environment_state = "disabled"
        image_state = "unavailable"
        provisioned = False
        environment_reason: str | None = None
        if enabled and agent_environment is not None:
            raw_state = agent_environment.get("state")
            environment_state = raw_state if isinstance(raw_state, str) else "unavailable"
            image_state = (
                str(agent_environment.get("image_state"))
                if agent_environment.get("image_state") in {"prepared", "unavailable"}
                else "unavailable"
            )
            provisioned = agent_environment.get("container_provisioned") is True
            raw_reason = agent_environment.get("reason_code")
            environment_reason = raw_reason if isinstance(raw_reason, str) else None
            failure = agent_environment.get("last_create_failure")
            if isinstance(failure, dict) and failure.get("cause") in {
                "docker_create_rejected", "docker_create_response_invalid",
            }:
                # The closed subclass remains private AgentEnvironment status;
                # the gateway publishes only the stable public reason.
                environment_reason = "environment_creation_failed"
        admitted = enabled and (
            agent_environment is None or environment_state == "running"
        )
        degraded = enabled and agent_environment is not None and not admitted
        return {
            "schema_version": RUNTIME_STATUS_SCHEMA,
            "status": "degraded" if degraded else self.status,
            "reason_code": environment_reason if degraded and environment_reason else self.reason_code,
            "config_revision": self.config.semantic_revision if self.config else None,
            "agent_enabled": enabled,
            "agent_tools_admitted": admitted,
            "environment_count": 1,
            "environment_state": environment_state,
            "environment_image_state": image_state,
            "environment_container_provisioned": provisioned,
            "environment_reason_code": environment_reason,
            "provider_mode": "local",
            "automatic_fallback": False,
        }

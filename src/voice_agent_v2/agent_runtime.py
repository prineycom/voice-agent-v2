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

    def status_document(self) -> dict[str, object]:
        return {
            "schema_version": RUNTIME_STATUS_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "config_revision": self.config.semantic_revision if self.config else None,
            "agent_enabled": bool(self.config and self.config.model.agent.enabled),
            "agent_tools_admitted": bool(self.config and self.config.model.agent.enabled),
            "environment_count": 1,
            "provider_mode": "local",
            "automatic_fallback": False,
        }

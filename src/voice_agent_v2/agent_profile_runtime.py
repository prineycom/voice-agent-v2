"""Restart-only runtime snapshot for the private agent profile.

The profile is a soft dependency of the existing gateway/controller process.  It
is loaded once during composition, never watched or repaired, and can authorize
no capability while the production registry is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Final

from .agent_config import AgentConfigError, AgentConfigService, AgentConfigSnapshot


RUNTIME_STATUS_SCHEMA: Final = "voice-agent.agent-profile-runtime-status.v1"
EFFECTIVE_POLICY_SCHEMA: Final = "voice-agent.effective-agent-policy.v1"
ACTIVE_REASON: Final = "agent_profile_active"
UNKNOWN_FAILURE_REASON: Final = "config_load_failed"


def _deny_all_policy_revision() -> str:
    canonical = json.dumps(
        {"capabilities": {}}, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(
        EFFECTIVE_POLICY_SCHEMA.encode("ascii") + b"\0" + canonical
    ).hexdigest()


DENY_ALL_POLICY_REVISION: Final = _deny_all_policy_revision()
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeAgentProfileSnapshot:
    """Deeply immutable startup identity and effective authority."""

    profile_id: str | None
    config_revision: str | None
    effective_policy_revision: str = DENY_ALL_POLICY_REVISION
    effective_capability_ids: tuple[str, ...] = ()
    agent_capabilities_admitted: bool = False

    def __post_init__(self) -> None:
        if self.effective_capability_ids or self.agent_capabilities_admitted:
            raise ValueError("the E1.2 production authority must remain deny-all")


@dataclass(frozen=True, slots=True)
class AgentProfileRuntime:
    """One pinned startup result; this type intentionally has no reload method."""

    snapshot: RuntimeAgentProfileSnapshot
    status: str
    reason_code: str

    @classmethod
    def startup(
        cls, service: AgentConfigService | None = None
    ) -> "AgentProfileRuntime":
        """Read the typed profile exactly once and normalize every failure."""

        try:
            loaded: AgentConfigSnapshot = (service or AgentConfigService()).status()
        except AgentConfigError as error:
            runtime = cls._degraded(error.code)
        except Exception:
            # Raw exception text and type are deliberately not observable.
            runtime = cls._degraded(UNKNOWN_FAILURE_REASON)
        else:
            runtime = cls(
                snapshot=RuntimeAgentProfileSnapshot(
                    profile_id=loaded.model.profile_id,
                    config_revision=loaded.semantic_revision,
                ),
                status="active",
                reason_code=ACTIVE_REASON,
            )
        _LOGGER.info(
            "agent_profile_startup status=%s reason_code=%s "
            "agent_capabilities_admitted=false",
            runtime.status,
            runtime.reason_code,
        )
        return runtime

    @classmethod
    def _degraded(cls, reason_code: str) -> "AgentProfileRuntime":
        return cls(
            snapshot=RuntimeAgentProfileSnapshot(profile_id=None, config_revision=None),
            status="degraded",
            reason_code=reason_code,
        )

    def status_document(self) -> dict[str, object]:
        return {
            "schema_version": RUNTIME_STATUS_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "profile_id": self.snapshot.profile_id,
            "config_revision": self.snapshot.config_revision,
            "effective_policy_revision": self.snapshot.effective_policy_revision,
            "effective_authority": "deny_all",
            "effective_capability_count": len(self.snapshot.effective_capability_ids),
            "agent_capabilities_admitted": self.snapshot.agent_capabilities_admitted,
        }

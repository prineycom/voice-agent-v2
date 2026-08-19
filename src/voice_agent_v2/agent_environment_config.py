"""Strict v2 configuration for the installation-owned AgentEnvironment.

V1 is accepted only as explicit upgrade input.  No V1 identity is copied into
V2, and no field can select an environment, container runtime, or backend.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml

from .agent_config import (
    AgentConfigError,
    AgentConfigService,
    AgentConfigV1,
    AgentUserContext,
    _decode_config,
    _open_candidate,
    _parse_yaml,
    _read_bounded,
    _typed_model,
)

CONFIG_SCHEMA = "voice-agent.config.v2"
CONFIG_STATUS_SCHEMA = "voice-agent.agent-config-status.v2"
PINNED_IMAGE = "ghcr.io/prineycom/voice-agent-environment@sha256:8a5a972b25f7c203c71b8e28af17f756d9daf39bc74ebbc5d86eaf6c9f3da421"
TOOL_IDS = (
    "shell.exec",
    "file.read",
    "file.search",
    "file.write",
    "file.edit",
    "file.patch",
    "execute_code",
    "process",
    "receipt",
    "web.search",
    "web.fetch",
    "web.extract",
    "report.artifact",
    "report.deliver",
)


class AgentSettingsV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    enabled: bool
    max_decisions: int = Field(ge=1, le=24)
    active_deadline_seconds: int = Field(ge=1, le=600)
    tools: tuple[str, ...]

    @field_validator("tools", mode="before")
    @classmethod
    def fixed_tools(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or tuple(value) != TOOL_IDS:
            raise ValueError("the release tool set is fixed")
        return TOOL_IDS


class ImageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    reference: Literal[PINNED_IMAGE]
    pull_at_runtime: Literal[False]


class LifecycleV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    lazy_create: Literal[True]
    persistent: Literal[True]
    restart_policy: Literal["no"]
    idle_action: Literal["none"]
    command_timeout_seconds: int = Field(ge=1, le=600)
    command_timeout_grace_seconds: int = Field(ge=1, le=30)


class ResourceV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    cpus: int = Field(ge=1, le=4)
    memory_mib: int = Field(ge=512, le=8192)
    pids: int = Field(ge=16, le=256)
    shm_mib: int = Field(ge=64, le=1024)
    rootfs_target_mib: int = Field(ge=512, le=16384)
    workspace_target_mib: int = Field(ge=512, le=65536)
    cache_target_mib: int = Field(ge=512, le=32768)
    host_free_reserve_mib: int = Field(ge=8192, le=65536)
    watchdog_interval_seconds: int = Field(ge=1, le=10)
    maximum_output_bytes: int = Field(ge=1024, le=1048576)
    maximum_stream_bytes: int = Field(ge=1024, le=1048576)
    stream_timeout_seconds: int = Field(ge=1, le=600)


class NetworkV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    enabled: bool
    publish_ports: tuple[()]

    @field_validator("publish_ports", mode="before")
    @classmethod
    def no_published_ports(cls, value: object) -> tuple[()]:
        if value not in ([], ()):
            raise ValueError("published ports are forbidden")
        return ()


class AdditionalMountV2(BaseModel):
    """One restart-pinned operator mount; runtime custody is checked separately."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    source: str
    destination: str
    mode: Literal["read_only", "read_write"]

    @field_validator("source", "destination")
    @classmethod
    def absolute_clean_path(cls, value: str) -> str:
        if (
            not isinstance(value, str) or not value.startswith("/") or "\0" in value or "," in value
            or value == "/" or "//" in value or value.endswith("/")
            or any(part in {"", ".", ".."} for part in Path(value).parts[1:])
        ):
            raise ValueError("mount paths must be clean absolute paths")
        return value


class CredentialsV2(BaseModel):
    """One declaration with fixed exposed names; private state owns all bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    creation_environment_names: tuple[str, ...] = Field(max_length=16)
    creation_file_names: tuple[str, ...] = Field(max_length=16)
    exec_environment_names: tuple[str, ...] = Field(max_length=16)
    exec_file_names: tuple[str, ...] = Field(max_length=16)

    @field_validator("*", mode="before")
    @classmethod
    def fixed_names(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("credential names must be a list")
        names = tuple(value)
        for name in names:
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name) is None
                or name.startswith("VOICE_AGENT_")
            ):
                raise ValueError("credential exposure name is invalid or reserved")
        if len(names) != len(set(names)):
            raise ValueError("credential exposure names must be unique")
        return names

    @model_validator(mode="after")
    def no_mode_overlap(self) -> "CredentialsV2":
        names = (
            self.creation_environment_names
            + self.creation_file_names
            + self.exec_environment_names
            + self.exec_file_names
        )
        if len(names) != len(set(names)):
            raise ValueError("a credential name has exactly one exposure mode")
        return self


class AgentEnvironmentV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    image: ImageV2
    lifecycle: LifecycleV2
    additional_mounts: tuple[AdditionalMountV2, ...] = Field(max_length=16)
    network: NetworkV2
    credentials: CredentialsV2
    resources: ResourceV2

    @field_validator("additional_mounts", mode="before")
    @classmethod
    def mount_list(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("additional mounts must be a list")
        return tuple(value)

    @model_validator(mode="after")
    def unique_mounts(self) -> "AgentEnvironmentV2":
        sources = tuple(item.source for item in self.additional_mounts)
        destinations = tuple(item.destination for item in self.additional_mounts)
        if len(sources) != len(set(sources)) or len(destinations) != len(set(destinations)):
            raise ValueError("additional mount sources and destinations must be unique")
        return self


class AgentConfigV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[CONFIG_SCHEMA]
    agent: AgentSettingsV2
    agent_environment: AgentEnvironmentV2


DEFAULT_DOCUMENT: dict[str, object] = {
    "schema_version": CONFIG_SCHEMA,
    "agent": {
        "enabled": True,
        "max_decisions": 24,
        "active_deadline_seconds": 600,
        "tools": list(TOOL_IDS),
    },
    "agent_environment": {
        "image": {"reference": PINNED_IMAGE, "pull_at_runtime": False},
        "lifecycle": {
            "lazy_create": True,
            "persistent": True,
            "restart_policy": "no",
            "idle_action": "none",
            "command_timeout_seconds": 120,
            "command_timeout_grace_seconds": 5,
        },
        "additional_mounts": [],
        "network": {"enabled": True, "publish_ports": []},
        "credentials": {
            "creation_environment_names": [],
            "creation_file_names": [],
            "exec_environment_names": [],
            "exec_file_names": [],
        },
        "resources": {
            "cpus": 2,
            "memory_mib": 4096,
            "pids": 128,
            "shm_mib": 1024,
            "rootfs_target_mib": 4096,
            "workspace_target_mib": 5120,
            "cache_target_mib": 2048,
            "host_free_reserve_mib": 8192,
            "watchdog_interval_seconds": 2,
            "maximum_output_bytes": 262144,
            "maximum_stream_bytes": 262144,
            "stream_timeout_seconds": 120,
        },
    },
}
DEFAULT_CONFIG_BYTES = yaml.safe_dump(DEFAULT_DOCUMENT, sort_keys=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AgentConfigV2Snapshot:
    model: AgentConfigV2
    semantic_revision: str

    def status_document(self) -> dict[str, object]:
        return {
            "schema_version": CONFIG_STATUS_SCHEMA,
            "config_schema_version": CONFIG_SCHEMA,
            "config_revision": self.semantic_revision,
            "agent_enabled": self.model.agent.enabled,
            "maximum_decisions": self.model.agent.max_decisions,
            "active_deadline_seconds": self.model.agent.active_deadline_seconds,
            "tool_count": len(self.model.agent.tools),
            "environment_count": 1,
        }


def _snapshot(document: object) -> AgentConfigV2Snapshot:
    if not isinstance(document, dict):
        raise AgentConfigError("config_model_invalid")
    schema = document.get("schema_version")
    if schema != CONFIG_SCHEMA:
        raise AgentConfigError("config_schema_unsupported")
    try:
        model = AgentConfigV2.model_validate(document, strict=True)
    except ValidationError as error:
        kinds = {str(item.get("type")) for item in error.errors(include_url=False)}
        if "extra_forbidden" in kinds:
            raise AgentConfigError("config_unknown_field") from error
        if "missing" in kinds:
            raise AgentConfigError("config_missing_field") from error
        raise AgentConfigError("config_model_invalid") from error
    encoded = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    revision = hashlib.sha256(CONFIG_SCHEMA.encode("ascii") + b"\0" + encoded).hexdigest()
    return AgentConfigV2Snapshot(model, revision)


def parse_agent_config_v2(source: bytes) -> AgentConfigV2Snapshot:
    return _snapshot(_parse_yaml(_decode_config(source)))


def upgrade_v1_to_v2(source: bytes) -> bytes:
    """Validate historical V1 and return the fixed V2 default without identity carryover."""
    historical = _typed_model(_parse_yaml(_decode_config(source)))
    if not isinstance(historical, AgentConfigV1):
        raise AgentConfigError("config_schema_unsupported")
    return DEFAULT_CONFIG_BYTES


class AgentConfigV2Service(AgentConfigService):
    """Reuse the landed no-follow owner/mode/tree custody for active V2."""

    def _load_profile_descriptor(self, descriptor: int) -> AgentConfigV2Snapshot:
        return parse_agent_config_v2(_read_bounded(descriptor))


def load_agent_config_v2(
    path: Path,
    *,
    context: AgentUserContext | None = None,
) -> AgentConfigV2Snapshot:
    """Securely load an explicit candidate or the complete managed source tree."""
    service = AgentConfigV2Service(context=context)
    if context is not None and path == context.profile_root / "config.yaml":
        return service.status()
    uid = (context or AgentUserContext.effective()).uid
    descriptor = _open_candidate(path, uid)
    try:
        return parse_agent_config_v2(_read_bounded(descriptor))
    finally:
        os.close(descriptor)

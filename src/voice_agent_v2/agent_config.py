"""Strict, private, capability-free agent profile foundation.

This module owns only the operator-facing E1.1 source tree.  It does not load the
profile into the voice runtime and it deliberately contains no capability
implementation or authority-bearing fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
import yaml

from .instance_runtime import InstanceRuntimeError, selected_instance_root

from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    DocumentStartEvent,
    ScalarEvent,
)
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode


PROFILE_SCHEMA = "voice-agent.config.v1"
REGISTRY_SCHEMA = "voice-agent.capability-registry.v1"
STATUS_SCHEMA = "voice-agent.agent-config-status.v1"
RESULT_SCHEMA = "voice-agent.agent-config-result.v1"
ERROR_SCHEMA = "voice-agent.agent-config-error.v1"
PROFILE_ROOT_NAME = ".voice-agent"
MAX_CONFIG_BYTES = 64 * 1024
MAX_YAML_NODES = 2_048
MAX_YAML_DEPTH = 16
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
MINIMAL_CONFIG_BYTES = (
    b"schema_version: voice-agent.config.v1\n"
    b"profile_id: default\n"
    b"access:\n"
    b"  capabilities: {}\n"
)
RESERVED_DIRECTORIES = ("skills", "sessions", "memory")
RESERVED_FILES = ("config.yaml", "SOUL.md")
PROFILE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
YAML_11_BOOLEAN_TRAP = re.compile(r"^(?:y|yes|n|no|on|off)$", re.IGNORECASE)
CANONICAL_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")

# The code-owned registry is immutable and intentionally empty.  The tracked
# registry is checked against it whenever the operator profile service starts.
EMPTY_CAPABILITY_REGISTRY: Mapping[str, object] = MappingProxyType({})


class AgentConfigError(RuntimeError):
    """A content-free, stable agent profile error."""

    _MESSAGES = {
        "config_missing": "agent profile input is missing",
        "config_path_unsafe": "agent profile path custody is unsafe",
        "config_owner": "agent profile ownership is unsafe",
        "config_permissions": "agent profile mode is unsafe",
        "config_hardlink": "agent profile file has multiple links",
        "config_file_type": "agent profile path type is unsafe",
        "config_too_large": "agent profile input exceeds its bound",
        "config_encoding": "agent profile input is not valid UTF-8",
        "config_bom": "agent profile input contains a byte-order mark",
        "config_control_character": "agent profile input contains a forbidden control",
        "config_nesting": "agent profile input exceeds its nesting bound",
        "config_node_limit": "agent profile input exceeds its node bound",
        "config_alias": "agent profile input contains an alias",
        "config_anchor": "agent profile input contains an anchor",
        "config_merge_key": "agent profile input contains a merge key",
        "config_tag": "agent profile input contains an explicit tag",
        "config_key_type": "agent profile mapping key is not a string",
        "config_duplicate_field": "agent profile input contains a duplicate field",
        "config_implicit_value": "agent profile input contains an ambiguous implicit value",
        "config_syntax": "agent profile syntax is invalid",
        "config_unknown_field": "agent profile contains an unknown field",
        "config_missing_field": "agent profile is missing a required field",
        "config_schema_unsupported": "agent profile schema is unsupported",
        "config_model_invalid": "agent profile value does not match the strict model",
        "config_capability_unknown": "agent profile requests an unregistered capability",
        "config_registry_invalid": "release capability registry is invalid",
        "config_init_failed": "agent profile initialization failed",
    }

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(self._MESSAGES.get(code, "agent profile operation failed"))


class AccessV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    capabilities: Mapping[str, object]

    @field_validator("capabilities", mode="after")
    @classmethod
    def freeze_capabilities(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))


class AgentConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str
    profile_id: str = Field(pattern=PROFILE_ID.pattern)
    access: AccessV1


@dataclass(frozen=True)
class AgentUserContext:
    uid: int
    home: Path
    profile_root_override: Path | None = None

    @classmethod
    def effective(cls) -> "AgentUserContext":
        uid = os.geteuid()
        try:
            account = pwd.getpwuid(uid)
        except KeyError as error:
            raise AgentConfigError("config_path_unsafe") from error
        home = Path(account.pw_dir)
        if not home.is_absolute():
            raise AgentConfigError("config_path_unsafe")
        try:
            instance = selected_instance_root()
        except InstanceRuntimeError as error:
            raise AgentConfigError("config_path_unsafe") from error
        profile = instance / "config" / "agent-profile" if instance is not None else None
        return cls(uid=uid, home=home, profile_root_override=profile)

    @property
    def profile_root(self) -> Path:
        return self.profile_root_override or (self.home / PROFILE_ROOT_NAME)


@dataclass(frozen=True)
class AgentConfigSnapshot:
    model: AgentConfigV1
    semantic_revision: str
    effective_capability_count: int = 0

    def status_document(self) -> dict[str, object]:
        # This deliberately exposes only the four fields accepted by E1.1.
        return {
            "schema_version": STATUS_SCHEMA,
            "profile_schema_version": self.model.schema_version,
            "profile_id": self.model.profile_id,
            "semantic_revision": self.semantic_revision,
            "effective_capability_count": self.effective_capability_count,
        }


@dataclass(frozen=True)
class InitResult:
    changed: bool
    snapshot: AgentConfigSnapshot

    def document(self) -> dict[str, object]:
        return {
            "schema_version": RESULT_SCHEMA,
            "operation": "init",
            "status": "initialized" if self.changed else "unchanged",
            "changed": self.changed,
            "profile_schema_version": self.snapshot.model.schema_version,
            "profile_id": self.snapshot.model.profile_id,
            "semantic_revision": self.snapshot.semantic_revision,
            "effective_capability_count": 0,
        }


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOATIME", 0)
)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _path_error(error: OSError, *, missing: str = "config_missing") -> AgentConfigError:
    if error.errno == errno.ENOENT:
        return AgentConfigError(missing)
    return AgentConfigError("config_path_unsafe")


def _open_directory_path(path: Path) -> int:
    """Open an absolute directory by no-follow dirfd walk."""

    lexical = _absolute_lexical(path)
    if not lexical.is_absolute():
        raise AgentConfigError("config_path_unsafe")
    try:
        current = os.open(lexical.anchor, _DIRECTORY_FLAGS)
        for component in lexical.parts[1:]:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError:
                os.close(current)
                raise
            os.close(current)
            current = child
        return current
    except OSError as error:
        raise _path_error(error) from error


def _verify_directory(metadata: os.stat_result, uid: int) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise AgentConfigError("config_file_type")
    if metadata.st_uid != uid:
        raise AgentConfigError("config_owner")
    if stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE:
        raise AgentConfigError("config_permissions")


def _verify_regular(metadata: os.stat_result, uid: int) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise AgentConfigError("config_file_type")
    if metadata.st_uid != uid:
        raise AgentConfigError("config_owner")
    if stat.S_IMODE(metadata.st_mode) != FILE_MODE:
        raise AgentConfigError("config_permissions")
    if metadata.st_nlink != 1:
        raise AgentConfigError("config_hardlink")


def _open_owned_directory_at(parent: int, name: str, uid: int) -> int:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise _path_error(error) from error
    if stat.S_ISLNK(before.st_mode):
        raise AgentConfigError("config_path_unsafe")
    _verify_directory(before, uid)
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as error:
        raise _path_error(error) from error
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise AgentConfigError("config_path_unsafe")
    try:
        _verify_directory(after, uid)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_owned_file_at(parent: int, name: str, uid: int) -> int:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise _path_error(error) from error
    if stat.S_ISLNK(before.st_mode):
        raise AgentConfigError("config_path_unsafe")
    _verify_regular(before, uid)
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
    except OSError as error:
        raise _path_error(error) from error
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise AgentConfigError("config_path_unsafe")
    try:
        _verify_regular(after, uid)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_candidate(path: Path, uid: int) -> int:
    lexical = _absolute_lexical(path)
    parent = _open_directory_path(lexical.parent)
    try:
        return _open_owned_file_at(parent, lexical.name, uid)
    finally:
        os.close(parent)


def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_CONFIG_BYTES + 1
    try:
        while remaining:
            chunk = os.read(descriptor, min(16 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise AgentConfigError("config_path_unsafe") from error
    value = b"".join(chunks)
    if len(value) > MAX_CONFIG_BYTES:
        raise AgentConfigError("config_too_large")
    return value


def _decode_config(source: bytes) -> str:
    if source.startswith(b"\xef\xbb\xbf"):
        raise AgentConfigError("config_bom")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AgentConfigError("config_encoding") from error
    if text.startswith("\ufeff"):
        raise AgentConfigError("config_bom")
    for character in text:
        codepoint = ord(character)
        if (
            codepoint == 0
            or codepoint < 0x20 and character not in {"\t", "\n", "\r"}
            or 0x7F <= codepoint <= 0x9F
        ):
            raise AgentConfigError("config_control_character")
    return text


def _scan_yaml(text: str) -> None:
    node_count = 0
    depth = 0
    documents = 0
    try:
        for event in yaml.parse(text, Loader=yaml.BaseLoader):
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if event.version is not None or event.tags:
                    raise AgentConfigError("config_tag")
            if isinstance(event, AliasEvent):
                raise AgentConfigError("config_alias")
            if isinstance(event, (ScalarEvent, CollectionStartEvent)):
                node_count += 1
                if node_count > MAX_YAML_NODES:
                    raise AgentConfigError("config_node_limit")
                if getattr(event, "anchor", None) is not None:
                    raise AgentConfigError("config_anchor")
                if getattr(event, "tag", None) is not None:
                    raise AgentConfigError("config_tag")
            if isinstance(event, ScalarEvent):
                if event.style is None and YAML_11_BOOLEAN_TRAP.fullmatch(event.value):
                    raise AgentConfigError("config_implicit_value")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise AgentConfigError("config_nesting")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
    except AgentConfigError:
        raise
    except yaml.YAMLError as error:
        raise AgentConfigError("config_syntax") from error
    if documents != 1 or depth != 0:
        raise AgentConfigError("config_syntax")


def _scalar_value(node: ScalarNode) -> object:
    value = node.value
    if node.style is None:
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "~"}:
            return None
        if CANONICAL_INTEGER.fullmatch(value):
            try:
                return int(value)
            except ValueError:
                pass
    return value


def _construct_yaml(node: Node, *, depth: int = 1) -> object:
    if depth > MAX_YAML_DEPTH:
        raise AgentConfigError("config_nesting")
    if isinstance(node, ScalarNode):
        return _scalar_value(node)
    if isinstance(node, SequenceNode):
        return [_construct_yaml(item, depth=depth + 1) for item in node.value]
    if isinstance(node, MappingNode):
        result: dict[str, object] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise AgentConfigError("config_key_type")
            key = _scalar_value(key_node)
            if not isinstance(key, str):
                raise AgentConfigError("config_key_type")
            if key == "<<":
                raise AgentConfigError("config_merge_key")
            if key in result:
                raise AgentConfigError("config_duplicate_field")
            result[key] = _construct_yaml(value_node, depth=depth + 1)
        return result
    raise AgentConfigError("config_syntax")


def _parse_yaml(text: str) -> object:
    _scan_yaml(text)
    try:
        node = yaml.compose(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as error:
        raise AgentConfigError("config_syntax") from error
    if node is None:
        raise AgentConfigError("config_syntax")
    return _construct_yaml(node)


def _typed_model(value: object) -> AgentConfigV1:
    if not isinstance(value, dict):
        raise AgentConfigError("config_model_invalid")
    schema = value.get("schema_version")
    if schema is not None and schema != PROFILE_SCHEMA:
        raise AgentConfigError("config_schema_unsupported")
    try:
        model = AgentConfigV1.model_validate(value, strict=True)
    except ValidationError as error:
        failures = error.errors(include_url=False, include_context=False, include_input=False)
        types = {str(failure.get("type")) for failure in failures}
        if "extra_forbidden" in types:
            raise AgentConfigError("config_unknown_field") from error
        if "missing" in types:
            raise AgentConfigError("config_missing_field") from error
        raise AgentConfigError("config_model_invalid") from error
    if model.schema_version != PROFILE_SCHEMA:
        raise AgentConfigError("config_schema_unsupported")
    return model


def load_production_registry(path: Path | None = None) -> Mapping[str, object]:
    registry_path = path or Path(__file__).resolve().parents[2] / "config/agent-capabilities-v1.json"
    try:
        raw = registry_path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise AgentConfigError("config_registry_invalid") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "capabilities"}
        or document.get("schema_version") != REGISTRY_SCHEMA
        or document.get("capabilities") != {}
        or not isinstance(document.get("capabilities"), dict)
    ):
        raise AgentConfigError("config_registry_invalid")
    return EMPTY_CAPABILITY_REGISTRY


def parse_agent_config(
    source: bytes,
    *,
    capability_registry: Mapping[str, object] = EMPTY_CAPABILITY_REGISTRY,
) -> AgentConfigSnapshot:
    if len(source) > MAX_CONFIG_BYTES:
        raise AgentConfigError("config_too_large")
    model = _typed_model(_parse_yaml(_decode_config(source)))
    unknown = set(model.access.capabilities) - set(capability_registry)
    if unknown:
        raise AgentConfigError("config_capability_unknown")
    # E1.1 has no registered capability, hence no request can be effective.
    if model.access.capabilities:
        raise AgentConfigError("config_capability_unknown")
    canonical = {
        "access": {"capabilities": {}},
        "profile_id": model.profile_id,
        "schema_version": model.schema_version,
    }
    canonical_json = json.dumps(
        canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    revision = hashlib.sha256(PROFILE_SCHEMA.encode("ascii") + b"\0" + canonical_json).hexdigest()
    return AgentConfigSnapshot(model=model, semantic_revision=revision)


class AgentConfigService:
    """Operator-only initialize/validate/status path with injected test custody."""

    def __init__(
        self,
        *,
        context: AgentUserContext | None = None,
        registry_path: Path | None = None,
    ) -> None:
        self.context = context or AgentUserContext.effective()
        self.registry = load_production_registry(registry_path)

    def _open_profile_root(self) -> int:
        root = _open_directory_path(self.context.profile_root)
        try:
            _verify_directory(os.fstat(root), self.context.uid)
        except BaseException:
            os.close(root)
            raise
        return root

    def _validate_reserved_tree(self, root: int) -> None:
        descriptors: list[int] = []
        try:
            for name in RESERVED_DIRECTORIES:
                descriptors.append(_open_owned_directory_at(root, name, self.context.uid))
            descriptors.append(_open_owned_file_at(root, "SOUL.md", self.context.uid))
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def _load_profile_descriptor(self, descriptor: int) -> AgentConfigSnapshot:
        return parse_agent_config(_read_bounded(descriptor), capability_registry=self.registry)

    def validate(self, candidate: Path | None = None) -> AgentConfigSnapshot:
        if candidate is not None:
            descriptor = _open_candidate(candidate, self.context.uid)
            try:
                return self._load_profile_descriptor(descriptor)
            finally:
                os.close(descriptor)
        root = self._open_profile_root()
        try:
            self._validate_reserved_tree(root)
            descriptor = _open_owned_file_at(root, "config.yaml", self.context.uid)
            try:
                return self._load_profile_descriptor(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(root)

    def status(self) -> AgentConfigSnapshot:
        return self.validate()

    @staticmethod
    def _existing_at(parent: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise AgentConfigError("config_path_unsafe") from error
        return True

    def _create_directory(self, parent: int, name: str) -> None:
        try:
            os.mkdir(name, DIRECTORY_MODE, dir_fd=parent)
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                os.fchmod(descriptor, DIRECTORY_MODE)
                _verify_directory(os.fstat(descriptor), self.context.uid)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise AgentConfigError("config_init_failed") from error

    def _create_file(self, parent: int, name: str, content: bytes) -> None:
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            descriptor = os.open(name, flags, FILE_MODE, dir_fd=parent)
            try:
                os.fchmod(descriptor, FILE_MODE)
                view = memoryview(content)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError(errno.EIO, "short profile write")
                    view = view[written:]
                os.fsync(descriptor)
                _verify_regular(os.fstat(descriptor), self.context.uid)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise AgentConfigError("config_init_failed") from error

    def init(self) -> InitResult:
        """Create only absent paths after preflighting every existing path."""

        profile_root = self.context.profile_root
        parent = _open_directory_path(profile_root.parent)
        root: int | None = None
        changed = False
        try:
            if self._existing_at(parent, profile_root.name):
                root = _open_owned_directory_at(parent, profile_root.name, self.context.uid)
            else:
                self._create_directory(parent, profile_root.name)
                changed = True
                root = _open_owned_directory_at(parent, profile_root.name, self.context.uid)

            # Preflight the complete existing known tree before adding anything.
            existing_directories: set[str] = set()
            for name in RESERVED_DIRECTORIES:
                if self._existing_at(root, name):
                    descriptor = _open_owned_directory_at(root, name, self.context.uid)
                    os.close(descriptor)
                    existing_directories.add(name)

            existing_files: set[str] = set()
            for name in RESERVED_FILES:
                if self._existing_at(root, name):
                    descriptor = _open_owned_file_at(root, name, self.context.uid)
                    try:
                        if name == "config.yaml":
                            self._load_profile_descriptor(descriptor)
                    finally:
                        os.close(descriptor)
                    existing_files.add(name)

            for name in RESERVED_DIRECTORIES:
                if name not in existing_directories:
                    self._create_directory(root, name)
                    changed = True
            if "config.yaml" not in existing_files:
                self._create_file(root, "config.yaml", MINIMAL_CONFIG_BYTES)
                changed = True
            if "SOUL.md" not in existing_files:
                self._create_file(root, "SOUL.md", b"")
                changed = True
            os.fsync(root)
        finally:
            if root is not None:
                os.close(root)
            os.close(parent)

        snapshot = self.status()
        return InitResult(changed=changed, snapshot=snapshot)

"""Private value custody for the single AgentEnvironment credential declaration.

The public v2 configuration owns only fixed exposure names and modes.  This
module reads only the installation-owned private file; it never searches the
host environment, home directory, credential helpers, agents, or keyrings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Protocol


PRIVATE_SCHEMA = "voice-agent.credentials.v1"
MAX_PRIVATE_BYTES = 256 * 1024


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def resolve(self, kind: str, name: str) -> bytes: ...
    def fingerprint(self, kind: str, name: str) -> str: ...


class EmptyCredentialStore:
    def resolve(self, kind: str, name: str) -> bytes:
        del kind, name
        raise CredentialStoreError("credential_value_unavailable")

    def fingerprint(self, kind: str, name: str) -> str:
        del kind, name
        raise CredentialStoreError("credential_value_unavailable")


class InstallationCredentialStore:
    """Reload one exact private document for each resolution.

    Per-exec rotation therefore appears on the next exec.  A controller-created
    keyed fingerprint is used only for create-time spec compatibility.
    """

    def __init__(self, state_root: Path) -> None:
        self.path = state_root / "credentials.json"
        self.key_path = state_root / "credential-fingerprint.key"

    @staticmethod
    def _secure_regular(path: Path, *, maximum: int) -> bytes:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size > maximum
            ):
                raise CredentialStoreError("credential_private_state_unsafe")
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining and (chunk := os.read(descriptor, min(64 * 1024, remaining))):
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            if len(value) > maximum:
                raise CredentialStoreError("credential_private_state_unsafe")
            return value
        except CredentialStoreError:
            raise
        except OSError as error:
            raise CredentialStoreError("credential_private_state_unavailable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _verify_root(self) -> None:
        try:
            metadata = self.path.parent.lstat()
        except OSError as error:
            raise CredentialStoreError("credential_private_state_unavailable") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise CredentialStoreError("credential_private_state_unsafe")

    def _document(self) -> dict[str, object]:
        self._verify_root()
        try:
            document = json.loads(self._secure_regular(self.path, maximum=MAX_PRIVATE_BYTES))
        except (UnicodeError, ValueError, TypeError) as error:
            raise CredentialStoreError("credential_private_state_invalid") from error
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "environment", "files"}
            or document.get("schema_version") != PRIVATE_SCHEMA
            or not isinstance(document.get("environment"), dict)
            or not isinstance(document.get("files"), dict)
        ):
            raise CredentialStoreError("credential_private_state_invalid")
        return document

    def resolve(self, kind: str, name: str) -> bytes:
        section_name = {"environment": "environment", "file": "files"}.get(kind)
        if section_name is None:
            raise CredentialStoreError("credential_kind_invalid")
        section = self._document()[section_name]
        assert isinstance(section, dict)
        encoded = section.get(name)
        if not isinstance(encoded, str):
            raise CredentialStoreError("credential_value_unavailable")
        try:
            value = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise CredentialStoreError("credential_value_invalid") from error
        if len(value) > 64 * 1024 or (kind == "environment" and (b"\0" in value or b"\n" in value or b"\r" in value or len(value) > 16 * 1024)):
            raise CredentialStoreError("credential_value_invalid")
        if kind == "environment":
            try:
                value.decode("utf-8")
            except UnicodeError as error:
                raise CredentialStoreError("credential_value_invalid") from error
        return value

    def _key(self) -> bytes:
        self._verify_root()
        if not self.key_path.exists():
            try:
                descriptor = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    output.write(os.urandom(32))
                    output.flush()
                    os.fsync(output.fileno())
            except FileExistsError:
                pass
            except OSError as error:
                raise CredentialStoreError("credential_private_state_unavailable") from error
        key = self._secure_regular(self.key_path, maximum=32)
        if len(key) != 32:
            raise CredentialStoreError("credential_private_state_invalid")
        return key

    def fingerprint(self, kind: str, name: str) -> str:
        value = self.resolve(kind, name)
        return hmac.new(
            self._key(),
            b"voice-agent-v2-credential\0" + kind.encode("ascii") + b"\0" + name.encode("ascii") + b"\0" + value,
            hashlib.sha256,
        ).hexdigest()

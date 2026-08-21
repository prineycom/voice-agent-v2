"""Fail-closed server-only configuration for the Slice 6 development app."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import struct
from urllib.parse import urlsplit

import fcntl

from .instance_runtime import listener_port
from .runtime_directory import require_lifetime_runtime_root


LOOPBACK_APP_ORIGIN = "http://127.0.0.1:8000"
BUILD_ID_PATTERN = re.compile(r"^(?:development|[0-9a-f]{40})$")
RELEASE_ID_PATTERN = re.compile(r"^(?:development|[0-9a-f]{24})$")
PROCESS_IDENTITY_PATTERN = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


class Slice6ConfigurationError(ValueError):
    pass


def _local_rtc_media_path() -> tuple[str, str]:
    """Select one host IPv4 media path without widening HTTP/signaling binds."""

    preferred: list[tuple[int, str]] = []
    try:
        for line in Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 8 and fields[1] == "00000000" and int(fields[3], 16) & 1:
                preferred.append((int(fields[6]), fields[0]))
    except (OSError, ValueError):
        pass
    names = [name for _metric, name in sorted(preferred)]
    names.extend(sorted(name for _index, name in socket.if_nameindex() if name not in names))
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for name in names:
            if name == "lo" or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
                continue
            try:
                state = (Path("/sys/class/net") / name / "operstate").read_text(
                    encoding="ascii"
                ).strip()
                if state not in {"up", "unknown"}:
                    continue
            except OSError:
                continue
            try:
                packed = fcntl.ioctl(
                    probe.fileno(), 0x8915, struct.pack("256s", name.encode("ascii"))
                )
                address = socket.inet_ntoa(packed[20:24])
                parsed = ipaddress.ip_address(address)
            except (OSError, UnicodeEncodeError, ValueError):
                continue
            if parsed.version == 4 and not (
                parsed.is_loopback or parsed.is_link_local or parsed.is_multicast
            ):
                return name, address
    finally:
        probe.close()
    raise Slice6ConfigurationError(
        "one active non-loopback IPv4 interface is required for same-host RTC media"
    )


def livekit_server_config(environment: dict[str, str] | None = None) -> str:
    signal_port = listener_port("VOICE_AGENT_LIVEKIT_PORT", 7880, environment)
    rtc_udp_port = listener_port("VOICE_AGENT_RTC_UDP_PORT", 7882, environment)
    media_interface, media_ip = _local_rtc_media_path()
    config = {
        "port": signal_port,
        "bind_addresses": ["127.0.0.1"],
        "rtc": {
            "tcp_port": 0,
            "udp_port": rtc_udp_port,
            "use_external_ip": False,
            "node_ip": media_ip,
            "interfaces": {"includes": [media_interface]},
            "ips": {"includes": [f"{media_ip}/32"]},
        },
        "room": {
            "auto_create": True,
            "max_participants": 2,
            "empty_timeout": 60,
            "departure_timeout": 15,
        },
        "logging": {"level": "info", "json": True},
    }
    return json.dumps(config, indent=2) + "\n"


def app_origin_allowed(origin: str | None, app_public_url: str) -> bool:
    return origin in {LOOPBACK_APP_ORIGIN, app_public_url}


def _process_stat(pid: str) -> tuple[str, str]:
    content = (Path("/proc") / pid / "stat").read_text(encoding="utf-8")
    closing = content.rfind(")")
    if closing < 0:
        raise ValueError("process stat is malformed")
    fields = content[closing + 2:].split()
    if len(fields) <= 19:
        raise ValueError("process stat is incomplete")
    return fields[0], fields[19]


def supervised_process_identity(pid: int) -> str:
    try:
        state, start_time = _process_stat(str(pid))
    except (OSError, ValueError) as error:
        raise Slice6ConfigurationError("supervised process identity is unavailable") from error
    identity = f"{pid}:{start_time}"
    if state == "Z" or not PROCESS_IDENTITY_PATTERN.fullmatch(identity):
        raise Slice6ConfigurationError("supervised process identity is invalid")
    return identity


def supervised_process_alive(identity: str) -> bool:
    if not PROCESS_IDENTITY_PATTERN.fullmatch(identity):
        return False
    pid, expected_start_time = identity.split(":", 1)
    try:
        state, start_time = _process_stat(pid)
    except (OSError, ValueError):
        return False
    return state != "Z" and start_time == expected_start_time


def _required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value or value != value.strip():
        raise Slice6ConfigurationError(f"required server configuration is missing or invalid: {name}")
    return value


def _url(value: str, *, name: str, schemes: set[str], loopback: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise Slice6ConfigurationError(f"invalid URL in {name}") from error
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise Slice6ConfigurationError(f"invalid URL in {name}")
    if loopback:
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = parsed.hostname == "localhost"
        if not is_loopback:
            raise Slice6ConfigurationError(f"{name} must remain loopback-only")
    return value.removesuffix("/")


@dataclass(frozen=True)
class Slice6Settings:
    livekit_api_key: str
    livekit_api_secret: str
    livekit_internal_url: str
    livekit_public_url: str
    app_public_url: str
    web_dist: Path
    room_token_ttl_seconds: int = 300
    browser_join_timeout_seconds: int = 30
    max_sessions: int = 1
    diagnostic_capture_root: Path | None = None
    diagnostic_capture_ttl_seconds: int = 15 * 60
    build_id: str = "development"
    release_id: str = "development"
    supervised_livekit_process: str | None = None
    supervised_lfm_process: str | None = None

    @classmethod
    def from_environment(
        cls,
        environment: dict[str, str] | None = None,
        *,
        project_root: Path | None = None,
    ) -> "Slice6Settings":
        values = dict(os.environ if environment is None else environment)
        api_key = _required(values, "LIVEKIT_API_KEY")
        api_secret = _required(values, "LIVEKIT_API_SECRET")
        if len(api_key) > 128 or len(api_secret) < 16 or len(api_secret) > 256:
            raise Slice6ConfigurationError("LiveKit signing material is outside local bounds")
        internal_url = _url(
            _required(values, "LIVEKIT_INTERNAL_URL"),
            name="LIVEKIT_INTERNAL_URL",
            schemes={"ws"},
            loopback=True,
        )
        internal_endpoint = urlsplit(internal_url)
        livekit_port = listener_port("VOICE_AGENT_LIVEKIT_PORT", 7880, values)
        gateway_port = listener_port("VOICE_AGENT_GATEWAY_PORT", 8000, values)
        # Validate the remaining complete-stack listeners even though settings
        # do not expose them directly.
        listener_port("VOICE_AGENT_LLM_PORT", 18080, values)
        listener_port("VOICE_AGENT_RTC_UDP_PORT", 7882, values)
        if internal_endpoint.hostname != "127.0.0.1" or internal_endpoint.port != livekit_port:
            raise Slice6ConfigurationError(
                "LIVEKIT_INTERNAL_URL must match the explicit IPv4 loopback listener"
            )
        public_url = _url(
            values.get("LIVEKIT_PUBLIC_URL", f"ws://127.0.0.1:{livekit_port}"),
            name="LIVEKIT_PUBLIC_URL",
            schemes={"ws", "wss"},
        )
        app_public_url = _url(
            values.get("SLICE6_APP_PUBLIC_URL", f"http://127.0.0.1:{gateway_port}"),
            name="SLICE6_APP_PUBLIC_URL",
            schemes={"http", "https"},
        )
        for name, value, secure_scheme in (
            ("LIVEKIT_PUBLIC_URL", public_url, "wss"),
            ("SLICE6_APP_PUBLIC_URL", app_public_url, "https"),
        ):
            parsed = urlsplit(value)
            try:
                loopback = ipaddress.ip_address(str(parsed.hostname)).is_loopback
            except ValueError:
                loopback = parsed.hostname == "localhost"
            if not loopback and parsed.scheme != secure_scheme:
                raise Slice6ConfigurationError(
                    f"non-loopback {name} must use {secure_scheme}"
                )
        if any(name.startswith("LITELLM_") for name in values):
            raise Slice6ConfigurationError(
                "LiteLLM configuration is forbidden in the local-LFM Slice 6 runtime"
            )
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        web_dist = Path(values.get("SLICE6_WEB_DIST", str(root / "web" / "dist"))).resolve()
        capture_enabled = values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE", "0")
        capture_root_value = values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT")
        if capture_enabled not in {"0", "1"}:
            raise Slice6ConfigurationError(
                "VOICE_AGENT_DIAGNOSTIC_CAPTURE must be exactly 0 or 1"
            )
        if capture_enabled == "0" and capture_root_value is not None:
            raise Slice6ConfigurationError(
                "diagnostic capture root requires explicit VOICE_AGENT_DIAGNOSTIC_CAPTURE=1"
            )
        diagnostic_capture_root: Path | None = None
        diagnostic_capture_ttl_seconds = 15 * 60
        if capture_enabled == "1":
            if capture_root_value is None or not capture_root_value.strip():
                raise Slice6ConfigurationError(
                    "explicit diagnostic capture requires an outside-Git root"
                )
            diagnostic_capture_root = Path(capture_root_value).expanduser().resolve()
            if diagnostic_capture_root.is_relative_to(root):
                raise Slice6ConfigurationError(
                    "diagnostic capture root must remain outside the project worktree"
                )
            runtime_root_value = values.get("XDG_RUNTIME_DIR")
            if runtime_root_value is None or not runtime_root_value.strip():
                raise Slice6ConfigurationError(
                    "diagnostic capture requires the lifetime-scoped private runtime tmpfs"
                )
            try:
                runtime_root = require_lifetime_runtime_root(Path(runtime_root_value))
            except ValueError as error:
                raise Slice6ConfigurationError(
                    "diagnostic capture requires the lifetime-scoped private runtime tmpfs"
                ) from error
            if not diagnostic_capture_root.is_relative_to(runtime_root):
                raise Slice6ConfigurationError(
                    "diagnostic capture root must remain in the lifetime-scoped private runtime tmpfs"
                )
            ttl_value = values.get("VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS", "900")
            try:
                diagnostic_capture_ttl_seconds = int(ttl_value)
            except ValueError as error:
                raise Slice6ConfigurationError("invalid diagnostic capture TTL") from error
            if not 60 <= diagnostic_capture_ttl_seconds <= 3_600:
                raise Slice6ConfigurationError("diagnostic capture TTL is outside 60..3600 seconds")
        build_id = values.get("VOICE_AGENT_BUILD_ID", "development")
        release_id = values.get("VOICE_AGENT_RELEASE_ID", "development")
        if not BUILD_ID_PATTERN.fullmatch(build_id) or not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise Slice6ConfigurationError("operational build/release identity is invalid")
        livekit_process = values.get("VOICE_AGENT_SUPERVISED_LIVEKIT_PROCESS")
        lfm_process = values.get("VOICE_AGENT_SUPERVISED_LFM_PROCESS")
        if any(
            value is not None and not PROCESS_IDENTITY_PATTERN.fullmatch(value)
            for value in (livekit_process, lfm_process)
        ) or (livekit_process is None) != (lfm_process is None):
            raise Slice6ConfigurationError("supervised process identity is invalid")
        return cls(
            livekit_api_key=api_key,
            livekit_api_secret=api_secret,
            livekit_internal_url=internal_url,
            livekit_public_url=public_url,
            app_public_url=app_public_url,
            web_dist=web_dist,
            diagnostic_capture_root=diagnostic_capture_root,
            diagnostic_capture_ttl_seconds=diagnostic_capture_ttl_seconds,
            build_id=build_id,
            release_id=release_id,
            supervised_livekit_process=livekit_process,
            supervised_lfm_process=lfm_process,
        )

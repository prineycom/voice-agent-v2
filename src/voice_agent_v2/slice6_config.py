"""Fail-closed server-only configuration for the Slice 6 development app."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
LOOPBACK_APP_ORIGIN = "http://127.0.0.1:8000"


class Slice6ConfigurationError(ValueError):
    pass


def livekit_server_config(node_ip: str) -> str:
    address = ipaddress.ip_address(node_ip)
    if address not in TAILSCALE_NETWORK:
        raise Slice6ConfigurationError("SLICE6_LIVEKIT_NODE_IP must be this host's Tailscale IPv4 address")
    config = {
        "port": 7880,
        "bind_addresses": ["127.0.0.1"],
        "rtc": {
            "tcp_port": 0,
            "udp_port": 7882,
            "use_external_ip": False,
            "node_ip": str(address),
            "interfaces": {"includes": ["tailscale0"]},
            "ips": {"includes": [f"{address}/32"]},
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
            schemes={"ws", "wss"},
            loopback=True,
        )
        public_url = _url(
            _required(values, "LIVEKIT_PUBLIC_URL"),
            name="LIVEKIT_PUBLIC_URL",
            schemes={"wss"},
        )
        app_public_url = _url(
            _required(values, "SLICE6_APP_PUBLIC_URL"),
            name="SLICE6_APP_PUBLIC_URL",
            schemes={"https"},
        )
        if urlsplit(public_url).hostname != urlsplit(app_public_url).hostname:
            raise Slice6ConfigurationError(
                "application and LiveKit public URLs must use the same tailnet host"
            )
        if any(name.startswith("LITELLM_") for name in values):
            raise Slice6ConfigurationError(
                "LiteLLM configuration is forbidden in the local-LFM Slice 6 runtime"
            )
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        web_dist = Path(values.get("SLICE6_WEB_DIST", str(root / "web" / "dist"))).resolve()
        return cls(
            livekit_api_key=api_key,
            livekit_api_secret=api_secret,
            livekit_internal_url=internal_url,
            livekit_public_url=public_url,
            app_public_url=app_public_url,
            web_dist=web_dist,
        )

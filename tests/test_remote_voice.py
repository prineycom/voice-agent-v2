from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_agent_v2.slice6_config import (
    Slice6ConfigurationError,
    Slice6Settings,
    app_origin_allowed,
    livekit_server_config,
)
from voice_agent_v2.slice6_gateway import _admission_identity, content_security_policy
from voice_agent_v2.operations import validate_server_configuration
from voice_agent_v2.stand_dev import (
    CommandResult,
    REMOTE_APP_HTTPS_PORT,
    REMOTE_SIGNAL_HTTPS_PORT,
    StandError,
    config_path,
    configure_remote_voice,
    initialize,
    parse_private_config,
    remote_voice_status,
)


HOSTNAME = "voice-host.example-tailnet.ts.net"
TAILSCALE_IP = "100.78.238.32"


class RemoteCommand:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.serve: dict[str, object] = {"TCP": {}, "Web": {}}
        self.active = False
        self.firewall_rule = {False: True, True: True}

    def run(self, arguments, *, cwd: Path | None = None) -> CommandResult:
        del cwd
        command = tuple(arguments)
        self.calls.append(command)
        if command == ("tailscale", "status", "--json"):
            return CommandResult(0, json.dumps({
                "Self": {
                    "DNSName": HOSTNAME + ".",
                    "TailscaleIPs": [TAILSCALE_IP, "fd7a:115c:a1e0::1"],
                    "Online": True,
                }
            }))
        if command == ("ip", "-json", "-4", "address", "show", "dev", "tailscale0"):
            return CommandResult(0, json.dumps([{
                "ifname": "tailscale0",
                "addr_info": [{"family": "inet", "local": TAILSCALE_IP}],
            }]))
        if command == ("tailscale", "serve", "status", "--json"):
            return CommandResult(0, json.dumps(self.serve))
        if command[:3] == ("tailscale", "serve", "--bg"):
            port = command[3].split("=", 1)[1]
            target = command[4]
            self.serve.setdefault("TCP", {})[port] = {"HTTPS": True}
            self.serve.setdefault("Web", {})[f"{HOSTNAME}:{port}"] = {
                "Handlers": {"/": {"Proxy": target}}
            }
            return CommandResult(0)
        if command[:3] == ("sudo", "-n", "firewall-cmd"):
            permanent = "--permanent" in command
            if "--query-rich-rule" in command:
                return CommandResult(0 if self.firewall_rule[permanent] else 1)
            if "--remove-rich-rule" in command:
                self.firewall_rule[permanent] = False
                return CommandResult(0)
            return CommandResult(0, "public\n  interfaces: wlan0\n")
        if command[:3] == ("systemctl", "--user", "is-active"):
            return CommandResult(0, "active\n") if self.active else CommandResult(3, "inactive\n")
        if command[:3] == ("systemctl", "--user", "restart"):
            return CommandResult(0)
        if command[:2] == ("curl", "--fail"):
            url = command[-1]
            if url.endswith("/api/status"):
                return CommandResult(0, json.dumps({
                    "accepting": True,
                    "health": {"overall_readiness": "ready"},
                    "remote_voice_configuration": "configured",
                }))
            return CommandResult(0, "OK")
        return CommandResult(1, stderr="unexpected command")


def write_udp_fixture(root: Path, address: str = TAILSCALE_IP, port: int = 7882) -> None:
    net = root / "net"
    net.mkdir(parents=True)
    encoded = bytes(reversed(__import__("ipaddress").ip_address(address).packed)).hex().upper()
    header = "slot local_address rem_address st tx_queue tr tm->when retrnsmt uid timeout inode\n"
    (net / "udp").write_text(
        header + f"0: {encoded}:{port:04X} 00000000:0000 07 0 0 0 1000 0 12345\n",
        encoding="ascii",
    )
    (net / "udp6").write_text(header, encoding="ascii")


class RemoteVoiceTopologyTests(unittest.TestCase):
    def initialize_state(self, root: Path) -> Path:
        state = root / "state"
        initialize(
            state_root=state,
            user_unit_directory=root / "units",
            stand_executable=Path("/test/stand"),
        )
        return state

    def test_apply_backs_up_system_state_and_renders_two_exact_serve_listeners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.initialize_state(root)
            command = RemoteCommand()
            topology, backup, restarted = configure_remote_voice(
                state_root=state, instance="dev", command=command,
            )

            self.assertFalse(restarted)
            self.assertEqual(topology.app_public_url, f"https://{HOSTNAME}:8443")
            self.assertEqual(topology.livekit_public_url, f"wss://{HOSTNAME}:7443")
            self.assertEqual({path.name for path in backup.iterdir()}, {
                "serve-status.json", "firewall-runtime.txt",
                "firewall-permanent.txt", "topology.json",
            })
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in backup.iterdir()))
            values = parse_private_config(config_path(state, "dev"))
            self.assertEqual(values["LIVEKIT_INTERNAL_URL"], "ws://127.0.0.1:7880")
            self.assertEqual(values["LIVEKIT_PUBLIC_URL"], f"wss://{HOSTNAME}:7443")
            self.assertEqual(values["SLICE6_APP_PUBLIC_URL"], f"https://{HOSTNAME}:8443")
            self.assertEqual(values["VOICE_AGENT_RTC_INTERFACE"], "tailscale0")
            self.assertEqual(values["VOICE_AGENT_RTC_IP"], TAILSCALE_IP)
            self.assertIn(
                ("tailscale", "serve", "--bg", "--https=8443", "http://127.0.0.1:8000"),
                command.calls,
            )
            self.assertIn(
                ("tailscale", "serve", "--bg", "--https=7443", "http://127.0.0.1:7880"),
                command.calls,
            )
            self.assertFalse(any(call[:2] == ("tailscale", "funnel") for call in command.calls))
            self.assertFalse(any("0.0.0.0" in argument for call in command.calls for argument in call))
            self.assertFalse(command.firewall_rule[False])
            self.assertFalse(command.firewall_rule[True])
            self.assertEqual(
                sum("--remove-rich-rule" in call for call in command.calls), 2,
            )

    def test_remote_readiness_requires_app_wss_and_exact_tailnet_udp_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.initialize_state(root)
            command = RemoteCommand()
            configure_remote_voice(state_root=state, instance="dev", command=command)
            proc = root / "proc"
            write_udp_fixture(proc)

            ready = remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )
            self.assertEqual(ready, {
                "state": "ready", "url": f"https://{HOSTNAME}:8443",
            })

            signal = command.serve["Web"].pop(f"{HOSTNAME}:{REMOTE_SIGNAL_HTTPS_PORT}")
            self.assertEqual(
                remote_voice_status(
                    state_root=state, instance="dev", command=command, proc_root=proc,
                )["state"],
                "not-ready",
            )
            command.serve["Web"][f"{HOSTNAME}:{REMOTE_SIGNAL_HTTPS_PORT}"] = signal
            command.serve["AllowFunnel"] = True
            self.assertEqual(
                remote_voice_status(
                    state_root=state, instance="dev", command=command, proc_root=proc,
                )["state"],
                "not-ready",
            )
            command.serve.pop("AllowFunnel")
            write_udp_fixture(root / "wrong-proc", address="192.0.2.10")
            self.assertEqual(
                remote_voice_status(
                    state_root=state, instance="dev", command=command,
                    proc_root=root / "wrong-proc",
                )["state"],
                "not-ready",
            )

    def test_apply_refuses_main_and_unrelated_existing_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            with self.assertRaisesRegex(StandError, "limited to the dev stand"):
                configure_remote_voice(state_root=state, instance="main", command=command)
            command.serve = {
                "TCP": {str(REMOTE_APP_HTTPS_PORT): {"HTTPS": True}},
                "Web": {
                    f"{HOSTNAME}:{REMOTE_APP_HTTPS_PORT}": {
                        "Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}
                    }
                },
            }
            with self.assertRaisesRegex(StandError, "unrelated Serve handler"):
                configure_remote_voice(state_root=state, instance="dev", command=command)


class RemoteApplicationContractTests(unittest.TestCase):
    def remote_environment(self) -> dict[str, str]:
        return {
            "LIVEKIT_API_KEY": "remote-test-key",
            "LIVEKIT_API_SECRET": "0123456789abcdef",
            "LIVEKIT_INTERNAL_URL": "ws://127.0.0.1:7880",
            "LIVEKIT_PUBLIC_URL": f"wss://{HOSTNAME}:7443",
            "SLICE6_APP_PUBLIC_URL": f"https://{HOSTNAME}:8443",
            "VOICE_AGENT_RTC_INTERFACE": "tailscale0",
            "VOICE_AGENT_RTC_IP": TAILSCALE_IP,
        }

    def test_exact_origin_csp_and_internal_public_separation(self) -> None:
        with patch(
            "voice_agent_v2.slice6_config._interface_ipv4", return_value=TAILSCALE_IP,
        ):
            settings = Slice6Settings.from_environment(self.remote_environment())
            server = json.loads(livekit_server_config(self.remote_environment()))
        self.assertTrue(settings.remote_voice_configured)
        self.assertEqual(settings.livekit_internal_url, "ws://127.0.0.1:7880")
        self.assertEqual(settings.livekit_public_url, f"wss://{HOSTNAME}:7443")
        self.assertEqual(server["bind_addresses"], ["127.0.0.1"])
        self.assertEqual(server["rtc"]["node_ip"], TAILSCALE_IP)
        self.assertEqual(server["rtc"]["interfaces"], {"includes": ["tailscale0"]})
        self.assertEqual(server["rtc"]["ips"], {"includes": [TAILSCALE_IP + "/32"]})
        self.assertTrue(app_origin_allowed(f"https://{HOSTNAME}:8443", settings.app_public_url))
        request = type("Request", (), {"headers": {
            "origin": f"https://{HOSTNAME}:8443",
            "x-voice-session-attempt": "9f31f340-40b5-4fb7-92b2-d2544ca29fa1",
        }})()
        self.assertEqual(
            _admission_identity(request, settings),
            "9f31f340-40b5-4fb7-92b2-d2544ca29fa1",
        )
        self.assertFalse(app_origin_allowed(f"https://{HOSTNAME}", settings.app_public_url))
        self.assertFalse(app_origin_allowed(f"http://{HOSTNAME}:8443", settings.app_public_url))
        policy = content_security_policy(settings.livekit_public_url)
        self.assertIn(f"connect-src 'self' wss://{HOSTNAME}:7443", policy)
        self.assertNotIn("ws://127.0.0.1:7880", policy)
        operational = validate_server_configuration(self.remote_environment())
        self.assertEqual(
            operational["public_values"]["VOICE_AGENT_RTC_INTERFACE"],
            "tailscale0",
        )
        self.assertNotIn("LIVEKIT_API_SECRET", operational["public_values"])

    def test_remote_configuration_refuses_app_only_wss_only_and_non_tailnet_media(self) -> None:
        cases = []
        app_only = self.remote_environment()
        app_only["LIVEKIT_PUBLIC_URL"] = "ws://127.0.0.1:7880"
        cases.append(app_only)
        wss_only = self.remote_environment()
        wss_only["SLICE6_APP_PUBLIC_URL"] = "http://127.0.0.1:8000"
        cases.append(wss_only)
        lan_media = self.remote_environment()
        lan_media["VOICE_AGENT_RTC_IP"] = "192.168.1.10"
        cases.append(lan_media)
        for environment in cases:
            with self.subTest(environment=environment), self.assertRaises(Slice6ConfigurationError):
                Slice6Settings.from_environment(environment)


if __name__ == "__main__":
    unittest.main()

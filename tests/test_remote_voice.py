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
    disable_remote_voice,
    initialize,
    parse_private_config,
    remote_voice_status,
)


HOSTNAME = "voice-host.example-tailnet.ts.net"
TAILSCALE_IP = "100.64.0.10"
APPROVED_PEER = "100.64.0.20/32"
OTHER_PEER = "100.64.0.21/32"


def exact_rule(peer: str = APPROVED_PEER, port: int = 7882) -> str:
    return (
        f'rule family="ipv4" source address="{peer}" '
        f'port port="{port}" protocol="udp" accept'
    )


class RemoteCommand:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.serve: dict[str, object] = {"TCP": {}, "Web": {}}
        self.active = False
        self.default_zone = "public"
        self.interface_zone: str | None = None
        self.rules: dict[bool, set[tuple[str, str]]] = {False: set(), True: set()}
        self.fail_add: bool | None = None
        self.target = "default"
        self.open_ports: dict[bool, set[tuple[str, str]]] = {False: set(), True: set()}
        self.unrelated_rule = (
            'rule family="ipv4" source address="192.0.2.0/24" '
            'port port="22" protocol="tcp" accept'
        )
        self.rules[False].add(("public", self.unrelated_rule))
        self.rules[True].add(("public", self.unrelated_rule))

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
            if command[-1] == "--get-zone-of-interface=tailscale0":
                return CommandResult(
                    0 if self.interface_zone else 2,
                    (self.interface_zone or "no zone") + "\n",
                )
            if command[-1] == "--get-default-zone":
                return CommandResult(0, self.default_zone + "\n")
            permanent = "--permanent" in command
            zone_argument = next((part for part in command if part.startswith("--zone=")), None)
            zone = zone_argument.split("=", 1)[1] if zone_argument else self.default_zone
            if "--query-rich-rule" in command:
                rule = command[command.index("--query-rich-rule") + 1]
                return CommandResult(0 if (zone, rule) in self.rules[permanent] else 1)
            if "--add-rich-rule" in command:
                if self.fail_add is permanent:
                    return CommandResult(1)
                rule = command[command.index("--add-rich-rule") + 1]
                self.rules[permanent].add((zone, rule))
                return CommandResult(0)
            if "--remove-rich-rule" in command:
                rule = command[command.index("--remove-rich-rule") + 1]
                self.rules[permanent].discard((zone, rule))
                return CommandResult(0)
            if "--list-rich-rules" in command:
                body = "".join(
                    rule + "\n" for observed_zone, rule in sorted(self.rules[permanent])
                    if observed_zone == zone
                )
                return CommandResult(0, body)
            if "--list-all" in command:
                ports = " ".join(
                    port for observed_zone, port in sorted(self.open_ports[permanent])
                    if observed_zone == zone
                )
                return CommandResult(
                    0,
                    f"{zone}\n  target: {self.target}\n  interfaces: wlan0\n  ports: {ports}\n",
                )
            return CommandResult(1, stderr="unexpected firewall command")
        if command == ("systemctl", "--user", "daemon-reload"):
            return CommandResult(0)
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


class SchemaAwareRemoteCommand(RemoteCommand):
    def __init__(self, *, unit: Path, config: Path) -> None:
        super().__init__()
        self.unit = unit
        self.config = config
        self.configuration_failed_interval = False

    def run(self, arguments, *, cwd: Path | None = None) -> CommandResult:
        command = tuple(arguments)
        if command[:3] == ("systemctl", "--user", "restart"):
            schema_is_new = "VOICE_AGENT_REMOTE_VOICE_PEER_IPV4_CIDR=" in self.config.read_text()
            launcher_is_durable = "/instances/%i/current/source/scripts/stand.py launcher %i" in self.unit.read_text()
            if schema_is_new and not launcher_is_durable:
                self.calls.append(command)
                self.active = False
                self.configuration_failed_interval = True
                return CommandResult(1)
        return super().run(arguments, cwd=cwd)


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

    def apply(self, state: Path, command: RemoteCommand, peer: str = APPROVED_PEER):
        return configure_remote_voice(
            state_root=state, instance="dev", command=command, approved_peer=peer,
        )

    def write_legacy_remote_schema(self, state: Path) -> bytes:
        path = config_path(state, "dev")
        values = parse_private_config(path)
        values.update({
            "LIVEKIT_PUBLIC_URL": f"wss://{HOSTNAME}:7443",
            "SLICE6_APP_PUBLIC_URL": f"https://{HOSTNAME}:8443",
            "VOICE_AGENT_RTC_INTERFACE": "tailscale0",
            "VOICE_AGENT_RTC_IP": TAILSCALE_IP,
        })
        body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        parse_private_config(path)
        return body.encode("utf-8")

    def test_old_installed_launcher_is_replaced_before_new_schema_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.initialize_state(root)
            self.write_legacy_remote_schema(state)
            unit = root / "units/voice-agent-v2@.service"
            unit.write_text("[Service]\nExecStart=/old/controller/stand launcher %i\n")
            command = SchemaAwareRemoteCommand(
                unit=unit, config=config_path(state, "dev"),
            )
            command.active = True

            configure_remote_voice(
                state_root=state, instance="dev", command=command,
                approved_peer=APPROVED_PEER, user_unit_directory=root / "units",
            )

            self.assertFalse(command.configuration_failed_interval)
            self.assertTrue(command.active)
            self.assertIn(
                "/instances/%i/current/source/scripts/stand.py launcher %i",
                unit.read_text(),
            )
            reload_call = ("systemctl", "--user", "daemon-reload")
            first_add = next(index for index, call in enumerate(command.calls) if "--add-rich-rule" in call)
            restart = command.calls.index((
                "systemctl", "--user", "restart", "voice-agent-v2@dev.service",
            ))
            self.assertLess(command.calls.index(reload_call), first_add)
            self.assertLess(first_add, restart)
            self.assertIn(
                "VOICE_AGENT_REMOTE_VOICE_PEER_IPV4_CIDR=",
                config_path(state, "dev").read_text(),
            )

    def test_launcher_upgrade_survives_firewall_failure_without_schema_or_service_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.initialize_state(root)
            config_before = self.write_legacy_remote_schema(state)
            unit = root / "units/voice-agent-v2@.service"
            unit.write_text("[Service]\nExecStart=/old/controller/stand launcher %i\n")
            command = SchemaAwareRemoteCommand(
                unit=unit, config=config_path(state, "dev"),
            )
            command.active = True
            command.fail_add = True

            with self.assertRaisesRegex(StandError, "could not be added"):
                configure_remote_voice(
                    state_root=state, instance="dev", command=command,
                    approved_peer=APPROVED_PEER, user_unit_directory=root / "units",
                )

            self.assertFalse(command.configuration_failed_interval)
            self.assertTrue(command.active)
            self.assertEqual(config_path(state, "dev").read_bytes(), config_before)
            self.assertIn(
                "/instances/%i/current/source/scripts/stand.py launcher %i",
                unit.read_text(),
            )
            for permanent in (False, True):
                self.assertNotIn(("public", exact_rule()), command.rules[permanent])
            self.assertFalse((state / "instances/dev/remote-voice/firewall-owner.json").exists())
            self.assertFalse(any(call[:3] == ("systemctl", "--user", "restart") for call in command.calls))

    def test_apply_owns_exact_bytes_in_effective_default_zone_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            topology, backup, restarted = self.apply(state, command)
            self.assertFalse(restarted)
            self.assertEqual(topology.app_public_url, f"https://{HOSTNAME}:8443")
            self.assertEqual({path.name for path in backup.iterdir()}, {
                "serve-status.json", "firewall-runtime.txt",
                "firewall-permanent.txt", "topology.json",
            })
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in backup.iterdir()))
            values = parse_private_config(config_path(state, "dev"))
            self.assertEqual(values["VOICE_AGENT_REMOTE_VOICE_PEER_IPV4_CIDR"], APPROVED_PEER)
            self.assertEqual(values["VOICE_AGENT_RTC_IP"], TAILSCALE_IP)
            owner_path = state / "instances/dev/remote-voice/firewall-owner.json"
            owner = json.loads(owner_path.read_text())
            self.assertEqual(owner["zone"], "public")
            self.assertEqual(owner["rule"], exact_rule())
            self.assertEqual(owner_path.stat().st_mode & 0o777, 0o600)
            for permanent in (False, True):
                self.assertIn(("public", exact_rule()), command.rules[permanent])
                self.assertIn(("public", command.unrelated_rule), command.rules[permanent])
            add_calls = [call for call in command.calls if "--add-rich-rule" in call]
            self.assertEqual(add_calls, [
                ("sudo", "-n", "firewall-cmd", "--zone=public", "--add-rich-rule", exact_rule()),
                ("sudo", "-n", "firewall-cmd", "--permanent", "--zone=public", "--add-rich-rule", exact_rule()),
            ])
            self.apply(state, command)
            self.assertEqual(
                len([call for call in command.calls if "--add-rich-rule" in call]), 2,
            )
            forbidden = ("--reload", "--add-port", "--add-interface", "--add-masquerade", "--add-forward-port")
            self.assertFalse(any(item in call for call in command.calls for item in forbidden))
            self.assertFalse(any("tcp" in item or "0.0.0.0" in item for call in add_calls for item in call))

    def test_apply_rolls_back_only_its_runtime_partial_when_permanent_add_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            command.fail_add = True
            with self.assertRaisesRegex(StandError, "could not be added"):
                self.apply(state, command)
            for permanent in (False, True):
                self.assertNotIn(("public", exact_rule()), command.rules[permanent])
                self.assertIn(("public", command.unrelated_rule), command.rules[permanent])
            self.assertFalse((state / "instances/dev/remote-voice/firewall-owner.json").exists())

    def test_apply_runtime_add_failure_leaves_permanent_and_unrelated_state_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            command.fail_add = False
            with self.assertRaisesRegex(StandError, "could not be added"):
                self.apply(state, command)
            for permanent in (False, True):
                self.assertNotIn(("public", exact_rule()), command.rules[permanent])
                self.assertIn(("public", command.unrelated_rule), command.rules[permanent])

    def test_peer_port_and_effective_zone_change_reconciles_only_owned_old_and_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            self.apply(state, command)
            path = config_path(state, "dev")
            path.write_text(path.read_text().replace(
                "VOICE_AGENT_RTC_UDP_PORT=7882", "VOICE_AGENT_RTC_UDP_PORT=7890",
            ))
            command.interface_zone = "work"
            self.apply(state, command, OTHER_PEER)
            for permanent in (False, True):
                self.assertNotIn(("public", exact_rule()), command.rules[permanent])
                self.assertIn(("work", exact_rule(OTHER_PEER, 7890)), command.rules[permanent])
                self.assertIn(("public", command.unrelated_rule), command.rules[permanent])

    def test_readiness_distinguishes_firewall_and_zone_drift_and_broad_lookalikes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.initialize_state(root)
            command = RemoteCommand()
            self.apply(state, command)
            proc = root / "proc"
            write_udp_fixture(proc)
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["transport"], "configured-admission")
            command.rules[False].remove(("public", exact_rule()))
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "firewall-runtime-absent")
            command.rules[False].add(("public", exact_rule()))
            command.rules[True].remove(("public", exact_rule()))
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "firewall-permanent-absent")
            command.rules[True].add(("public", exact_rule()))
            command.interface_zone = "work"
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "zone-drift")
            command.interface_zone = None
            broad = exact_rule("100.64.0.0/10")
            command.rules[False].add(("public", broad))
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "broader-firewall-lookalike")
            command.rules[False].remove(("public", broad))
            command.open_ports[True].add(("public", "7882/udp"))
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "broader-firewall-lookalike")
            command.open_ports[True].clear()
            path = config_path(state, "dev")
            path.write_text(path.read_text().replace(APPROVED_PEER, OTHER_PEER))
            self.assertEqual(remote_voice_status(
                state_root=state, instance="dev", command=command, proc_root=proc,
            )["reason"], "stale-owned-rule")

    def test_disable_and_repeated_cleanup_remove_only_exact_owned_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            self.apply(state, command)
            self.assertTrue(disable_remote_voice(
                state_root=state, instance="dev", command=command,
            ))
            self.assertFalse(disable_remote_voice(
                state_root=state, instance="dev", command=command,
            ))
            for permanent in (False, True):
                self.assertNotIn(("public", exact_rule()), command.rules[permanent])
                self.assertIn(("public", command.unrelated_rule), command.rules[permanent])
            values = parse_private_config(config_path(state, "dev"))
            self.assertNotIn("VOICE_AGENT_REMOTE_VOICE_PEER_IPV4_CIDR", values)
            self.assertFalse((state / "instances/dev/remote-voice/firewall-owner.json").exists())

    def test_apply_requires_explicit_exact_peer_and_refuses_main_or_unrelated_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.initialize_state(Path(temporary))
            command = RemoteCommand()
            with self.assertRaisesRegex(StandError, "explicitly approved"):
                configure_remote_voice(state_root=state, instance="dev", command=command)
            with self.assertRaisesRegex(StandError, "explicitly approved"):
                self.apply(state, command, "100.64.0.0/24")
            with self.assertRaisesRegex(StandError, "limited to the dev stand"):
                configure_remote_voice(
                    state_root=state, instance="main", command=command,
                    approved_peer=APPROVED_PEER,
                )
            command.serve = {
                "TCP": {str(REMOTE_APP_HTTPS_PORT): {"HTTPS": True}},
                "Web": {f"{HOSTNAME}:{REMOTE_APP_HTTPS_PORT}": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}
                }},
            }
            with self.assertRaisesRegex(StandError, "unrelated Serve handler"):
                self.apply(state, command)


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

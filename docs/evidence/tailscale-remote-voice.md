# Tailscale remote voice topology evidence

**Decision:** [ADR-0016](../adr/0016-tailscale-remote-voice-topology.md)  
**Host proof date:** pending deployment  
**Scope:** versioned `dev` stand only

## Implemented topology

| Boundary | Authoritative result |
| --- | --- |
| Browser application | Current self MagicDNS hostname, HTTPS `8443`, Tailscale Serve root proxy to dev gateway loopback |
| Browser LiveKit signaling | Same hostname, WSS `7443`, separate Tailscale Serve root proxy to dev LiveKit loopback; no path-prefix rewrite |
| Internal signaling/control | `ws://127.0.0.1:<dev-livekit-port>` for gateway/Python agent |
| Media/ICE | One current private-config-owned Tailscale IPv4 on exact `tailscale0` and exact dev UDP port |
| Refused paths | Funnel, public/LAN/wildcard service bind, wildcard media, ICE/TCP, TURN/cloud fallback, alternate candidate, blind retry, 409 suppression |
| Remote readiness | `./stand status dev` requires current identity, both Serve handlers, HTTPS app health, LiveKit HTTPS/WSS listener, absence of the obsolete LAN-zone media rule, and exact UDP bind together |

`./stand remote-voice apply dev` is reproducible from live self identity. It saves mode-`0600` structured Serve and runtime/permanent firewalld snapshots beneath the private dev instance before mutation. It removes only the obsolete exact dev-port LAN-zone tailnet-source rule if present, refuses unrelated Serve handlers, applies only the two current supported Serve listeners, writes the complete HTTPS/WSS/RTC group atomically to the mode-`0600` dev configuration, and restarts dev only if already active. No moving IP is tracked here.

## Deterministic evidence

`tests.test_remote_voice` covers:

- exact HTTPS Origin admission and refusal of scheme/port variants;
- CSP `connect-src` containing the exact WSS URL without internal loopback signaling;
- issued capability preserving the exact public WSS URL;
- app/WSS public URLs remaining separate from the loopback internal agent URL;
- exact LiveKit `node_ip`, interface include, IP allowlist and UDP port;
- current self identity validation and two independent Serve command renderings;
- no Funnel, wildcard, or `0.0.0.0` command path;
- pre-mutation Serve/firewall backups and exact obsolete-rule removal;
- refusal to replace unrelated handlers or configure `main`;
- app-only, WSS-only, non-tailnet media, Funnel-enabled, missing-handler, wrong-candidate, and stale/incomplete readiness cases.

The canonical command and full saved output are recorded in the matching Do report.

## Host-side proof

Pending after the task commit is deployed to `dev`:

- exact selected release, running/enabled/internal-ready/remote-ready stand;
- current structured Serve handlers and TLS listeners;
- exact Origin `POST /api/session` and returned WSS capability;
- real WSS upgrade/signaling through Serve;
- exact Tailscale UDP bind and selected/configured ICE candidate;
- healthy persistent AgentEnvironment;
- explicit session cleanup and no public/LAN listener.

## Remaining second-device Acceptance

Host evidence cannot claim these observations. Pasha must use the reported exact HTTPS URL from another device in the same tailnet and observe:

1. CONNECT completes;
2. microphone permission and publication succeed;
3. the remote participant/media path appears;
4. agent response audio is audible;
5. DISCONNECT releases the one session cleanly.

No remote-peer path, physical microphone, audio, audibility, or playback result is claimed until that test occurs.

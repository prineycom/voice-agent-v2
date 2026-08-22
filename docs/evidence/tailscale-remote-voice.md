# Tailscale remote voice topology evidence

**Decision:** [ADR-0016](../adr/0016-tailscale-remote-voice-topology.md)
**Host proof date:** 2026-08-23
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

The committed task release `2671b18ad80af8debe94ea4fca0e8a2cc9df7df1` was deployed to `dev`. The one canonical prepared-image refresh selected the already-merged current native context without deleting the retained prior image. The existing `./stand init` bootstrap then refreshed the repository launcher contract without changing the selected-release pointer; no unit was hand-edited. The single final `./stand status dev` proof reported running, enabled, internally ready, remote-voice ready, and the AgentEnvironment running.

Structured Serve status contained the preserved unrelated HTTPS `:443` handler plus exactly these task handlers on the current self MagicDNS name:

- HTTPS `:8443` root → `http://127.0.0.1:8000`;
- HTTPS/WSS `:7443` root → `http://127.0.0.1:7880`.

There was no Funnel record. Listener inspection showed gateway, LiveKit signaling and llama.cpp only on IPv4 loopback; Serve only on the Tailscale addresses; and LiveKit UDP `7882` only on the current Tailscale IPv4. The public firewalld zone retained only its existing bounded SSH rules and no LAN/tailnet media-port rule. No application, signaling, inference, generic-service or media listener appeared on a LAN or wildcard address.

`GET https://priney-arch.darter-smoot.ts.net:8443/` returned HTTP/2 `200` with CSP `connect-src 'self' wss://priney-arch.darter-smoot.ts.net:7443`. An exact-Origin `POST /api/session` with a fresh UUIDv4 returned a non-empty private token and the exact public WSS URL; only non-secret capability fields were displayed. The official Python LiveKit RTC client used that token privately, connected through the public WSS URL, observed the already joined agent participant, and disconnected. LiveKit's current start/ICE logs matched `node_ip` and the selected UDP host candidate on the current `tailscale0` address and port `7882`. The matching exact-Origin `DELETE` returned `204`; public status then reported accepting/available, remote configuration loaded, and AgentEnvironment running.

Chrome DevTools AXI could not launch because this host has no supported Chrome binary, so this host proof makes no browser, microphone, or playback claim. The real official-client WSS/ICE proof is not relabelled as the intended second-device Acceptance.

## Remaining second-device Acceptance

Host evidence cannot claim these observations. Pasha must use the reported exact HTTPS URL from another device in the same tailnet and observe:

1. CONNECT completes;
2. microphone permission and publication succeed;
3. the remote participant/media path appears;
4. agent response audio is audible;
5. DISCONNECT releases the one session cleanly.

No remote-peer path, physical microphone, audio, audibility, or playback result is claimed until that test occurs.

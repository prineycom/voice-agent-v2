# Tailscale remote voice topology evidence

**Decision:** [ADR-0016](../adr/0016-tailscale-remote-voice-topology.md)
**Correction proof date:** 2026-08-23
**Scope:** versioned `dev` stand and one explicitly approved iPhone peer

## Corrected topology

| Boundary | Authoritative result |
| --- | --- |
| Browser application | Current self MagicDNS hostname, HTTPS `8443`, Tailscale Serve root proxy to dev gateway loopback |
| Browser LiveKit signaling | Same hostname, WSS `7443`, separate root proxy to dev LiveKit loopback; no path-prefix rewrite |
| Internal signaling/control | Loopback-only gateway, agent, LiveKit signaling and inference paths |
| Media/ICE | One current private-config host Tailscale IPv4 on exact `tailscale0` and exact configured UDP mux port |
| Media admission | One explicitly approved private peer IPv4 `/32`, UDP only, exact mux port, effective firewalld zone, runtime and permanent |
| Refused paths | Peer auto-selection, whole-tailnet/interface/LAN/wildcard admission, Funnel, wildcard media, ICE/TCP, TURN, forwarding, masquerade, alternate candidate, retry/fallback |
| Remote readiness | Current identity + Serve/app/WSS + exact listener + exact private owner + effective zone + runtime/permanent exact-rule presence; broader lookalikes are not ready |

Exact host/peer addresses remain installation-private. The tracked implementation contains only reserved test values.

## Why exact bind was insufficient

The original host proof established the exact `tailscale0` UDP listener but incorrectly inferred that it needed no firewalld admission. A later full iPhone ICE trace disproved that premise. Nominated tailnet STUN reached the exact host interface and port continuously, while firewalld's later default-zone hook returned administrative-prohibited responses before LiveKit received any request. Consequently ICE timed out before the frontend reached microphone capture, even while the former `stand status` said remote voice was ready.

A synchronized, separately authorized A/B changed one variable: one runtime-only rich rule admitting the current iPhone's exact tailnet IPv4 `/32` to UDP/7882. With the same release, listener, Serve topology, route, client order, and transports:

- the exact UDP pair selected and the browser became active;
- LiveKit recorded browser microphone publication;
- the privacy-safe trace recorded `session.ready`, real microphone endpoints, response PCM delivery, and normal cleanup;
- Pasha physically observed the permission prompt, READY UI, and audible response;
- removal of the temporary rule restored byte-equivalent runtime/permanent baseline.

This is positive causal and physical evidence for the narrow correction. It does not prove another peer/network, IPv6, reboot persistence, future physical audibility, or a need for ICE/TCP, TURN, permission reordering, or wider firewall authority.

## Repository-owned durable correction

First `./stand remote-voice apply dev --peer <approved-tailnet-ipv4>/32` requires explicit approval; later apply reuses only that private value. The existing stand owner:

1. discovers current self identity and resolves an assigned `tailscale0` zone or, when unassigned, the effective default zone;
2. stores mode-`0600` Serve/firewall backups and an exact private ownership document;
3. applies only the canonical IPv4 source `/32`, configured-port, UDP, accept rich rule through `sudo -n firewall-cmd`;
4. proves immediate runtime and permanent presence without `--reload`;
5. transactionally reconciles only recorded old/new peer, port, or zone bytes and rolls back only partial owned mutation;
6. preserves unrelated and broader lookalike rules rather than deleting them;
7. makes stale ownership, zone drift, absent runtime, absent permanent, and broader lookalikes distinct not-ready reasons;
8. removes and proves absence of exactly the owned two surfaces on explicit `remote-voice disable` and installation deletion/uninstall cleanup.

Ready means **configured transport admission**. It does not mean that a device is online or that audio was heard now.

## Deterministic evidence

`tests.test_remote_voice` covers:

- strict exact peer `/32` admission and refusal to infer a peer;
- exact Origin/CSP/public WSS/internal loopback separation and LiveKit tailnet bind;
- unassigned-interface default-zone and assigned-zone resolution;
- exact rule and `sudo -n firewall-cmd` command bytes, with no reload/widening command;
- runtime/permanent idempotence and each partial-add failure boundary;
- preservation of unrelated rules;
- peer/port/zone update removing only exact old ownership;
- runtime absence, permanent absence, zone drift, stale owner, broad lookalike, Serve and listener drift;
- exact disable cleanup and repeated no-op cleanup;
- no Funnel, wildcard bind, TCP, TURN, masquerade, forwarding, interface trust, or broad-source mutation.

The canonical PR command remains the sole deterministic gate. Live host mutation and the dated physical A/B remain separate evidence tiers.

## Post-merge Acceptance

After the exact green commit is installed and host readiness proves the service, exact listener, AgentEnvironment, owned runtime/permanent rule, unrelated-rule preservation, and healthy journal, the stand remains running. Pasha then repeats physical iPhone CONNECT, microphone permission/publication, READY, audible response, and DISCONNECT. That future observation is not preclaimed by configured host readiness.

# ADR-0016: Support one exact Tailscale remote voice topology

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Pasha

## Context

The prior same-PC boundary allowed an operator to proxy the application but did not make that proxy a voice topology. A real remote browser needs four consistent paths: the exact HTTPS application Origin, browser-reachable TLS/SNI-valid LiveKit signaling, a WebSocket-capable proxy to the unchanged LiveKit endpoints, and one reachable ICE/media candidate. The diagnosed dev stand proxied only application HTTP, rejected its HTTPS Origin, issued loopback signaling, and advertised a non-tailnet media candidate. An app GET or `/api/status` result therefore could not truthfully mean remote voice readiness.

Current Tailscale `1.102.x` Serve supports independent HTTPS listeners on one MagicDNS hostname. Separate listener ports avoid rewriting LiveKit paths. LiveKit `1.13.5` can bind and advertise the node's exact `tailscale0` IPv4 address, so this host does not require a wildcard media bind, TURN, ICE/TCP, or a LAN candidate. Physical iPhone tracing later proved that exact bind alone was insufficient: packets reached `tailscale0`, but firewalld's later default-zone hook rejected UDP media before the LiveKit socket. A synchronized one-variable runtime A/B admitting only that iPhone's exact tailnet IPv4 `/32` to UDP/7882 removed the reject; ICE selected exact UDP, microphone publication and `session.ready` occurred, and Pasha observed the prompt, READY UI, and audible response.

## Decision

The personal installation supports one tailnet-only remote voice topology for the versioned dev stand:

- application HTTPS uses the current node MagicDNS hostname on port `8443` and proxies only to the dev gateway loopback listener;
- LiveKit WSS uses the same hostname on port `7443` and proxies the complete root namespace to the dev LiveKit loopback listener, preserving `/rtc` and every signaling endpoint;
- the issued room capability contains that exact `wss://` URL, and CSP `connect-src` contains only the exact configured public signaling URL plus same-origin;
- same-origin admission accepts only the exact configured HTTPS application Origin (while the direct loopback development origin remains the existing local exception);
- the Python agent and gateway retain `ws://127.0.0.1` internal signaling/control, and gateway/LiveKit signaling/LLM listeners remain IPv4 loopback;
- LiveKit binds and advertises only the current Tailscale IPv4 address on exact interface `tailscale0` and the instance's one UDP media port; ICE/TCP, TURN, alternate candidates, Funnel, LAN/wildcard service listeners, and fallback remain disabled;
- the moving host Tailscale IPv4, current MagicDNS identity, and one explicitly approved remote peer IPv4 `/32` live only in instance-private mode-`0600` configuration/owned state, not in tracked durable configuration; the peer is never inferred from recent activity;
- firewalld admits only that peer `/32` to the configured UDP mux port in the effective IPv4 zone, in both runtime and permanent state.

`./stand remote-voice apply dev --peer <approved-tailnet-ipv4>/32` is the narrow existing-stand operation. The first apply requires that explicit private peer; subsequent apply may reuse it but never discovers another peer. It discovers only current self identity and the effective firewalld zone, backs up Serve and the effective zone's runtime/permanent state, refuses unrelated handlers, applies the two supported `tailscale serve --bg --https=<port> <loopback-target>` listeners, and uses only `sudo -n firewall-cmd` to transactionally reconcile the exact owned old/new peer `/32` UDP rule in runtime and permanent state. It proves both surfaces, records exact ownership privately, preserves unrelated rules, rolls back only partial owned mutation, updates dev configuration atomically, and restarts dev only if already running. It never reloads firewalld, uses Funnel/raw nftables/iptables/interface trust, edits routes, or admits TCP, TURN, LAN, wildcard, forwarding, masquerade, inference/Docker/credentials/admin services. Explicit remote-voice disable and installation deletion/uninstall cleanup remove and prove absence of exactly the owned two rule surfaces.

Application status reports only whether public HTTPS/WSS plus Tailscale-RTC configuration is loaded. `./stand status dev` owns configured transport-admission readiness: it additionally requires ownership bytes, the current effective zone, both exact runtime/permanent rules, no stale owner or broader media lookalike, and the exact UDP bind. It distinguishes actionable runtime, permanent, zone, ownership, and lookalike drift. Internal voice readiness remains separately visible. It does not infer current peer reachability or physical audibility. The positive A/B is dated physical acceptance for one approved iPhone/path, not a guarantee for another peer, network, reboot, or future attempt.

The existing one-session/idempotent-attempt contract is unchanged. This topology adds no retry, 409 suppression, session selector, or second controller.

## Consequences

### Positive

- One exact URL is usable from another tailnet device without exposing a generic LAN or public service.
- Browser-facing and internal URLs are deliberately different without moving local control traffic through the proxy.
- Separate HTTPS listeners preserve LiveKit endpoint paths and WebSocket upgrade behavior.
- Remote-ready cannot be inferred from an app-only proxy or local `/api/status` health.
- Clean setup is reproducible from the current authoritative Tailscale identity while moving host data remains private.

### Costs and limits

- Tailscale identity/Serve, exact dev media bind, active firewalld, and the private exact-peer admission owner are explicit supported installation dependencies for remote voice, though they remain outside the supervised application process tree.
- A changed Tailscale IPv4 identity requires rerunning the explicit apply operation; stale private identity fails startup/status rather than selecting LAN or another candidate.
- Host-side HTTPS, WSS, capability, bind, and configured-rule proof cannot reproduce or guarantee physical microphone/media/audio acceptance; the synchronized iPhone A/B remains separate dated evidence.
- Main remains unexposed until a separately authorized release/acceptance action; this ADR configures only dev.

## Alternatives considered

- **One path-prefixed HTTPS listener:** rejected because path rewriting can alter LiveKit endpoints and adds no value when independent HTTPS ports are supported.
- **Proxy only the app and leave signaling loopback:** rejected because the browser capability would be unreachable remotely.
- **Advertise the LAN address with a tailnet-source firewall rule:** rejected because the intended peer should not need or learn a LAN path.
- **Trust `tailscale0`, admit the whole tailnet, or use wildcard media:** rejected because each is broader than the physically proven one-peer UDP boundary; if exact binding or approval stops matching, readiness fails rather than widening automatically.
- **TURN, ICE/TCP, cloud fallback, or blind ICE retry:** rejected as alternate media paths and additional authority.
- **Funnel or a public certificate/proxy service:** rejected because the product is tailnet-only.

# ADR-0016: Support one exact Tailscale remote voice topology

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Pasha

## Context

The prior same-PC boundary allowed an operator to proxy the application but did not make that proxy a voice topology. A real remote browser needs four consistent paths: the exact HTTPS application Origin, browser-reachable TLS/SNI-valid LiveKit signaling, a WebSocket-capable proxy to the unchanged LiveKit endpoints, and one reachable ICE/media candidate. The diagnosed dev stand proxied only application HTTP, rejected its HTTPS Origin, issued loopback signaling, and advertised a non-tailnet media candidate. An app GET or `/api/status` result therefore could not truthfully mean remote voice readiness.

Current Tailscale `1.102.x` Serve supports independent HTTPS listeners on one MagicDNS hostname. Separate listener ports avoid rewriting LiveKit paths. LiveKit `1.13.5` can bind and advertise the node's exact `tailscale0` IPv4 address, so this host does not require a wildcard media bind, TURN, ICE/TCP, a LAN candidate, or a firewall exception.

## Decision

The personal installation supports one tailnet-only remote voice topology for the versioned dev stand:

- application HTTPS uses the current node MagicDNS hostname on port `8443` and proxies only to the dev gateway loopback listener;
- LiveKit WSS uses the same hostname on port `7443` and proxies the complete root namespace to the dev LiveKit loopback listener, preserving `/rtc` and every signaling endpoint;
- the issued room capability contains that exact `wss://` URL, and CSP `connect-src` contains only the exact configured public signaling URL plus same-origin;
- same-origin admission accepts only the exact configured HTTPS application Origin (while the direct loopback development origin remains the existing local exception);
- the Python agent and gateway retain `ws://127.0.0.1` internal signaling/control, and gateway/LiveKit signaling/LLM listeners remain IPv4 loopback;
- LiveKit binds and advertises only the current Tailscale IPv4 address on exact interface `tailscale0` and the instance's one UDP media port; ICE/TCP, TURN, alternate candidates, Funnel, LAN/wildcard service listeners, and fallback remain disabled;
- the moving Tailscale IPv4 and current MagicDNS identity live only in the instance-private mode-`0600` configuration discovered at explicit setup time, not in tracked durable configuration.

`./stand remote-voice apply dev` is the narrow existing-stand operation that discovers the current self identity, backs up current structured Serve and runtime/permanent firewalld state, removes only the obsolete dev-port LAN-zone tailnet-source rule when present, refuses unrelated handlers, applies the two current supported `tailscale serve --bg --https=<port> <loopback-target>` listeners, atomically updates only the dev private configuration, and restarts dev only if it was already running. It does not use Funnel, create a daemon, edit routes, expose inference/Docker/credentials/admin services, or install a certificate/proxy/TURN service. Because LiveKit binds the exact Tailscale address, no firewalld media opening is required or created.

Application status reports only whether the complete public HTTPS/WSS plus Tailscale-RTC configuration is loaded. `./stand status dev` owns remote transport readiness: it requires the current private identity, both exact tailnet-only Serve handlers, HTTPS app health, the LiveKit HTTPS/WSS listener, and the exact UDP bind together. Internal voice readiness remains separately visible. Neither result claims a second device reached ICE, published a microphone, received media, heard agent audio, or completed physical cleanup; those remain Pasha's remote-device observations.

The existing one-session/idempotent-attempt contract is unchanged. This topology adds no retry, 409 suppression, session selector, or second controller.

## Consequences

### Positive

- One exact URL is usable from another tailnet device without exposing a generic LAN or public service.
- Browser-facing and internal URLs are deliberately different without moving local control traffic through the proxy.
- Separate HTTPS listeners preserve LiveKit endpoint paths and WebSocket upgrade behavior.
- Remote-ready cannot be inferred from an app-only proxy or local `/api/status` health.
- Clean setup is reproducible from the current authoritative Tailscale identity while moving host data remains private.

### Costs and limits

- Tailscale identity/Serve and the exact dev media bind are now explicit supported installation dependencies for remote voice, though they remain outside the supervised application process tree.
- A changed Tailscale IPv4 identity requires rerunning the explicit apply operation; stale private identity fails startup/status rather than selecting LAN or another candidate.
- Host-side HTTPS, WSS, capability, and bind proof cannot close real second-device microphone/media/audio acceptance.
- Main remains unexposed until a separately authorized release/acceptance action; this ADR configures only dev.

## Alternatives considered

- **One path-prefixed HTTPS listener:** rejected because path rewriting can alter LiveKit endpoints and adds no value when independent HTTPS ports are supported.
- **Proxy only the app and leave signaling loopback:** rejected because the browser capability would be unreachable remotely.
- **Advertise the LAN address with a tailnet-source firewall rule:** rejected because the intended peer should not need or learn a LAN path and exact `tailscale0` binding works.
- **Wildcard media plus firewall:** not needed on the measured host; if exact Tailscale binding stops being supported, readiness fails rather than widening automatically.
- **TURN, ICE/TCP, cloud fallback, or blind ICE retry:** rejected as alternate media paths and additional authority.
- **Funnel or a public certificate/proxy service:** rejected because the product is tailnet-only.

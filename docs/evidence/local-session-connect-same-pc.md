# Same-PC local session connection correction

- **Scope:** `http://127.0.0.1:8000` on the same host only
- **Diagnosis date:** 2026-08-21
- **Verification authority:** `./verify`

## Preserved diagnosis

The failing clean local sequence reached LiveKit signaling, accepted the agent JWT, and created the room, but the official Python RTC participant timed out forming its peer connection. The stand configuration advertised only `127.0.0.1:7882`, while the Python client gathered non-loopback host candidates. A controlled candidate-only counterfactual connected immediately when the server advertised one actual host interface/address. The separate reload `409` was server-side capacity: the first controller already occupied `max_sessions=1`; a clean second tab reproduced it. These causes interact but are not conflated.

The HTTPS/Tailscale entry remains contradictory and out of scope: the application origin and signaling capability are still local, and this change does not alter Tailscale Serve, add `wss`, or claim remote voice.

## Corrected bounded contract

`livekit_server_config()` keeps the gateway and LiveKit signaling on IPv4 loopback. It selects the active default-route IPv4 interface/address, with a deterministic non-loopback-interface fallback, and restricts LiveKit UDP media/node/interface/IP advertisement to exactly that address and the instance's one configured UDP port. It adds no wildcard bind, ICE/TCP, TURN, route, proxy, firewall, application-origin, or Tailscale mutation. Startup fails closed when no non-loopback IPv4 media path exists.

Before POST, the browser stores one unguessable UUIDv4 attempt identity in `sessionStorage` and sends it only in `X-Voice-Session-Attempt`. Identical concurrent/reload requests share one startup/controller and receive same-room/same-browser capability reissue bounded by the remaining attempt lifetime. A different identity remains `409`. Explicit end and terminal client failure close the exact attempt and clear storage; document unload retains it for reload. Startup failure cleans the slot before retirement. Expiry/end create a bounded in-process stale-replay tombstone; a service restart intentionally has no identity memory and can treat the still-stored value as a new attempt only when the fresh registry is empty. Cleanup that cannot prove completion retains capacity. No identity or capability is added to logs, status, diagnostics, or response fields.

## Executable evidence

The canonical short Firefox phase now starts the pinned LiveKit binary with the exact JSON returned by `livekit_server_config()`, including a fixed task-owned UDP port, and requires the official Python `livekit` participant to connect before the browser/media lifecycle proceeds. This exact-config join would fail under the diagnosed loopback-only candidate mismatch. Hermetic registry/browser regressions cover shared startup, post-capability reissue before media readiness, distinct-attempt rejection, expiry/stale replay, startup cleanup, explicit end, process-restart semantics, and preservation of one-controller capacity.

This proves generated-configuration participant/ICE connectivity and admission behavior only. It does **not** prove physical microphone capture, speech recognition, audible Kseniya playback, speaker rendering, remote HTTPS/Tailscale topology, reboot behavior, or full deployed-stand acceptance.

# ADR-0007: Use a low-level LiveKit and React boundary for Slice 6 realtime voice

- **Status:** Accepted for Slice 6; active provider/TTS/control composition superseded by ADR-0008 and ADR-0009
- **Date:** 2026-08-11
- **Decision owner:** Pasha

## Context

Slice 6 must add real browser media, visible correlated state, agent audio, reconnection safety, and full barge-in without creating a second voice pipeline or prematurely designing the Slice 7 avatar contract. The implementation must remain a deliberately small development application, keep inference and credentials server-only, and preserve the Slice 5 resource reserve and every failed provider result.

The recovery session verified that LiveKit Server 1.13.5 can restrict signaling to loopback while advertising one Tailscale IPv4 for UDP media. Current official Python RTC/API and browser client surfaces cover the required low-level room, track, data, and capability operations without adopting a higher-level competing agent framework.

## Decision

Slice 6 uses these pinned components:

- LiveKit Server `1.13.5`, acquired outside Git from its authoritative release archive and checked against SHA-256 `c020fac437b7cc9b776eef1ad5ea8af77be9acfa07602eca20a3a44930dfbc70`;
- official Python `livekit` `1.1.14` and `livekit-api` `1.2.0` around the existing `RealTurnController`; active LLM and TTS composition is owned by ADR-0008 and ADR-0009;
- FastAPI `0.141.1` and Uvicorn `0.52.1` for loopback-only static assets and room-capability/application glue;
- React `19.2.8`, TypeScript `7.0.2`, Vite `8.2.1`, and official `livekit-client` `2.21.0` for the browser.

The gateway and controller may share one Python process, but capability, room/media, realtime-session, and inference ownership remain separate objects and tests. A deployment admits one generated room at a time with exactly two participants: one generated browser identity and one agent. The browser receives a 300-second JWT restricted to that room, microphone publication, subscription, and data publication; it receives no signing material, provider endpoint/token, inference administration, or room-management grant.

Media and control remain separate:

- browser microphone audio is resampled through the official RTC SDK to 16 kHz mono PCM and endpointed by the pinned cache-local Silero v6 ONNX CPU speech detector; absolute energy is telemetry only;
- agent PCM uses one persistent LiveKit audio source with a 100 ms source queue;
- the active reliable controller-to-browser topic uses `realtime-control.v2`, while the bounded browser reconnect notice remains `client-control.v1`; historical V1 contracts stay immutable;
- both server tests and browser state/media gates reject wrong version/session/epoch/turn/generation/request, duplicate, late, out-of-order, oversized, and malformed data before state or media actions.

Barge-in and reconnection preserve this ADR's low-level LiveKit ownership; ADR-0009 owns the active request/turn/media-generation invalidation and persistent-publication attachment rules. Reconnection resets in-memory conversation context rather than risk replay or conditioning on an answer not known to have played.

The development bind is intentionally narrow: gateway TCP `8000` and LiveKit signaling TCP `7880` on loopback; explicit Tailscale Serve HTTPS ports for application/signaling (example `8443`/`7443`); and LiveKit media only on the host Tailscale IPv4 UDP `7882`. ICE/TCP media (`7881`) and TURN are disabled. Before changing Serve state, the foreground runner accepts exact existing application/signaling mappings as externally owned, rejects conflicts, and starts absent foreground mappings serially with a bounded status check. It supervises and stops only children it created, preserving exact pre-existing mappings and every unrelated handler; a global Serve reset is forbidden. The foreground runner is not production supervision.

No avatar component, state, contract, semantic input, design system, public endpoint, separate authorization system, wake behavior, or deployment service is introduced.

## Consequences

### Positive

- Slice 6 extends the existing controller instead of forking inference and cancellation semantics.
- React has a small typed reducer/context and media boundary that can host later UI modules without guessing the avatar contract.
- Capability scope, control ordering, reconnect resets, and interruption are deterministic and independently testable without models, secrets, or network.
- Active provider selection is outside this LiveKit boundary and is governed by ADR-0008.

### Costs and risks

- Pinned legacy provenance is `prineycom/voice-agent@93c5c397:infra/pi/agent/agent.py`, which loads local Silero once. V2 adapts only that ownership/provenance into its low-level controller; it does not import `livekit-agents` or legacy topology.
- Silero decisions and levels are content-free diagnostics. Real microphone false-positive/miss quality still requires the physical browser gate.
- One session and no TURN/TCP media are deliberate measured limits. A different topology requires evidence, not an implicit fallback.
- Resetting context on reconnect reduces continuity.
- Real browser audio, the second tailnet client, the 250 ms stop bound, and combined resource reserve are not proven by deterministic tests.
- Historical ADR-0006 provider failures remain failed evidence; ADR-0008 supersedes that provider in the active Slice 6 runtime.

## Alternatives considered

- **Higher-level LiveKit agent framework:** rejected for this slice because it would compete with the existing controller/contracts and broaden lifecycle ownership.
- **Next.js or broad client state/design frameworks:** rejected because the private single-page path needs no server rendering or additional state platform.
- **WebSocket control beside LiveKit:** rejected because it would add a second reconnect/correlation transport when reliable LiveKit data already satisfies the bounded contract.
- **Keep provider context across reconnect:** rejected because the server cannot prove that an offline browser heard a completed answer.
- **TURN or ICE/TCP by default:** rejected because the restricted Tailscale UDP smoke succeeded and no measured client has demonstrated the need.
- **Design an avatar seam now:** rejected; Design Gate V remains mandatory after Slice 6 acceptance.

# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion centered on one Arch Linux PC. Local LiveKit, speech-to-text (STT), and text-to-speech (TTS) form the realtime voice path. A local large-language model (LLM) is the initial preference; an explicitly selected cloud LLM is allowed only if measured local candidates cannot meet the real VRAM, latency, and quality budget.

The MVP visual is an original deterministic animated AI eye, not a Live2D-first avatar. Its pupil can follow a bounded external target or move in a bounded idle pattern, blink, change palette and state behavior, and replace the pupil with a thinking indicator. An outer ring or pattern pulses from actual speech playout. A renderer-agnostic avatar boundary allows later custom, Live2D, or 3D modules without letting an LLM generate frames.

## Status

**Pre-implementation.** This repository currently contains the architectural foundation and ordered roadmap, not a runnable voice stack. No inference model, cloud provider, renderer framework, or detailed avatar-control contract has been selected. Those choices are gated by measured slices and a separate avatar Grill/design task rather than documentation claims.

## Current scope

- One Arch Linux host runs every self-hosted media, orchestration, STT, TTS, application, and optional local-LLM process; there is no Pi/Desktop compute split.
- A private browser experience uses local LiveKit and local STT/TTS. LLM access uses one explicitly configured provider mode, with local preferred initially.
- A cloud LLM may be selected only after local candidates fail preregistered resource, latency, or quality gates. It is never an automatic or silent fallback.
- Tailscale membership is sufficient authorization during the current private stage.
- The MVP uses a deterministic custom AI-eye module behind a renderer-agnostic avatar boundary.
- The avatar runtime, never the LLM, owns pupil motion, blinking, speech-synchronous pulsing, palette/state behavior, interpolation, scheduling, and frames.
- Interruption, explicit failure behavior, privacy-safe observability, and measured resource budgets are part of the supported product.

## Deferred

- The detailed avatar-module and visual-control contract requires a dedicated Grill/design task before MVP eye implementation.
- Live2D and 3D remain possible later avatar modules; neither is an MVP renderer decision.
- Optional wake-word activation, including any custom Russian wake model, begins only after the core MVP is reliable.
- Exact model, quantization, inference server, cloud provider, and application-framework choices wait for their measurement gates.
- Selective migration from the legacy repository waits for a concrete vertical slice and fresh validation.

## Non-goals

- A Raspberry Pi/Desktop or other required user-managed compute-host split.
- Cloud STT/TTS or silent/automatic failover between local and cloud LLM providers.
- Audio2Face (A2F) or Audio2Emotion (A2E) in the active V2 architecture.
- Kiosk mode.
- A separate application authorization subsystem before evidence shows that tailnet membership is insufficient.
- Proprietary avatar assets, copied character design, or LLM-generated renderer parameters, keyframes, or per-frame animation data.

## Pinned legacy reference

The legacy repository is read-only evidence at [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Future work must inspect that pinned tree rather than assume its default branch is unchanged.

When a vertical slice finds a useful legacy contract, component, or test:

1. record the pinned source commit and exact path;
2. bring over only the smallest unit required by that slice;
3. adapt it to V2-owned contracts and revalidate its behavior in V2;
4. do not import legacy architecture docs, backlog state, deployment assumptions, secrets, recordings, or model artifacts wholesale.

See the authoritative inspection and migration boundary in [`docs/architecture.md`](docs/architecture.md#34-pinned-legacy-reference).

## Authoritative documentation

| Document | Purpose |
| --- | --- |
| [`CONTEXT.md`](CONTEXT.md) | Stable project vocabulary. |
| [`docs/architecture.md`](docs/architecture.md) | Authoritative system boundaries, contracts, lifecycle, operations, provider/privacy rules, and resource policy. |
| [`docs/roadmap.md`](docs/roadmap.md) | Dependency-ordered implementation slices and their evidence gates. |
| [`docs/adr/`](docs/adr/) | Accepted decisions that are costly or confusing to reverse. |

When documents disagree, accepted ADRs govern the decision they record, `docs/architecture.md` governs the current system design, and `docs/roadmap.md` governs implementation order. `CONTEXT.md` defines terminology only.

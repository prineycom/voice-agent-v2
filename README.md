# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion for one Arch Linux PC. It is intended to support low-latency spoken conversation through local LiveKit and local speech-to-text (STT), large-language-model (LLM), and text-to-speech (TTS) inference. Its visual presence is an original, deliberately simple Live2D AI-eyes avatar. The LLM supplies only bounded semantic animation intent; deterministic runtime code owns every rendered frame.

## Status

**Pre-implementation.** This repository currently contains the architectural foundation and ordered roadmap, not a runnable voice stack. No inference model or implementation framework has been selected. Those choices are gated by measurements on the target host rather than documentation claims.

The legacy [`prineycom/voice-agent`](https://github.com/prineycom/voice-agent) repository remains read-only provenance. V2 will migrate only a proven contract, component, or test when a roadmap slice needs it; it will not inherit the legacy repository wholesale.

## Current scope

- One Arch Linux host runs every required server, media, orchestration, and inference process.
- A private browser experience uses local LiveKit for realtime media and local STT, LLM, and TTS for the active voice path.
- Tailscale membership is sufficient authorization during the current private stage.
- An original Live2D AI-eyes avatar consumes a small, versioned semantic animation-intent contract.
- The session controller validates semantic animation intent; deterministic avatar runtime code maps, interpolates, schedules, and renders it.
- Interruption, explicit failure behavior, privacy-safe observability, and measured resource budgets are part of the supported product.

## Deferred

- Optional wake-word activation, including any custom Russian wake model, begins only after the core MVP is reliable.
- Exact model, quantization, inference-server, and application-framework choices wait for the host/model measurement slice.
- Selective migration from the legacy repository waits for a concrete vertical slice and fresh validation.

## Non-goals

- A Raspberry Pi/Desktop or other multi-compute-host split.
- 3D avatars, Audio2Face (A2F), or Audio2Emotion (A2E). These remain historical experiments in the legacy repository.
- Kiosk mode.
- Cloud inference or a silent cloud fallback in the active voice path.
- A separate application authorization subsystem before evidence shows that tailnet membership is insufficient.
- Proprietary avatar assets, copied character design, or LLM-generated per-frame animation data.

## Authoritative documentation

| Document | Purpose |
| --- | --- |
| [`CONTEXT.md`](CONTEXT.md) | Stable project vocabulary. |
| [`docs/architecture.md`](docs/architecture.md) | Authoritative system boundaries, contracts, lifecycle, operations, and resource policy. |
| [`docs/roadmap.md`](docs/roadmap.md) | Dependency-ordered implementation slices and their evidence gates. |
| [`docs/adr/`](docs/adr/) | Accepted decisions that are costly or confusing to reverse. |

When documents disagree, accepted ADRs govern the decision they record, `docs/architecture.md` governs the current system design, and `docs/roadmap.md` governs implementation order. `CONTEXT.md` defines terminology only.

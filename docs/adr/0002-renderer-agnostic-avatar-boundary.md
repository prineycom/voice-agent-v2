# ADR-0002: Renderer-agnostic avatar boundary and deterministic MVP eye

- **Status:** Accepted
- **Date:** 2026-08-10
- **Amended:** 2026-08-14 (Design Gate V)
- **Decision owner:** Voice Agent v2 project architecture

## Context

The legacy repository explored Live2D, A2F/A2E-driven facial motion, and a later 3D avatar path. Those approaches added renderer-specific contracts, model artifacts, GPU/runtime coupling, and high-rate animation data before the new product had proven its core voice experience.

V2 still needs a recognizable visual presence, but choosing Live2D or 3D for the MVP would be premature. The desired initial visual is a deliberately simple original AI eye. Future custom, Live2D, or 3D visuals should be replaceable modules rather than reasons to rewrite session control.

Language generation is neither predictable nor timely enough to control frames. Design Gate V subsequently fixed the detailed visual states, input precedence, module contract, and treatment of any future semantic LLM contribution before Slice 7 implementation; the accepted protocol is recorded in [`../design/gate-v-grill-results.md`](../design/gate-v-grill-results.md).

## Decision

Define a renderer-agnostic avatar host that loads one avatar module behind a versioned capability boundary. Session lifecycle, speech playout, cancellation, palette/state changes, and optional external tracking targets reach the module only through validated inputs. Each module owns its renderer, interpolation, scheduling, motion limits, and every frame.

The MVP module is a custom deterministic animated AI eye. Its required behavior is:

- a pupil that follows a bounded external target when supplied or uses bounded idle movement otherwise;
- deterministic blinking;
- an outer ring or pattern that pulses from actual speech playout;
- explicit palette changes;
- state-specific pupil behavior, including a thinking/loading representation;
- bounded cancellation, return-to-neutral, and reduced-motion behavior.

“Random” idle behavior must be bounded and reproducible under a fixed seed for validation. The external tracking producer—for example, future camera-based tracking—is outside the MVP eye renderer.

Design Gate V defined the detailed avatar-module and visual-control contract before the MVP eye slice started. The active versioned boundary and precedence rules are owned by [`../architecture.md`](../architecture.md#52-avatar-boundary-constraints). The MVP does not require visual instructions from the LLM. If a later design admits LLM-provided semantics, they must be bounded, validated, renderer-neutral, and coarse; an LLM must never emit renderer parameters, keyframes, shaders, executable content, or per-frame data.

Live2D and 3D are optional later avatar modules, not active MVP renderer paths. A2F and A2E remain historical experiments outside the active V2 architecture.

## Consequences

### Positive

- The voice/session architecture is not coupled to one renderer technology.
- The MVP visual can be small, original, deterministic, and synchronized to actual audio playout.
- Later custom, Live2D, or 3D work can target one module boundary instead of changing inference and turn control.
- Render behavior remains reproducible when model output is late, invalid, or absent.
- The completed design task resolved visual priorities before the versioned implementation boundary was frozen.

### Costs and risks

- The stable module boundary and explicit fallback semantics add a small adapter/capability surface even though the MVP ships one selected renderer plus its static fallback.
- External tracking and audio-derived pulsing require the fixed precedence, staleness, and accessibility rules to remain compatible.
- Live2D/3D modules receive no implied compatibility until they pass their own later slice.

## Alternatives considered

- **Live2D-first MVP:** rejected as unnecessary complexity before the core voice and simple eye are proven.
- **3D-first MVP:** rejected for the same reason and for its larger renderer/asset surface.
- **Hard-code the eye directly into session control:** rejected because later renderer replacement would leak visual details across the architecture.
- **LLM-generated per-frame or renderer-parameter data:** rejected as nondeterministic, high-rate, unsafe to validate, and tightly coupled to one renderer.
- **No visual:** rejected because a visual presence remains part of the product vision.

## Provenance and supersession

The pinned legacy reference is [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Legacy [ADR-0010](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0010-live2d-pixi-cubism-core.md) and [ADR-0017](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0017-3d-face-arkit-pipeline.md) are reference evidence for possible later modules, not active V2 choices. Legacy [ADR-0012](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0012-audio2face-hybrid-facial-animation.md) and [ADR-0016](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0016-a2f-emotion-supply.md) remain historical A2F/A2E experiments outside V2.

The boundary constraints are authoritative in [`../architecture.md`](../architecture.md); the required Grill/design gate and implementation order are authoritative in [`../roadmap.md`](../roadmap.md).

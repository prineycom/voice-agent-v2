# ADR-0002: Live2D AI-eyes driven by semantic animation intent

- **Status:** Accepted
- **Date:** 2026-08-10
- **Decision owner:** Voice Agent v2 project architecture

## Context

The legacy repository explored Live2D driven by facial-inference frames, A2F/A2E, and a later 3D avatar path. Those approaches added model artifacts, GPU/runtime coupling, high-rate data, renderer-specific mappings, and visual directions that are no longer the active product.

V2 needs a recognizable but deliberately simple visual presence. Language generation can usefully express communicative meaning, but it is neither predictable nor timely enough to control animation frames. Allowing generated parameter streams would also couple prompts and model behavior to a particular avatar rig.

The desired visual is aesthetically inspired by the broad idea of an AI-eyes companion such as Fairy from Zenless Zone Zero, while remaining an original character with no proprietary asset or copied character design.

## Decision

The only active V2 visual is an original Live2D AI-eyes avatar.

The LLM may produce a small, versioned animation-intent contract containing bounded semantic cues. The session controller validates that contract. A deterministic avatar runtime owns mapping, interpolation, scheduling, motion limits, and all Live2D frame data.

The intent vocabulary is finite and coarse. It may express meanings such as attention, gaze, expression, and emphasis at semantic response anchors. It must not contain Live2D parameter names, keyframes, frame timestamps, executable content, or arbitrary per-frame values.

3D rendering, A2F, and A2E are excluded from the active V2 architecture and roadmap. Their legacy evidence remains available only for historical reference.

## Consequences

### Positive

- Generated output is bounded, schema-validatable, testable, and independent of the Live2D rig.
- Animation remains smooth and reproducible even when LLM output is late, invalid, or absent.
- The active stack avoids an additional facial-inference model and its GPU/data pipeline.
- The visual can be developed and replay-tested with deterministic intent fixtures before real LLM integration.
- Original asset provenance is explicit.

### Costs and risks

- Expressiveness is limited to vocabulary and behaviors implemented locally.
- Designing an appealing original avatar and tuning deterministic motion still requires visual review.
- Semantic cues may feel repetitive until measured and refined.
- New avatar capabilities require deliberate contract/runtime evolution rather than arbitrary model output.

## Alternatives considered

- **LLM-generated per-frame or renderer-parameter data:** rejected as nondeterministic, high-rate, unsafe to validate, and tightly coupled to one rig.
- **A2F/A2E facial inference feeding Live2D:** rejected for the active product because it adds GPU and mapping complexity without matching the deliberately simple eyes direction.
- **3D avatar:** rejected because V2 has chosen Live2D as its visual medium and does not need two renderer stacks.
- **No avatar:** rejected because a visual presence is part of the product vision.

## Provenance and supersession

For V2, this decision retains only the general Live2D evidence from legacy [ADR-0010](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0010-live2d-pixi-cubism-core.md). It supersedes the active direction of legacy [ADR-0012](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0012-audio2face-hybrid-facial-animation.md), [ADR-0016](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0016-a2f-emotion-supply.md), and [ADR-0017](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0017-3d-face-arkit-pipeline.md) for the V2 product only. The legacy artifacts remain unchanged historical experiments.

Contract invariants and ownership are authoritative in [`../architecture.md`](../architecture.md); the measured implementation gate is in [`../roadmap.md`](../roadmap.md).

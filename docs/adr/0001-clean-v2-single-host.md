# ADR-0001: Clean V2 repository and single-host topology

- **Status:** Accepted
- **Date:** 2026-08-10
- **Decision owner:** Voice Agent v2 project architecture

## Context

The legacy voice-agent repository grew around a Pi control/media plane and a separate desktop GPU plane. Its audit found valuable implemented experiments and tests, but also contradictory documentation, unavailable-host dependencies, stale backlog assumptions, and no trustworthy clean full-turn baseline.

The current product has one canonical Arch Linux PC with an NVIDIA RTX 4070 12 GB. Continuing the split would preserve network, deployment, and availability boundaries that no longer match the available hardware. Reworking the legacy repository in place would also make it difficult to distinguish proven provenance from inherited assumptions.

## Decision

Create `prineycom/voice-agent-v2` as a clean private repository.

Every required runtime service—including LiveKit, session control, STT, LLM, TTS, and the application gateway—runs on the canonical Arch PC. A tailnet browser is permitted as a presentation endpoint, but no second compute host is required.

Preserve the legacy repository unchanged as provenance. Migrate only the smallest proven contract, component, or test demanded by a future vertical slice. Each migration must record its origin and pass V2 validation; no directory is copied wholesale.

## Consequences

### Positive

- One availability, deployment, and resource boundary matches the real machine.
- The roadmap can measure end-to-end contention on the actual 12 GB GPU.
- New documentation and tests do not need to reconcile obsolete Pi/Desktop paths.
- Legacy work remains inspectable without becoming an implicit dependency.

### Costs and risks

- The canonical PC is a single point of failure and has a shared GPU budget.
- Useful legacy components must be rediscovered and revalidated slice by slice.
- A clean repository initially has fewer runnable capabilities than the legacy repository.
- Moving to multiple compute hosts later would require an explicit topology change and new failure/security analysis.

## Alternatives considered

- **Modernize the legacy repository in place:** rejected because stale active assumptions and historical experiments would continue to obscure the supported architecture.
- **Nest V2 inside the legacy repository:** rejected because ownership, history, tooling, and source-of-truth boundaries would remain ambiguous.
- **Retain a Pi/Desktop split:** rejected because the external desktop is unavailable and the canonical PC is the sole supported compute target; its full-stack feasibility remains gated by measurement.

## Provenance and supersession

For V2, this decision supersedes the topology direction in legacy [ADR-0001: hybrid architecture](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0001-hybrid-architecture.md). It does not rewrite or invalidate the legacy repository as historical evidence.

The current boundary and unresolved measurements are authoritative in [`../architecture.md`](../architecture.md); implementation order is authoritative in [`../roadmap.md`](../roadmap.md).

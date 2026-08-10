# ADR-0001: Clean V2 repository and single-host topology

- **Status:** Accepted
- **Date:** 2026-08-10
- **Decision owner:** Voice Agent v2 project architecture

## Context

The legacy voice-agent repository grew around a Pi control/media plane and a separate desktop GPU plane. Its audit found valuable implemented experiments and tests, but also contradictory documentation, unavailable-host dependencies, stale backlog assumptions, and no trustworthy clean full-turn baseline.

The current product has one canonical Arch Linux PC with an NVIDIA RTX 4070 12 GB. Continuing the split would preserve network, deployment, and availability boundaries that no longer match the available hardware. Reworking the legacy repository in place would also make it difficult to distinguish proven provenance from inherited assumptions.

A measured cloud LLM may become an external dependency if local LLM candidates cannot meet the shared-host budget. That provider exception does not restore a second inference/control host. After the local Slice 2 failure, [ADR-0004](0004-tailnet-litellm-cloud-gateway-evaluation.md) admitted one narrower exception: an allowlisted user-operated tailnet gateway may relay only the selected cloud LLM path.

## Decision

Create `prineycom/voice-agent-v2` as a clean private repository.

Every required self-hosted media, session-control, STT, TTS, application, and local-inference runtime runs on the canonical Arch PC. A tailnet browser is permitted as a presentation endpoint. If cloud LLM mode passes its separate gate, the one LiteLLM relay allowed by ADR-0004 may run on a user-operated tailnet node; it performs no STT/TTS or local response inference and cannot become a general orchestration plane.

An explicitly selected managed cloud LLM is the only permitted external inference exception and is governed by [ADR-0003](0003-local-first-llm-with-explicit-cloud-option.md) and [ADR-0004](0004-tailnet-litellm-cloud-gateway-evaluation.md). It is not an automatic fallback or a return to the legacy Pi/Desktop inference topology.

Preserve the pinned legacy repository unchanged as provenance. Migrate only the smallest proven contract, component, or test demanded by a future vertical slice. Each migration must record its pinned source commit and exact path, adapt to V2-owned contracts, and pass V2 validation; no directory is copied wholesale.

## Consequences

### Positive

- One self-hosted availability, deployment, and resource boundary matches the real machine.
- The roadmap can measure end-to-end contention on the actual 12 GB GPU.
- New documentation and tests do not need to reconcile obsolete Pi/Desktop paths.
- Legacy work remains inspectable without becoming an implicit dependency.
- A measured cloud LLM option can preserve product quality; the narrow tailnet gateway exception keeps inference and orchestration off the auxiliary node.

### Costs and risks

- In local-LLM mode, the canonical PC is a single point of failure with a shared GPU budget.
- In cloud-LLM mode, response generation adds external availability, credential, privacy, cost, and latency boundaries; the selected tailnet gateway also adds one user-operated relay dependency.
- Useful legacy components must be rediscovered and revalidated slice by slice.
- A clean repository initially has fewer runnable capabilities than the legacy repository.
- Moving other inference or control capabilities off-host later would require an explicit topology change and new failure/security analysis.

## Alternatives considered

- **Modernize the legacy repository in place:** rejected because stale active assumptions and historical experiments would continue to obscure the supported architecture.
- **Nest V2 inside the legacy repository:** rejected because ownership, history, tooling, and source-of-truth boundaries would remain ambiguous.
- **Retain a Pi/Desktop split:** rejected because the external desktop is unavailable and the canonical PC is the sole self-hosted compute target; its local-LLM feasibility remains gated by measurement.
- **Call any external inference a multi-host topology:** rejected because an explicitly managed cloud LLM is a provider boundary, not a second user-operated deployment plane.

## Provenance and supersession

The pinned legacy reference is [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). For V2, this decision supersedes the topology direction in legacy [ADR-0001: hybrid architecture](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0001-hybrid-architecture.md). It does not rewrite or invalidate the legacy repository as historical evidence.

The current boundary, pinned inspection rule, and unresolved measurements are authoritative in [`../architecture.md`](../architecture.md); implementation order is authoritative in [`../roadmap.md`](../roadmap.md).

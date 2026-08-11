# ADR-0003: Local-first LLM with an explicit measured cloud option

- **Status:** Accepted
- **Date:** 2026-08-10
- **Decision owner:** Voice Agent v2 project architecture

## Context

The canonical RTX 4070 has 12 GB VRAM shared by STT, LLM, TTS, browser rendering, and transient overlap. A local LLM best supports privacy and independence, and it is the initial product preference. That preference is not evidence that a sufficiently capable local model can meet the final resource, latency, and response-quality budget.

Refusing every cloud option could force an unacceptably weak or slow assistant. Conversely, a silent cloud fallback would move private transcript/context data across a new provider boundary without a deliberate choice and would make failures, cost, and behavior difficult to explain.

Slice 2 later preregistered the user's sole local candidate, official `LiquidAI/LFM2.5-2.6B` native BF16 on vLLM. It failed hard gates and the user declined accepting the failed selection, stopped further LFM inference, and explicitly activated this ADR's cloud-evaluation branch through [ADR-0004](0004-tailnet-litellm-cloud-gateway-evaluation.md).

## Decision

Evaluate local LLM candidates first under preregistered VRAM, latency, and quality gates on the canonical host. The preferred candidate is recorded only when the user supplies it to the measurement slice.

If no measured local candidate passes, the project may explicitly select one cloud LLM provider after measuring response quality and latency and reviewing credential handling, data retention/training policy, region where relevant, cost, and operational observability.

A deployment configures exactly one LLM provider mode at a time. Failure of the selected provider fails the affected response honestly; it never triggers automatic, silent, or per-request fallback to another local or cloud provider. Changing provider is an explicit configuration/operational action with a visible provider identity.

The session controller owns the provider-neutral request/result contract. In cloud mode:

- only the final transcript and explicitly permitted conversation/context fields cross the provider boundary;
- raw microphone audio and TTS audio remain on the canonical host and authorized LiveKit path;
- the cloud credential stays server-side in untracked secret storage and never reaches the browser, logs, traces, or repository;
- the provider endpoint, gateway endpoint when used, model alias, and underlying provider/model identity are allowlisted configuration, not LLM-generated or implicit default values;
- default observations record provider mode/identity, request correlation, latency, token/usage and cost data when available, error class, and that an external transfer occurred, without prompt/response content;
- the approved provider privacy/retention assumptions are recorded before activation;
- encrypted transport remains the production target; [ADR-0005](0005-operator-fixed-slices-2-5-model-stack.md) records Pasha's narrow acceptance of the exact temporary HTTP gateway without runtime DNS/route/TSMP proof while HTTPS is deferred.

The only authorized cloud path is LiteLLM at `http://rpi:4000`. Authenticated discovery and public-synthetic measurements selected only alias `deepseek-v4-flash`, while its underlying route remains operator-attested and opaque and its automated gates remain failed. ADR-0005 authorizes that failed alias only for cumulative Slices 3–5 and one final human turn; it does not grant production approval or permit another alias or fallback. Runtime still checks bearer-authenticated alias capability and rejects redirects, but it deliberately performs no Tailscale transport proof.

STT and TTS remain local in both modes.

## Consequences

### Positive

- Local privacy and independence remain the first path tested.
- Product quality is not permanently capped by the 12 GB shared GPU.
- A single provider-neutral contract keeps orchestration stable across the measured choice.
- Explicit selection makes external data transfer, credentials, failure, cost, and observability reviewable.

### Costs and risks

- Cloud mode adds network latency, provider availability, recurring cost, secret rotation, and a third-party privacy boundary.
- Supporting two provider modes adds contract tests even though only one is active per deployment.
- Switching providers is intentionally operational rather than transparent to a failing turn.
- Provider policy or API changes can invalidate an approved cloud configuration.

## Alternatives considered

- **Local-only regardless of measurements:** rejected because resource, latency, or quality evidence may show that the available local model is inadequate.
- **Cloud-primary:** rejected because local remains the user's initial preference and must be measured first.
- **Automatic local↔cloud fallback:** rejected because it silently changes privacy, cost, latency, and behavior during failure.
- **Retry or silently replace the failed local model:** rejected after measurement because the user stopped further LFM inference and explicitly chose the separate cloud gate.

## Provenance

The pinned legacy reference is [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Legacy [ADR-0001](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0001-hybrid-architecture.md) records an older cloud-oriented topology, while legacy [ADR-0014](https://github.com/prineycom/voice-agent/blob/93c5c39786ff790d7ae436772d2cf37a2eeb32c6/docs/adr/0014-local-llm-qwen35-mtp.md) records a later local-model choice. Neither selects the V2 provider or model; V2 requires fresh measurement under this ADR.

Provider boundaries are authoritative in [`../architecture.md`](../architecture.md); the selection gate is authoritative in [`../roadmap.md`](../roadmap.md).

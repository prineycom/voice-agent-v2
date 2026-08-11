# ADR-0006: Authorize Slice 6 live transcripts with environment-only LiteLLM endpoint configuration

- **Status:** Accepted for Slice 6 private live testing
- **Date:** 2026-08-11
- **Decision owner:** Pasha

## Context

ADR-0005 authorized the operator-fixed Whisper, LiteLLM `deepseek-v4-flash`, and Qwen3 CustomVoice/`ryan` stack only through cumulative Slices 2–5. The provider route failed its recorded latency, reliability, fairness, and isolation gates, remains operator-opaque, and still lacks approved retention/training, region, pricing, and underlying-provider evidence. The final Slice 5 human turn consumed ADR-0005's live-transcript authorization; it did not authorize the Slice 6 browser path.

Slice 6 must exercise the same selected-provider route with private live transcripts to prove local LiveKit media, correlated UI state, audible local synthesis, reconnect safety, and barge-in. The existing runtime also hard-coded the LiteLLM endpoint. That would make an HTTPS/domain migration require a source change and would embed deployment-specific topology in application code.

## Decision

Pasha explicitly authorizes private Slice 6 live transcript traffic from the server-side provider adapter to the single LiteLLM alias `deepseek-v4-flash`, despite all preserved failed and missing provider evidence. This is a Slice 6 implementation and acceptance-test exception, not production security/privacy approval and not permission to relabel any failed gate.

The provider endpoint is required untracked server-side configuration named exactly `LITELLM_BASE_URL`:

- there is no compiled or tracked endpoint default;
- no alternate environment name, redirect, endpoint alias, implicit model default, alternate model, or fallback is accepted;
- the current private test value is `http://rpi:4000`;
- both readiness and completion use the same parsed scheme, host, and port;
- redirects remain rejected and the only model alias remains `deepseek-v4-flash` with exact response-identity enforcement;
- a future HTTPS/domain endpoint requires only changing `LITELLM_BASE_URL` and passing readiness, not changing code;
- the endpoint and credential remain in server-only process configuration and never appear in browser capability responses, browser assets, or browser control events.

Only final transcript and the already bounded permitted in-memory context may cross this boundary. Raw microphone audio, synthesized audio, local files, environment values, tokens, and inference-management material remain forbidden. STT and TTS remain local and process-isolated. No request failure can choose a different provider or endpoint.

This decision must be committed before the first new Slice 6 real live transcript is sent. Deterministic fake-inference and browser-boundary tests do not cross the provider boundary and may run before physical-browser acceptance.

## Consequences

### Positive

- Slice 6 can validate its real browser media path without disguising the selected provider's known failures.
- Deployment-specific endpoint topology is removed from runtime source.
- Moving to the target HTTPS/domain endpoint is a configuration-plus-readiness operation.
- Browser clients receive only a short-lived room capability and public LiveKit URL; LiteLLM endpoint and credentials remain server-only.

### Costs and risks

- The accepted current HTTP hop and opaque onward route are not production privacy/security approval.
- Private transcript disclosure, availability, latency, empty responses, and provider identity remain bounded only by the prior narrow controls and Pasha's exception.
- A missing or invalid `LITELLM_BASE_URL` now makes cloud-provider readiness fail closed; there is intentionally no compatibility fallback.
- Real loopback and second-tailnet-browser evidence is still required and cannot be inferred from deterministic tests.

## Alternatives considered

- **Keep the endpoint hard-coded:** rejected because deployment topology would remain in code and HTTPS migration would require a release.
- **Retain ADR-0005 as implicit authorization:** rejected because it explicitly ended at Slice 5.
- **Add a default plus environment override:** rejected because a missing deployment value could silently use the wrong transport.
- **Allow aliases or redirect-based migration:** rejected because they weaken endpoint identity and no-fallback behavior.
- **Wait for production provider/privacy approval:** not selected for Slice 6; Pasha explicitly accepted the narrower private-test risk while preserving every failed/missing gate.

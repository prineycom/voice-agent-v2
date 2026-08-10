# Do Report: 5-selected-llm-provider-response

**Source:** https://github.com/prineycom/voice-agent-v2/issues/5
**Parent:** Issues #3 and #4
**Status:** ⚠️ automated implementation checkpoint pass under operator-fixed provider; Slice 2 performance/quality failures remain failed

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/cloud_llm.py` | Added single-alias LiteLLM adapter with Tailscale route proof, exact field/model allowlists, memory-only bounded per-session context, streaming collection, cancellation, safe observations, and no fallback. | Provider contract, privacy, isolation, identity, cancellation/failure semantics. |
| `scripts/verify_slice4.py`, `verify-slice4` | Runs public real-STT → selected-provider → deterministic-audio turn and controlled real provider cancellation. | Public correlated selected-provider tracer and cancellation evidence. |
| `tests/test_real_adapters.py` | Covers exact alias, permitted fields, context isolation/reset, transcript bounds, explicit provider failure, and safe observation shape. | Contract/redaction/no-fallback validation. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Fixed response quality/latency budget | ❌ preserved | Slice 2 primary failed 17 automated gates and repeat failed 9; operator delivery override fixes the alias without relabelling those failures. |
| Explicit mode/model/endpoint/readiness | ✅ | Safe readiness reports cloud, `litellm/deepseek-v4-flash`, `http://rpi:4000`, external transfer, no fallback, and Tailscale route. |
| Only permitted final transcript/context crosses boundary | ✅ | Body schema is closed; roles are closed; transcript length ≤4096; no files/audio/environment/tools/arbitrary fields. |
| Credential/content absent from observations | ✅ | Token is read only from mode-0600 task file; observations contain timings, numeric usage, response model, identity, transfer fact, and content-free error class. |
| Context isolation/non-persistence | ✅ | Unit tests prove session B receives no session A context; history is memory-only, bounded to two exchanges, and explicitly resettable. |
| Failure/cancellation has no fallback | ✅ | Injected provider 503 remains `selected_provider_http_503`; real mid-request cancellation reports `selected_provider_cancelled`; alias never changes. |
| Real STT → selected provider → deterministic TTS | ✅ | `./verify-slice4` completed one licensed public-corpus turn with one terminal completion. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | 34 deterministic/unit/contract tests. |
| `./verify-slice4` | ✅ pass | Real Whisper + real LiteLLM alias + deterministic local tone; real provider cancellation explicit. |

## Unresolved uncertainty

- Provider cost/privacy/provenance and failed Slice 2 latency/fairness gates remain accepted risks, not passes.
- The cumulative final human test is the only authorization for live transcript transfer in this branch; no content is retained by default.
- Real Qwen TTS integration follows in Slice 5; no review/no-mistakes is run at this intermediate checkpoint.

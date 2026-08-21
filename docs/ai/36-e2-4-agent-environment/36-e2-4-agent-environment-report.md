# Do Report: 36-e2-4-agent-environment

> Historical assumption note: active personal-install architecture later replaced the unavailable published OCI-index prerequisite with explicit host-native locked-context preparation and a private exact local image ID. This does not alter the issue-era deterministic AgentEnvironment results or turn them into real Docker evidence.

**Source:** https://github.com/prineycom/voice-agent-v2/issues/36  
**Parent:** —  
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/agent_run*.py`, `local_lfm.py` | Versioned exact-local multi-step loop, realtime identity, budgets, result reinjection, cancellation and voice composition | Natural multi-step exact-model production path; no fallback; full-identity late drop |
| `src/voice_agent_v2/agent_environment*.py` | Strict V2 configuration, stable installation/endpoint/spec registry, lock, Docker resolution, exec routing, receipts, resources, status and lifecycle | Single persistent exact ID; fail-closed truth; at-most-once; Docker-exec-only; explicit lifecycle |
| `agent-environment/` | Locked native image definition and fixed readiness/file/patch/process/receipt helper | Fixed helper surface, rootfs ledger, security/user/image identity |
| `contracts/`, `config/agent-config-v2.example.yaml` | V2 configuration/runtime/public status plus AgentRun/environment V1 schemas | Versioned closed contracts; no selector/backend/raw arguments; safe status |
| gateway/runtime/operations CLI | Active V2 status/composition and selector-free `agent-environment` controls | Ordinary voice remains ready; operator status/reset/rebuild/remove/retire |
| `tests/test_agent_environment.py`, verification manifest | Deterministic fake-Docker concurrency, persistence, failure, routing, receipt, cancellation, resource and lifecycle matrix | Deterministic PR acceptance without Docker/network/model/hardware |
| `CONTEXT.md`, `README.md`, `docs/` | Coherent active contract, historical boundary, evidence separation and nonclaims | Documentation/status/spec alignment; E2.1 and E2.2/E2.3 preserved |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Natural RU/EN multi-step exact local model, one final answer, no fallback/E2.1 rerun | Pass | `AgentRun`, `ExactLocalLFMAgentAdapter`, `AgentRunProvider`; deterministic Russian multi-step test |
| Concurrent first use creates one; later calls/runs reuse exact ID and state | Pass | Cross-process lock/resolution plus concurrent and controller-restart fake-Docker cases |
| Ordinary lifecycle never stops/removes; stopped compatible ID starts/reuses | Pass | No ordinary lifecycle hook in provider/environment; explicit transcript assertions |
| Docker failure/partial/endpoint change cannot create duplicate or reach host | Pass | Exact runner construction and endpoint/partial/duplicate tests |
| Stale/effective mismatch and duplicate fail closed | Pass | Fresh inspect validation and failure tests |
| Every supported operation uses fixed Docker exec helper | Pass | Fixed `HELPERS` mapping and nine-route transcript test |
| Ambiguity is never redispatched; definite stop may retry once | Pass | Private dispatch record, rootfs claim ledger and ambiguity/preacceptance cases |
| Cancellation targets foreground call identity and drops late work | Pass | `AgentRun.cancel`, `cancel-call` helper route and noncooperative late-model test |
| Resource controls/reserve are truthful and stop, never remove | Pass in deterministic tier | Effective inspect checks and reserve-stop transcript; platform effectiveness remains separate |
| Safe persistence/process status | Pass | Nullable persistence truth and explicit process-stop statement |
| Exact confirmed selector-free lifecycle | Pass | CLI surface and exact endpoint/ID/owner/spec/generation reinspection |
| Active V2 has no execution selector/identity alternative | Pass | Strict Pydantic/JSON schema and rejection matrix |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Complete output: `artifacts/issue-36-verify.log`; canonical PR tier only |

## Unresolved uncertainty

- Linux Docker Engine, Docker Desktop/macOS, locked-image availability, exact-model/network, daemon/Desktop restart, host reboot/write pressure and physical acceptance remain separate tiers as required.

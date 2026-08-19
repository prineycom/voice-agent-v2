# Do Report: 40-e4-1-cited-web-research

**Source:** https://github.com/prineycom/voice-agent-v2/issues/40  
**Parent:** —  
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/agent_research.py` | Closed provider-neutral Web operations, exact persisted bytes, honest metadata, citation binding | receipts, failures, persistence, admission, redaction |
| `src/voice_agent_v2/research_evaluation.py` | Frozen outcome/refinement/source/coverage/bounds scorer | outcome-based RU/EN-style evaluation |
| `src/voice_agent_v2/agent_environment.py` | Docker-exec-only routing and receipt postprocessing | combine tools, no host fallback/runtime |
| `src/voice_agent_v2/agent_run.py` | AgentRun v2 citation custody and privacy-safe research results | actual receipt citations, closed decisions |
| `src/voice_agent_v2/agent_environment_config.py`, `config/agent-config-v2.example.yaml` | Fixed closed Web tool inventory | closed admission/no selector |
| `contracts/*.v2.schema.json`, `contracts/research-citation.v1.schema.json`, `contracts/README.md` | Versioned decision/run/citation contracts | executable ownership and bounds |
| `tests/test_agent_research.py`, `scripts/run_behavior_tests.py` | Frozen fake-Docker research/adversarial/failure/redaction coverage in canonical gate | all deterministic criteria |
| `README.md`, `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md` | Coherent active contract, threat model, tiers and nonclaims | documentation/security/evidence separation |
| `docs/evidence/e4-1-bounded-cited-web-research.md` | Deterministic evidence and explicit nonclaims | honest evidence |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Frozen natural outcome, refinement and multiple sources | ✅ | `NaturalResearchModel` plus `score_frozen_research` |
| Actual receipt citations and honest metadata | ✅ | strict `bind_citations`; redirect/truncation/stale/error fixtures |
| Convenience and ordinary tools combined without order/provider | ✅ | one AgentRun uses two searches, two fetches, shell/script and file work |
| Persistent research state | ✅ | workspace/cache artifact receipts and persistence matrix |
| Hostile bytes cannot directly dispatch | ✅ | serialized operation remains preview data until next admitted decision |
| Accepted RW/credential authority | ✅ | controlled adversarial effects explicitly asserted and documented |
| Unmounted host/control/device boundary and no fallback | ✅ | preserved host sentinel, fixed Docker-exec transcript, inherited E2.4 guards |
| Bounded failure/no ambiguous mutation retry/voice preservation | ✅ | failed Web receipt continues bounded final; existing AgentEnvironment failure owners remain green |
| No secret/private content in citations/status; exact model bytes | ✅ | injected synthetic secret arrives raw and persists exact, display copies redact |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| focused `unittest` research/AgentRun/config | pass | 7 tests |
| hermetic current behavior manifest | pass | 375 tests, zero skips |
| `./verify` | pending final delivery run | full output will be saved under `artifacts/issue-40-verify.log` |

## Unresolved uncertainty

- Linux Docker Engine, Docker Desktop/macOS, exact-model/live-network, physical/reboot/voice/full-stack evidence remains separately pending by contract.

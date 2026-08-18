# Do Report: 32-e2-3-custody-failure-tracer

**Source:** https://github.com/prineycom/voice-agent-v2/issues/32
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/operation_framework.py` | Adds full operation identity, split admission/dispatch, immutable proposal/argument/result bounds, inert structured-result validation, and strict correlated one-shot result consumption. | Full custody; stable bounded validation; one-operation limit |
| `src/voice_agent_v2/operation_runtime.py` | Adds the test-injected whole-loop owner with one monotonic deadline, handler sub-budget, cancellation fan-out, full-identity late-result drops, and guarded final callbacks. | All cancellation seams; late success/failure; no fallback; monotonic budgets |
| `src/voice_agent_v2/real_turn.py` | Passes existing epoch/request/generation identity only to compatible injected providers. | Actual realtime turn ownership; full identity |
| `src/voice_agent_v2/realtime.py` | Requires the existing cleanup/rollback barrier before replacement listening admission even after a separately completed interrupt. | Bounded cleanup; replacement isolation |
| `contracts/operation-*.v2.schema.json`, `contracts/admitted-operation.v2.schema.json` | Adds closed full-identity v2 contracts while preserving frozen E2.2 v1 schemas. | Correlation; malformed-version rejection; immutable release bounds |
| `tests/test_operation_tracer.py` | Drives the actual `RealtimeSession`/`RealTurnController` through six cancellation seams, late non-cooperative outcomes, replacement turns, adversarial data, deny-all policy/profile states, one-operation enforcement, and fake-clock budgets. | Complete bounded matrix |
| `scripts/verify_operation_tracer.py`, `scripts/verify.py`, `scripts/run_behavior_tests.py` | Makes the matrix a single network-denied canonical phase with an 8-second inner bound and no duplicate hermetic selection. | Sole canonical `./verify`; IP-network denial |
| `CONTEXT.md`, `contracts/README.md`, `docs/architecture.md`, `docs/testing.md` | Records E2.3 custody, contract ownership, nonclaims, and verification ownership. | Production-empty/no-new-surface invariants; physical gaps remain open |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Every preregistered cancellation point has one terminal, bounded cleanup, and no late output | ✅ | `RealtimeCustodyTests.test_all_preregistered_cancellation_seams_isolate_replacement_turn` covers proposal request, admitted-before-dispatch, typed handler, result-before-injection, final generation, and TTS delivery. |
| Replacement waits for cleanup and sees no cancelled operation data | ✅ | Realtime admission barrier plus empty ordinary-turn history assertion across all six seams. |
| Late non-cooperative success/failure is dropped by full identity | ✅ | Dedicated success/failure matrix releases the handler only after replacement completion and asserts no new event/terminal/audio. |
| Oversize/depth/count/version/Unicode failures are stable and content-free | ✅ | Proposal/result contract matrices assert exact codes and zero pre-admission dispatch. |
| Instruction/role/tool-call data is inert; second proposal dispatches nothing | ✅ | Adversarial result reaches one opaque handoff but is absent from events/TTS/observations; second proposal fails at the consumed admission gate with one handler call total. |
| Missing/invalid profile or policy mismatch preserves ordinary local voice | ✅ | Degraded deny-all profile snapshots and revision mismatch deny the synthetic operation; the next same-session ordinary turn completes with the selected local identity. |
| Observations contain no arguments/results/content/exception/environment/credentials | ✅ | Observation key whitelist and late private exception fixture assertions; adversarial bytes are absent. |
| Production remains exact-empty with no new service/UI/store/fallback | ✅ | Exact production registry/config assertions and dependency-injected-only runtime boundary; no production composition change. |
| Sole canonical PR gate passes | ✅ | `artifacts/issue-32-verify.log`. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `~/.cache/voice-agent-v2/slice-6/runtime/venv/bin/python -B scripts/verify_operation_tracer.py` | PASS | 10 cases, IP network denied, under the 8-second bound. |
| `scripts/run_behavior_tests.py hermetic` with the canonical runtime/environment | PASS | 334 tests, zero unexpected skips. |
| Targeted realtime/adapter/TTS and profile/config suites | PASS | Existing cancellation, streaming, and degraded-voice regressions remain green. |
| `./verify` | PASS | Full output saved at `artifacts/issue-32-verify.log`; canonical 90-second gate, zero unexpected skips/leaks/artifacts. |

## Unresolved uncertainty

- Physical microphone, audibility, physical barge-in, reboot, Raspberry Pi, and full-stack host acceptance remain open exactly as documented in `docs/testing.md`.

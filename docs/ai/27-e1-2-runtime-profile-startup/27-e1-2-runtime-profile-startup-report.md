# Do Report: 27-e1-2-runtime-profile-startup

**Source:** https://github.com/prineycom/voice-agent-v2/issues/27  
**Parent:** E1 private agent profile foundation  
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/agent_profile_runtime.py` | Frozen, restart-only startup result with normalized degraded codes and deny-all policy revision | valid/degraded startup, immutable pin, no authority, privacy |
| `src/voice_agent_v2/slice6_gateway.py`, `src/voice_agent_v2/livekit_runtime.py` | One composition-root load injected into the existing registry; public status composes the soft plane without changing voice health | single startup load, unchanged voice readiness, no daemon/listener |
| `src/voice_agent_v2/operations_cli.py` | Existing operator status carries the same bounded runtime profile result | content-free operator status |
| `contracts/agent-profile-runtime-status.v1.schema.json`, `contracts/public-operational-status.v1.schema.json`, `contracts/fixtures/public-operational-status.v1.json`, `contracts/README.md` | Executable content-free status contract and fixture | stable revisions/reason/deny-all facts, no values/raw errors |
| `tests/test_agent_profile_runtime.py` | Disposable valid/degraded/restart pinning, no-repair, sandbox, privacy, readiness, and real controller STT → LLM → TTS regression matrix | complete E1.2 behavior evidence |
| `tests/test_slice9_runtime.py`, `scripts/verify_runtime_contract.py`, `scripts/run_behavior_tests.py` | Existing status/composition fixtures consume the required pinned startup dependency; new suite is in the canonical manifest | canonical integrated regression evidence |
| `CONTEXT.md`, `docs/architecture.md`, `docs/testing.md` | Authoritative restart-only vocabulary, boundary, lifecycle, and verification ownership | accepted startup behavior/non-goals |
| `docs/ai/27-e1-2-runtime-profile-startup/evidence/verify.txt` | Full saved canonical gate output | canonical verification evidence |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Valid minimal startup reports profile/config/effective-policy revisions and zero admitted capabilities | ✅ | `AgentProfileStartupTests.test_valid_profile_is_loaded_once_into_an_immutable_deny_all_snapshot`; public status fixture/schema |
| Missing, unsupported, invalid YAML, unsafe owner/mode, symlink, or unknown capability degrades deny-all with stable code | ✅ | `AgentProfileDegradedVoiceRegressionTests` disposable seven-case matrix |
| Every degraded case preserves local voice readiness and a bounded voice turn | ✅ | Same matrix asserts unchanged public five-component readiness and completes the actual `RealTurnController` STT → LLM → TTS path |
| Runtime edit cannot mutate pinned snapshot or authority | ✅ | `test_running_snapshot_is_pinned_until_a_new_process_composition` |
| Controlled restart loads a changed valid revision | ✅ | Same test composes a second runtime and observes only the new config revision |
| Runtime never repairs, rewrites, restores, or migrates invalid source | ✅ | Source inventory is byte/type/mode identical before/after every degraded load |
| Status/logging contains no content, credentials, environment, or raw errors | ✅ | Closed schemas plus unexpected-exception normalization/log assertion |
| No daemon/listener/service/restart/write exception | ✅ | In-process frozen dependency only; sandbox regression asserts unchanged `ProtectHome=read-only` and cache-only `ReadWritePaths` |
| Disposable regression evidence uses no real profile | ✅ | All profile roots use `TemporaryDirectory` plus injected `AgentUserContext` |
| Canonical `./verify` passes | ✅ | `evidence/verify.txt`: `RESULT: PASS`, 326 hermetic tests, 9 local-socket tests, runtime contract, 88 Vitest tests, typecheck/builds, actual-LiveKit Firefox smoke |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Canonical 90-second PR gate; full final output saved in `evidence/verify.txt` |
| `git diff --check` | PASS | No whitespace errors |

## Unresolved uncertainty

- None. Physical microphone/audibility and canonical-host tiers remain the pre-existing non-PR acceptance tiers and are not claimed by E1.2.

# Fix Report: 71-agent-environment-live-acceptance

**Source:** Promoted live acceptance report at `/home/priney/Projects/mymate/data/voice-agent-v2-agent-environment-live-acceptance-resume-71/report.md`  
**Status:** ✅ pass  
**Scope stayed small:** yes

## Clarification decisions

- Keep `stand agent-image prepare` strict over the canonical locked bytes and original file modes.
- Give runtime a separate contract loader that admits either the complete original mode set or only the complete deterministic `stand_dev._make_immutable` read-only transform; mixed/per-file normalization is rejected.
- Preserve the prepared ledger context revision and every fresh exact image/container inspection rather than bypassing either boundary.

## Changed behavior

- Before: immutable release construction changed locked context modes from `0644/0755` to `0444/0555`, then AgentEnvironment runtime rejected its own exact release as `image_context_invalid` before container resolution.
- After: runtime accepts the identical locked content/spec/revision under the complete deterministic immutable-release mode transform and reaches the selected prepared image/container path.
- Unexpected or mixed modes, changed bytes, a changed valid context revision, and image-inspect mismatch still return explicit unavailable truth before any container mutation.
- Native preparation remains original-mode strict and retains its existing one-build/idempotency/no-registry/no-deletion contract.

## Files changed

| File | Change |
|---|---|
| `src/voice_agent_v2/agent_environment_image.py` | Separated strict prepare-time context loading from narrowly normalized immutable-release runtime loading. |
| `src/voice_agent_v2/agent_environment.py` | Uses the runtime custody loader before reading the prepared ledger and freshly inspecting the exact image. |
| `tests/test_agent_environment.py` | Added fake-Docker end-to-end preparation → actual immutable transform → instance V2 profile → exact container-resolution regression and pre-mutation negative matrix. |
| `README.md` | Documented original-mode preparation and exact whole-release runtime normalization. |
| `docs/architecture.md` | Recorded the prepare/runtime custody split and closed failure boundary. |
| `docs/evidence/local-native-agent-environment-image.md` | Updated deterministic evidence ownership and nonclaims. |

## Validation

| Command | Result | Notes |
|---|---|---|
| `PYTHONPATH=src python -m unittest -v tests.test_agent_environment.ImmutableReleasePreparedImageTests tests.test_agent_environment_image.NativeImagePreparationTests` | ✅ pass | 5 tests; exact immutable transform plus prepare-time strictness. |
| `PYTHONPATH=src python -m unittest -v tests.test_agent_environment` | ✅ pass | 22 AgentEnvironment/AgentRun/config tests. |
| `./verify` | ✅ pass | Run exactly once: 426 hermetic Python + 9 local-socket tests, Vitest 91, typecheck, both builds, and actual-LiveKit Firefox smoke; `RESULT: PASS`. Full ignored log: `artifacts/agent-environment-live-acceptance-resume-71-verify.log`. |
| `git diff --check` | ✅ pass | No whitespace errors before the canonical gate. |

## Follow-ups

- After merge, resume the authorized live acceptance from strict V2 profile initialization and repeat the real AgentRun, same-ID reuse, and one ordinary dev stop/start proof.
- This Ship makes no live Docker build, AgentRun, deployment, reboot, Web/provider, credential, #86, #72, #73, main, or SemVer claim.

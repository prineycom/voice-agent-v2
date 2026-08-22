# Review: 71-agent-environment-final-live

**Source:** promoted private-root custody correction and current diff
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| — | Audited the descriptor-relative directory owner, mode-admission predicate, status/lock/atomic-write call order, first-use error translation, adversarial tests and operator/architecture claims against `promotion.md`. | No critical, important or minor finding remains. | Proceed to the single canonical gate and exact-head delivery. | — |

## Fixed issues during implementation review

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Safe-broad mode admission initially allowed group/other-writable modes. | `src/voice_agent_v2/agent_environment.py`, `tests/test_agent_environment.py` | Admission now requires owner `rwx`, rejects group/other write and special bits, and the `0777` regression proves zero chmod/Docker/registry mutation. |
| Atomic registry writes still carried the historical `parents=True`/path-`chmod` fallback. | `src/voice_agent_v2/agent_environment.py` | `_atomic_private()` now consumes the same already-owned parent with `create=False`; it cannot recreate or broaden the private state root. |
| Standalone fixtures created trusted parent directories with ordinary umask modes. | `tests/test_agent_environment.py`, `tests/test_stand_dev.py` | Fixtures now model the exact instance-owned `0700` parent, leaving only the product-owned state root/client leaf to the new owner. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Intermittent live Silero synthesis failures | Explicitly separate from this promoted correction; no TTS change is authorized. |
| Fresh physical AgentEnvironment acceptance | Requires a merged correction and separate live authority; deterministic results cannot substitute. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| Focused AgentEnvironment/stand/architecture matrix | ✅ pass | 141 tests, including unsafe custody and typed realtime failure. |
| `python -m compileall -q src tests` | ✅ pass | Sources compile. |
| `git diff --check` | ✅ pass | Clean diff. |
| `./verify` | ✅ pass | Exactly once: 445 hermetic Python, 9 local-socket, Vitest 91, typecheck, both builds and Firefox/actual-LiveKit smoke; `RESULT: PASS`. |

## Recommendations

- Commit and push only `fm/voice-agent-v2-agent-environment-final-live-71`, open a focused PR, and require green exact-head CI.
- After merge, restart issue #71 physical acceptance from one newly authorized first turn; do not borrow prior failed-turn or uncreated-environment evidence.

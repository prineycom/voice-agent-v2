# Fix Report: voice-agent-v2-update-s15-node-builder-compatibility

**Source:** PR #63 exact-head CI run `32359751203`
**Status:** ✅ pass
**Scope stayed small:** yes

## Clarification decisions

- Treat the post-smoke exit `124` as exhaustion of the repository-owned canonical deadline, not a browser or LiveKit behavior failure.
- Preserve the complete canonical phase order and every existing phase-specific behavior/cleanup bound.

## Changed behavior

- Before: the hosted run entered the final smoke with `24.1s` remaining, reported smoke PASS after about `23.6s` of phase wall time, then the 90-second outer verifier deadline sent `TERM` before final owner settlement could complete.
- After: the same canonical surface owns a 120-second monotonic budget. The CI step/job ceilings are 3/4 minutes so the verifier retains responsibility for its bounded TERM/KILL cleanup instead of racing a GitHub ceiling.
- The 15-second functional browser bound, 10-second browser cleanup bound, leak checks, tests, typecheck, builds, and actual-LiveKit smoke are unchanged.

## Files changed

| File | Change |
| ---- | ------ |
| `verify` | Raises the canonical outer process budget from 90 to 120 seconds. |
| `scripts/verify.py` | Raises the matching monotonic PR phase deadline to 120 seconds. |
| `.github/workflows/behavioral.yml` | Keeps CI ceilings outside the verifier deadline and cleanup reserve. |
| `scripts/verify_firefox_livekit.py` | Corrects the comment describing the enclosing canonical deadline. |
| `docs/testing.md` | Documents the corrected canonical budget without changing tier ownership. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | 100 launcher/release tests, 390 hermetic Python tests, 9 local-socket tests, SDK contract, 88 Vitest tests, typecheck, both builds, and Firefox/actual-LiveKit smoke; `canonical_pr_gate: PASS deadline_seconds=120 unexpected_skips=0`. |
| `git diff --check` | PASS | No whitespace errors. |

## Follow-ups

- Exact-head GitHub CI is required after push.

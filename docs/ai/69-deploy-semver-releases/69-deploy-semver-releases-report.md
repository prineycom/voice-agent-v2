# Do Report: 69-deploy-semver-releases

**Source:** https://github.com/prineycom/voice-agent-v2/issues/69
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py` | Adds exact main SemVer-tag resolution, durable first-target records, moved-tag refusal, target-release external configuration validation, post-activation failure visibility, and explicit older-tag rollback through the same deploy path. | SemVer-only admission; exact SHA; immutable tag target; pre-switch validation; visible failure; manual rollback. |
| `scripts/stand.py` | Restricts main CLI deployment to an explicit remote version tag and keeps local SHA deployment dev-only. | Reject branches, SHAs, missing versions, and automatic latest behavior. |
| `tests/test_stand_dev.py` | Adds repository-fixture smoke paths for strict admission, annotated-tag peeling, SemVer-named branch rejection, moved tags, failed pre-switch validation, visible failed readiness, and deliberate older-tag rollback. | Focused deterministic smoke coverage. |
| `README.md`, `docs/architecture.md` | Documents the main tag/SHA identity boundary, durable observation, activation order, diagnostics behavior, and no-auto-latest/no-auto-rollback policy. | Operator interface and explicit non-goals. |
| `artifacts/issue-69-verify.log` | Preserves full canonical verification output. | Delivery evidence. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Exact `vMAJOR.MINOR.PATCH` only, resolved to one full commit SHA | ✅ | Strict no-leading-zero SemVer admission; exact tag-namespace lookup; annotated fixture peels through `FETCH_HEAD^{commit}`; local main SHA is closed. |
| Durable moved-tag refusal | ✅ | First observed tag target is an fsynced mode-`0600` external record created with exclusive creation; a later different commit fails before build or selection. |
| Target external configuration validated before activation | ✅ | Completed target manifest, runtime entrypoints, strict main/dev configuration, listener/credential isolation, and target launcher environment are checked before atomic `current` replacement. |
| Older accepted tag is manual rollback; no automatic rollback | ✅ | The ordinary main deploy path revalidates the recorded older tag and explicitly selects its existing immutable release; no latest lookup or compensating pointer write exists. |
| Failed post-activation readiness remains visible | ✅ | Pointer selection precedes readiness; failure reports `release selected but readiness failed`, while status retains the failed SHA and `not-ready` until a later explicit deployment. |
| Focused deterministic coverage | ✅ | Four new main-release fixture tests cover admission/exact resolution, moved tags, pre-switch validation, post-switch failure, and older-tag rollback without touching real stand state or remote tags. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Canonical 90-second PR gate; 404 Python tests, 88 Vitest tests, typecheck/builds, and actual-LiveKit Firefox smoke passed. Full output: `artifacts/issue-69-verify.log`. |

## Unresolved uncertainty

- —

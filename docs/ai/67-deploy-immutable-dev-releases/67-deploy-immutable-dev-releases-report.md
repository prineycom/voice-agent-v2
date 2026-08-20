# Do Report: 67-deploy-immutable-dev-releases

**Source:** https://github.com/prineycom/voice-agent-v2/issues/67
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py` | Ordinary controller clone, safe remote-ref resolution, complete immutable release construction and reuse | All deployment criteria |
| `scripts/stand.py` | Adds `stand deploy dev <ref>` while retaining `--local` | Remote deployment interface |
| `requirements-stand-production.lock` | Dedicated pinned per-release production Python lock | Production Python environment |
| `tests/test_stand_dev.py` | Hermetic Git/build/systemd seams for the remote-release transaction | Focused smoke coverage |
| `README.md` | Documents clone ownership, remote deployment, artifacts, and failure boundary | Operator contract |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| One ordinary external controller clone; no token handling | ✅ | `StandDevTests.test_init_creates_external_private_state_and_one_ordinary_controller_clone` |
| Remote branch/tag/SHA resolves before construction | ✅ | Branch/tag/full-SHA deployment smoke resolves the returned 40-character commit before construction |
| Archive-only complete production release and manifest | ✅ | Remote branch smoke proves absent `.git`, production `dist`, Python environment, manifest, and temporary pre-promotion state |
| Exact-SHA reuse and no automatic deletion | ✅ | `StandDevTests.test_remote_tag_and_full_sha_reuse_the_completed_release` |
| Failure preserves completed releases and `dev/current` | ✅ | Fetch, unresolved-ref, frontend-build, and manifest failure smoke preserves prior state |
| Focused deterministic smoke coverage | ✅ | `tests.test_stand_dev` uses disposable Git repositories and fake build/systemd commands |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | Full canonical output: `artifacts/issue-67-verify.log`; 397 hermetic Python tests and all PR phases passed. |

## Unresolved uncertainty

- —

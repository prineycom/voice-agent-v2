# Review: voice-agent-v2-update-s2-fresh-install

**Source:** Current I2 implementation diff
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Critical | `launcher/install.cjs` originally checked only the nearest existing path component | A symlinked ancestor could violate canonical no-follow custody | Inspect every existing absolute path component | Fixed |
| Critical | Resume originally trusted only a completed installation sequence and accepted an open journal phase | Metadata rollback or an unknown durable phase could make recovery ambiguous | Trust the journal sequence and close phase/time/identity fields | Fixed |
| Important | Immutable release modes prevented fixture cleanup | Canonical verification reported private artifact leakage despite functional success | Make only the exact disposable test tree writable in its after-hook | Fixed |
| Important | Unsupported-host text named an unavailable doctor flag | The actionable message was not honest | Point to the supported read-only `voice-agent doctor` command | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Complete no-follow ancestor custody | `launcher/install.cjs` | Foreign/symlinked preservation case passes |
| Closed monotonic recovery journal | `launcher/install.cjs`, contracts | Interruption/retry cases pass; rollback verifier retained |
| Exact disposable cleanup | `launcher/test/install.test.cjs` | Canonical leak owner passes |
| Honest unsupported-host guidance | `launcher/install.cjs` | Unsupported host remains zero-mutation |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Production channel/key/artifact provisioning | Explicit non-goal and separately owned release-maintainer work |
| Real host/systemd/reboot/physical acceptance | Explicit separate evidence tier; no live-stand mutation permitted |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Full repository-owned gate; output saved in the task artifact |

## Recommendations

- Proceed to the commit boundary and direct PR delivery.
- Keep I3 update/rollback, legacy adoption and real-host acceptance out of this PR.

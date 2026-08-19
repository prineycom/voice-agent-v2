# Review: voice-agent-v2-update-s3-transactional-update

**Source:** `docs/ai/voice-agent-v2-update-s3-transactional-update/voice-agent-v2-update-s3-transactional-update-report.md`
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Critical | Initial `launcher/update.cjs` reads of release/config/unit files preceded complete no-follow descriptor custody | Could follow a swapped canonical leaf before rejection | Use the shared owner/inode/link/mode/no-follow reader for every authoritative leaf and inspect all canonical roots | Fixed |
| Critical | Initial production adapter inherited a short best-effort stop/start fallback | Did not prove the declared graceful stop/generation-gone/start-once boundary outside fixtures | Add exact bounded `quiesce`, stopped listener/process proof and one reset/start action to the system adapter | Fixed |
| Important | A crash/failure after durable `quiescing` but before actual stop was initially classified as post-quiesce solely by phase | Recovery could unnecessarily restart an unchanged healthy prior service | Treat service-stopped receipt plus an exact prior readiness probe as authoritative when the stop action is ambiguous | Fixed |
| Important | Candidate compatibility initially checked remaining bytes but not exact signed asset descriptor availability | Could activate after model/runtime identity drift | Require exact descriptor hashes and available/compatible facts after safe pre-GC | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| No-follow leaf custody | `launcher/voice-agent.cjs`, `launcher/update.cjs` | `readOwnedRegular` is exported and used for release record/manifest, config, unit, installation and snapshot reads |
| Production service transition | `launcher/install.cjs` | 75-second quiesce + bounded systemctl control, listener/process-gone probe and one start action |
| Ambiguous quiesce recovery | `launcher/update.cjs`, interruption matrix | Prior exact-readiness observation keeps pre-stop failures service-unchanged; stopped/uncertain failures restore |
| Exact assets | `launcher/update.cjs` | Descriptor hash/availability/runtime compatibility gate runs after pre-GC and before quiesce |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Real-host/power-cut proof | Explicit I8 non-goal; deterministic injected I3 scope only |
| Legacy selected-new/running-old import | Explicit I4 non-goal; canonical release split is supported without legacy adoption |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| Node launcher install/update/contract phase | Pass | 28 tests, including every observed durable write/action and 0/1/2/3/100 release inventory |
| `git diff --check` | Pass | No whitespace errors |
| `./verify` | Pass | Full output: `artifacts/update-s3-transactional-update-verify.log`; canonical gate reports no unexpected skips |

## Recommendations

- Commit the verified diff, then push/open the direct PR and wait for exact-head CI.

# Review: voice-agent-v2-legacy-adoption

**Source:** `docs/ai/voice-agent-v2-legacy-adoption/voice-agent-v2-legacy-adoption-report.md`
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Critical | Initial rollback stopped but did not disable the prepared user unit and could retain a committed `install.json` | A restored old system service could race the user service at boot or leave false canonical truth | Disable the user unit and remove uncommitted canonical selection/install receipt before exact prior restore | Fixed |
| Important | Initial mutator private inspection did not repeat legacy root/releases owner+mode checks | Discovery and mutation custody were not identical | Require exact mode-`0700` current-user canonical root/releases before pointers | Fixed |
| Important | Initial imported payload retained historical `release.json`, including its old absolute private config locator | Violated the content/path-minimal import boundary | Copy only the exact application inventory (whose historical digest already excludes `release.json`) and retain only metadata SHA-256 in the closed import record | Fixed |
| Important | Initial post-stop receipt did not freshly prove MainPID/listener absence | Could journal a quiesce action without proving generation loss | Probe system service and loopback listener before `legacy_stopped` receipt | Fixed |
| Important | Initial production Docker evidence derived a claimed daemon UID from socket ownership | Socket owner alone is not daemon/rootless identity | Require explicit-host query, rootless security option and owner/canonical user Docker data-root proof separately | Fixed |
| Important | Initial user-unit preparation wrote/reloaded but did not enable the unit | Successful adoption would not preserve boot lifecycle | Enable without starting while the old system service remains authoritative; make retry inspect exact unit+enablement | Fixed |
| Important | Initial adoption host preflight did not compare inspected user identity with the canonical layout identity | Could bind an injected/misreported host user to another layout | Reuse full installer host preflight and exact uid/name/home equality | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Exact failed-safe service restoration | `launcher/adopt.cjs`, `launcher/test/adopt.test.cjs` | Candidate unit disable, selection/install receipt removal, exact old readiness and double-failure tests |
| Root/import/path custody | `launcher/adopt.cjs`, `launcher/update.cjs`, tests | Exact owner/mode root checks; imported payload rejects historical record/path bytes; later GC revalidates inventory/unit |
| Quiesce and Docker proof | `launcher/voice-agent.cjs`, `launcher/adopt.cjs`, launcher fixtures | MainPID/cgroup/listener generation proof; explicit socket + rootless data-root identity |
| User service/host lifecycle | `launcher/install.cjs`, `launcher/adopt.cjs`, tests | Full host identity preflight; enable-without-start and idempotent prepared-state inspection |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Remove disabled historical system-unit bytes after success | Explicit I4 non-goal; retaining disabled exact bytes preserves forensic/recovery evidence and grants no service authority |
| Real-host Docker/systemd/reboot proof | Explicit later acceptance tier; this implementation task must not touch the live stand |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | Pass | Final full output: `artifacts/legacy-adoption-verify.log`; 33 launcher, 388 Python, 9 local-socket, 88 Vitest, typecheck/build and production Firefox/LiveKit smoke; zero unexpected skips |
| Final diff inspection | Pass | Obsolete checkpoint behavior absent vs `origin/main`; no private brief/report path introduced; `git diff --check` clean |

## Recommendations

- Commit the complete I4 diff, push only the feature branch, open the direct PR, and require terminal CI on the exact pushed head.

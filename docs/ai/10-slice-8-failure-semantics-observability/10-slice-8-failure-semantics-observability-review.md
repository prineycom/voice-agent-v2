# Review: 10-slice-8-failure-semantics-observability

**Source:** Issue #10, do report, current Slice 8 diff
**Status:** ✅ pass after autonomous important fixes authorized by the task

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Important | `livekit_runtime.py` initially derived selected-LLM liveness only from the startup snapshot. A llama.cpp crash after warm-up could admit another turn after the first failed request. | Process crash readiness/no-loop criterion. | Make the fixed provider own current liveness, drop it on non-cancellation failure, and let terminal cleanup publish unready/degraded health before later admission. | Fixed. |
| Important | `AvatarHostV1` counted malformed/stale input but continued to report `ready`; only the detailed numeric count changed. | Malformed avatar input needed a user-visible degraded state. | Report transient input degradation until a fully valid monotonic update/cancel and surface visual degradation in the steady indicator/System rows. | Fixed. |
| Important | Browser diagnostics originally normalized exception messages but accepted arbitrary server `stage`/`code` strings. A content-shaped value in those generic payload slots could be downloaded. | Default-log privacy criterion. | Close failure stage/code to bounded identifier syntax and normalize invalid values before reducer/diagnostic use. | Fixed. |
| Important | Initial timing coverage omitted cancellation latency and did not explicitly correlate the first programmatic browser signal. | Architecture §8 timing completeness. | Add cancellation latency to public/diagnostic/preregistered reports and emit one request/media-correlated browser signal observation; require it in the deterministic Firefox run. | Fixed. |
| Minor | Capture deletion used the instance path guard but did not revalidate its owned manifest; constructor manifest failure could leave an empty directory. | Retention/deletion hardening. | Reuse manifest-validated deletion and clean partial creation. | Fixed. |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Live selected-LLM readiness | `local_lfm.py`, `livekit_runtime.py`, `test_local_lfm.py` | Readiness becomes live after probe/success, false after selected-provider failure, and runner admission reports dead/unready without retry/fallback. |
| Avatar degraded consequence | `AvatarHost.ts`, avatar/state-mapping tests | Malformed input reports degraded/rejected count; the next valid input restores ready; the steady label shows DEGRADED. |
| Diagnostic string normalization | `voiceClient.ts`, `state.ts`, browser tests, observation scalar allowlist tests | Content-shaped stage/code/note values are absent from downloaded/default JSON. |
| Missing timing observations | `realtime.py`, `observability.py`, config, Timeline UI, `voiceClient.ts`, Firefox verifier | Cancellation metric is preregistered/reconstructable; deterministic Firefox requires correlated first signal without claiming audibility. |
| Capture deletion hardening | `diagnostics.py`, capture tests/CLI verifier | Manifest/path/symlink guard, partial-create cleanup, expiry and public CLI deletion pass. |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Physical microphone/audibility/Raspberry Pi evidence | Requires physical/manual environment; explicitly outside controlled proof and not fabricated. |
| Actual Tailscale interface shutdown | Would disrupt shared remote/network state and is prohibited by the safety contract. |
| Destructive OOM/shared model-service kill | Unsafe/destructive; controlled GPU/process semantics and disposable process loss are the honest boundary. |
| 20-turn full-stack acceptance percentiles | Current one-turn report is diagnostic only and labelled as such; physical/full-stack acceptance remains open. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | 237 network-denied Python behavioral tests. |
| `./verify-slice8` | PASS | Full matrix/privacy/readiness/capture/process/resource and focused browser gate. |
| `./verify-slice7` | PASS | Avatar/UI tests/builds. |
| `./verify-local-lfm` | PASS | Exact real local provider and cancellation/recovery. |
| `./verify-silero-kseniya` | PASS | Exact real two-worker focused gate. |
| `./verify-slice6` | PASS | Installed runtime, all browser tests/builds and official LiveKit/Firefox synthetic regression; first programmatic signal correlation passes. |
| Focused post-review Python/web tests and compile/typecheck | PASS | Selected-LLM liveness, avatar degradation, privacy normalization and no regressions. |

## Recommendations

- Commit the reviewed slice on the feature branch, then use the required no-mistakes pipeline to push/open the unmerged PR and wait only for CI green.
- Keep every exact physical/shared/destructive gap in the PR body; do not convert the one-turn synthetic report into a physical or percentile acceptance claim.

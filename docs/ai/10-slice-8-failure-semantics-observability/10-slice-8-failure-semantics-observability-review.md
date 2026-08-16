# Review: 10-slice-8-failure-semantics-observability

**Source:** Issue #10, do report, current Slice 8 diff
**Status:** ✅ pass after autonomous important fixes authorized by the task

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Important | `livekit_runtime.py` initially derived selected-LLM liveness only from the startup snapshot. A llama.cpp crash after warm-up could admit another turn after the first failed request. | Process crash readiness/no-loop criterion. | Make the fixed provider own current liveness/readiness and publish unready health before later admission, while preserving liveness for a responding contract failure. | Fixed. |
| Important | `AvatarHostV1` counted malformed/stale input but continued to report `ready`; only the detailed numeric count changed. | Malformed avatar input needed a user-visible degraded state. | Report transient input degradation until a fully valid monotonic update/cancel and surface visual degradation in the steady indicator/System rows. | Fixed. |
| Important | Browser diagnostics originally normalized exception messages but accepted arbitrary server `stage`/`code` strings. A content-shaped value in those generic payload slots could be downloaded. | Default-log privacy criterion. | Close failure stage/code to bounded identifier syntax and normalize invalid values before reducer/diagnostic use. | Fixed. |
| Important | Initial timing coverage omitted cancellation latency and did not explicitly correlate the first programmatic browser signal. | Architecture §8 timing completeness. | Add cancellation latency to public/diagnostic/preregistered reports and emit one request/media-correlated browser signal observation; require it in the deterministic Firefox run. | Fixed. |
| Minor | Capture deletion used the instance path guard but did not revalidate its owned manifest; constructor manifest failure could leave an empty directory. | Retention/deletion hardening. | Reuse manifest-validated deletion and clean partial creation. | Fixed. |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Live selected-LLM readiness | `local_lfm.py`, `livekit_runtime.py`, `test_local_lfm.py` | The initial fix established runtime-owned health and blocked admission after provider failure; the final `READY-002` row below owns the corrected transport-versus-contract split. |
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
| `./verify` | PASS | Network-denied cumulative Python behavioral suite after all follow-up regressions. |
| `./verify-slice8` | PASS | Full matrix/privacy/readiness/capture/process/resource and focused browser gate. |
| `./verify-slice7` | PASS | Avatar/UI tests/builds. |
| `./verify-local-lfm` | PASS | Exact real local provider and cancellation/recovery. |
| `./verify-silero-kseniya` | PASS | Exact real two-worker focused gate. |
| `./verify-slice6` | PASS | Installed runtime, all browser tests/builds and official LiveKit/Firefox synthetic regression; first programmatic signal correlation passes. |
| Focused post-review Python/web tests and compile/typecheck | PASS | Selected-LLM liveness, avatar degradation, privacy normalization and no regressions. |

## No-mistakes follow-up findings

The first no-mistakes run found additional accepted-scope misses. Pipeline commit `364a276` fixed correlation extraction/refusal handling (`OBS-001`), fail-closed ready-report parsing before timer/reconnect state changes (`READY-001`), typed degraded-state preservation during transport cleanup (`FAIL-001`), and full automatic-purge ownership validation (`DATA-001`). It also added an in-process expiry timer, which did not by itself satisfy the independent hard-TTL boundary.

The follow-up commit on top of the exact pipeline head closes every remaining review/document finding:

| Finding | Final correction |
| --- | --- |
| `READY-002` / `READY-003` / `READY-004` | Local LFM owns separate live/ready/compatible/reason state. Header/body I/O loss is dead/unready but remains independently compatible; a responding identity/protocol/health-contract failure is alive/unready/incompatible. Both block admission and success is the only compatibility recovery. |
| `OBS-002`–`OBS-010` | `dependency_class` is closed; controller, publication, and control-publish terminal failures use correlated common enrichment; reconstruction retains matrix/stage/code/state. Resource percentiles consume canonical samples, every distribution reports actual N, and terminal replacement counters remain exclusive. Admission counting includes a publication attempt that reaches an enriched terminal but excludes an abandoned/cancelled unannounced VAD candidate with no public event. Provider TTFT includes partial first reasoning-or-visible token while first visible remains separate. |
| `PRIV-001`–`PRIV-007` | [`architecture.md` §8.2](../../architecture.md#82-data-handling) owns the final retention contract. Only verified `/run/user/<uid>` tmpfs with lingering disabled is accepted; focused evidence exercises minimal-env monotonic expiry, four-slot custody across early deletion, locked owner-nonce deletion, and rejection of configurable persistent lookalikes. |
| `PERF-001` | `/proc`/`nvidia-smi` sampling is serialized off the asyncio event loop via background `to_thread` tasks; endpoint admission has a timing regression proving it does not wait for a slow sampler. |
| `FAIL-002` / `REVIEW-001`–`REVIEW-004` | Publication and cancellation behavior is owned by [`architecture.md` §7 and §8.1](../../architecture.md#7-failure-semantics). Focused regressions cover pre-publication rollback, post-`turn.listening` caller cancellation, media-send cancellation, and bounded control-send timeout/closure. Delayed resource samples retain their original epoch. |
| `DOC-001`–`DOC-003` | Late/duplicate control has the allowed `degraded` consequence via a dedicated session-scoped latch: strict drop, visible/diagnostic count, no phase/media action, and neither terminal interruption nor later valid turns clear it; only reset/new-session recovery does. |

## Recommendations

- Keep every exact physical/shared/destructive gap in the PR body; do not convert the one-turn synthetic report into a physical or percentile acceptance claim.

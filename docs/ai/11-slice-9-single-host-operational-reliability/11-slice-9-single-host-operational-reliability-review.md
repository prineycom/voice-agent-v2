# Review: 11-slice-9-single-host-operational-reliability

**Source:** Issue #11, do report, and complete current diff
**Status:** ✅ pass for implemented/safe scope; physical acceptance gaps remain explicit

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Critical | Recovered release gained seven `src/voice_agent_v2/__pycache__/*.pyc` files and `tmp`; kernel inotify identified the detached diagnostic expiry worker and helper environments | Rollback release became incompatible after its own tracer | Route all bytecode/temp state to explicit mutable roots and prove full inventory stability | Fixed |
| Critical | `ReleaseStore.rollback()` originally validated the current target before the previous target | A corrupt current release could block recovery to a valid previous release | Validate previous as the authority; tolerate/report incompatible current | Fixed with regression |
| Important | `release_tree_digest()` initially omitted directories/modes and payload digest was not bound into release ID | Empty-directory/mode mutation or rewritten metadata could weaken immutable-release identity | Inventory path/type/mode/target/size/hash and include digest in release identity | Fixed with regression |
| Important | `_require_exact_keys()` only required a subset | Unknown operations/release fields contradicted the documented closed contract | Reject missing and unknown top-level/manifest fields | Fixed with regression |
| Important | `Type=simple` plus an already-active identical unit could report installation while still running an old release | Partial activation could be followed by false success | Use `Type=notify`, exact post-route health, release-ID reconciliation, and wait-for-ready | Fixed; real disposable Type=notify run passed |
| Important | Host verifier wrote its transient counter below `ROOT/tmp` | Running it from a release could mutate the immutable tree | Use an explicit private mutable root | Fixed |
| Important | Configuration validation followed symlinks and separated metadata/read operations | Configuration could change across the validation read boundary | `O_NOFOLLOW`, one descriptor, `fstat`, bounded read | Fixed with regression |
| Important | NM-005 reread configuration after release validation | A credential change between reads could execute values other than the validated snapshot | Return the validated private snapshot through `execve` without a second read | Fixed with regression |
| Important | NM-006 could observe an old port owner/status and notify readiness without exact identity or a final child repoll | systemd could accept the wrong generation | Refuse occupied runtime ports; require exact build/release and live owned children immediately around READY | Fixed with regressions |
| Important | NM-007 cached LiveKit/local-LFM readiness at the public boundary | `/api/status` could briefly claim dead parent-owned processes were alive | Pass PID/start-time identities to the gateway and probe fresh process custody | Fixed with regression |
| Important | NM-008 persisted a secret-derived configuration verifier | Copied releases could support offline credential guesses | Persist no credential/verifier and add no independent key; require explicit validation/restart for credential changes | Fixed per Pasha decision |
| Minor | NM-009 accepted boolean, negative, non-finite, or out-of-range sustained values | Invalid evidence could pass scalar thresholds | Validate every measurement before percentile/growth calculations | Fixed with regression |
| Minor | NM-010 left the avatar failure wording ambiguous | Documentation could imply a forbidden replacement representation | State that MVP-eye failure stays visibly degraded with no alternate representation, selector, or fallback | Fixed |
| Minor | Release/link writes did not explicitly fsync their durability boundary | Crash durability relied on normal writeback | fsync payload/files/directories and atomic metadata/link parent directories | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Immutable runtime writer boundary | `verify`, `scripts/verify.py`, `diagnostics.py`, STT/TTS environments, systemd unit, tests | Pre/post inventory, one-factor controls, inotify writer identity, post-fix zero release events, recovered tracer twice |
| Recovery from corrupt current | `operations.py`, `tests/test_operations.py` | `test_rollback_recovers_to_verified_previous_when_current_is_corrupt` |
| Exhaustive payload-bound identity | `operations.py`, operations tests | Empty-directory/mode digest regression and final real A/B/no-op/rollback rehearsal |
| Honest service application | unit, `run_slice6.py`, `operations_cli.py`, startup/operations tests | Disposable user-systemd Type=notify returned ready at 23.102 s with exact release, then drained with zero orphan |
| Config and host-validator custody | `operations.py`, `verify_slice9_host.py`, tests | Symlink refusal, one validated execution snapshot, no persisted credential verifier, and final canonical-host gate |
| Exact readiness and health custody | `run_slice6.py`, `slice6_config.py`, `livekit_runtime.py`, tests | Exact build/release match, occupied-port refusal, READY child repolls, fresh LiveKit/LFM PID generations |
| Sustained/avatar boundary clarity | `operations.py`, ADR-0013, architecture, tests | Invalid scalar rejection and explicit no-alternate-avatar failure wording |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Physical reboot, physical voice/avatar soak, second tailnet device, Raspberry Pi | These are evidence gaps, not code-review fixes; performing them was unsafe or physically unavailable in this lane and they remain unclaimed |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | ✅ | 310 behavioral tests, deterministic trace/PCM |
| `./verify-slice6` | ✅ | Runtime contract, 87 web tests, builds, Firefox/LiveKit synthetic media |
| `./verify-slice7` | ✅ | 86 focused web tests/builds |
| `./verify-slice8` | ✅ | 88 Python + 68 web controlled failure/privacy checks |
| `./verify-slice9` | ✅ | 43 Python + 49 web operational checks |
| `./verify-local-lfm` | ✅ | Exact real local LFM runtime/model/parallel/cancellation |
| `./verify-silero-kseniya` | ✅ | Exact real Silero/Kseniya cache/runtime |
| `verify_slice9_host.py` | ✅ | 13 artifacts, one recovery, next blocked, production unit untouched |
| Real disposable A/B/no-op/rollback + Type=notify | ✅ | Exact release readiness, repeated tracer, byte-identical inventory, graceful zero-orphan stop |
| NM-005..NM-010 focused regressions | ✅ | 38 operations/startup tests plus 23 pinned-runtime readiness/health tests |
| `git diff --check` | ✅ | No whitespace errors |

## Recommendations

- Commit the reviewed Slice 9 implementation as one vertical slice.
- Keep physical reboot/voice/avatar/tailnet-device/Raspberry Pi evidence open and do not claim core MVP sign-off.
- Run the configured no-mistakes/PR gate only when firstmate authorizes that separate ship step.

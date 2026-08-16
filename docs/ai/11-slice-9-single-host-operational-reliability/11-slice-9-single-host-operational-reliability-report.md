# Do Report: 11-slice-9-single-host-operational-reliability

**Source:** https://github.com/prineycom/voice-agent-v2/issues/11
**Parent:** Voice Agent v2 roadmap Slice 9
**Status:** ⚠️ partial — implementation and safe host gates pass; explicitly physical acceptance remains unperformed

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `config/operations-v1.json` | Fixed component, lifecycle, artifact/runtime, cache/disk/release, contract, and sustained thresholds | compatibility, bounds, no fallback |
| `src/voice_agent_v2/operations.py`, `operations_cli.py`, `voice-agent-ops` | Fail-closed validation, payload-bound immutable releases, no-op deploy, status/install, verified rollback, sustained evaluator, runtime execution | deploy, rollback, build report, disk/cache, secrets |
| `ops/systemd/voice-agent-v2.service` | Non-root single unit, ordered custody, exit-2 incompatibility, one completed recovery window, hard-stop boundary, read-only release | boot/restart/drain/no orphan |
| `scripts/run_slice6.py`, `run-review-stand` | Role-aware start/stop, graceful drain, direct-child/readiness failure exit, exact build propagation | lifecycle, recovery, reporting |
| `src/voice_agent_v2/livekit_runtime.py`, `slice6_gateway.py`, `slice6_config.py` | Admission drain, fresh public five-component health, build/release identity | readiness and build report |
| `src/voice_agent_v2/diagnostics.py`, `local_stt.py`, `local_tts.py`, `verify`, `scripts/verify.py` | Explicit external mutable bytecode/temp state and focused recovered-release tracer | immutable release, rollback tracer |
| `contracts/public-operational-status.v1.schema.json`, fixture, `contracts/README.md` | Versioned public operational report | status contract |
| `scripts/verify_slice9.py`, `verify_slice9`, `scripts/verify_slice9_host.py` | Controlled lifecycle/resource gate and canonical-host exact-cache/disposable-systemd gate | deterministic and host validation |
| `tests/test_operations.py`, `test_slice9_runtime.py`, related Slice 6/8 tests | Release, compatibility, runtime, shutdown, schema, recovered-tracer/inventory regressions | executable acceptance |
| `docs/adr/0013-systemd-bounded-single-host-operations.md`, `docs/architecture.md`, `docs/roadmap.md`, `CONTEXT.md`, `README.md`, `AGENTS.md` | Accepted ownership, operator procedure, scope, nonclaims | documentation |
| `docs/evidence/slice-9-single-host-reliability.md` | Content-free host/systemd/failure/rollback/writer-diagnosis evidence and exact gaps | evidence |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| One bounded non-root boot/process owner | ✅ | Tracked system unit; disposable real user-systemd run completed one recovery and blocked the next at `start-limit-hit` |
| Exact configuration/artifact/runtime compatibility | ✅ | Canonical host validated 13 selected artifacts, pinned Python packages, mode-0600 config, tailnet identity, and local/no-fallback policy |
| Graceful ordered drain and no orphan inference | ✅ | Real full stack reached ready, stopped in declared order, preserved unrelated Serve/443, and left no project process or owned route |
| One-at-a-time local LLM/LiveKit/gateway/STT/TTS loss | ✅ | Each reached bounded failed state and complete cleanup; no invented provider process or inference retry |
| Cache/disk/release bounds | ✅ | Fixed limits, non-destructive pressure refusal, retained fixture unchanged, no automatic cleanup |
| Exact public build/readiness reporting | ✅ | Versioned `/api/status` returned matching loopback/tailnet ready report with five components, build/release, local/no-fallback, avatar identities |
| Idempotent deploy and incompatible refusal | ✅ | Identical release returned no-op; forbidden `LITELLM_*` exited 2 without pointer movement |
| Verified rollback and deterministic tracer | ✅ | Payload-bound A/B rollback revalidated restored A; direct and two recovered tracer outputs matched; full path/hash/mode/mtime inventory stayed byte-identical |
| Immutable runtime boundary | ✅ | Before/after inventory plus kernel inotify identified minimal-env Python writers; all controls moved outside release; post-fix inotify saw zero release events and real deployed runtime preserved inventory |
| Sustained acceptance evaluator | ✅ | 20/20 controlled metadata-only turns met all configured scalar thresholds |
| Physical reboot and boot recovery | ⚠️ open | Deliberately not performed: clean committed durable continuation and no-other-worker safety gate were not provable |
| Real post-rollback microphone/voice, physical 20-turn soak, second device, Raspberry Pi | ⚠️ open | Explicitly unclaimed; synthetic/startup evidence is not substituted |

## Validation

| Command/check | Result | Notes |
| --- | --- | --- |
| `./verify` | ✅ | 310 Python behavioral tests; deterministic trace/PCM hashes pass |
| `./verify-slice6` | ✅ | Installed runtime, 87 web tests, production/review build, Firefox/LiveKit synthetic media regression |
| `./verify-slice7` | ✅ | 86 focused web tests and production/review builds |
| `./verify-slice8` | ✅ | 88 Python and 68 focused web tests; controlled privacy/failure/resource gate |
| `./verify-slice9` | ✅ | 35 Python and 49 focused web tests; deterministic operations gate |
| `./verify-local-lfm` | ✅ | Exact model/runtime, parallel slots, context, streaming, cancellation recovery |
| `./verify-silero-kseniya` | ✅ | Exact real cache/model/runtime focused verification |
| `scripts/verify_slice9_host.py` | ✅ | 13 artifacts; exact runtime/config/cache/tailnet; one restart, next blocked; production unit untouched |
| Disposable real release A/B/no-op/rollback | ✅ | Final evidence IDs and complete procedure recorded in Slice 9 evidence |
| Deployed-release full runtime | ✅ | Ready five-component status, graceful stop, zero orphan, complete inventory unchanged |
| `git diff --check` | ✅ | No whitespace errors |

## Unresolved uncertainty

- Product system unit installation plus a physical reboot was not authorized as safe from the dirty disposable implementation lane.
- Real post-rollback microphone → Whisper → local LFM → Kseniya playback, 20 physical turns, physical cancellation/avatar measurements, second-device tailnet voice, and Raspberry Pi rendering remain open.
- Silero private/noncommercial licensing and separate legal approval remain required by ADR-0009.

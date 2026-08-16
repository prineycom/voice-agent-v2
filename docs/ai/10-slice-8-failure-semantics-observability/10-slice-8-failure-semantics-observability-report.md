# Do Report: 10-slice-8-failure-semantics-observability

**Source:** https://github.com/prineycom/voice-agent-v2/issues/10
**Parent:** —
**Status:** ✅ pass for controlled/safe Slice 8 scope; exact physical/shared/destructive gaps remain unclaimed

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/observability.py`, `contracts/{observation,health-readiness}.v1.schema.json`, fixtures, `config/observability-v1.json` | One typed owner for content-free observation/readiness schemas, full failure mapping, resource sampling, timeline reconstruction, preregistered nearest-rank percentiles and slow-stage report. | Correlated timeline; compatible readiness; complete failure matrix; percentile/resource computation. |
| `src/voice_agent_v2/diagnostics.py`, `diagnostic_expiry.py`, `manage-diagnostics`, `scripts/manage_diagnostics.py`, `slice6_config.py`, `.env.slice6.example` | Metadata trace v1 bounds/privacy, explicit opt-in outside-Git/private-runtime content capture, TTL/size/file/mode limits, manifest ownership, detached hard-expiry worker and public deletion path. | Default privacy; bounded capture off by default; independent expiry and exercised deletion. |
| `realtime.py`, `livekit_runtime.py`, `local_stt.py`, `local_lfm.py` | Component health reports, admission compatibility, public failure dispositions, timing/provider/usage/queue/drop/cancellation/resource metadata, model load/unload, dead-readiness drop, no-fallback invariants, opt-in capture integration. | Distinct liveness/readiness; one correlated turn; provider/transfer/usage/count/resource facts; bounded failure/recovery. |
| `web/src/state.ts`, `voiceClient.ts`, System/Timeline/UI mapping and styles | Strict health parsing, unavailable/degraded/retrying/interrupted reducer states, seven component rows with separate liveness/readiness/compatibility, complete per-turn timeline/provider/queue/resource display, exception-message privacy removal. | User-visible consequences; System/Timeline boundary; privacy-safe browser diagnostics. |
| Python/browser tests, `scripts/verify_slice8.py`, `verify-slice8` | Full controlled matrix, alive-but-incompatible readiness, trace/report/capture/resource tests, real disposable process loss/recovery bound, safe resource pressure, visible browser failure states. | Executable/controlled validation for every architecture row and safe real-process/resource cases. |
| `docs/evidence/slice-8-failure-observability.md`, `architecture.md`, `roadmap.md`, `README.md`, `CONTEXT.md`, `contracts/README.md`, `AGENTS.md` | Architecture ownership, public state matrix, privacy/retention rules, example redacted report, validation results and exact nonclaims. | Evidence required before Slice 9; durable project knowledge. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| One metadata-only correlated utterance → terminal timeline | ✅ | `./verify-slice8` emits/reconstructs endpoint, STT final, selected-LLM first token/completion, TTS first audio, completion, terminal and slowest stage solely from `voice-agent.observation.v1`. |
| Every architecture §7 row has controlled validation and public consequence | ✅ controlled | 12/12 exact row names in `FAILURE_MATRIX`; executable dispositions and controller/avatar/browser faults assert unavailable/degraded/retrying/interrupted. Late/duplicate input is a soft degraded/count-only consequence with no phase/media action. |
| Readiness differs from liveness and detects incompatibility/unloaded state | ✅ | A responding selected LLM with incompatible identity is alive/unready/incompatible; transport loss is dead/unready; both block admission and the browser rejects false-ready reports. Actual runner checks warmed processes/exact identities/two TTS workers. |
| No silent provider/model/cloud STT/TTS/auth/wake/avatar change | ✅ | Every disposition closes six change flags false; health contract fixes local mode/transfer/fallback/STT/TTS/auth/wake/avatar facts; existing active composition unchanged. |
| Bounded retry/recovery; no admission/restart loop | ✅ | Browser reconnect publications bounded by 5-second/10-attempt window; inference request retry and product automatic service restart are zero. Disposable real process permits one test-only recovery and blocks a second restart. |
| Preregistered latency/resource percentiles and slow stage computable | ✅ diagnostic | `config/observability-v1.json`; nearest-rank p50/p95/p99 report with sample count. A one-turn example is diagnostic only; 20-turn acceptance claim remains open. |
| Content capture off by default, bounded/outside Git, deletion exercised | ✅ | [`architecture.md` §8.2](../../architecture.md#82-data-handling) owns the exact lifetime-runtime, custody, expiry, and deletion contract; the focused evidence exercises its persistent-root rejection and, when the verified runtime is available, public early deletion. |
| Safe real process/resource validation | ✅ bounded | Two disposable process losses, one bounded recovery, second blocked; exact 16-MiB/100-ms safe pressure with serialized background `/proc`/read-only GPU sampling. A slow-sampler regression proves endpoint admission does not wait. |

## No-mistakes follow-up

The first pipeline head `556d57eb` fixed `OBS-001`, `READY-001`, `FAIL-001`, and `DATA-001`; its fix review then reported the accepted `READY-002`, `OBS-002`, `PRIV-001`, `PERF-001`, and `DOC-001`. The follow-up pipelines refined those through `READY-004`, `OBS-007`, `PRIV-006`, and `DOC-003`. The final boundary classifies transport versus responding contract health independently, reconstructs every failure through one enrichment path, measures first reasoning-or-visible provider token separately from first visible, deduplicates resource samples, enforces minimal-env monotonic expiry plus file-locked guardian custody/TOCTOU-safe deletion, removes resource subprocess work from asyncio, and latches late/duplicate `degraded` state until reset/new session. The review artifact records each correction.

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `no-mistakes doctor` | PASS | Shared daemon healthy; not restarted/updated. |
| `./verify-slice8` | PASS | 54 Python focused tests, safe process/resource/privacy verifier, 67 focused browser tests; 12-row matrix complete. |
| `./verify` | PASS | 251 Python behavioral tests; network-denied deterministic root trace. |
| `./verify-slice7` | PASS | 84 focused avatar/UI/browser tests plus production/review builds; replay hash unchanged. |
| `./verify-local-lfm` | PASS | Exact local identity/hashes/2×32K slots, parallel/cancellation/recovery; no cloud/fallback. |
| `./verify-silero-kseniya` | PASS | Exact-cache two-worker/native-48 focused real check; physical claims explicitly absent. |
| `./verify-slice6` | PASS | 251 Python + installed runtime + 86 web tests/builds + deterministic Firefox/official LiveKit regression. Physical audibility not claimed. |
| `git diff --check` / Python compile / TypeScript typecheck | PASS | Clean whitespace/syntax/type boundaries at implementation checkpoint. |

## Unresolved uncertainty

- Physical microphone capture/cleanup, physical audibility/Kseniya quality, physical interruption timing, Raspberry Pi rendering/performance, and 20-turn full-stack acceptance percentiles remain open from prior slices.
- Actual Tailscale interface disconnect was not run because it would disrupt shared remote access/network state; controlled remote-path semantics only.
- Destructive RAM/VRAM exhaustion, actual production-model OOM, and killing shared active services were not run; controlled GPU/process loss semantics plus disposable real process and safe resource pressure only.
- Cloud credential/allowlist/privacy failure is a controlled inactive-mode row because the accepted active provider is fixed local and has no cloud credential/endpoint surface.
- No legacy material was inspected or used.

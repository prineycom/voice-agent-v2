# Slice 8 failure semantics and privacy-safe observability evidence

- **Issue:** [#10](https://github.com/prineycom/voice-agent-v2/issues/10)
- **Checkpoint date:** 2026-08-16
- **Status:** deterministic fault/privacy/readiness/browser boundaries and bounded safe process/resource cases pass; physical/shared-network/destructive cases remain explicitly unclaimed
- **Legacy source:** none inspected or used

## Observation and failure architecture

`src/voice_agent_v2/observability.py` is the single typed owner of:

- `voice-agent.observation.v1` scalar-only metadata observations;
- `voice-agent.health-readiness.v1` component liveness/readiness/compatibility reports;
- the complete architecture §7 failure-to-user-state map;
- safe resource snapshots, timeline reconstruction, nearest-rank percentiles, and slow-stage derivation.

Existing boundaries remain in place: `PrivacySafeTrace` stores bounded outside-Git JSONL, `realtime-control.v2` carries public health/failure/timing/provider/count/resource payloads, `voiceReducer` owns client state, and the existing System/Timeline tabs render it. No telemetry server, generalized event bus, control plane, hosted vendor, alternate provider, or TTS/avatar selector was added.

The active composition is unchanged: local LiveKit, Whisper large-v3-turbo, fixed local LFM2.5 Q4_K_M/llama.cpp, exact Silero `v5_5_ru` / `kseniya`, renderer-agnostic avatar host with the selected MVP eye/static safety representation, tailnet authorization, no wake, and no fallback. Health reports close these facts as `provider_mode=local`, `external_transfer=false`, `automatic_fallback=false`, local STT/TTS, `auth_boundary=tailnet`, `wake_enabled=false`, and `selected_avatar_module=mvp-eye-svg-v1`.

## Readiness compatibility report

The server report separates liveness, readiness, compatibility, identity, contract version, reason code, and bounded recovery attempts for LiveKit, controller, STT, selected LLM, and TTS. The browser System panel combines those five hard-capability records with the existing avatar-host health boundary, rendering separate avatar-host and active-module rows with the same distinctions.

A controlled five-component server report with an alive selected-LLM process but wrong/incompatible model contract yields `liveness=alive`, `readiness=unready`, `compatible=false`, and overall `unready`. The browser rejects a report that relabels that same component set as ready. Actual resident readiness additionally checks warmed Whisper process custody, the exact local-LFM readiness identity/no-transfer/no-fallback record, and two live compatible Silero workers. Local-LFM transport loss reports dead/unready; a responding process with an identity/contract failure remains alive but becomes unready/incompatible. Either blocks admission without routing to another provider/model/backend.

## Complete controlled fault matrix

Run `./verify-slice8`. Every row invokes the executable failure mapper and asserts its public consequence, retry/admission bound, and unchanged provider/privacy/auth/wake/avatar-selection facts. Existing controller/adapter/avatar/browser suites exercise the corresponding turn, cancellation, malformed-input, render-failure, disconnect, and duplicate-event behavior.

The authoritative dispositions and admission/retry consequences are in [`architecture.md` §7.1](../architecture.md#71-executable-user-state-mapping); this evidence records only how every owned matrix row was exercised.

| Matrix row | Injection/validation | Result |
| --- | --- | --- |
| `livekit_unavailable` | Bounded reconnect plus controlled media-publication setup/identity/stream, cancellation, failure, and timeout | Pass against §7.1; publication setup emits the enriched correlated failure before session degradation/closure, control transport records an equivalent terminal replacement even when it cannot publish that terminal, and both refuse later inference admission. |
| `microphone_capture_failure` | Existing recorder/stream/setup/cleanup failure seams | Pass against §7.1; retention truth remains explicit. |
| `stt_unavailable` | Controlled adapter/process failure | Pass against §7.1. |
| `selected_llm_failure` | Controlled selected-provider transport/identity/terminal failure | Pass against §7.1. |
| `cloud_configuration_invalid` | Controlled inactive-cloud configuration case | Pass against §7.1; no credential was sought in fixed local mode. |
| `tts_failure` | Controlled pool/readiness/synthesis/late-failure cases | Pass against §7.1. |
| `avatar_input_invalid` | Bounded host contract cases | Pass against §7.1. |
| `avatar_runtime_failure` | Controlled render-loop failure callback | Pass against §7.1. |
| `client_disconnect` | Existing session disconnect cleanup case | Pass against §7.1. |
| `local_inference_crash` | Controlled GPU failure mapping plus real disposable process loss | Pass against §7.1. |
| `tailscale_unavailable` | Controlled remote-path loss policy | Pass against §7.1. |
| `late_duplicate_event` | Strict gate replay plus reducer consequence | Pass against §7.1; focused reducer cases retain the session latch across interruption, completion, reconnect, and later valid turns until reset/new session, with no dropped-event phase/media action. |

A safe real-process case starts a task-owned disposable Python worker, kills it, observes lost liveness, permits exactly one test-only recovery, kills it again, and proves the second restart is blocked. It touches no shared service. The real product runtime is stricter in Slice 8: it performs zero automatic inference request retry and zero automatic service restart; service supervision remains Slice 9. This cannot form an admission/restart loop.

## Correlated timeline and percentile report

`config/observability-v1.json` preregisters nearest-rank p50/p95/p99 for:

- endpoint → STT final;
- selected-provider time to first non-empty reasoning-or-visible token (separate from first visible content) and completion;
- TTS time to first accepted audio;
- cancellation latency when interruption occurs;
- total turn;
- CPU, host RAM, process RSS, GPU VRAM, and GPU utilization.

It requires at least 20 turns before percentiles may be called acceptance evidence. Smaller samples remain honest diagnostic output with `turn_count`, and every timing/resource distribution carries its own `sample_count` so optional/missing observations never borrow the global turn N.

The bounded verifier emits one synthetic metadata-only turn and reconstructs, without input/output content:

```json
{
  "session_id": "session-slice8",
  "turn_id": "turn-slice8",
  "terminal_outcome": "completed",
  "dependency_class": null,
  "failure_matrix_id": null,
  "failure_stage": null,
  "failure_code": null,
  "user_state": null,
  "endpoint_to_stt_final_ms": 80.0,
  "provider_time_to_first_token_ms": 45.0,
  "provider_completion_ms": 110.0,
  "tts_time_to_first_audio_ms": 30.0,
  "total_turn_ms": 250.0,
  "slowest_stage": "selected_llm"
}
```

The one-sample p50/p95/p99 are therefore identical and are **not** an acceptance-percentile claim. A failed-turn regression reconstructs terminal outcome, `hard|soft` dependency class, failure-matrix ID, failure stage/code, and allowed user state from the emitted metadata. A late protocol-failure regression retains first-provider-token timing from partial reasoning without manufacturing it from the later visible event. Transport replacements of completion, interruption, and failed controls retain full request/media correlation and failure enrichment while the exclusive terminal counters record exactly one failure. Admission-count regressions exclude abandoned/cancelled candidates before their first public event, but count a publication-setup attempt that reaches its enriched failed terminal. Every bounded control-send race proves that a completed send commits before cancellation propagates and only a genuinely unfinished send is canceled. Once `turn.listening` is published, caller cancellation lets a bounded media announcement finish and then emits one correlated interruption when that publication succeeds; cancellation raised by media publication itself also triggers the interruption immediately. Focused regressions prove a blocked media send, timed-out/canceled interruption, canceled initial failure terminal, or canceled post-announcement non-media control becomes one recorded failed terminal, cancels and drains remaining turn work, closes the session within the publication bound, and makes no false claim that the terminal reached the browser. Additional races cancel during audio clear and immediately after a committed interruption control: both preserve exactly-one interrupted accounting and session closure, while `fail(stage, code)` still applies its requested stage/code degradation before cancellation propagates. A completed initial failure terminal likewise applies its session degradation and admission closure before cancellation propagates. Delayed resource work carries the originating stream epoch even after reconnect. The same correlated stream also carries provider mode/identity, `external_transfer=false`, usage-unit counts when the local runtime supplies them, PCM/segment queue high-water marks, cancellation/stale/control-drop counts, and independently emitted endpoint/terminal resource samples. The public terminal control exposes the latest resource sample only when one has already completed; a missing or late sample remains optional metadata. The percentile report consumes the correlated observation stream for its reconstructed timing timeline and timing/resource percentiles. Runtime-level content-free model `loaded`/`unloaded` events are recorded separately.

## Default-log privacy review and capture deletion

Default metadata paths reject keys for raw audio/PCM, transcript, prompt, response/text/content, secrets/tokens, and content-bearing identifiers. Only specifically named count/timing fields such as provider time-to-first-token are allowlisted. Values must be bounded scalar JSON; browser error diagnostics keep a normalized class/code and never an exception message. Conversation text/history remains current-page memory and is not included in downloaded diagnostics.

Content capture is off by default. Enabling it requires the exact flag and an outside-Git root beneath the exact `/run/user/<uid>` `XDG_RUNTIME_DIR` tmpfs, with user lingering disabled. Merely private persistent directories fail closed. TTL defaults to 900 seconds; the third setting is an optional override within 60–3,600 seconds.

```text
VOICE_AGENT_DIAGNOSTIC_CAPTURE=1
VOICE_AGENT_DIAGNOSTIC_CAPTURE_ROOT=<absolute path beneath /run/user/<uid>>
# Optional: VOICE_AGENT_DIAGNOSTIC_CAPTURE_TTL_SECONDS=<60..3600>
```

Each capture is capped at 16 content files / 1 MiB plus its owned manifest/expiry; one file-locked root admits at most four owned captures / 4 MiB of content, the directories are `0700`, and every content/manifest/lock file is `0600`. Creation fails closed at the aggregate bound or unless it can launch a detached expiry worker carrying the manifest's random owner nonce, wall-clock manifest expiry, and boot-time monotonic deadline. That worker survives backend exit/crash, receives only a minimal non-secret environment, remains bounded across wall-clock rollback, exits promptly after early deletion, and deletes only the same manifest owner. Thus retained directories, guardians, and reaper threads have the same hard aggregate bound; manifest purge is defense in depth and the verified lifetime runtime tmpfs erases the root across final logout/reboot. On a host with that runtime available, the verifier captures only labelled synthetic raw bytes/transcript/prompt/response, invokes the public `./manage-diagnostics delete <capture-directory>` path, and proves the directory no longer exists; otherwise it proves that a persistent private root is rejected. Focused cases execute the real detached expiry worker beneath a supported lifetime runtime tmpfs and reject the same executable against a persistent root. They also exercise the monotonic deadline and prove creation, public deletion, purge, guardian launch/expiry, and guardian-lease release revalidate the lifetime root; persistent or forged lookalikes are retained rather than deleted. No capture or content was written to Git.

## Safe resource case

The safe real resource case allocates exactly 16 MiB, applies about 100 ms bounded CPU work, and samples `/proc` plus read-only `nvidia-smi` metadata before/under pressure. Runtime sampling is serialized on a background thread boundary, so the bounded `nvidia-smi` timeout never blocks the asyncio event loop or turn admission; a missing/late sample remains optional metadata. The latest run observed the allocation/RSS change and returned finite host/GPU values; these transient numbers are intentionally not frozen as a capacity claim. It performs no RAM/VRAM exhaustion and makes no OOM result claim.

## Validation commands

```sh
./verify-slice8
./verify
./verify-local-lfm
./verify-silero-kseniya
./verify-slice7
./verify-slice6
```

`./verify-slice8` is the focused safe gate. The remaining commands are cumulative regression/exact-cache gates required by repository memory; their current run results belong in the Slice 8 do report and PR checks.

## Exact validation gaps

Not performed or claimed by Slice 8 automation:

- physical microphone behavior or cleanup on a new private recording;
- physical speaker audibility, Kseniya quality/joins, or physical interruption timing;
- Raspberry Pi rendering/performance or a physical browser render crash;
- an actual Tailscale interface disconnect, because it would disrupt shared remote access/network state;
- destructive RAM/VRAM exhaustion or a real production-model OOM;
- killing the shared active LiveKit/model services;
- 20-turn physical/full-stack acceptance percentiles.

Controlled browser render, remote-path, provider, GPU-allocation, and process-loss cases validate state/custody semantics only and are not substituted for those physical/shared/destructive results. The pre-existing Slice 6/7 physical/full-stack gaps remain open and visible.

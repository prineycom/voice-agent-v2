# Silero `v5_5_ru` / `kseniya` native-48 private evaluation evidence

- **Decision:** [ADR-0009](../adr/0009-silero-kseniya-tts-v2-native-48-private-evaluation.md)
- **Checkpoint date:** 2026-08-14
- **Status:** deterministic and focused exact-cache checks pass; Pasha physical acceptance, tailnet exercise, and full-stack resources remain open
- **Delivery boundary:** private unmerged test branch only; no production/commercial authority

## Exact identity and license boundary

Tracked manifest: [`config/silero-kseniya-tts-v1.json`](../../config/silero-kseniya-tts-v1.json).

| Field | Frozen value |
| --- | --- |
| Upstream | `snakers4/silero-models` |
| Revision evidence | `d9355348e2781dc8fa25a135d1602c530afae24c` |
| Model | `v5_5_ru.pt` |
| Existing read-only cache path | `~/.cache/voice-agent-v2/experiments/silero-baya-tts/downloads/v5_5_ru.pt` |
| Size | `145420684` bytes |
| SHA-256 | `50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437` |
| Speaker | exact lowercase `kseniya` |
| Native rates | 8/24/48 kHz; this branch admits only 48 kHz output |
| Runtime | CPython 3.12.13; PyTorch 2.8.0+cpu; exact runtime hashes in the manifest |
| License | Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International |
| Attribution | Silero Models by Silero Team / `snakers4` |

The model is used only for private local noncommercial evaluation under CC BY-NC-SA 4.0. This report does not determine the copyright status of synthesized outputs and is not legal advice. Ordinary commercial use, production recommendation, merge, or deployment requires separate Silero licensing/permission and legal review. No weights, runtime, venv, generated audio, prompt, transcript, or private reference is tracked.

`./setup-silero-kseniya` is verify-only. It neither downloads nor replaces, modifies, copies, substitutes, or relocates an artifact. Absence or any hash/size/runtime mismatch fails readiness.

## Implemented contract and format spine

- Historical TTS/event/realtime-control v1 schemas, fixtures, traces, and Qwen code remain unchanged and inactive.
- Active composition is fixed directly to one Silero/Kseniya adapter behind `voice-agent.tts.v2`; no TTS registry, selector, environment switch, second adapter, comparison, co-start, retry, fallback, or conversion path exists.
- Input remains explicit mono `pcm_s16le/16000`: 20-ms/640-byte microphone frames and 30-second/960,000-byte request bound.
- Output is native mono `pcm_s16le/48000`: `AudioSource(48000,1)`, 20-ms/1,920-byte LiveKit frames, 60-ms/5,760-byte delivery blocks, two blocks maximum, separate final padding/original totals, and 180-second/17,280,000-byte turn bound.
- Each segment is at most 240 visible characters and 15 seconds / 1,440,000 raw bytes. Worker protocol chunks are ordered and at most 64 KiB; the parent validates exact identity/format/chunk/sample/byte/duration/terminal totals, aggregates one complete segment, and releases the worker before playback.
- One process-wide segment permit is reserved before synthesis and retained through segment-buffer consumption. The two permits therefore bound in-flight synthesis plus buffered PCM across turns; pending PCM is preserved across segment boundaries before one final partial frame.

## Text behavior

The controller targets 40–100 characters using protected sentence punctuation first, then comma/semicolon/colon/spaced dash clause boundaries. It never cuts at ordinary whitespace before the hard limit. At 240 characters it uses only the nearest safe preceding whitespace and otherwise fails rather than split a word. Decimal, date, abbreviation, ellipsis, initials, and compound-hyphen fixtures are deterministic; final short tails are emitted only after validated LFM final.

Visible/history text and synthesis text are separate. Common number/date/time/abbreviation and conservative `ё` normalization plus versioned Silero `+` stress hints exist only inside the TTS request. UI/history and content-free diagnostics retain original LLM text and never expose injected `+`. SSML/tag input is rejected; the adapter admits plain text only.

## Two-worker and interruption behavior

Exactly two stable worker slots, `silero-1` and `silero-2`, each own one resident model and one request. Each process is frozen to two intra-op threads and one inter-op thread (four intra-op threads total on the 6-core/12-thread host). Same-turn segments serialize with worker affinity and do not migrate while their affinity slot is occupied. There is no worker backlog and no third simultaneous process. Initial readiness remains unavailable to every caller until both workers finish warm-up, then publishes atomically.

A current turn may use the second worker while one obsolete non-cooperative call finishes silently. If no eligible slot is available, admission waits no longer than 750 ms and then fails `silero_capacity_timeout`. Request errors are never retried. Worker/protocol failure quarantines that slot and drops readiness below two; only an explicit post-degradation recovery may recreate it, never ordinary barge-in.

Freshness key: `(session_id, stream_epoch, turn_id, turn_generation, request_id, segment_index)` plus output media generation. Checks run before dispatch, after worker return, before/inside segment and block buffers, pump, sink, every LiveKit frame, and completion/context commit. Valid interruption invalidates the old turn before replacement admission, clears old segment/block/source queues, suppresses stale PCM/completion, and lets only the active Silero call finish silently. Cooperative STT/LFM cleanup still drains before conflicting replacement work. UI suspension alone is not treated as sufficient.

The persistent publication remains session-owned. `realtime-control.v2` publishes `turn.media-ready` with the publication and current media generation before PCM. The browser stores subscriptions without autoplay, attaches only the matching current generation, immediately suspends that generation for a valid interruption/new turn, and ignores old/wrong rapid interruption controls.

## Deterministic evidence

Commands:

```sh
./verify
./verify-slice6
```

Passing behavior includes:

- v1 preservation and TTS/event/control v2 strict fixtures;
- STT v1 rejection of 48-kHz input and TTS v2 rejection of 16-kHz output;
- Russian segmentation/shaping, hard-cap/no-midword/final-tail/plain-text/visible-text separation;
- exactly two workers, atomic post-warm-up readiness, no third, 750-ms eligible-slot capacity failure, same-turn affinity/serialization, obsolete/current overlap, stale post-worker discard, no request retry, below-two readiness, explicit recovery;
- late TTS failure preserving visible and already accepted prefix without completion/fallback;
- request freshness and the shared two-segment permit bound across synthesis, segment buffer, two-block pump, sink, frame, and completion boundaries;
- exact 60-ms blocks, 20-ms/1,920-byte 48-kHz frames, persistent publication, and final partial original/padded accounting;
- browser deferred attachment, matching publication generation, immediate valid suspension, invalid/old no-op, reconnect suppression, current-page history, and the validated license/voice badge;
- real headless Firefox plus official local LiveKit with deterministic fake inference. This is transport/publication evidence, not audibility.

## Focused exact-cache evidence

Command:

```sh
./verify-silero-kseniya
```

The check starts no network service or shared port and writes content-free evidence only under `~/.cache/voice-agent-v2/experiments/silero-kseniya-48k-ship/`. Frozen thresholds are embedded before measurements. Latest passing observations:

| Observation | Result |
| --- | ---: |
| Ready/warmed worker processes | exactly 2 stable PIDs |
| Full pool warmed idle RSS | 1,240.484 MiB |
| Obsolete/current overlap | 2 workers simultaneously busy |
| Obsolete result | `selected_tts_cancelled`; zero delivered result |
| Current native output | 841,200 bytes / 420,600 samples / 8,762.5 ms at 48 kHz |
| Current synthesis | 219.389 ms; RTF 0.025037 |
| Full pool observed peak RSS | 1,382.047 MiB |
| Full pool aggregate CPU | 303.937% one-core equivalent |
| Stale post-worker discard | 1 |
| Third simultaneous worker | none |
| Silero + pinned VAD + warmed Whisper RSS | 2,212.355 MiB |
| Compute-process GPU used before → after Whisper | 49 → 2,333 MiB |
| Controlled loss | readiness fell to 1; no retry/fallback |
| Explicit post-degradation recovery | exactly 2 simultaneous workers restored |

This is full two-worker measurement, not a 2× extrapolation. It proves native sample/byte totals and stale result suppression, not physical sound. It does not load/start llama.cpp, LiveKit, or a browser because focused verification must not occupy shared service ports. Therefore the combined LFM + Whisper + two Silero workers + LiveKit + browser resource/latency gate remains open.

## Explicit failures and diagnostics

TTS readiness, identity, capacity, synthesis, format, protocol, timeout, and resource failures are content-free and terminal for spoken output. Visible text and already accepted prefix remain; later segments stop; `turn.completed` is absent. No hidden retry/fallback exists. Default server/browser diagnostics prohibit prompt, transcript, response, raw audio, PCM, environment, token, or secret content. They expose only bounded correlation, backend/model/speaker/rate/worker IDs, counts, timings, queue high-water, padding, stale/failure counters, and readiness transitions.

## Official startup ownership

`./run-slice6` discards only ambient inherited `LITELLM_BASE_URL` and `LITELLM_TOKEN_FILE` before it sources operator-owned `.env.slice6`; putting either name in that file remains an explicit fail-closed configuration error. It reads current Tailscale Serve status without credentials before mutation, including the CLI's per-client `Foreground` documents. Exact existing application/signaling mappings are externally owned and remain after shutdown, conflicts fail before any route starts, and absent routes start as foreground children serially only after a bounded exact-status check. Cleanup stops only children created by that invocation, so unrelated handlers (including HTTPS `443` → `127.0.0.1:3000`) and pre-existing exact mappings survive. There is no global reset, background route, service manager, retry, or blind startup sleep.

The corrected host smoke started the official wrapper with both forbidden names present only in the inherited ambient environment. The operator file did not configure them, so the fixed local-LFM gate admitted startup. Both initially absent Serve routes registered serially as two foreground children and remained supervised. The stable stack observed:

- loopback HTTP `8000`, LiveKit signaling TCP `7880`, tailnet WebRTC UDP `7882`, and local LFM HTTP `18080` listening on their documented addresses;
- HTTP 200 from loopback `8000`, tailnet HTTPS `8443`, and signaling HTTPS `7443`, plus local LFM `{"status":"ok"}`;
- exactly two resident `silero_kseniya_worker.py` processes and the fixed Kseniya/native-48 identity printed by the runner;
- unchanged unrelated HTTPS `443` → `127.0.0.1:3000` throughout;
- clean Ctrl+C teardown, semantic equality of Serve status before/after, no `8443`/`7443` foreground entries, and no owned process or `8000`/`7880`/`7882`/`18080` listener remaining.

This is startup, routing, identity, residency, and cleanup evidence only. It does not prove browser interaction, audibility, voice quality, physical barge-in, or second-client tailnet acceptance.

## Pasha manual acceptance — still required

Keep captures outside Git and do not paste secrets or conversation content into a PR.

1. Record the previous branch/commit. On this branch run `./setup-slice6`, `./setup-silero-kseniya`, `./verify-slice6`, `./verify-local-lfm`, and `./verify-silero-kseniya`; fill ignored `.env.slice6`; start only `./run-slice6` after confirming no other stack occupies its loopback/service ports. Confirm the printed Serve ownership for `8443` and `7443` is `owned` or intentionally `preexisting`; any conflict must fail closed.
2. Open `http://127.0.0.1:8000`, connect the microphone, and verify the exact Silero/Kseniya/native48/private-noncommercial/CC badge.
3. Speak a normal Russian request. Confirm a useful visible original-LFM response and audible Kseniya speech; automation does not establish either physical fact.
4. Exercise numbers, a `14.08.2026` date, `09:30`, `PDF`/`SSD`, `ё`, and an answer long enough for multiple sentence/clause segments. Confirm natural/intelligible pronunciation and joins. Join target is ≤120 ms; repeated >200-ms gaps or broken intonation fails. Confirm visible/history text did not change and contains no injected `+`.
5. During audible multi-segment speech, begin another utterance. Confirm old sound stops within 250 ms of server speech-start detection, never resumes, and the replacement speaks. Interrupt that replacement again rapidly and confirm only the newest valid generation remains.
6. Inject/observe one controlled TTS readiness or synthesis failure. Confirm explicit content-free failure, retained visible/accepted prefix, no later segments, no completion, no Qwen/cloud/fallback/retry, and no crash/OOM/rate mismatch.
7. Capture redacted endpoint-to-visible/accepted-PCM timing, join timing, two Silero PIDs, total board/process VRAM/RAM/CPU with resident llama.cpp + Whisper + two Silero workers + LiveKit + browser, and safe reserve under overlap/barge-in. Test the configured tailnet browser separately without changing exposure.
8. Record Pasha's pass/fail without inferring adjectives or granular scores not supplied.

## Rollback

Stop the foreground run with Ctrl+C. Check out the previously recorded branch/commit and run that revision's own setup/verify/start procedure. Preserve all caches and ensure no foreground worker remains. There is no in-branch Qwen switch or conversion rollback. Do not merge this branch without Pasha's physical pass plus separate licensing/legal approval.

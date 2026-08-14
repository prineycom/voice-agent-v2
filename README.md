# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion centered on one Arch Linux PC. Local LiveKit, speech-to-text (STT), and text-to-speech (TTS) form the realtime voice path. A local large-language model (LLM) is the initial preference; an explicitly selected cloud LLM is allowed only if measured local candidates cannot meet the real VRAM, latency, and quality budget.

The MVP visual is an original deterministic animated AI eye, not a Live2D-first avatar. Its pupil can follow a bounded external target or move in a bounded idle pattern, blink, change palette and state behavior, and replace the pupil with a thinking indicator. An outer ring or pattern pulses from actual speech playout. A renderer-agnostic avatar boundary allows later custom, Live2D, or 3D modules without letting an LLM generate frames.

## Status

**The private Silero/Kseniya native-48 test branch passes deterministic and focused exact-cache checks; Pasha's physical Firefox acceptance is still required, and the branch must remain open and unmerged.** Active Slice 6 composition is fixed directly to cache-local Silero `v5_5_ru`, speaker `kseniya`, behind TTS v2. There is no TTS selector, Qwen adapter, co-start, hot switch, fallback, or cross-adapter retry. Input microphone/VAD/Whisper stays mono `pcm_s16le/16000`; agent output is native mono `pcm_s16le/48000`. The local LFM2.5 Q4_K_M path remains fixed and cloud-free.

Silero is licensed CC BY-NC-SA 4.0. This branch authorizes only private local noncommercial evaluation; it is not a production/commercial recommendation or authorization. Separate licensing and legal review are mandatory before any merge, production, or commercial use. Historical DeepSeek/Qwen evidence and contracts remain factual and inactive.

## Root verification

Run this single command from the repository root:

```sh
./verify
```

It uses only POSIX `sh` and the Python 3.11+ standard library, creates isolated empty temporary cache/home directories, denies socket creation with a Python audit policy, exercises success/failure/cancellation behavior, and compares two normalized traces and generated PCM artifacts byte-for-byte. Default runs remove their temporary artifacts. To preserve the verified trace and playable raw PCM files in a new directory, run:

```sh
./verify --output-directory ./slice-1-artifacts
```

The command reports each preserved path. Both PCM files use signed 16-bit little-endian mono samples at 16 kHz; `output.pcm` contains the deterministic audible tone. The destination must not already exist, preventing accidental replacement of prior evidence. The toolchain is a Slice 1 reproducibility choice only; it does not select the future production application or inference framework.

## Slice 6 Silero/Kseniya development application

One-time setup acquires pinned LiveKit/browser tooling and verifies local model prerequisites. Silero setup is strictly verify-only: it never downloads, replaces, modifies, copies, or relocates the exact existing artifact.

```sh
./setup-slice6
./setup-silero-kseniya
cp .env.slice6.example .env.slice6
```

Generate a dedicated LiveKit key pair and fill every blank in ignored `.env.slice6`:

```sh
"${XDG_CACHE_HOME:-$HOME/.cache}/voice-agent-v2/slice-6/tooling/livekit-server-v1.13.5" generate-keys
```

The active app accepts no `LITELLM_*` or TTS-selection configuration. Run the model-free/full browser boundary and the two separate exact-cache checks:

```sh
./verify-slice6
./verify-local-lfm
./verify-silero-kseniya
```

`./verify-silero-kseniya` loads exactly two resident CPU workers from the pinned read-only model cache, proves native 48-kHz totals, obsolete/current overlap with stale discard, full two-worker RSS/CPU, VAD/Whisper coexistence, and explicit controlled recovery. It writes content-free task evidence only under `~/.cache/voice-agent-v2/experiments/silero-kseniya-48k-ship/`. It does not claim audibility, voice quality, physical barge-in, or the complete browser stack. `./verify-real-streaming` is now only a compatibility alias for this active check; it no longer imports or starts Qwen.

Start the foreground development stack with:

```sh
./run-slice6
```

Startup verifies the exact LFM and Silero identities before it starts local llama.cpp, LiveKit, the gateway/controller, and foreground Tailscale Serve. The agent publishes one persistent 48-kHz LiveKit source; request/media generations determine when the browser may attach it. Microphone input remains explicitly 16 kHz. Shared services, firewall, Tailscale, and LiveKit server settings are not modified by setup or verification.

### Pasha physical acceptance and rollback

1. Check out this branch, run the setup/verification commands above, fill ignored `.env.slice6`, and run `./run-slice6`. Do not run it beside another stack on the same ports.
2. Open `http://127.0.0.1:8000`. Confirm the badge says **Silero v5_5_ru · kseniya · native mono PCM16 48 kHz · private noncommercial · CC BY-NC-SA 4.0**.
3. Exercise an ordinary Russian answer; numbers; `14.08.2026` and `09:30`; `PDF`/`SSD`; and a punctuation-rich response long enough to require several segments. Visible/history text must remain the original LLM text and must never show internal `+` stress markers.
4. While speech is audible, speak to barge in; interrupt the replacement again rapidly. Old audio must stop within 250 ms of confirmed server speech start, never resume, and the newest turn must remain usable. Any capacity/synthesis/readiness failure must be explicit, content-free, and must not retry or fall back.
5. Record pass/fail, perceived joins (target ≤120 ms; repeated >200 ms fails), redacted diagnostics, and full-stack resources. Automated counters do not prove the speaker was audible.
6. To roll back, stop this foreground run with Ctrl+C, check out the previously recorded branch/commit, and run that revision's own setup/verify/start procedure. Preserve caches. There is deliberately no in-branch Qwen switch.

The detailed evidence boundary and checklist are in [`docs/evidence/silero-kseniya-48k-private-evaluation.md`](docs/evidence/silero-kseniya-48k-private-evaluation.md). Do not merge this branch until physical acceptance, separate licensing, and legal review are all complete.

## Final Slice 5 human acceptance

Pasha ran this canonical-host command against PR 14 head `f1a3de997296e6dac87203cf5c2e157945a865b8` on 2026-08-11:

```sh
./run-voice-turn --microphone --duration 8 --play
```

Pasha attested that the complete physical microphone capture, Whisper → `deepseek-v4-flash` → Qwen3 `ryan` turn, audible playback, and overall manual Slice 5 experience succeeded. No transcript, response, latency, pronunciation detail, quality adjective, or granular score was provided or inferred.

Microphone capture is bounded to 1–30 seconds. The command removes temporary microphone PCM before admitting inference, streams Qwen output without an output file, and writes only content-free evidence under the authorized cache. On this host PipeWire 1.6.8 may return status 1 after producing the exact requested bounded capture; the CLI accepts that case only when byte count is exact. Recorder availability/setup/startup, timeout, nonzero-without-complete-output, missing/unreadable/wrong-size output, and cleanup failures produce content-free `Failure: microphone_capture/<code>` and `Terminal: turn.failed` evidence without a traceback or model admission. Cleanup failure also reports whether microphone bytes may remain after deletion and scrubbing attempts. A live transcript crosses the exact operator-approved HTTP LiteLLM boundary; runtime DNS/route/TSMP proof is not enforced, redirects and fallback aliases remain forbidden, and HTTPS is deferred.

## Current scope

- One Arch Linux host runs every media, orchestration, STT, TTS, application, and optional local-LLM process. ADR-0004 permits only one narrow auxiliary role: an allowlisted tailnet LiteLLM relay for a measured cloud path, never Pi-side inference/control.
- A private browser experience uses local LiveKit and local STT/TTS. LLM access uses one explicitly configured provider mode, with local preferred initially.
- A cloud LLM may be selected only after local candidates fail preregistered resource, latency, or quality gates. It is never an automatic or silent fallback.
- Tailscale membership is sufficient authorization during the current private stage.
- The MVP uses a deterministic custom AI-eye module behind a renderer-agnostic avatar boundary.
- The avatar runtime, never the LLM, owns pupil motion, blinking, speech-synchronous pulsing, palette/state behavior, interpolation, scheduling, and frames.
- Interruption, explicit failure behavior, privacy-safe observability, and measured resource budgets are part of the supported product.

## Deferred

- The detailed avatar-module and visual-control contract requires a dedicated Grill/design task before MVP eye implementation.
- Live2D and 3D remain possible later avatar modules; neither is an MVP renderer decision.
- Optional wake-word activation, including any custom Russian wake model, begins only after the core MVP is reliable.
- Production cloud identity/provenance, privacy/cost facts, and approval remain deferred. ADR-0006 separately permits Slice 6 private live testing but not production use; every earlier provider failure and unknown remains explicit.
- Selective migration from the legacy repository waits for a concrete vertical slice and fresh validation.

## Non-goals

- A Raspberry Pi/Desktop inference or control split; ADR-0004's single LiteLLM relay is the only narrow topology exception.
- Cloud STT/TTS or silent/automatic failover between local and cloud LLM providers.
- Audio2Face (A2F) or Audio2Emotion (A2E) in the active V2 architecture.
- Kiosk mode.
- A separate application authorization subsystem before evidence shows that tailnet membership is insufficient.
- Proprietary avatar assets, copied character design, or LLM-generated renderer parameters, keyframes, or per-frame animation data.

## Pinned legacy reference

The legacy repository is read-only evidence at [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Future work must inspect that pinned tree rather than assume its default branch is unchanged.

When a vertical slice finds a useful legacy contract, component, or test:

1. record the pinned source commit and exact path;
2. bring over only the smallest unit required by that slice;
3. adapt it to V2-owned contracts and revalidate its behavior in V2;
4. do not import legacy architecture docs, backlog state, deployment assumptions, secrets, recordings, or model artifacts wholesale.

See the authoritative inspection and migration boundary in [`docs/architecture.md`](docs/architecture.md#34-pinned-legacy-reference).

## Authoritative documentation

| Document | Purpose |
| --- | --- |
| [`CONTEXT.md`](CONTEXT.md) | Stable project vocabulary. |
| [`docs/architecture.md`](docs/architecture.md) | Authoritative system boundaries, contracts, lifecycle, operations, provider/privacy rules, and resource policy. |
| [`docs/roadmap.md`](docs/roadmap.md) | Dependency-ordered implementation slices and their evidence gates. |
| [`docs/adr/`](docs/adr/) | Accepted decisions that are costly or confusing to reverse. |

When documents disagree, accepted ADRs govern the decision they record, `docs/architecture.md` governs the current system design, and `docs/roadmap.md` governs implementation order. `CONTEXT.md` defines terminology only.

# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion centered on one Arch Linux PC. Local LiveKit, speech-to-text (STT), and text-to-speech (TTS) form the realtime voice path. A local large-language model (LLM) is the initial preference; an explicitly selected cloud LLM is allowed only if measured local candidates cannot meet the real VRAM, latency, and quality budget.

The MVP visual is an original deterministic animated AI eye, not a Live2D-first avatar. Its pupil can follow a bounded external target or move in a bounded idle pattern, blink, change palette and state behavior, and replace the pupil with a thinking indicator. An outer ring or pattern pulses from actual speech playout. A renderer-agnostic avatar boundary allows later custom, Live2D, or 3D modules without letting an LLM generate frames.

## Status

**Combined Slice 6 / Issue #15 manual acceptance failed on 2026-08-12; the authorized correction remains awaiting a new physical-browser and full-stack resource acceptance.** The active app uses only the pinned cache-local official LFM2.5 Q4_K_M model on GPU-enabled llama.cpp; LiteLLM/DeepSeek is absent from Slice 6 configuration/readiness/requests and there is no cloud fallback. Network-denied controller tests, installed LiveKit/provider contracts, React state/media tests, focused real two-slot LFM integration, scoped capability, loopback gateway, and restricted LiveKit bind smokes pass. Real loopback/tailnet microphone/listening, actual barge-in timing, sustained coexistence with Whisper/Qwen/LiveKit/browser, and subjective response acceptance remain required and are not claimed. Historical DeepSeek failures and authorizations remain factual in ADR-0005/0006; ADR-0008 supersedes them for the active app.

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

## Slice 6 development application

One-time setup acquires the checksum-pinned LiveKit server, the pinned Python SDK/runtime, and locked browser packages into ignored cache/build directories. It does not download models or place configuration, credentials, recordings, or generated audio in Git:

```sh
./setup-slice6
cp .env.slice6.example .env.slice6
```

Generate a dedicated LiveKit key pair and fill every blank in the ignored `.env.slice6`:

```sh
"${XDG_CACHE_HOME:-$HOME/.cache}/voice-agent-v2/slice-6/tooling/livekit-server-v1.13.5" generate-keys
```

The active app accepts no `LITELLM_*` configuration. `./setup-slice6` verifies, but never downloads or replaces, the exact cache-local Issue #15 model/runtime. Run the focused real provider check with `./verify-local-lfm`; it proves the hashes, loopback endpoint, exact identity, two simultaneous requests, two 32,768-token slots, bounded visible Russian output, cancellation and recovery. Run `./verify-real-streaming` to start/reuse the exact loopback LFM plus resident Qwen and prove the first PCM chunk arrives before total sentence/TTS completion; it logs timings/counts only, not content or audibility. Then this is the single development start command:

```sh
./run-slice6
```

It verifies and starts the pinned llama.cpp server, LiveKit, gateway/controller, and two foreground Tailscale Serve proxies; all are cleaned up on exit. Open `http://127.0.0.1:8000` on the host (loopback is a browser secure-context exception) or the configured tailnet HTTPS application URL. The configured paths are loopback TCP `18080` for local LFM, `8000` for the gateway, and `7880` for LiveKit signaling; Tailscale Serve HTTPS for the application/signaling (example ports `8443`/`7443`); and only tailnet UDP `7882` for WebRTC media. The LFM endpoint is never proxied or returned to the browser. ICE/TCP media, port `7881`, TURN, public exposure, provider/model administration, and detailed health endpoints are disabled/absent. The restricted server binds were measured; the two Tailscale HTTPS paths still require the real second-client gate.

Run the complete deterministic/installed-runtime/browser check with:

```sh
./verify-slice6
```

The command also runs a real headless Firefox against official local LiveKit with deterministic fake inference; this proves data-channel/track/publication/error lifecycle only. It reports the physical-browser gates as required rather than claiming them. `./verify-local-lfm` is a separate real-model host check and remains outside dependency-free CI. Privacy-safe server JSONL diagnostics are retained outside Git under `~/.cache/voice-agent-v2/slice-6/diagnostics/`; the browser exposes **Скачать диагностику** for a redacted lifecycle timeline. Neither contains audio or conversation text. Exact evidence and the operator checklist are in [`docs/evidence/slice-6-livekit-media-interruption.md`](docs/evidence/slice-6-livekit-media-interruption.md).

### Optional Parakeet STT experiment

Whisper remains the default. This branch includes a pinned local Parakeet challenger, but its automated evidence is a **no-go for replacing Whisper** and no physical Russian recognition is claimed. See [`docs/experiments/parakeet-stt.md`](docs/experiments/parakeet-stt.md) for the isolated setup/run commands, measured evidence, bounds, and rollback.

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

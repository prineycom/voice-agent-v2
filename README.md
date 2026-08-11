# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion centered on one Arch Linux PC. Local LiveKit, speech-to-text (STT), and text-to-speech (TTS) form the realtime voice path. A local large-language model (LLM) is the initial preference; an explicitly selected cloud LLM is allowed only if measured local candidates cannot meet the real VRAM, latency, and quality budget.

The MVP visual is an original deterministic animated AI eye, not a Live2D-first avatar. Its pupil can follow a bounded external target or move in a bounded idle pattern, blink, change palette and state behavior, and replace the pupil with a thinking indicator. An outer ring or pattern pulses from actual speech playout. A renderer-agnostic avatar boundary allows later custom, Live2D, or 3D modules without letting an LLM generate frames.

## Status

**Cumulative Slices 2–5 branch ready for final human acceptance.** The deterministic Slice 1 tracer remains executable. The branch now runs real local Whisper large-v3-turbo, only LiteLLM alias `deepseek-v4-flash` at the exact temporary endpoint `http://rpi:4000`, and local Qwen3 CustomVoice/`ryan`. Automated public-corpus diagnostics exercised complete turns and interruption, but the final contract-format run completed only two of three turns and encountered the known provider failures. Every Slice 2 gate failure and operator exception remains documented. Physical microphone and subjective listening are intentionally pending for one final command.

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

## Final Slice 5 human acceptance

After the automated verifiers pass, run exactly one microphone/listening turn:

```sh
./run-voice-turn --microphone --duration 8 --play
```

Speak one Russian utterance during the capture window. Check the printed transcript and response, listen for intelligibility, pronunciation, and naturalness, and confirm `Terminal: turn.completed`. The command deletes temporary microphone PCM, streams Qwen output without an output file, and writes only content-free evidence under the authorized cache. On this host PipeWire 1.6.8 may return status 1 after producing the exact requested bounded capture; the CLI accepts that case only when byte count is exact. Recorder startup, timeout, nonzero-without-complete-output, missing-output, and size failures produce a content-free `Terminal: turn.failed` without a traceback. A live transcript crosses the exact operator-approved HTTP LiteLLM boundary; runtime DNS/route/TSMP proof is not enforced, redirects and fallback aliases remain forbidden, and HTTPS is deferred.

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
- Production cloud identity/provenance, privacy/cost facts, and application-framework choices wait for later approval; the current operator-attested alias is limited to public diagnostics and the single final human turn.
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

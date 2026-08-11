# ADR-0005: Operator-fixed model stack for cumulative Slices 2–5

- **Status:** Accepted for cumulative delivery; final human test attested 2026-08-11
- **Date:** 2026-08-11
- **Decision owner:** Pasha

## Context

Slice 2 did not produce an all-gates-pass selection:

- official local LFM failed visible-output, isolation, pressure, and latency gates;
- LiteLLM alias `deepseek-v4-flash` failed 17 primary and 9 repeat automated cloud gates;
- Whisper large-v3-turbo passed WER/latency but its `88.04%` CPU p95 missed the original `83.33%` limit;
- both Piper candidates were superseded by Pasha's request to reuse the exact Qwen3 TTS configuration from pinned legacy commit `93c5c39786ff790d7ae436772d2cf37a2eeb32c6`.

The delivery contract now asks for one cumulative branch through Slice 5 and one final microphone/listening test rather than intermediate human/review gates. This is a delivery override, not permission to rewrite failed measurements.

## Decision

Use exactly this fixed stack through Slices 3–5:

- STT: `mobiuslabsgmbh/faster-whisper-large-v3-turbo` at the already measured revision/runtime, with an explicit postmeasurement CPU delivery ceiling of `90%`. The original `83.33%` gate remains failed. All WER, latency, RAM, swap, cancellation, and non-retention expectations remain unchanged.
- LLM: only LiteLLM alias `deepseek-v4-flash` through exact endpoint `http://rpi:4000`. Routing is operator-attested and opaque, all measured cloud failures remain visible, and fallback/other aliases are forbidden. Pasha accepts temporary plaintext HTTP for this private test setup without runtime DNS/route/TSMP proof; HTTPS is deferred.
- TTS: official `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice@0c0e3051f131929182e2c023b9537f8b1c68adfe`, Apache-2.0, through `faster-qwen3-tts` 0.2.6 and `qwen-tts` 0.1.1. Configuration is CustomVoice speaker `ryan`, Russian, chunk size 4, temperature 0.8, top-p 0.9, top-k 50, repetition penalty 1.05, max 2048 generated audio tokens, no fixed seed, neutral/no instruct for baseline, 24 kHz mono PCM s16le.

The Qwen configuration/provenance was selectively revalidated from pinned paths `infra/desktop/tts/.env.example`, `infra/desktop/tts/engines.py`, `infra/desktop/tts/requirements.txt`, and `docs/adr/0020-customvoice-emotion-and-voice-switcher.md`. No legacy source, private clone/reference audio, credential, model weight, cache, service topology, or historical quality claim is migrated.

Qwen automated timing/resource/cancellation/repeat evidence passes the fixed thresholds; the preregistered blind listening rubric was not separately scored. Fixed-stack overlap has both passes and failures, all retained in `benchmarks/results/fixed-stack-delivery.v1.json`.

Implement Slices 3–5 cumulatively with automated checkpoints. Defer physical microphone/listening acceptance and the only review/no-mistakes run until Slice 5. Never fabricate human evidence, and never merge without separate authority.

The implemented pre-LiveKit runtime keeps Whisper and Qwen in bounded subprocesses, keeps provider context memory-only and session-scoped, admits only allowlisted provider fields, and persists no conversation content by default. Provider readiness performs only a bearer-authenticated, content-free exact-alias check; request admission does not run Tailscale or shell-command proof. Its automated public diagnostics observed a first three-turn/interruption success and a final TTS-v1-format run with two completions, one explicit empty-provider failure, and a provider failure before the cancellation seam. Peak VRAM was at most `7,600 MiB` with more than `24 GiB` RAM available. The successful and failed attempts are both preserved outside Git.

On 2026-08-11, after the PipeWire correction at PR head `f1a3de997296e6dac87203cf5c2e157945a865b8`, Pasha ran `./run-voice-turn --microphone --duration 8 --play` and attested that the complete physical-microphone, Whisper → `deepseek-v4-flash` → Qwen3 `ryan`, audible-playback, and overall Slice 5 experience succeeded. No transcript, response, timing, pronunciation detail, quality adjective, or granular score is claimed. This attestation closes only the final Slice 5 human gate; it does not supersede any automated failure or authorize Slice 6.

## Consequences

### Positive

- Delivery can exercise one real end-to-end stack instead of stopping at repeated model-selection interviews.
- Every result-driven exception is explicit and machine-readable.
- Qwen CustomVoice avoids private reference audio and provides playable deterministic-format artifacts.
- One final user test evaluates the behavior that matters across Slices 3–5.

### Costs and risks

- The fixed stack is not an all-gates-pass Slice 2 selection. Cloud latency/fairness/isolation and overlap regressions are known risks.
- The 90% CPU allowance reduces host headroom and is valid only for this canonical host/delivery stack.
- Pasha accepted the overall Qwen-backed listening experience, but no granular listening-rubric scores or quality attributes were captured.
- Provider provenance/privacy/cost remain operator-opaque; temporary HTTP lacks a runtime transport proof; no fallback or broad production approval is inferred.

## Alternatives considered

- **Stop after failed Slice 2:** declined by Pasha.
- **Retune thresholds and relabel failures as passes:** rejected; original outcomes remain immutable.
- **Try another cloud alias or local model:** rejected; the fixed stack forbids fallback/substitution.
- **Use the legacy private voice clone:** rejected because private reference audio is outside V2 migration scope.
- **Run intermediate microphone/review gates:** superseded by the cumulative delivery contract.

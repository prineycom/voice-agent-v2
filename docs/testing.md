# Verification tiers and invariant ownership

This document is the command authority for tests. Historical slice reports remain dated evidence; they do not create additional current gates.

## Canonical PR tier

Provision dependencies once, without running tests, typecheck, or builds:

```sh
./setup-test-runtime
```

Then run the only PR command:

```sh
./verify
```

`./verify` owns a 90-second monotonic deadline and runs these phases in order:

1. the explicit current Python behavior manifest under IP-network denial;
2. the bounded local-socket readiness/VAD phase;
3. the installed SDK, capability, and active-composition contract;
4. the complete Vitest surface once;
5. TypeScript typecheck once, then one production and one review-fixture Vite build without repeated typecheck;
6. a short production-entry Firefox smoke through an actual task-owned local LiveKit.

Every phase is a separate process group. The owner sends `TERM`, waits at most 10 seconds, then sends `KILL`; detached children are found by a per-run environment nonce. A leaked process, TCP listener, or private artifact makes the gate fail even if final cleanup succeeds. Python skips also fail. The browser smoke is bounded to 15 seconds including cleanup and injects regressions for ReviewStand at the production URL, an invalid capability, missing current-generation PCM, and stale request/media correlation.

`./verify --tracer-only` remains the narrow immutable-release recovery check. It does not run the PR suite and requires only Python 3.11+.

## Hosted extended tier

```sh
./verify-extended
```

This command has a 600-second outer deadline and owns the longer mute/unmute, reconnect, diagnostics-download, production/review isolation, detached guardian, and repeated interruption scenarios. It uses deterministic inference over actual Firefox and local LiveKit; it does not claim hardware acceptance.

## Canonical-host extended tier

Each command has its own 600-second monotonic outer timeout and process cleanup owner:

```sh
./verify-local-lfm
./verify-silero-kseniya
./verify-real-stt
./verify-canonical-host --config <mode-0600-sanitized-copy-outside-git>
```

These commands require their exact cache, GPU/runtime, corpus, or user-systemd boundary. Missing hardware/artifacts are an unavailable/failing tier, never synthetic green coverage. `verify-canonical-host` starts only its disposable user service; it does not install the product unit or reboot.

## Historical evidence-integrity tier

```sh
./verify-evidence
```

This 120-second offline command validates the frozen Slice 2–5 benchmark/evidence schemas, preregistration ancestry, fixtures, and privacy guards. LiteLLM/Qwen executable runtime verification is retired: the dated evidence stays factual, but it is not supported current runtime tooling and cannot block the active PR gate.

Run this command explicitly or when `benchmarks/**`, its schemas/validators, or evidence-integrity wiring changes.

## Physical Acceptance tier

Automation does not close any of these product gates:

- normal Russian microphone → Whisper → local LFM → audible Kseniya;
- natural joins for numbers/date/time/PDF/SSD/`ё` and multi-segment gaps;
- two rapid physical barge-ins with old audio stopped and never resumed;
- resident LFM + Whisper + two Silero workers + LiveKit + browser resource/latency reserve;
- 20 real turns and avatar frame health;
- installed production unit through a normal reboot;
- a real voice turn after rollback;
- Raspberry Pi visual/frame acceptance;
- separate Silero production/commercial licensing and legal approval.

The operator procedure remains in [`evidence/silero-kseniya-48k-private-evaluation.md`](evidence/silero-kseniya-48k-private-evaluation.md). Synthetic RTP, PCM, WebAudio, callbacks, and process counters must not be relabelled as physical evidence.

## Historical defect probes

| Defect class | Current owner |
| --- | --- |
| Production URL serves `ReviewStand` | short actual Firefox/LiveKit smoke in `./verify`; production surface is tested against an injected review build |
| Wrong LFM/TTS identity or fallback | local-LFM, Silero, capability, reducer, configuration, and operations owners in `./verify`; exact identities in canonical-host commands |
| One Silero worker incorrectly reports ready | `tests.test_slice6_livekit_runtime.TTSHealthSnapshotTests` and `tests.test_silero_tts.SileroPoolTests` |
| Stale request/media generation publishes PCM or terminal | realtime/LiveKit owners plus injected browser stale generation |
| Cancellation publishes two terminals or skips cleanup | realtime cancellation and cooperative-cleanup owners |
| Transcript/audio/secret reaches diagnostics | observability, diagnostics, run-voice-turn, and browser diagnostics owners |
| Config/release symlink or pointer race is accepted | operations release/configuration owners |
| Corrupt/incompatible prior release moves pointers | operations rollback owners |
| Gateway dies while adapter descendant survives | real adapter parent-death and startup descendant cleanup owners |
| Alive nonresponsive LiveKit/LFM remains ready | bounded local-socket Slice 9 and backend-readiness owners |

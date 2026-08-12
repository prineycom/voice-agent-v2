# Contract ownership

These machine-readable V1 contracts are executable controller seams. The original inference contracts remain transport-neutral; Slice 6 adds the LiveKit data-transport envelopes without selecting a future avatar contract. [`docs/architecture.md`](../docs/architecture.md#5-contracts-and-ownership) remains authoritative for system boundaries.

| Contract | Owner | Producer → consumer | Limits and terminal semantics |
| --- | --- | --- | --- |
| `event-envelope.v1` | Session controller | Session controller → trace consumer | Every observation carries the same bounded session/turn IDs, all contract versions, and a contiguous sequence. A turn ends in exactly one `turn.completed`, `turn.failed`, or `turn.interrupted`; nothing follows it. Optional diagnostic timestamps are not part of normalized evidence. |
| `stt.v1` | STT seam | Session controller ↔ deterministic STT | Slice-local PCM is mono signed 16-bit little-endian at 16 kHz and at most 1 MiB per request. A final transcript or an explicit hard failure advances/ends the turn; failure fabricates no transcript. |
| `llm-provider.v1` | Session controller/provider adapter seam | Session controller ↔ deterministic fake provider | Request/response text is bounded to 4096 characters. `deterministic-fake` is only the Slice 1 fixture identity; the real selected adapter reuses this controller seam under the [provider boundary](../docs/architecture.md#51-llm-provider-boundary). Provider failure fabricates no answer and sends nothing to TTS. |
| `tts.v1` | TTS seam | Session controller ↔ deterministic TTS | Text is bounded to 4096 characters. PCM chunks are ordered from zero, each is at most 64 KiB, and share the declared format. Failure emits no audio; cancellation emits no chunks after interruption. |
| `realtime-control.v1` | Session controller | Session controller → browser | Reliable LiveKit data messages carry bounded session/turn IDs, a connection epoch, one session-wide increasing sequence, closed event types, lifecycle order, and terminal semantics. The browser drops wrong-version/session/epoch/turn, duplicate, late, out-of-order, oversized, and malformed input before UI actions. `turn.completed` means local generation finished and all accepted PCM frames were submitted to the persistent server media source; it does not depend on browser playback observations or prove physical playback, audibility, microphone operation, or barge-in timing. |
| `client-control.v1` | Session controller | Browser → session controller | The sole admitted control is a bounded, increasing, current-epoch-correlated reconnect notice. Browser playback and autoplay observations are telemetry only; no media acknowledgement participates in turn correctness. Duplicate, late, malformed, wrong-session/epoch, and unknown client events are dropped. |

The JSON Schema files are checked by `./verify` using the dependency-free validator. Python `Protocol` definitions and `src/voice_agent_v2/realtime.py` exercise the producer/consumer ownership without requiring LiveKit or network access.

## Fixture policy

- `fixtures/*.v1.json` are valid producer/consumer examples.
- `fixtures/traces/*.jsonl` are canonical normalized public traces for success, injected hard failures, and cancellation.
- `fixtures/pcm-hashes.json` declares the raw generated PCM identities and format. PCM bytes are regenerated, not stored as recordings.
- Changing a contract, fixture, or tracer without updating the others makes `./verify` fail.

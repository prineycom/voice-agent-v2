# ADR-0009: Fix Silero/Kseniya behind TTS v2 for native-48 private evaluation

- **Status:** Accepted for a private, unmerged test branch
- **Date:** 2026-08-14
- **Decision owner:** Pasha

## Context

Slice 6 historically used Qwen3 CustomVoice/`ryan` behind TTS v1 and converted its native 24 kHz output to the shared 16 kHz media format. Pasha separately heard a physically promising Silero `v5_5_ru` / `kseniya` sample pack and authorized an end-to-end test branch. The existing artifact is cache-local and exact, returns only a complete waveform, has no cooperative cancellation API, and is licensed CC BY-NC-SA 4.0.

Mutating TTS v1 would rewrite historical Slice 1–6 evidence. Reusing one ambiguous 16-kHz `AudioFormat` for microphone and output would also risk changing the established VAD/Whisper input path. A non-cooperative complete-waveform model requires request freshness beyond UI-only playback stop.

A short-lived follow-up considered a runtime TTS selector. Pasha explicitly rejected that direction for this branch: future adapters need a clean interface, but no second adapter, selector, registry, conversion path, co-start, or fallback is authorized now.

## Decision

Add the backend-neutral `voice-agent.tts.v2` interface and compatible `event-envelope.v2` and `realtime-control.v2` contracts. Preserve every v1 schema, fixture, trace, and historical Qwen implementation. Fix the active Slice 6 composition root directly to one contained Silero adapter:

- official `snakers4/silero-models` revision `d9355348e2781dc8fa25a135d1602c530afae24c`;
- model `v5_5_ru.pt`, 145,420,684 bytes, SHA-256 `50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437`;
- exact speaker `kseniya`;
- native mono `pcm_s16le/48000` output;
- private local noncommercial evaluation under CC BY-NC-SA 4.0.

Microphone, VAD, and Whisper remain explicitly mono `pcm_s16le/16000`. The persistent LiveKit agent source becomes `AudioSource(48000, 1)` with 20-ms/1,920-byte frames. Delivery uses 60-ms/5,760-byte blocks, a two-block pump, exact final-partial padding/accounting, a process-wide two-segment capacity reserved before synthesis and held through bounded segment buffering, 15-second/1,440,000-byte segment limits, and 180-second/17,280,000-byte turn limits.

Run exactly two isolated resident Silero worker processes with two intra-op threads and one inter-op thread each. Each worker owns one model and one request at a time. Same-turn synthesis is serial with worker affinity and never migrates to the other slot while its affinity slot is occupied. One obsolete active call may finish silently while its replacement uses the other worker. When no eligible worker is available, admission waits at most 750 ms and then fails explicitly. Request failure has no retry. Initial pool readiness publishes atomically only after both workers finish warm-up, including for direct pool callers. Worker/protocol failure drops readiness; recreation is permitted only as an explicit, serialized post-degradation recovery after the old process has fully stopped, never as ordinary interruption. A replacement slot remains unready until its warm-up completes, and recovery cannot create a third live process.

The controller owns deterministic punctuation/clause-aware segmentation and TTS-only Russian shaping. Visible/history LLM text remains original. Automatic normalization, `ё`, and `+` stress hints exist only inside the synthesis request and never enter UI/history/diagnostics. Input is plain text; SSML is not admitted.

Every asynchronous boundary carries or checks session, stream epoch, turn, turn generation, request, segment, and media generation as applicable. A valid interruption immediately invalidates old server work, clears queued/source PCM, suppresses stale worker output and completion, and tells the browser to suspend the matching generation. It does not wait for a non-cooperative active Silero call. Invalid/old browser controls do nothing. One persistent publication may remain, but it is attachable only after a matching `turn.media-ready` generation.

There is no active Qwen import/readiness/start path, TTS selector, hidden backend switch, co-start, comparison, fallback, cross-adapter retry, or in-branch rollback conversion. Rollback is stopping the foreground run, checking out the previous branch/commit, and starting that revision.

## Consequences

### Positive

- Historical TTS v1 evidence stays immutable while future model work has one neutral extension point.
- Native 48 kHz preserves the selected Silero output rather than downsampling it.
- Two workers permit replacement-turn progress without unsafe shared-model concurrency or destructive ordinary cancellation.
- Browser and server freshness rules make UI stop observable without pretending the model cooperatively cancelled.
- Input 16 kHz remains an explicit independent contract.

### Costs and limits

- Two resident CPU models use materially more RAM/CPU than one. Focused evidence must measure the full pool, not extrapolate one worker.
- Complete-waveform synthesis is not native streaming. Segment scheduling, punctuation quality, and joins still require physical acceptance.
- CC BY-NC-SA 4.0 does not authorize ordinary commercial use. No merge, production use, commercial recommendation, or commercial deployment is authorized without separate licensing and legal review.
- Automated LiveKit frames, counters, and subscription events do not prove audibility, naturalness, or physical barge-in timing.

## Alternatives considered

- **Mutate TTS v1 to 48 kHz:** rejected because it would rewrite historical evidence.
- **Keep one shared 16-kHz format:** rejected because it couples input truth to an unrelated output choice and needlessly downsamples Silero.
- **One worker plus destructive cancellation/restart:** rejected because it loses residency and delays replacement by about a second.
- **Share one model across concurrent requests:** rejected because that concurrency is unverified.
- **Start three workers:** rejected as unnecessary and outside the bounded resource design.
- **Add a Qwen/Silero selector or fallback now:** explicitly rejected by Pasha. Historical Qwen remains inactive evidence; a future adapter requires separate authorization.
- **Treat browser suspension as sufficient:** rejected because stale server PCM/completion could still cross the turn boundary.

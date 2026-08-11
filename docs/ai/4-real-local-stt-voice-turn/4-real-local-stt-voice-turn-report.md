# Do Report: 4-real-local-stt-voice-turn

**Source:** https://github.com/prineycom/voice-agent-v2/issues/4
**Parent:** Issue #3
**Status:** ✅ automated checkpoint pass; deferred physical microphone path accepted downstream in Slice 5 on 2026-08-11

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/process_adapter.py` | Added bounded JSON-lines child lifecycle with correlation, timeout, cancellation, and secret-minimal environment. | Local service readiness/process-loss/cancellation. |
| `src/voice_agent_v2/local_stt.py` | Added selected Whisper large-v3-turbo adapter, strict PCM format, cache-local model, safe metadata, and delete-in-finally temporary WAV. | Real local STT, identity, format negotiation, non-retention. |
| `verify-slice3`, `scripts/verify_slice3.py` | Runs one licensed public corpus utterance through real STT and unchanged deterministic downstream legs. | Public real-STT tracer evidence. |
| `tests/test_real_adapters.py` | Covers format rejection, temporary-file deletion, metadata privacy, process loss, and cancellation. | Producer/consumer/failure contract. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Fixed Russian corpus meets selected delivery limits | ✅ | Slice 2 WER `11.4551%`, finalization p95 `294.39 ms`, RTF p95 `0.051`; CPU delivery exception is explicit. |
| Real STT completes correlated tracer with deterministic LLM/TTS | ✅ | `./verify-slice3` completed public sample `stt-01` with one `turn.completed`. |
| Audio mismatch/process loss/cancellation explicit | ✅ | Unit tests produce `unsupported_audio_format`, `selected_stt_unavailable`, and bounded process cancellation. |
| No raw audio retained by default | ✅ | WAV exists only during request and is deleted in `finally`; automated acceptance verifies empty temp directory. |
| Safe identity/latency observations omit transcript | ✅ | Adapter observations contain identity, byte/duration, correlation-presence, latency, and retention flag only. |
| Physical microphone turn | ✅ downstream attestation | Pasha later ran the complete Slice 5 command and attested that physical capture and the overall voice experience succeeded; no transcript or detailed STT judgment is inferred. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | Deterministic foundation and 31 unit/contract tests after checkpoint. |
| `./verify-slice3` | ✅ pass | Real GPU Whisper on public 16 kHz mono corpus; deterministic downstream completed. |

## Unresolved uncertainty

- Pasha's dated Slice 5 attestation closes the physical microphone path; it provides no device-specific, room-acoustic, transcript, or latency detail.
- Partial transcript UI is not exposed because selected faster-whisper adapter is bounded-final in this simplest pre-LiveKit path.

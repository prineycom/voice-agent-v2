# Do Report: 6-real-local-stt-tts-spoken-conversation

**Source:** https://github.com/prineycom/voice-agent-v2/issues/6
**Parent:** Issues #3–#5
**Status:** ✅ Pasha-attested Slice 5 human acceptance under preserved operator-fixed limitations

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `benchmarks/slice2/runners/qwen3_tts_runner.py` | Added optional ordered base64 PCM chunk protocol without default file retention. | Local streaming Qwen audio boundary. |
| `src/voice_agent_v2/local_tts.py` | Added selected Qwen3 CustomVoice/`ryan` adapter, explicit native-24 kHz → contract-16 kHz HQ conversion, PCM validation, readiness, streaming, cancellation, safe observations, and no default output file. | Real TTS contract, format, cancellation, non-retention. |
| `src/voice_agent_v2/real_turn.py` | Added cumulative real-turn controller with provider-to-TTS sentence handoff, ordered public lifecycle, text-preserving TTS failure, terminal semantics, and cancellation. | First complete real-inference conversation before LiveKit. |
| `scripts/verify_slice5.py`, `verify-slice5` | Runs three sustained public real turns plus a full-stack cancellation and writes explicit public playable WAV diagnostics outside Git. | Automated sustained/cancellation/resource evidence. |
| `scripts/run_voice_turn.py`, `run-voice-turn` | Provides the single final PipeWire microphone/listening command governed by the operator guidance in [`README.md`](../../../README.md#final-slice-5-human-acceptance) and failure policy in [`docs/architecture.md`](../../architecture.md#7-failure-semantics). | Physical microphone and listening acceptance attested by Pasha. |
| `tests/test_real_adapters.py`, `tests/test_run_voice_turn.py` | Covers ordered PCM chunks/totals, format/text bounds, no output path, lifecycle ordering, text-preserving TTS failure, no post-terminal chunks, recorder/STT cleanup and retention reporting, and no traceback. | Producer/consumer/failure/privacy contracts. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Qwen fixed listening/timing material | ⚠️ preserved rubric gap + accepted overall experience | Automated first-signal p95 ~161 ms and RTF p95 ~0.402 pass; twelve Slice 2 plus three full-turn WAVs are playable. Pasha accepted the overall audible Slice 5 experience; no separate intelligibility/naturalness scores or adjectives were provided. |
| Real local STT → selected cloud LLM → local Qwen TTS | ⚠️ | A first public diagnostic completed three turns. The final TTS-v1-format run completed two of three and explicitly failed one `empty_selected_provider_response`; no alias changed. |
| Provider→TTS overlap and resources | ⚠️ | Runtime hands complete sentences to resident TTS while the provider stream remains open. Peak VRAM was at most `7,600 MiB`, leaving `4,682 MiB`, with more than `24 GiB` RAM available. Earlier preregistered overlap regression gates remain failed. |
| Ordered chunks/format/terminal | ✅ | Qwen's native 24 kHz output is converted to the preserved v1 `pcm_s16le/16000Hz/mono` contract; one `turn.completed` per sustained turn. |
| Cancellation/stale output | ⚠️ | The first public diagnostic produced `turn.interrupted` with no post-terminal event. The final-format run hit a selected-provider failure before the TTS cancellation seam; unit and Slice 2 real-TTS coverage still prove bounded process stop and no post-terminal chunks. |
| TTS failure preserves text | ✅ | Unit test retains `llm.final`, emits no TTS chunk, and terminates `turn.failed` at TTS. |
| Microphone capture failure contract | ✅ | Canonical PipeWire 1.6.8 reproduced exact-size PCM with status 1; executable regression coverage enforces the authoritative failure policy in [`docs/architecture.md`](../../architecture.md#7-failure-semantics). |
| Default non-retention | ✅ | Default microphone and STT cleanup is mandatory and reports any unconfirmed retention; Qwen streaming has no output path and final output is memory/playback only. Explicit public automated WAVs are bounded diagnostics. |
| Live microphone/listening acceptance | ✅ operator attestation | On 2026-08-11 Pasha ran the documented command at PR head `f1a3de997296e6dac87203cf5c2e157945a865b8` and reported that the complete test worked. Scope is limited to physical capture, full fixed-stack turn, audible playback, and overall experience. |

## Validation at the documented checkpoints

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | 65 deterministic/unit/contract tests at the acceptance-documentation checkpoint. |
| `./verify-slice3` | ✅ pass | Real Whisper public-corpus turn, deterministic downstream. |
| `./verify-slice4` | ✅ pass | Real Whisper + selected provider + deterministic audio; real provider cancellation. |
| `PYTHONPATH=src python3 -m unittest -v tests.test_run_voice_turn` | ✅ pass | Four focused capture success/failure/cleanup/no-traceback tests. |
| Bounded real `microphone_pcm(1.0)` | ✅ pass | 32,000 bytes captured on the canonical default source and temporary PCM removed; no inference or listening claim. |
| `./verify-slice5` | ✅ historical harness pass with fixed-provider limitations | Final format: two of three completed, one explicit empty-provider failure, cancellation attempt failed at the fixed provider before TTS; public playable artifacts created. |

## Dated human acceptance

On 2026-08-11, after PR 14 head `f1a3de997296e6dac87203cf5c2e157945a865b8` corrected the PipeWire path, Pasha ran `./run-voice-turn --microphone --duration 8 --play` and attested: `все сработало! Что дальше?` This accepts the complete physical microphone capture, Whisper → `deepseek-v4-flash` → Qwen3 `ryan` turn, audible playback, and overall manual Slice 5 experience. No transcript, response, latency, pronunciation observation, audio-quality adjective, or granular rubric score was supplied or inferred.

## Unresolved uncertainty

- No granular microphone-device, room-acoustic, transcript-correctness, latency, pronunciation, or audio-quality measurement accompanies the overall operator acceptance.
- LiteLLM's known Slice 2 latency/fairness/isolation failures remain; successful public sustained turns do not erase them.
- LiveKit/browser/avatar/deployment remain later slices and are not included here.

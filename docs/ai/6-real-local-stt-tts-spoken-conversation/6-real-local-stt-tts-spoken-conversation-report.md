# Do Report: 6-real-local-stt-tts-spoken-conversation

**Source:** https://github.com/prineycom/voice-agent-v2/issues/6
**Parent:** Issues #3–#5
**Status:** ⚠️ automated full-stack pass under operator-fixed limitations; final microphone/listening acceptance pending

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `benchmarks/slice2/runners/qwen3_tts_runner.py` | Added optional ordered base64 PCM chunk protocol without default file retention. | Local streaming Qwen audio boundary. |
| `src/voice_agent_v2/local_tts.py` | Added selected Qwen3 CustomVoice/`ryan` adapter, explicit native-24 kHz → contract-16 kHz HQ conversion, PCM validation, readiness, streaming, cancellation, safe observations, and no default output file. | Real TTS contract, format, cancellation, non-retention. |
| `src/voice_agent_v2/real_turn.py` | Added cumulative real-turn controller with provider-to-TTS sentence handoff, ordered public lifecycle, text-preserving TTS failure, terminal semantics, and cancellation. | First complete real-inference conversation before LiveKit. |
| `scripts/verify_slice5.py`, `verify-slice5` | Runs three sustained public real turns plus a full-stack cancellation and writes explicit public playable WAV diagnostics outside Git. | Automated sustained/cancellation/resource evidence. |
| `scripts/run_voice_turn.py`, `run-voice-turn` | Provides the single final PipeWire microphone/listening command with default non-retention. | Deferred physical microphone and listening acceptance. |
| `tests/test_real_adapters.py` | Covers ordered PCM chunks/totals, format/text bounds, no output path, lifecycle ordering, text-preserving TTS failure, and no post-terminal chunks. | Producer/consumer/failure/privacy contracts. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Qwen fixed listening/timing material | ⚠️ | Automated first-signal p95 ~161 ms and RTF p95 ~0.402 pass; twelve Slice 2 plus three full-turn WAVs are playable. Human intelligibility/naturalness remains pending. |
| Real local STT → selected cloud LLM → local Qwen TTS | ⚠️ | A first public diagnostic completed three turns. The final TTS-v1-format run completed two of three and explicitly failed one `empty_selected_provider_response`; no alias changed. |
| Provider→TTS overlap and resources | ⚠️ | Runtime hands complete sentences to resident TTS while the provider stream remains open. Peak VRAM was at most `7,600 MiB`, leaving `4,682 MiB`, with more than `24 GiB` RAM available. Earlier preregistered overlap regression gates remain failed. |
| Ordered chunks/format/terminal | ✅ | Qwen's native 24 kHz output is converted to the preserved v1 `pcm_s16le/16000Hz/mono` contract; one `turn.completed` per sustained turn. |
| Cancellation/stale output | ⚠️ | The first public diagnostic produced `turn.interrupted` with no post-terminal event. The final-format run hit a selected-provider failure before the TTS cancellation seam; unit and Slice 2 real-TTS coverage still prove bounded process stop and no post-terminal chunks. |
| TTS failure preserves text | ✅ | Unit test retains `llm.final`, emits no TTS chunk, and terminates `turn.failed` at TTS. |
| Default non-retention | ✅ | Microphone capture uses a delete-in-finally cache-local PCM; Qwen streaming command has no output path; final output is memory/playback only. Explicit public automated WAVs are bounded diagnostics. |
| Live microphone/listening acceptance | ⏳ | One documented command is ready for Pasha; not run or claimed by the agent. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | 40 deterministic/unit/contract tests. |
| `./verify-slice3` | ✅ pass | Real Whisper public-corpus turn, deterministic downstream. |
| `./verify-slice4` | ✅ pass | Real Whisper + selected provider + deterministic audio; real provider cancellation. |
| `./verify-slice5` | ✅ harness pass with fixed-provider limitations | Final format: two of three completed, one explicit empty-provider failure, cancellation attempt failed at the fixed provider before TTS; public playable artifacts created. |

## Final human command

```sh
./run-voice-turn --microphone --duration 8 --play
```

Pasha should speak one Russian utterance during the capture window, verify the printed transcript/response, listen for Qwen3 `ryan` intelligibility/pronunciation/naturalness, and confirm the terminal is `turn.completed`. This command retains neither microphone PCM nor synthesized PCM by default and writes only content-free evidence under the authorized cache.

## Unresolved uncertainty

- Human microphone, room-acoustic, playback, and subjective Qwen listening acceptance are pending.
- LiteLLM's known Slice 2 latency/fairness/isolation failures remain; successful public sustained turns do not erase them.
- LiveKit/browser/avatar/deployment remain later slices and are not included here.

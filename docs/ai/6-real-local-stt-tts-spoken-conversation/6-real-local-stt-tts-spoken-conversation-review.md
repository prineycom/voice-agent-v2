# Review: 6-real-local-stt-tts-spoken-conversation

**Source:** Issues #3–#6, cumulative diff from `main`, and the Slice 5 do report
**Status:** ⚠️ pass with known external/human acceptance findings

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Critical | `src/voice_agent_v2/local_stt.py` originally interpolated unvalidated correlation IDs into its temporary WAV path; real adapters/controller did not enforce the schema's ID bounds. | A non-contract ID could escape the authorized temporary directory; correlation and non-retention guarantees would be invalid. | Enforce the closed correlation-ID contract before any adapter/process/filesystem work; bound STT input duration. | Fixed. |
| Important | `src/voice_agent_v2/cloud_llm.py` proved a Tailscale DNS result, then opened a second hostname resolution; malformed SSE shapes were not all normalized to a content-free `StageFailure`. | A resolution race weakened the endpoint proof and malformed provider output could escape the explicit failure contract. | Connect to the already-proven CGNAT address with the fixed Host header; classify malformed SSE as protocol failure. | Fixed. |
| Important | The selected Qwen runtime natively produced 24 kHz PCM while unchanged `voice-agent.tts.v1` requires 16 kHz PCM. | Claiming TTS v1 with 24 kHz output would violate the preserved Slice 1 producer/consumer contract. | HQ-convert at the process boundary and validate final sample rate/channels/encoding/totals. | Fixed before final review commit. |
| Important | Cache-local final-format evidence completed two of three turns; one turn was `empty_selected_provider_response`, and the cancellation case failed at the fixed provider before reaching TTS. Earlier diagnostic evidence completed three turns and interruption. | The latest run does not prove reliable provider success or a final-format full-stack interruption. It confirms the already-measured provider instability; Slice 6 must not start. | Preserve both attempts, keep no fallback, and require the final human turn before accepting Slice 5. | Not code-fixed by design; alternate/retry routing is forbidden. |
| Important | Subjective Qwen listening and physical microphone behavior are still marked pending in the report/roadmap. | Slice 5's human acceptance criterion is open. | Pasha runs the single documented command and reports transcript/playback judgment. | Pending human evidence. |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Correlation/path and utterance bounds | `src/voice_agent_v2/contracts.py`, `local_stt.py`, `local_tts.py`, `cloud_llm.py`, `real_turn.py`, `tests/test_real_adapters.py` | Executable tests reject traversal-shaped IDs before executor/process/filesystem work; STT input is capped at 30 seconds. |
| Pinned endpoint and provider protocol errors | `src/voice_agent_v2/cloud_llm.py` | Requests connect to the address returned by the Tailscale route gate; errors remain content-free and fail closed. |
| TTS v1 format mismatch | `benchmarks/slice2/runners/qwen3_tts_runner.py`, `src/voice_agent_v2/local_tts.py`, tests and docs | Runtime requests 16 kHz HQ conversion; terminal fields and chunk totals are validated; root contract tests remain green. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Fixed-provider instability | The operator explicitly fixed only `deepseek-v4-flash`; retry/fallback/alternate alias would violate the delivery contract and disguise failed Slice 2 gates. |
| Human microphone/listening evidence | An agent cannot fabricate physical capture or subjective listening. The one final command is prepared but intentionally unrun. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | 41 behavioral/unit/contract tests under network denial. |
| `./benchmark-slice2 validate` | ✅ pass | All tracked preregistration/evidence/selection artifacts validate offline. |
| `./verify-slice3` | ✅ pass | Real Whisper public-corpus tracer; no temporary audio retained. |
| `./verify-slice4` | ✅ previously passed | Exact real provider turn and real mid-request cancellation over the proven endpoint. |
| `./verify-slice5` | ⚠️ harness pass with fixed-provider limitations | Final contract-format run: two completions, one explicit empty response, provider failure before cancellation seam; failures preserved. |
| Python compilation / `git diff --check` | ✅ pass | No syntax or whitespace errors. |

## Recommendations

- Run the single final human-acceptance command documented in [`README.md`](../../../README.md#final-slice-5-human-acceptance) once and record the human result without retaining conversation content.
- Keep Slice 6 blocked until that result is accepted.
- Run no-mistakes on the final committed review fixes; never merge without a later explicit instruction.

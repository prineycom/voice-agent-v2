# Review: 6-real-local-stt-tts-spoken-conversation

**Source:** Issues #3–#6, cumulative diff from `main`, and the Slice 5 do report
**Status:** ⚠️ pass with known automated limitations; later human acceptance attested

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Critical | `src/voice_agent_v2/local_stt.py` originally interpolated unvalidated correlation IDs into its temporary WAV path; real adapters/controller did not enforce the schema's ID bounds. | A non-contract ID could escape the authorized temporary directory; correlation and non-retention guarantees would be invalid. | Enforce the closed correlation-ID contract before any adapter/process/filesystem work; bound STT input duration. | Fixed. |
| Important | At review time `src/voice_agent_v2/cloud_llm.py` proved a Tailscale DNS result, then opened a second hostname resolution; malformed SSE shapes were not all normalized to a content-free `StageFailure`. | The malformed-output gap required repair; the transport-proof concern was later made non-applicable by Pasha's explicit removal of runtime Tailscale proof. | Keep protocol failures content-free; follow the later post-review transport decision instead of retaining address pinning. | Protocol fix retained; runtime proof superseded. |
| Important | The selected Qwen runtime natively produced 24 kHz PCM while unchanged `voice-agent.tts.v1` requires 16 kHz PCM. | Claiming TTS v1 with 24 kHz output would violate the preserved Slice 1 producer/consumer contract. | HQ-convert at the process boundary and validate final sample rate/channels/encoding/totals. | Fixed before final review commit. |
| Important | Cache-local final-format evidence completed two of three turns; one turn was `empty_selected_provider_response`, and the cancellation case failed at the fixed provider before reaching TTS. Earlier diagnostic evidence completed three turns and interruption. | The latest run does not prove reliable provider success or a final-format full-stack interruption. It confirms the already-measured provider instability; Slice 6 must not start. | Preserve both attempts and keep no fallback. At review time the final human turn was required; Pasha later accepted it without reclassifying automated failures. | Not code-fixed by design; alternate/retry routing is forbidden. |
| Important | At review time subjective Qwen listening and physical microphone behavior were pending. | Slice 5's human acceptance criterion was open. | Pasha runs the single documented command and reports the overall result without fabricated detail. | Resolved by Pasha's dated 2026-08-11 overall acceptance attestation; no granular scores inferred. |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Correlation/path and utterance bounds | `src/voice_agent_v2/contracts.py`, `local_stt.py`, `local_tts.py`, `cloud_llm.py`, `real_turn.py`, `tests/test_real_adapters.py` | Executable tests reject traversal-shaped IDs before executor/process/filesystem work; STT input is capped at 30 seconds. |
| Provider protocol errors | `src/voice_agent_v2/cloud_llm.py` | Output identity/type/size failures remain content-free and fail closed. The later authorized correction removes runtime route proof/address pinning without weakening endpoint/alias/redirect/no-fallback boundaries. |
| TTS v1 format mismatch | `benchmarks/slice2/runners/qwen3_tts_runner.py`, `src/voice_agent_v2/local_tts.py`, tests and docs | Runtime requests 16 kHz HQ conversion; terminal fields and chunk totals are validated; root contract tests remain green. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Fixed-provider instability | The operator explicitly fixed only `deepseek-v4-flash`; retry/fallback/alternate alias would violate the delivery contract and disguise failed Slice 2 gates. |
| Human microphone/listening evidence at review time | An agent could not fabricate it; Pasha later ran the command and attested overall success on 2026-08-11. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | 65 behavioral/unit/contract tests under network denial at the later acceptance-documentation checkpoint. |
| `./benchmark-slice2 validate` | ✅ pass | All tracked preregistration/evidence/selection artifacts validate offline. |
| `./verify-slice3` | ✅ pass | Real Whisper public-corpus tracer; no temporary audio retained. |
| `./verify-slice4` | ✅ historical pass | Exact real provider turn and real mid-request cancellation. Its historical route proof is preserved as evidence, not required by the corrected runtime. |
| `./verify-slice5` | ⚠️ harness pass with fixed-provider limitations | Final contract-format run: two completions, one explicit empty response, provider failure before cancellation seam; failures preserved. |
| Python compilation / `git diff --check` | ✅ pass | No syntax or whitespace errors. |

## Recommendations

- Preserve Pasha's dated overall acceptance without inventing granular observations.
- Do not start Slice 6 as part of this update.
- Pasha authorized merging PR 14 after the acceptance documentation reaches green checks; the agent does not perform the merge.

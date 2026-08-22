# Do Report: truthful-voice-action-context-correction

**Source:** accepted private diagnosis and failed-turn option-B decision dated 2026-08-22  
**Parent:** direct correction PR over `51bbcf1372d870a76cc732d1d583f43806bcdadd`  
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/{agent_run,agent_run_provider,local_lfm,tts_text}.py` | Russian shaped-form admission, receipt-backed provenance, bounded transactional same-session conversation | A, B, C |
| `src/voice_agent_v2/{real_turn,realtime,livekit_runtime}.py` | Public independent action/speech outcomes and option-B snapshot/rollback/retain lifecycle | B, C |
| `src/voice_agent_v2/{silero_tts,observability}.py`, observation schema | Private allowlisted worker enum and scalar failed-segment observation | D |
| `contracts/agent-run.v5.schema.json`, `contracts/README.md` | Closed additive run-result provenance contract and ownership text | B, C, integration |
| `web/src/` | Strict control parsing and explicit action/speech history/status labels without premature no-action claims | B |
| `tests/` | Deterministic fake-provider/worker/Docker/realtime/UI regressions at integration seams | A–D |
| `README.md`, `CONTEXT.md`, `docs/architecture.md`, ADR-0014, correction evidence | Authoritative temporary-language, provenance, context, observability and nonclaim documentation | integration/docs |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| A. Temporary Russian-only speech admission | Implemented | Complete final admission precedes visibility/worker calls; structural all-English/mixed regressions and technical shaping fixtures |
| B. Executed-action provenance | Implemented | AgentRun v5 count/outcome, realtime terminal/public/UI fields, promise/no-op and receipt success/failure regressions |
| C. Bounded same-session context, option B | Implemented | Four-message/8,192-byte exact-session state, receipt separation, transaction lifecycle and visible-failed typed status regressions |
| D. Safe Silero error class | Implemented | Full allowed-enum table, generic public code, exact private allowlist/counters, corrupt-frame quarantine regressions |
| Documentation/nonclaims | Implemented | ADR-0014, architecture, README, contracts, glossary and deterministic evidence |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Single invocation: 452 hermetic Python, 9 local-socket Python, 93 Vitest, TypeScript, both web builds, runtime contract and Firefox/LiveKit smoke passed; full output saved at ignored `artifacts/voice-action-context-verify.log` |

## Unresolved uncertainty

- Physical Docker, live network/model, browser, microphone, audio, reboot, full-stack and global multilingual acceptance remain outside this deterministic PR.

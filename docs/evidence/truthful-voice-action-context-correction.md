# Truthful voice, action provenance, and bounded context correction

**Scope:** deterministic correction evidence for ADR-0014 over base `51bbcf1372d870a76cc732d1d583f43806bcdadd`  
**Tier:** hermetic PR evidence only

## Proven behavior

- AgentRun admits a final only after redaction plus the same deterministic segmentation and Russian TTS shaping used for worker requests. Fixed Russian, punctuation/digits/date/time, Russian abbreviations and the established `PDF`/`SSD`/`HTTP`/redaction pronunciations remain admitted. Fixed all-English, mixed Russian/English, and one-Cyrillic-token-then-English fixtures fail as `tts_language_unsupported` before `llm.visible`, TTS dispatch or PCM. Observations contain only reason and bounded aggregate counts, never text/hash.
- A fixed action-shaped request whose model returns a future promise executes no helper and reports `operation_count=0` / `action_outcome=no_operation`. A successful helper receipt plus final reports completed action. A failed helper receipt plus failed speech reports action failed and speech failed as separate server fields. Realtime completion carries receipt-backed action provenance plus delivered speech; cancellation and late model output remain identity-dropped with no retry.
- AgentRunProvider supplies a distinct `conversation` field while same-run tool receipts remain in `history`. Exact-session memory holds at most four messages and 8,192 serialized UTF-8 bytes. Deterministic cases cover completed follow-up, `visible_tts_failed` retention after snapshot restore, four-message and byte eviction, another-session isolation, snapshot/restore, reset/reconnect-equivalent empty context, close, operation/non-operation parity, transaction scratch cleanup, and absence of receipt/call/output/container fields.
- Every allowlisted Silero worker request error is table-driven through the adapter boundary: its private enum is preserved, public code remains `silero_synthesis_failed`, one exact allowlisted failed-segment observation is emitted, ready workers remain two, quarantines remain zero, requests/failures are one and retry is zero. Corrupt chunk/final/error frames remain `silero_worker_failed`, quarantine one slot, reduce readiness and expose no private enum or failed-segment observation.
- The browser reducer and panels validate/render action and speech outcomes independently. Unknown in-flight action is not prematurely rendered as no action; only the server's explicit zero-operation outcome produces that label.

## Privacy and authority assertions

Default observations and public control/history contain no final/request text beyond the existing visible conversation events, no text hash, PCM, traceback, arguments, paths, file bytes, stdout/stderr, raw receipt/container/worker/session/request identity, credential, or secret. Same-session conversation is process memory only and never copied to AgentEnvironment, diagnostics or durable browser replay.

The correction adds no tool, environment, lifecycle authority, hidden host access, marker probe, model/provider/TTS selector, automatic retry, translation, fallback, cloud path, external transfer, Silero artifact change, or synthesis behavior change.

## Verification owner

The single canonical `./verify` invocation passed once with `python_hermetic: PASS tests=452`, `python_local_socket: PASS tests=9`, Vitest `93 passed`, TypeScript typecheck, both web builds, installed-runtime contract and the short Firefox/actual-LiveKit smoke all passing. Full combined output is saved in ignored worktree artifact `artifacts/voice-action-context-verify.log`. The gate uses synthetic/fake providers, workers, Docker and UI fixtures for this correction and performs no model/audio/network content task.

## Nonclaims

This evidence does not run Docker, a browser, microphone, physical speaker, live network, exact LFM generation, cached model or Silero audio. It does not claim global English/multilingual speech, translation quality, semantic correctness of an operation, physical audibility, the root cause behind a worker enum, durable/cross-session memory, reload identity changes for issue #86, or physical/live/full-stack acceptance. Those remain separately authorized tiers.

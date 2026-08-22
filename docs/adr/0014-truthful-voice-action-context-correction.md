# ADR-0014: Correct voice admission, action provenance, and same-session AgentRun context

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Pasha

## Context

A private diagnosis of one physical session proved three independent integration failures after production AgentRun replaced the ordinary local-provider path: AgentRun could publish English prose to fixed Russian-only Silero, a valid `final` promise could look like execution despite zero tool receipts, and AgentRun supplied no prior same-session conversation. A separate Silero diagnosis proved that validated request-local worker error enums were collapsed at the adapter boundary even though malformed frames correctly followed the quarantining worker-failure path.

The selected speech model remains exact Russian `v5_5_ru` / `kseniya`; its selector-free, no-retry/no-fallback decision in ADR-0009 is unchanged. Global English or multilingual speech needs a later design and is outside this correction. Pasha separately chose failed-turn context option B: once a final became visible, a subsequent TTS failure should remain available to later turns in that exact live session without becoming a completed or delivered turn.

## Decision

Ship one correction slice with four causally independent boundaries.

1. **Temporary Russian-only final admission.** The decision instruction requires Russian. After display redaction, AgentRun deterministically segments and shapes the complete exact spoken form before any visible callback or worker request. Unsupported Latin prose fails as typed `tts_language_unsupported`; only bounded aggregate character counts and reason are observable. The smallest pronunciation map is limited to established `PDF`, `SSD`, `HTTP`, and redaction tokens. Existing punctuation, digit, date, time and Russian-abbreviation shaping remains authoritative.
2. **Server-owned action provenance.** The decision instruction requires `operation` when a request needs an admitted fixed tool; a `final` may answer or ask one necessary clarification but cannot promise future execution. Prompt wording is not evidence. AgentRun v5 carries `operation_count` and the closed `action_outcome=no_operation|completed|failed` through realtime history and UI independently from `speech_outcome`. Receipt success/failure is the only execution boundary; public fields contain no arguments, paths, file/output bytes, raw receipts or raw runtime identities.
3. **Bounded same-session conversation with option B.** AgentRunProvider owns at most two exchanges/four messages and 8,192 serialized UTF-8 bytes in process memory for one exact session. Conversation is a separate request field from same-run tool-receipt history and never contains raw tool results. Completed turns commit. A visible final followed by TTS failure commits a bounded user/final pair with typed `visible_tts_failed`, operation count and action outcome, while remaining failed and undelivered. Pre-visibility failures, interruption and cancellation restore the snapshot. Reset, reconnect, close and another session receive no context.
4. **Private safe Silero error classification.** A structurally valid correlated worker error retains its allowlisted enum privately while the public code stays `silero_synthesis_failed`. Exactly one failed-segment observation may contain the enum, logical slot class, segment index, shaped character/UTF-8 and aggregate character-class counts, dispatch timing, readiness/pool counters and zero retry. Malformed/corrupt frames remain `silero_worker_failed`, quarantine the slot, drop readiness and claim no enum.

No part adds translation, a second model decision, automatic retry, a TTS selector, fallback, cloud transfer, Silero-model change, browser-history replay, durable/cross-session history, prose execution heuristics, host access, or synthesis behavior changes.

## Consequences

### Positive

- Unsupported Latin cannot be displayed as an ordinary speakable AgentRun answer and then be silently or partially omitted.
- A future-tense final with zero operations is visibly receipt-free; successful or failed action remains truthful even if the final or speech leg later fails.
- Follow-up turns regain the documented bounded same-session context, including what the user actually saw on a TTS-failed turn, without retaining raw receipts or audio.
- A future validated Silero request error remains privately classifiable without weakening the public/privacy boundary or changing worker readiness.

### Costs and limits

- English and ordinary unlisted Latin tokens are rejected, not translated or transliterated. Global multilingual speech remains queued separately.
- Option B retains bounded request/final content in process memory until session reset/close; it intentionally exposes that same-session context to the same local model.
- Action completion proves validated helper receipt outcome, not semantic correctness of model intent or arbitrary external side effects.
- The private Silero enum explains only the worker's validated error class; it does not infer root cause, inspect audio, or prove physical audibility.

## Alternatives considered

- **Permit multilingual finals and let Russian Silero attempt them:** rejected because observed mixed input can produce silent omission or partial-prefix speech.
- **General transliteration/translation:** deferred; it would add language policy and quality/provider behavior beyond this correction.
- **Trust prompt wording or future-tense prose as action state:** rejected because no receipt or execution boundary exists.
- **Completed-only failed-turn rollback:** rejected by Pasha because it contradicts visible same-session experience.
- **Status-only failed-turn context:** rejected because it cannot support semantic follow-up about the shown response.
- **Public worker enum or raw diagnostic payload:** rejected because the public failure and privacy boundary require a generic code and bounded scalar metadata only.

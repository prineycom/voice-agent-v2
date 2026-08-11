# Fix Report: 6-real-local-stt-tts-spoken-conversation

**Source:** Item 1, `/home/priney/Projects/mymate/data/voice-agent-v2-slice-2-model-budget/post-review-fixes.md`
**Status:** ✅ pass
**Scope stayed small:** yes

## Clarification decisions

- Historical Slice 2 Tailscale discovery, schemas, benchmark code, and results remain unchanged as historical evidence.
- This correction changes only Slice 4–5 runtime readiness/request admission and current documentation. It does not add HTTPS or substitute network-verification machinery.

## Changed behavior

- Before: provider readiness and each completion request resolved/classified DNS, checked the kernel route, and ran TSMP/WireGuard proof before contacting LiteLLM.
- After: runtime contacts only exact endpoint `http://rpi:4000`; readiness performs a bearer-authenticated, content-free `/v1/models` check for exact alias `deepseek-v4-flash`. It reports that runtime network proof is not enforced and that temporary HTTP is operator-accepted.
- Redirects remain rejected because only HTTP 200 is accepted and the adapter never follows redirects. Exact alias/response identity, token-file validation, bounded output, cancellation, redaction, explicit failures, and no fallback are unchanged.

## Files changed

| File | Change |
| ---- | ------ |
| `src/voice_agent_v2/cloud_llm.py` | Removed DNS-class, route-interface, Tailscale/TSMP, resolved-address pinning, and shell-command gates; retained exact host/port and authenticated capability/request boundaries. |
| `tests/test_real_adapters.py` | Replaced runtime WireGuard expectations with exact endpoint/authenticated alias/readiness evidence checks and redirect rejection. |
| `scripts/verify_slice4.py` | Updated content-free evidence/output to state that runtime network proof is disabled and temporary HTTP is accepted. |
| `README.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/adr/0003`–`0005` | Recorded Pasha's temporary HTTP decision, deferred HTTPS, and the historical/runtime distinction. |
| Slice 4–5 reports/review | Removed current runtime-proof claims while preserving historical validation context. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src python3 -m unittest -v tests.test_real_adapters.LiteLLMProviderContractTests` | ✅ pass | 9 focused provider tests, including authenticated alias readiness and readiness/completion redirect rejection. |
| `python3 -m py_compile src/voice_agent_v2/cloud_llm.py scripts/verify_slice4.py` | ✅ pass | Corrected runtime and evidence producer compile. |
| `./verify` | ✅ pass | 54 network-denied behavioral/unit/contract tests. |
| `git diff --check` | ✅ pass | No whitespace errors. |

## Correction 2 — PipeWire microphone capture

**Source:** `/home/priney/Projects/mymate/data/voice-agent-v2-slice-2-model-budget/microphone-capture-failure.md`

### Diagnosis

- **Initiating trigger:** the documented CLI invoked a bounded `pw-record --sample-count` capture with `check=True`.
- **Environment/version condition:** canonical `pw-record` is PipeWire/pw-cat 1.6.8. Both raw and WAV sample-count captures, and a raw SIGINT stop, produced audio but returned status 1.
- **Visible symptom:** `check=True` converted that normal host-specific exit into an uncaught `CalledProcessError` traceback before STT admission.
- **Earliest divergence/counterfactual:** the exact one-second production command returned 1 while writing exactly 32,000 expected bytes; changing raw→WAV and sample-count→SIGINT did not change the exit status, disproving flag spelling and raw mode as the cause. Pinned legacy commit `93c5c397…` contains only unrelated Pi `arecord` wakeword examples and no reusable desktop PipeWire path.
- **Independent defect:** capture startup/nonzero/timeout/missing-output exceptions had no CLI normalization boundary.

### Correction

This correction introduced the exact-size PipeWire status-1 regression handling. The current capture, cleanup, retention-reporting, and STT-admission contract is owned by [`docs/architecture.md`](../../architecture.md#7-failure-semantics); the operator command and now-attested human acceptance are owned by [`README.md`](../../../README.md#final-slice-5-human-acceptance).

### Validation at the correction checkpoint

| Command | Result | Notes |
| ------- | ------ | ----- |
| Exact and counterfactual diagnostic captures | ✅ reproduced | Raw sample-count wrote 32,000 bytes with status 1; WAV sample-count and raw SIGINT also returned 1. All diagnostic files were deleted without content inspection. |
| `PYTHONPATH=src python3 -m unittest -v tests.test_run_voice_turn` | ✅ pass | Four executable regression tests cover complete status-1 capture, startup/timeout/nonzero/missing/size failures, cleanup, content-free evidence, and no traceback. |
| Bounded real `microphone_pcm(1.0)` | ✅ pass | Canonical source returned exactly 32,000 bytes and retained no temporary PCM; STT/LLM/TTS were not started. |
| `./verify` | ✅ pass | 58 network-denied behavioral/unit/contract tests. |

## Correction 3 — dated human acceptance

On 2026-08-11, after the microphone correction at PR head `f1a3de997296e6dac87203cf5c2e157945a865b8`, Pasha ran `./run-voice-turn --microphone --duration 8 --play` and reported verbatim: `все сработало! Что дальше?`

This is recorded only as Pasha's acceptance that physical microphone capture, the full Whisper → `deepseek-v4-flash` → Qwen3 `ryan` turn, audible playback, and the overall manual Slice 5 experience succeeded. No transcript, response, latency, pronunciation detail, audio-quality adjective, or granular score is recorded or inferred. Every automated failure and operator limitation remains unchanged.

## Follow-ups

- Slice 6 is not started by this acceptance update.
- HTTPS remains explicitly deferred.
- Pasha authorized merging PR 14 after this documentation update reaches green checks; the agent does not merge it.

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

## Follow-ups

- HTTPS remains explicitly deferred.
- Pasha indicated that more targeted post-review corrections may follow; no new broad final review was started.

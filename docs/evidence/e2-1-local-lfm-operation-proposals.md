# E2.1 pinned-local-LFM operation-proposal evidence

**Issue:** [#30](https://github.com/prineycom/voice-agent-v2/issues/30)  
**Preregistration commit:** `49af77a7c1b5c8b7ca839348f1a99dc207e39fa4`  
**Machine result:** [`tool-proposals-lfm2.5-q4.v1.json`](../../benchmarks/evidence/tool-proposals-lfm2.5-q4.v1.json)  
**Outcome:** `model_operation_proposals_unavailable`

## Frozen measurement

Before the authorized real-model command, the checkpoint committed:

- 240 CC0 public-synthetic Russian fixtures: 80 direct positive requests, 80 ordinary no-operation turns, 40 ambiguous/near-miss turns, and 40 injection/adversarial-data turns;
- five fully materialized fixture permutations (1,200 evaluations per mechanism);
- exactly `llama_cpp_openai_single_tool_call`, `closed_json_choice`, and `deterministic_russian_command_grammar`;
- the exact ADR-0008 LFM2.5 Q4_K_M artifact and llama.cpp `b10357` identity, response alias, no credentials, and no fallback;
- 100% valid closed envelopes, zero proposal false positives in every non-positive class, at least 98% exact positive selection, 100% response identity, zero timeouts, and a complete matrix;
- two concurrent requests maximum, a 570-second matrix reserve inside the 600-second outer process/cleanup bound;
- a closed counts-only result schema and a fixed model-mechanism priority rule.

The file and semantic hashes are in the preregistration and are executable invariants. Result validation proves that its preregistration commit is an ancestor and that the historical preregistration equals the current file.

## Authorized invocation and exact failure

The exact command was invoked once:

```sh
./verify-local-lfm --tool-proposals-only
```

The 600-second owner admitted its child, but the committed `verify_local_lfm.py` consumed the selector and then called the nested benchmark parser without clearing that selector. The nested parser rejected `--tool-proposals-only` and exited before artifact hashing, server startup, runtime identity proof, fixture admission, or any model request.

This is recorded as stable outer failure class `benchmark_cli_admission_failure`. The forwarding defect is corrected for future code integrity, but the frozen benchmark is **not rerun**. There was no model observation from which to tune a prompt, format, threshold, order, corpus, identity, timeout, concurrency, or schema.

## Privacy-safe raw counts

Each row had 1,200 preregistered evaluations.

| Mechanism | Attempted | Not attempted | Valid | Invalid | False positives | Exact positive selections | Timeouts | Identity match / mismatch / missing | Pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `llama_cpp_openai_single_tool_call` | 0 | 1,200 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | no |
| `closed_json_choice` | 0 | 1,200 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | no |
| `deterministic_russian_command_grammar` | 0 | 1,200 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | no |

All proposal-by-class and measured failure counters are zero because nothing was admitted. Those zeros are not safety or quality results. `matrix_complete=false` and `artifact_and_runtime_verified=false` prevent relabelling them as a pass. The hermetic suite separately proves the deterministic grammar implementation over the fixed corpus, but that unit fact does not replace the unstarted authorized matrix.

Tracked evidence retains zero private fixtures, model completions, prompts paired with completions, raw arguments/results, credentials, environment values, host identity, or raw sensitive paths. The public authored corpus is intentionally tracked; default evidence is counts and fixed identifiers only.

## Decision and production effect

No model-backed mechanism passed because none was measured. The exact machine decision is therefore:

```text
model_operation_proposals_unavailable
```

Ordinary local conversation is the only active behavior. `config/agent-capabilities-v1.json` remains exact-empty, and test capability IDs/handlers exist only in benchmark/test ownership outside `src/`. There is no runtime tool schema, dispatcher, prompt change, proposal adapter, capability activation, status/UI/voice change, host/web/network/file/credential/lifecycle operation, cloud route, or fallback.

## Nonclaims

- No valid-envelope, false-positive, positive-selection, timeout, or response-identity rate was measured from the pinned model.
- The grammar's hermetic deterministic pass is not a real-model result and was not substituted into the machine evidence.
- The failure does not authorize a retry, another model/provider, changed benchmark field, runtime activation, Issue #31, or Issue #32.
- This slice makes no physical voice, browser, resource, reboot, or Raspberry Pi claim.

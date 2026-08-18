# Do Report: 30-e2-1-local-lfm-tool-proposals

**Source:** https://github.com/prineycom/voice-agent-v2/issues/30  
**Parent:** E2 — Tool-Calling Framework  
**Status:** ⚠️ partial — implementation passes hermetic/canonical verification; the one authorized real-model matrix is factually unavailable

## Changed files

| File(s) | Change | Acceptance criteria |
| --- | --- | --- |
| `benchmarks/config/tool-proposals-*.json` | Frozen candidate formats, hashes, orders, identity, thresholds, timeout, concurrency, schema and selection rule | Preregistration before result |
| `benchmarks/fixtures/tool-proposals-russian.v1.json` | Fixed 240 public-synthetic Russian fixtures in exact 80/80/40/40 classes | Public corpus |
| `benchmarks/schemas/tool-proposals-*.schema.json` | Closed stable corpus/order/preregistration/result shapes | Stable schemas/evidence validation |
| `benchmarks/tool_proposals/` | Strict parsers, scoring, pure test handlers, bounds, ancestry/hash/privacy/result checks | Reproducible benchmark; test-only ownership |
| `scripts/verify_tool_proposals.py`, `scripts/verify_local_lfm.py` | One-shot exact-cache benchmark mode and corrected selector forwarding after the consumed unavailable attempt | 600-second/two-slot/no-fallback command |
| `benchmarks/evidence/tool-proposals-lfm2.5-q4.v1.json` | Exact counts-only unavailable result, tied to checkpoint `49af77a…` | No hidden failures; machine decision |
| `tests/test_tool_proposals_benchmark.py`, `scripts/run_behavior_tests.py` | Network-denied hermetic corpus/schema/parser/grammar/threshold/production-isolation coverage | Deterministic PR coverage |
| `CONTEXT.md`, `docs/architecture.md`, `docs/testing.md`, `README.md`, `benchmarks/README.md`, `docs/evidence/e2-1-local-lfm-operation-proposals.md` | Vocabulary, boundary, command authority, result/nonclaims, and raw-count evidence | Accepted docs/evidence integration |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Committed preregistration precedes executable result evidence | Pass | Checkpoint `49af77a7c1b5c8b7ca839348f1a99dc207e39fa4`; result records and validates it |
| Exact 240 public fixtures and 80/80/40/40 classes; no production test capabilities | Pass | Corpus, hermetic structural test, exact-empty production registry |
| Three-mechanism raw counts and stable failure classes without hidden runs | Pass as unavailable evidence | All mechanisms attempted 0 / not-attempted 1,200; `matrix_complete=false`; outer class `benchmark_cli_admission_failure` |
| Name passing model or decide unavailable | Pass | `model_operation_proposals_unavailable`; selected mechanism null |
| No forbidden operation/provider/private evidence | Pass | Benchmark-only pure functions, local fixed endpoint, content-free result validation; no model request occurred |
| Production registry exact-empty; ordinary behavior unchanged | Pass | No `src/`, runtime prompt/composition/status/UI/voice or config-registry change |
| Canonical `./verify` passes | Pending until final delivery step | Must run exactly once after all edits |
| Complete exact-model matrix | Unavailable, not falsely passed | Sole invocation failed CLI admission before runtime/model/fixture work; no retry permitted |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `python3 -B -m unittest tests.test_tool_proposals_benchmark -v` | Pass | 8 deterministic tests before real invocation |
| `./verify-local-lfm --tool-proposals-only` | Unavailable/fail | Invoked exactly once; nested parser rejected selector before matrix start |
| `python3 -B scripts/verify_tool_proposals.py --validate-only` | Pass | Validates preregistration and recorded unavailable evidence without model work |
| `./verify` | Pending | Sole canonical PR gate; exact one run at final head |

## Unresolved uncertainty

- The pinned LFM's behavior for both model-backed mechanisms remains unmeasured. This preregistration cannot be retried or tuned under Issue #30's authorization.
- Issues #31 and #32 remain out of scope and unimplemented.

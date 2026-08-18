# Review: 30-e2-1-local-lfm-tool-proposals

**Source:** Issue #30, implementation checkpoint/result diff  
**Status:** ⚠️ pass with factual unavailable-model finding

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Critical | The sole `./verify-local-lfm --tool-proposals-only` output shows the nested parser rejected the already-consumed selector before matrix admission | Exact-model matrix cannot be claimed complete or passing and cannot be retried | Record incomplete/unavailable counts, select no model mechanism, fix forwarding only for code integrity, preserve frozen fields | Fixed transparently; measurement remains unavailable |
| Important | The initial content-key guard also rejected its own exact zero-valued retention-count field | Recorded unavailable evidence could not validate | Keep the closed schema's exact zero counters and reject raw-content key shapes instead | Fixed |
| Minor | None | — | — | — |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Nested selector forwarding | `scripts/verify_local_lfm.py`, `scripts/verify_tool_proposals.py` | Nested `main([])` no longer reparses the outer selector; no benchmark retry performed |
| Privacy guard self-rejection | `benchmarks/tool_proposals/harness.py` | `--validate-only` and the hermetic benchmark suite validate the counts-only evidence |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Complete the real-model matrix | Explicit single-run/no-retry contract forbids rerunning after the consumed invocation; zeros are retained only as not-attempted counts |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `python3 -B -m unittest tests.test_tool_proposals_benchmark -v` | Pass | 8 tests; no external/model network |
| `python3 -B scripts/verify_tool_proposals.py --validate-only` | Pass | Preregistration ancestry/hashes, stable counts, no activation |
| `git diff --check` | Pass | No whitespace errors |
| `./verify` | Pending final exact-once gate | Must be saved in full and not rerun locally |

## Recommendations

- Deliver the transparent no-go result. Do not activate, tune, substitute, or retry a proposal mechanism under this preregistration.
- Keep Issues #31 and #32 queued; Issue #30 supplies only `model_operation_proposals_unavailable`.

# Review: 3-measured-host-model-and-llm-provider-budget

**Source:** Issue #3 and current diff
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Important | `benchmarks/slice2/cloud_measure.py`: fixture text and the system prompt could otherwise change after preregistration. | A modified input could make results no longer correspond to precommitted thresholds. | Require the preregistration, both fixture hashes, and runner itself to be tracked, committed, and clean before network I/O. | Fixed before review completion. |
| Important | `benchmarks/slice2/cloud_measure.py`: an HTTP/provider error originally aborted without a complete content-free observation. | Error-rate and privacy-safe failure gates could not be aggregated. | Convert transport/HTTP/redirect/SSE failures into bounded error classes without retaining response bodies. | Fixed before review completion. |
| Important | `benchmarks/slice2/cloud_scoring.py`: the first draft did not evaluate every declared identity/privacy/cancellation/sustained threshold. | A cloud candidate could appear eligible without full preregistered gate coverage. | Add gates for route/alias/fallback, usage, request filtering, cancellation peers/recovery, sustained throughput/RAM, and safe error handling. | Fixed before review completion. |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Commit/fixture integrity | `benchmarks/slice2/cloud_measure.py` | `_require_committed_preregistration()` checks Git cleanliness and fixture SHA-256 before transport/token access. |
| Content-free failure capture | `benchmarks/slice2/cloud_measure.py` | `_chat_request()` records only HTTP status and bounded error class while discarding error bodies. |
| Complete automated gate aggregation | `benchmarks/slice2/cloud_scoring.py` | Deterministic score covers concurrency, token throughput, fairness, isolation, identity, transport, privacy, cancellation, sustained behavior, and context pressure. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Provider provenance, cost, retention/training, region, and upstream endpoint are unverified. | Explicit user decision accepts operator-attested opaque routing only for public synthetic Slice 2 testing; preregistration marks production/private use false. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `python3 -m py_compile benchmarks/slice2/cloud_measure.py benchmarks/slice2/cloud_scoring.py` | ✅ pass | Runner/scorer compile. |
| `./benchmark-slice2 validate` | ✅ pass | Local/cloud preregistrations, discovery, schemas, results, and selection validate offline. |
| Precommit cloud measurement attempt | ✅ blocked | Exit 2 before transport/token/request while v2 preregistration/runner are uncommitted. |
| `./verify` | ✅ pass | 26 behavioral tests; Slice 1 unchanged. |
| `git diff --check` | ✅ pass | No whitespace errors. |
| Secret/large-file preflight | ✅ pass | No secret-like untracked repository files or files over 1 MiB. |

## Recommendations

- Commit the complete superseding preregistration and runner before the first completion request.
- After commit, run only the primary `deepseek-v4-flash` synthetic benchmark; stop on automated failure or at blind human scoring.

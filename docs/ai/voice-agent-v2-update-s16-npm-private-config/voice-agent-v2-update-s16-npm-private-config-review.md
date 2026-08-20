# Review: voice-agent-v2-update-s16-npm-private-config

**Source:** `voice-agent-v2-update-s16-npm-private-config-report.md`
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Important | `release/runtime-assembler.cjs` initially omitted the read-only npm cache mount from the final network-none assembly container, while the new boundary requires that exact cache identity before `npm run`. | A physical assembly would fail the boundary after preflight despite valid cache/config custody. | Carry the exact read-only cache mount into assembly and assert it in orchestration coverage. | Fixed |
| Important | Initial npm-boundary mismatch handling was folded into generic content extraction failure and host inspection checked the tool report first. | Wrong config identity could lose its precise privacy-safe upfront classification. | Preserve the bounded npm report, inspect it before the tool report when present, and prove cache/acquisition never starts. | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Final assembly lacked the validated cache view. | `release/runtime-assembler.cjs`, `release/runtime-assembler.test.cjs` | Assembly now receives `/npm-cache:ro`; the phase matrix asserts it. |
| Npm boundary errors were not first-class orchestration failures. | `release/assemble-runtime.sh`, `release/runtime-assembler.cjs`, `release/runtime-assembler.test.cjs` | Boundary mismatch retains its report, returns `runtime_npm_boundary_invalid`, remains content-free and stops after `tool-preflight`. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Real OCI/npm acquisition and complete physical assembly | Explicitly prohibited by the task and owned by the separate deployment tier. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | The final canonical run is retained at `evidence/verify.txt`; no alternate validation pipeline was used. |

## Recommendations

- Commit the complete isolated branch, push it, open the direct PR and require exact-head CI.
- After merge, rerun the repository-owned opt-in no-output preflight against the existing exact private cache before any assembly.

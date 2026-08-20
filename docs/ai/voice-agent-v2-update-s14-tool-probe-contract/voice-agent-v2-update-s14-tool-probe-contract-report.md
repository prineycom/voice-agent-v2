# Do Report: voice-agent-v2-update-s14-tool-probe-contract

**Source:** Private S14 production-preflight correction brief (task authority; not committed)
**Parent:** PR 61 builder-tool closure
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/inputs/builder-tools.v1.json` | Move to the closed v2 per-tool contract, remove unused `false`, canonicalize tool paths, define bounded public/content probes, and pin exact parent-bound custody for non-public compiler children. | 1–3, 6 |
| `release/tool-preflight.sh`, `release/assemble-runtime.sh` | Execute only admitted exact vectors, cap/hash output, validate no-link owner/mode/hash custody without executing internals, emit content-free receipts, and keep all build phases network-disabled. | 2–5 |
| `release/runtime-assembler.cjs` | Validate the exact invoked inventory and parent graph, mount the fixed probe implementation, bind deterministic evidence into receipts, and bound/sanitize the complete mismatch report. | 1–4 |
| `release/runtime-assembler.test.cjs` | Cover public/content probes, the six observed special cases, missing/tampered/version/exit/oversize/link/parent failures, builder digest refusal, and unchanged cache/network orchestration. | 4–5 |
| `README.md`, `release/inputs/README.md`, `docs/architecture.md`, `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/testing.md`, `docs/evidence/runtime-assembler-model-views.md`, `AGENTS.md` | Record the invoked-only evidence boundary, deterministic non-public custody, failure semantics, and opt-in no-fetch repeat command for an already complete cache. | 1–6 |
| `docs/ai/voice-agent-v2-update-s14-tool-probe-contract/evidence/verify.txt` | Retain the complete canonical verification output. | 5 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Invoked-only closure | ✅ | The exact ordered authority excludes `false`; CMake disables Git discovery without a fake executable, and unknown/missing inventory is rejected. |
| Closed per-tool evidence | ✅ | Public/content rows bind exact argv, exit status, output cap, prefix and version. `cc1`, `cc1plus`, `lto1`, `lto-wrapper`, and canonical CUDA `cicc` bind exact root-owned mode-`0755` SHA-256 custody to a passing GCC/G++/NVCC version. |
| Digest plus per-tool receipts | ✅ | Builder digest inspection remains first; successful rows emit deterministic probe-output hashes or exact internal-file hashes into the authority and assembly receipts. No CLI/environment probe override exists. |
| Content-free complete failures | ✅ | Raw tool output never enters the report. One size-bounded parser reports all absent/mismatch/blocked/extra names and sanitizes malformed extras. |
| Deterministic tests and unchanged orchestration | ✅ | Network-free fixtures exercise each evidence class and requested failure; existing orchestration still gives only raw npm acquisition `pasta`, with extraction/install/build `--network=none` and unchanged content-addressed input identities. |
| Deployment rerun | ✅ | The documented full `preflight-runtime --cache <same-complete-cache>` repeat performs no acquisition once the exact npm/content cache is complete; it is not a builder-only selector. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Canonical gate passed; complete output retained under `evidence/verify.txt`. |

## Unresolved uncertainty

- Physical builder preflight remains an opt-in deployment-tier check. No full assembler, publication, signing/token access, or live-stand operation was run.

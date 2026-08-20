# Do Report: voice-agent-v2-update-s11-codeload-authority

**Source:** First production `assemble-runtime --fetch` blocker brief
**Parent:** ADR-0017 distribution correction
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/inputs/runtime-sources.v1.json`, `release/inputs/README.md` | Replace only the llama.cpp locator with the canonical direct codeload full-commit URL while retaining its filename, commit, size, SHA-256 and license receipt. | 1–2 |
| `release/runtime-assembler.cjs`, `release/runtime-assembler.test.cjs` | Close codeload to one exact owner/repository/path/receipt commit, forbid codeload redirects and credentials, and cover direct acquisition, attacks, digest refusal and cache reuse with network-free fixtures. | 1–4 |
| `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/evidence/runtime-assembler-model-views.md`, `docs/testing.md` | Record the corrected immutable-input boundary and deterministic evidence without changing launcher authority. | 3–5 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Direct immutable llama.cpp locator | ✅ | Exact `codeload.github.com/ggml-org/llama.cpp/tar.gz/<40-hex-commit>` input; explicit prior archive filename retained. |
| Accepted byte/source/license/builder identity retained | ✅ | Committed-authority test fixes the original size, SHA-256, commit/version, MIT receipt and exact OCI builder digest. |
| No redirect/token/launcher trust broadening | ✅ | Codeload refuses every redirect into or out of the authority and the request fixture proves no Authorization/Cookie header; launcher source code is unchanged. |
| Deterministic attack/cache/actual-document coverage | ✅ | Runtime assembler tests cover owner, repository, commit abbreviation/mismatch, tag/branch-style path, query, fragment, userinfo, host, redirect, digest and repeat-cache cases; the OCI fixture validates the committed input document without network. |
| Canonical verification only | ✅ | `./verify` passes; full output is saved under `evidence/verify.txt`. No real input fetch, OCI build, signing, publication or live-stand operation ran. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | Pass | Sole canonical gate; `canonical_pr_gate: PASS`, no unexpected skips. |

## Unresolved uncertainty

- Real production input acquisition and the two physical OCI assemblies remain outside canonical verification and were not run in this correction.

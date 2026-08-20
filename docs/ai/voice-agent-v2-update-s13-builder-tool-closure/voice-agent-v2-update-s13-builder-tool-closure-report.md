# Do Report: voice-agent-v2-update-s13-builder-tool-closure

**Source:** Private S13 builder-tool-closure brief (task authority; not committed)
**Parent:** Compiled distribution bridge / ADR-0017
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/inputs/runtime-sources.v1.json`, `release/inputs/builder-tools.v1.json`, `release/inputs/README.md` | Replace Node 26.7.0 `.tar.xz` with the official exact `.tar.gz`; bind exact size/SHA-256, signed checksum/signature/signer and MIT receipt; declare the complete builder/content/npm tool authority. | 1–2, 4, 6 |
| `release/runtime-assembler.cjs`, `release/assemble-runtime.sh`, `release/release.cjs` | Add early no-output preflight, phased tool-only acquisition, restricted PATH, complete privacy-safe tool report, raw npm cache acquisition, network-disabled extraction/install/build, exact tool receipts and the `preflight-runtime` command. | 2–5 |
| `release/runtime-assembler.test.cjs` | Add deterministic gzip-without-xz success, size/digest/cache custody, absent/tampered/version/provenance/extra tool, complete pre-output failure, host-fallback denial and exact network-phase orchestration coverage. | 1–5 |
| `README.md`, `AGENTS.md`, `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/runtime-assembler-model-views.md` | Record the corrected immutable archive and complete tool/network/receipt authority without claiming a physical production build or live-stand change. | 2–6 |
| `docs/ai/voice-agent-v2-update-s13-builder-tool-closure/evidence/verify.txt` | Retain the complete final canonical verification output. | 5 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Smallest reproducible Node correction | ✅ | Official Node 26.7.0 Linux x64 gzip archive is fixed at 62,014,253 bytes and SHA-256 `bd6b6c…28df`; its exact checksum document and detached EdDSA signature identities, signer fingerprint and license receipt are committed and validated. Node version is unchanged; no xz or mutable package is added. |
| Complete early assembler tool closure | ✅ | One closed manifest separates builder-digest shell/archive/file/checksum/compiler/linker/binutils/CUDA tools, content-addressed Node/npm/CPython/pip/CMake/patchelf, and npm-lock Linux web build tools. Preflight reports all absent/mismatch/blocked/extra rows before remaining large input acquisition or output creation. |
| Network and host boundary retained | ✅ | Builder/content extraction and npm installation are `--network=none`; only integrity-bound raw npm acquisition uses fixed rootless pasta. Containers retain empty environment/config/auth, declared mounts only and no socket/host build-tool fallback. |
| Output identity, receipts and exclusions retained | ✅ | Runtime generation still excludes Node and all build tools; runtime receipt continues to omit web-only Node, while assembly receipts add exact tool authority/version/provenance. Candidate/runtime source/component authority is otherwise unchanged. |
| Deterministic network-free tests | ✅ | Canonical fixtures cover gzip extraction without xz, signed authority, size/digest/cache variants, complete tool failure, restricted PATH, offline tool install/extraction, pasta-only raw acquisition and network-none build. |
| Documentation/evidence and operational safety | ✅ | ADR/architecture/testing/evidence/input docs updated. No real assembler/preflight, production input/model fetch, signing/token/publication action, or live-stand inspection/mutation occurred. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Final complete output retained at `evidence/verify.txt`; `canonical_pr_gate: PASS`, `RESULT: PASS`. An initial run identified only a deterministic fixture mount-suffix error; it was corrected before the retained passing run. |

## Unresolved uncertainty

- The opt-in `preflight-runtime` and real builder remain intentionally unexecuted here. Exact physical builder paths/versions and the later multi-gigabyte byte-identical assemblies remain deployment-tier evidence.

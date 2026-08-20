# Do Report: voice-agent-v2-update-s16-npm-private-config

**Source:** Private S16 npm private-config brief (task authority; not committed)
**Parent:** PR 63 Node builder compatibility / ADR-0017
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/npm-boundary-preflight.cjs`, `release/assemble-runtime.sh` | Create and prove distinct empty owner-only npm user/global configs; isolate home/cache/prefix/temp; strip ambient package/network authority; execute npm 11 through the exact Node wrapper. | 1–4 |
| `release/runtime-assembler.cjs`, `release/release.cjs` | Validate the reachable integrity-complete public-registry lock inventory, emit/inspect privacy-safe npm-boundary authority, check cache offline before acquisition, restrict pasta to missing bytes, mount offline cache read-only and bind cache/npm evidence into receipts. | 2–4 |
| `release/runtime-assembler.test.cjs` | Cover exact success, npm 11 duplicate-role refusal, path/inode/link/mode/owner/escape attacks, ambient variables/registry override, malformed lock inventory, offline phases and repeat cache identity. | 5 |
| `AGENTS.md`, `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/runtime-assembler-model-views.md`, `release/inputs/README.md` | Record the corrected npm authority, evidence boundary and non-network repeat behavior without adding physical-build claims. | 6 |
| `evidence/verify.txt` | Retain the complete final canonical verification output. | 5–6 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Distinct private config and directory custody | ✅ | Exact fixed paths are created under owner-only private roots; regular-file, emptiness, link count, owner, mode, canonical path and distinct inode checks precede npm. |
| No ambient package/network authority | ✅ | Container host environment remains disabled; the script additionally strips npm/yarn/pnpm/Corepack/Node options plus registry/auth/token/certificate/proxy variables and supplies only the fixed public registry/config boundary. Host package configs are neither mounted nor copied. |
| Exact bounded network and lock authority | ✅ | The full reachable lock-v3 inventory requires canonical registry locators and SHA-512 integrity. A network-none cache check precedes any optional pasta acquisition; offline install/build remain network-none with the cache read-only. |
| Upfront npm 11 evidence and repeat identity | ✅ | The content-free boundary receipt proves exact npm 11 version/config behavior before acquisition/output. Complete-cache reruns skip pasta and compare the cache tree byte-for-byte around offline preparation. |
| Deterministic refusal matrix | ✅ | Canonical Node tests cover distinct success, identical paths and the observed double-load behavior, inode aliases, symlinks, wrong custody, ambient injection, registry/integrity/inventory attacks, offline network phases and repeat identity. |
| Operational safety | ✅ | No real preflight/assembler, production secret, signing/publication action, Herdr lifecycle action or live-stand mutation was performed. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Complete final output retained at `evidence/verify.txt`; 107/107 launcher/release tests, 390 hermetic Python tests, 9 local-socket tests, 88 Vitest tests and Firefox/actual-LiveKit smoke passed; `canonical_pr_gate: PASS`, `RESULT: PASS`. An earlier canonical run exposed an existing-cache `mkdir` rerun bug; the final implementation uses idempotent owner-mode restoration and the retained run is fully passing. |

## Unresolved uncertainty

- The real opt-in production preflight/assembler and live stand were deliberately not run. Physical builder/cache acceptance remains the separately authorized deployment tier.

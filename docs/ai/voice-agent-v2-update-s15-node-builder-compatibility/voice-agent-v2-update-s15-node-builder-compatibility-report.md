# Do Report: voice-agent-v2-update-s15-node-builder-compatibility

**Source:** Firstmate launch brief after PR 62
**Parent:** ADR-0017 compiled distribution authority
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `release/inputs/*`, notices | Add exact Rocky `libatomic` RPM/hash/signature/source/license and v3 Node-runtime authority | Smallest content-addressed correction; license/provenance |
| `release/node-compatibility-preflight.sh`, assembler/release code | Network-none RPM extraction, exact-loader wrapper, complete ELF/symbol/cause report before npm/output, receipt propagation | Exact correction; upfront bounded compatibility; no host fallback |
| `release/runtime-assembler.test.cjs` | Deterministic pass/failure/isolation/reproducibility/runtime-exclusion coverage | Requested regression matrix |
| ADR/architecture/roadmap/testing/evidence/README/AGENTS | Record cause, authority and nonclaims without private paths | Durable authority/evidence |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Privacy-safe exact diagnosis | Pass | `docs/evidence/node-builder-compatibility.md`; exact focused network-none builder reproduction |
| Smallest compatible authority | Pass | Unchanged Node bytes plus matching build-only `libatomic`; no separate builder/fallback |
| Build-only isolation | Pass | Fixed loader/library wrapper exists only below `/build/tools`; application runtime exclusion retained |
| Upfront complete preflight | Pass | 16 exact compatibility facts and parent/child provenance precede npm acquisition/output |
| Deterministic refusal matrix | Pass | Canonical Node tests cover exit 127, corrected boundary, loader/library/symbol/architecture/interpreter/environment differences, network-none and repeat receipts |
| Documentation/licenses | Pass | ADR-0017, input authority, notices and evidence updated |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | 100 Node tests, 400 Python tests across bounded phases, 88 Vitest tests, builds and Firefox/LiveKit smoke; full output in `evidence/verify.txt` |

## Unresolved uncertainty

- Full cached production preflight and two multi-GB byte-identical assemblies remain deployment-tier work after merge; no live stand, signing, token or publication authority was touched.

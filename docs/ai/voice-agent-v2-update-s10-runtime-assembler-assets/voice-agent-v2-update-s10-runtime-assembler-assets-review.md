# Review: voice-agent-v2-update-s10-runtime-assembler-assets

**Source:** S10 do report and complete diff against `origin/main`
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| -------- | -------- | ----------------- | -------------- | ---------- |
| Important | `launcher/update.cjs` restoration originally rewrote prior config without re-materializing its aggregate views; rollback target views also relied on earlier presence. | An interrupted/tampered missing view could prevent exact prior restart despite retained raw custody. | Revalidate recorded model sets, rebuild exact views before quiesce/restoration, then write prior runtime config before start. | Fixed |
| Important | `launcher/asset-cache.cjs` originally used path-based copy and cleanup after validation. | Same-user race could replace a staged/source entry between validation and copy/cleanup. | Copy through no-follow source/target descriptors with fstat/hash/fsync and use one validated single-link stage cleanup. | Fixed |
| Important | `release/assemble-runtime.sh` originally passed every ELF to patchelf and copied only regular llama `.so` files. | A static LiveKit binary could fail assembly; skipped SONAME symlinks or duplicate llama providers could make the native closure incomplete/duplicated. | Rewrite RUNPATH only for dynamic ELF, materialize all llama library aliases and move reached llama/NVIDIA providers into the common closure. | Fixed |
| Minor | Distribution schema/docs still described an external/blocked assembler and had open runtime receipt rows. | Authority text and machine documentation lagged the implemented closure. | Close source/component/file schema fields and update ADR/architecture/roadmap/testing/README/notices/evidence. | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Exact prior view/config recovery | `launcher/install.cjs`, `launcher/update.cjs`, `launcher/adopt.cjs`, lifecycle tests | Candidate-failure and recorded rollback tests delete prior views, then prove regenerated views/runtime config and exact readiness. |
| No-follow model copy/cleanup | `launcher/asset-cache.cjs`, `launcher/test/assets-self-update.test.cjs` | Descriptor-backed fd copy rehashes bytes; partial/race/tamper/extra/link cases pass. |
| Static ELF and native de-duplication | `release/assemble-runtime.sh`, `release/runtime-assembler.test.cjs` | Static dynamic-section guard and provider-move contract pass the canonical launcher phase. |
| Closed authority/docs | contracts, ADR-0017, architecture, roadmap, testing, README, input README, notices, evidence | JSON/schema parsing and canonical repository gate pass. |

## Skipped issues

| Finding | Reason |
| ------- | ------ |
| Real OCI assembly and physical archive measurements | Intentionally outside canonical PR verification; documented separate maintainer/physical tier. |
| Production signing/publication and live-host acceptance | Production secrets/live mutation are explicitly forbidden in this task. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Sole canonical gate: 85 launcher/release tests, 390 hermetic Python tests, 9 local-socket tests, 88 Vitest tests, typecheck/builds and Firefox/LiveKit smoke; no unexpected skips. |

## Recommendations

- Consolidate the two WIP checkpoints plus final fixes/docs/evidence into one final commit.
- Push only the task branch, open the direct PR, and require exact-head CI before delivery-ready status.
- Keep real runtime assembly, production authority and disposable physical-host evidence as the documented follow-up tiers.

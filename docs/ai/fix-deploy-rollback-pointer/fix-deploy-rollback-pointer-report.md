# Do Report: fix-deploy-rollback-pointer

**Source:** Firstmate launch brief for the canonical-stand immutable-release transition defect
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/operations.py` | Narrow exact prior runtime-contract admission; deploy preservation; transactional exact-target prior-pointer reconciliation; rollback support | Preserve/reconstruct rollback custody without weakening immutable release checks |
| `src/voice_agent_v2/operations_cli.py` | Pre-restart active runtime/build/unit/MainPID/cwd/executable/argv validation and bounded reconciliation | Repair selected-new/running-old/no-previous safely; exact rollback on activation failure |
| `tests/test_operations.py` | Transition, compatible control, divergent apply, mismatch, idempotence, failure restore, exact-new readiness, process identity, and unrelated-retention regressions | Behavioral regression matrix |
| `README.md`, `docs/architecture.md`, `docs/adr/0013-systemd-bounded-single-host-operations.md`, `docs/testing.md` | Authoritative operational contract and invariant ownership | Document only changed operational behavior |
| `docs/evidence/deploy-rollback-contract-transition.md` | Diagnostic reasoning, deterministic evidence, disconfirming evidence, and nonclaims | Durable privacy-safe evidence |
| `artifacts/fix-deploy-rollback-pointer-verify.log` | Full canonical verification output | Delivery validation record |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Distinguish trigger, old-contract masking, and selected-new/running-old state | ✅ | Transition evidence and divergent fixtures |
| Identify earliest pointer divergence and smallest counterfactual | ✅ | Evidence traces the original conditional retention decision; only the exact superseded Python declaration is admitted |
| Preserve compatible deploy behavior | ✅ | Deploy fixture retains a narrow-transition prior release; existing compatible failure restore remains green |
| Reconcile the already-divergent state without blind pointer edits | ✅ | Apply uses exact runtime/build target plus immutable host/unit/process proof before an atomic transaction |
| Reject unsafe/mismatched/unrelated candidates | ✅ | Mismatch and custody owners leave pointers, processes, and unrelated releases unchanged |
| Idempotent recovery and exact-new success | ✅ | Repeated apply fixture performs one activation restart total and proves exact-new readiness |
| Restore exact prior runtime after activation failure | ✅ | Divergent and compatible induced-failure fixtures swap back and wait for exact prior readiness |
| No cleanup/migration/restart scope growth | ✅ | No scanning, deletion, GC, count change, wildcard, generalized migration, or additional restart path |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ PASS | 393 hermetic Python behaviors, 9 local-socket behaviors, 88 Vitest tests, typecheck/builds, and production Firefox/LiveKit smoke passed; full output saved in the tracked artifact |

## Unresolved uncertainty

- Real canonical-host service apply, rollback, reboot, physical voice, full-stack soak, and Raspberry Pi acceptance remain separate parked deployment/acceptance work. No live release store or service was touched.

# Fix Report: 71-acceptance-host-prep

**Source:** Promoted canonical-host preparation report at `/home/priney/Projects/mymate/data/voice-agent-v2-acceptance-host-prep-71/report.md`  
**Status:** ✅ pass  
**Scope stayed small:** yes

## Clarification decisions

- Preserve the accepted immutable-cache claim: symlinks are admitted only through explicit resolved-target custody, never from their Linux `0777` link mode alone.
- Declare the smallest common user-owned external target of the selected local-LFM CUDA overlay, exact `nvidia/cu13`, as `local-lfm-cuda-runtime` with a 2-GiB non-destructive bound.
- Keep mutable STT/Silero state roots excluded and retain read-only diagnosis.

## Changed behavior

- Before: every symlink made `immutable_tree()` fail, while its printed `chmod -R a-w` remedy could not change symlink mode bits.
- After: a stable symlink passes only when its resolved target is non-writable and contained in the same tree, belongs to another declared immutable cache root, or has root-owned/non-group-or-other-writable path custody plus positive Arch package ownership evidence.
- Writable declared targets now print `chmod -R a-w` for the exact dependency root. Dangling, looping, replaced, undeclared external, writable, symlinked-root, and unsafe-traversal cases remain closed.
- The canonical host now identifies the writable `local-lfm-cuda-runtime` root precisely instead of mislabelling four symlink-containing runtime trees.

## Files changed

| File | Change |
| ---- | ------ |
| `src/voice_agent_v2/stand_doctor.py` | Added stable resolved-target inspection, declared-cache/package custody, race/escape failure details, and exact remedies. |
| `config/operations-v1.json` | Declared the exact local-LFM CUDA package subtree and 2-GiB bound. |
| `src/voice_agent_v2/operations.py` | Closed the tracked manifest contract over the new cache root. |
| `tests/test_stand_doctor.py` | Added real-filesystem regression coverage for contained/declared links, remedy-to-ready behavior, undeclared targets, dangling links, loops, unsafe declared roots, and package evidence. |
| `README.md` | Documented operator-visible cache symlink custody. |
| `docs/architecture.md` | Recorded the authoritative shared-cache custody boundary. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src python -m unittest -v tests.test_stand_doctor` | ✅ pass | 9 tests; real temporary filesystem and deterministic package-evidence seam. |
| `PYTHONPATH=src python -m unittest -v tests.test_operations.OperationsManifestTests` | ✅ pass | 9 exact-manifest/systemd contract tests. |
| `./stand doctor` on the canonical host | ✅ corrected diagnosis | Existing five prepared roots pass symlink custody; exact new `local-lfm-cuda-runtime` root and remedy are reported. This is not an Acceptance claim. |
| `./verify` | ✅ pass | Run exactly once: Python 412+9, Vitest 88, typecheck, both builds, actual-LiveKit Firefox smoke; `RESULT: PASS`, exit 0. Full owner-only log: `/home/priney/Projects/mymate/data/voice-agent-v2-acceptance-host-prep-71/verify.log`. |
| `git diff --check` | ✅ pass | No whitespace errors. |

## Follow-ups

- After this PR is merged, extend the existing owner-only permission/ACL inventory to the newly declared `local-lfm-cuda-runtime`, apply its printed read-only remedy, and require literal `stand doctor: READY` before issue #71 Acceptance.
- Do not start or claim issue #71 Acceptance from this code-only correction.

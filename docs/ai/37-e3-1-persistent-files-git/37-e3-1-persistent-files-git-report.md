# Do Report: 37-e3-1-persistent-files-git

**Source:** https://github.com/prineycom/voice-agent-v2/issues/37  
**Parent:** #36  
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `agent-environment/helpers/agent-helper` | Bounded binary read/search/write/edit/patch helper, atomic/fsynced convenience writes, fresh-shell cwd observation, content-free receipt accounting | Ordinary files, hashes/ranges, atomicity, cwd, no ambient state |
| `agent-environment/Dockerfile` | Local Git/archive tools and container-local package-manager privilege | Local Git/archive/package workflows in persistent rootfs |
| `src/voice_agent_v2/agent_environment.py`, `agent_run.py` | Status/receipt v2 accounting, bounded result propagation, logical-cwd/persistence truth | Binary-safe receipts and complete persistence matrix |
| `contracts/` | Versioned status and receipt schemas | Closed machine-readable status/receipt surface |
| `tests/`, `scripts/run_behavior_tests.py` | Disposable-root helper checks plus fake-Docker cwd/status cases in canonical manifest | Deterministic ordinary-file and no-host-fallback coverage |
| `README.md`, `docs/` | E3.1 boundary, evidence, tiers, and nonclaims | Coherent documentation without platform claims |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Normal Docker-exec-only file/shell/package/archive/local-Git authority in one environment | Pass | Fixed helper route retained; image tooling and no-selector E2.4 tests |
| Binary round-trip, hashes, ranges and truncation are explicit | Pass | `PersistentOrdinaryFilesTests.test_binary_range_hash_and_atomic_write_are_truthful` and receipt v2 |
| Atomic write/edit/patch preserves committed bytes on failure | Pass | Same-directory `0600` temporary, fsync/rename implementation and disposable-root tests |
| Persistent rootfs/workspace/cache and truthful tmpfs/process/shell matrix | Pass in deterministic tier | Status v2 and fake-Docker lifecycle/reserve cases |
| Logical cwd persists/falls back; shell export does not persist | Pass | Helper fresh-shell test and controller cwd restart/fallback transcript |
| No ambient host Git/credential/home/Docker state | Pass in deterministic tier | Explicit minimal helper environment; no extra mount/exec route |
| Ordinary lifecycle is non-destructive and resource response never prunes | Pass | Retained E2.4 exact-ID/lifecycle/resource tests |
| Linux/macOS/exact-model claims remain separate | Pass | `docs/evidence/e3-1-persistent-ordinary-files.md` and `docs/testing.md` |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Complete canonical output: `artifacts/issue-37-verify.log` |

## Unresolved uncertainty

- Linux Docker Engine and Docker Desktop/macOS real-image/package/platform behavior, exact-model natural tasks, daemon/Desktop/reboot, physical voice, and real disk-pressure evidence remain separately authorized tiers.

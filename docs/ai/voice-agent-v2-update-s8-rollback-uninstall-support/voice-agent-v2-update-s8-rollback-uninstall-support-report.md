# Do Report: voice-agent-v2-update-s8-rollback-uninstall-support

**Source:** Firstmate I7 launch brief
**Parent:** signed launcher installation evolution
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `launcher/update.cjs`, `launcher/voice-agent.cjs` | Selector-free recorded-prior rollback on the existing lock/journal/readiness/retention path; CLI and recovery facts | 1, 2, 8 |
| `launcher/lifecycle.cjs`, `launcher/build`, `launcher/install.cjs` | Closed preservation-first uninstall and deterministic privacy-scanned local support archive | 3–7 |
| `launcher/test/*.cjs`, `scripts/verify.py` | Deterministic rollback, uninstall, purge, Docker, interruption, archive and privacy matrices | 8 |
| `contracts/*.schema.json`, `config/launcher-protocol-v1.json`, `contracts/README.md` | Closed transaction/result/inventory/support/status/doctor protocol fields | 8, 9 |
| `docs/adr/0016-*`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `README.md`, `AGENTS.md` | One authoritative decision, lifecycle/security/testing boundaries and CLI examples | 9 |
| `docs/evidence/final-v1-lifecycle-support-surfaces.md` | Deterministic evidence and explicit nonclaims | 9 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Recorded-prior only rollback, compatibility/custody/snapshot/readiness | Pass | `rollbackVoiceAgent`; rollback success/incompatible/missing/selector tests |
| Automatic entry restoration, double failure, retention and interruption | Pass | shared recovery/commit path; failure and durable-phase matrices |
| Default preservation-first uninstall and exact service/program scope | Pass | closed inventory/removal; preservation/service-order tests |
| Typed purge categories and exact rootless AgentEnvironment removal | Pass | CLI closure, root allowlists, immediate endpoint/ID/owner reinspection tests |
| Idempotence, changed-target denial, launcher-last and no linger mutation | Pass | journal receipts and interruption/symlink/retry tests |
| Closed local support archive contents | Pass | canonical documents and five fixed tar entries |
| Precommit scanner, permissions/checksum/no upload | Pass | scanner positive/negative, deterministic bytes, mode/output/no-upload tests |
| Status/doctor/result and deterministic matrices | Pass | lifecycle result/recovery schema and launcher phase |
| Proportionate authoritative docs | Pass | ADR-0016 plus architecture/roadmap/testing/README/contracts |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | `canonical_pr_gate: PASS deadline_seconds=90 unexpected_skips=0`; full output in `artifacts/update-s8-rollback-uninstall-support-verify.log` |

## Unresolved uncertainty

- Production publication and real systemd/Docker/filesystem/VM/reboot/voice/physical/macOS acceptance remain explicitly outside this deterministic slice.

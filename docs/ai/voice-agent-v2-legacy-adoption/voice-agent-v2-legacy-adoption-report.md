# Do Report: voice-agent-v2-legacy-adoption

**Source:** private I4 legacy-adoption continuation brief
**Parent:** Installation evolution I4
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `launcher/adopt.cjs`, `launcher/voice-agent.cjs`, `launcher/install.cjs`, `launcher/update.cjs`, `launcher/build` | Add exact discovery custody, durable import/config/Docker records, signed candidate handoff, user/system service transition, restoration/recovery, later legacy GC and SEA dispatch | 1–8 |
| `launcher/test/adopt.test.cjs`, `launcher/test/launcher.test.cjs`, `scripts/verify.py` | Add exact split/mismatch/config/Docker/success/failure/double-failure/all-interruption deterministic matrix | 9 |
| `contracts/{legacy-adoption,legacy-import-release,legacy-config-migration,docker-endpoint}.v1.schema.json`, `config/launcher-protocol-v1.json`, `contracts/README.md` | Close durable I4 machine records and launcher protocol catalog | 2–6, 8 |
| `README.md`, `CONTEXT.md`, `AGENTS.md`, `docs/architecture.md`, `docs/adr/0014-launcher-owned-signed-install-update.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/legacy-adoption.md` | Update authoritative ownership, lifecycle, status, UX, evidence and nonclaims | 10 |
| checkpoint-only old operational repair files | Restore to merged-main state/remove obsolete narrow-fix artifacts so final diff contains only accepted launcher-owned I4 | Non-goal boundary |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Strict zero-mutation legacy discovery | Pass | Existing read-only discovery plus strengthened cgroup/listener/explicit-Docker proof |
| Exact running-old custody independent of old app manifest | Pass | Own stored legacy inventory/release digest, unit/MainPID/cgroup/process/runtime/readiness/listener validation |
| Prior ready rollback; selected legacy uncommitted | Pass | Separate closed import records and adoption journal receipts |
| Private config stage/hash/CAS/fsync/conflict | Pass | No-follow owner/mode reads, private staged copy/receipt, repeated source/stage hash and atomic destination |
| Safe absent v2 defaults | Pass | AgentRun/tools/Telegram disabled, no credentials/mounts invented |
| Explicit rootless Docker only, no container lifecycle | Pass | Endpoint record gated by explicit socket/owner/rootless daemon facts; rootful/foreign rejected |
| User/system service transactional boundary | Pass | All prerequisites durable before old stop; signed candidate exact-ready or exact prior restoration |
| Interruption/idempotence | Pass | Every observed write/action injected; bounded service actions and exact copies converge |
| Deterministic matrix | Pass | `launcher/test/adopt.test.cjs` plus strengthened discovery cases |
| Authoritative docs/UX | Pass | ADR-0014 and architecture §6.7 remain sole owners |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | Pass | Full output: `artifacts/legacy-adoption-verify.log`; exact result recorded after implementation |

## Unresolved uncertainty

- Production signing/channel authority, real-host legacy transition, rootless Docker/systemd/reboot/voice/physical acceptance and AgentEnvironment state migration remain separately gated.
- The implementation disables but deliberately retains old system-unit bytes for forensic/recovery evidence; it does not touch the live stand.

# Do Report: voice-agent-v2-update-s3-transactional-update

**Source:** Firstmate I3 transactional canonical update brief
**Parent:** Installation evolution I3
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `launcher/update.cjs`, `launcher/install.cjs`, `launcher/voice-agent.cjs`, `launcher/build` | Add embedded canonical update owner, flock/journal recovery, prior custody, staging/migration, service transition, readiness, restoration, GC, offline/current truth and CLI | 1–7 |
| `launcher/test/update.test.cjs`, `launcher/test/install.test.cjs`, `launcher/test/launcher.test.cjs`, `scripts/verify.py` | Add deterministic end-to-end transaction/failure/interruption/migration/GC/identity/offline coverage to the sole PR gate | 8 |
| `contracts/update-transaction.v1.schema.json`, `contracts/config-migrations.v1.schema.json`, `contracts/installation.v1.schema.json`, `contracts/README.md`, `config/launcher-protocol-v1.json` | Close durable update/migration fields and add `update` to launcher protocol 1 | 1, 4, 7 |
| `README.md`, `CONTEXT.md`, `AGENTS.md`, `docs/architecture.md`, `docs/adr/0014-launcher-owned-signed-install-update.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/transactional-canonical-update.md` | Update the single authoritative lifecycle, vocabulary, CLI/errors, roadmap, test ownership and evidence/nonclaims | 9 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Exclusive kernel lock + durable content-free journal | Pass | Real open-FD `flock`; closed schema/validator; disposable mode checks |
| Recover/stage/migrate/quiesce/activate/readiness/commit/GC or restore | Pass | Transaction implementation plus every observed durable write/action interruption retry matrix |
| Exact prior running healthy custody independent of application manifest | Pass | Stable release record + signed manifest inventory + exact injected runtime/readiness/listener identity |
| Ordered idempotent migration/CAS and decision refusal | Pass | Digest-bound descriptors, snapshot, candidate copy, preimage SHA-256, safe/destructive tests |
| Bounded exact service transition | Pass | Generation-gone fixture, atomic pointers/config, unit byte comparison, one start and exact readiness |
| Reachability retention and exact deletion boundary | Pass | Active+rollback+journal protection; preservation and unsafe/unowned tests |
| Honest current/offline/split/interruption status | Pass | Online/cached/unavailable cases, recovery journal, canonical running-by-ID status logic |
| Deterministic injected failure matrix | Pass | `launcher/test/update.test.cjs`; no live owners |
| Authoritative docs/ADR/CLI/errors | Pass | ADR-0014 and architecture §6.7 remain sole lifecycle owners |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | Pass | Full output: `artifacts/update-s3-transactional-update-verify.log`; canonical PR gate reports no unexpected skips |

## Unresolved uncertainty

- Production key/channel publication and real-host/systemd/network/model/reboot/physical acceptance remain later gates.
- Legacy adoption, AgentEnvironment migration, explicit rollback/uninstall/support bundle and launcher self-update remain excluded.

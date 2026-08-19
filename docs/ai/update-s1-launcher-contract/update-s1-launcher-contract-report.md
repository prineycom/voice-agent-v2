# Do Report: update-s1-launcher-contract

**Source:** Firstmate launch brief for the first simple installation/update vertical slice
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `launcher/voice-agent.cjs`, `launcher/build`, `launcher/test/**` | Separately versioned read-only launcher, Node 26 SEA packaging, release verification, legacy discovery, status/doctor and deterministic behavior matrix | Self-contained launcher; privacy-safe zero-mutation diagnostics; exact legacy custody |
| `launcher/tools/sign-fixture.cjs`, `launcher/fixtures/**`, `launcher/keys/**` | Canonical deterministic signing tool, bounded signed fixtures and public test key only | Ed25519 evidence without production/private key material |
| `contracts/*launcher*`, `contracts/release-*`, `contracts/platform-artifact-*`, `config/launcher-protocol-v1.json` | Closed channel, platform manifest, release record, protocol, legacy/status/doctor schemas | Stable machine contracts and exact platform/protocol/archive policy |
| `scripts/verify.py`, `.github/workflows/behavioral.yml`, `artifacts/update-s1-launcher-verify.log` | Canonical Node 26 launcher phase and exact full verification output | One repository-owned delivery gate |
| `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `contracts/README.md` | Terminology, single ownership boundary, dependency order and test-tier ownership | No duplicated install/update contract |
| `docs/adr/0014-launcher-owned-signed-install-update.md`, `docs/adr/0013-systemd-bounded-single-host-operations.md` | Accepted launcher transaction ADR and explicit legacy-runtime supersession boundary | One authoritative target owner while current service remains factual |
| `README.md`, `AGENTS.md`, `docs/evidence/signed-launcher-read-only-foundation.md` | User entrypoint, durable project pointer and evidence/nonclaims | User-visible outcome and live-host boundary |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Signed channel/artifact/release contracts | ✅ | Canonical Ed25519 stable channel, sequence/expiry, exact bytes/hashes/platform/protocol/schema and stable release record validators/schemas |
| Safe platform archive | ✅ | Complete declared index plus absolute/traversal/duplicate/device/ancestor/internal-link closure |
| Self-contained existing-toolchain launcher | ✅ | Node.js 26 direct SEA build; two byte-identical binaries execute without another runtime |
| Read-only status/doctor | ✅ | Stable JSON/human output distinguishes selected/running/ready/rollback/transaction/AgentEnvironment/Docker; zero-mutation snapshot test |
| Exact legacy discovery | ✅ | Canonical owner/private root, complete inventory, no-follow regular files, unit, MainPID/process, runtime/readiness and explicit rootless endpoint proof |
| Historical incompatibility does not erase running custody | ✅ | Legacy fixture carries an intentionally incompatible application-manifest shape while exact running-old remains ready and eligible evidence |
| Invalid/unowned/unrelated candidates rejected | ✅ | Deterministic negative cases remain noneligible and cause no writes |
| Privacy | ✅ | Output denies fixture secret marker, configuration location, arbitrary root, PID and argv/script content |
| Stable XDG ownership boundary | ✅ | ADR-0014 and architecture §6.7 own the launcher/application/config/data/cache/state/runtime/AgentEnvironment split |
| No live-host mutation or excluded feature | ✅ | No install/update/repair/service/Docker/live-stand command exists or ran; evidence document records nonclaims |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | ✅ PASS | 9 launcher behaviors, 388 hermetic Python behaviors, 9 local-socket behaviors, 88 Vitest tests, typecheck/builds and production Firefox/LiveKit smoke; full output in `artifacts/update-s1-launcher-verify.log` |

## Unresolved uncertainty

- Production signing-key custody/publication and every mutating install/update/migration/rollback/GC/self-update behavior remain dependency-ordered later slices.
- Disposable real-host install, VM power loss, live legacy migration, reboot, real Docker/model/network/Telegram/physical/full-stack/licensing/Raspberry Pi evidence remain separate and unclaimed.

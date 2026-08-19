# Do Report: voice-agent-v2-update-s2-fresh-install

**Source:** Firstmate fresh supported-Linux installation brief
**Parent:** Installation evolution I2
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `launcher/install.cjs`, `launcher/voice-agent.cjs`, `launcher/build` | Add embedded fresh-install owner, fixed production CLI/XDG boundary, signed preflight, exact extraction, safe defaults, journal, user service/linger and readiness commit | 1–7 |
| `launcher/test/install.test.cjs`, `launcher/test/launcher.test.cjs`, `scripts/verify.py` | Add deterministic host/archive/service/readiness/failure/interruption/privacy matrix to the canonical launcher phase | 8 |
| `contracts/install-transaction.v1.schema.json`, `contracts/installation.v1.schema.json`, `config/launcher-protocol-v1.json`, `contracts/README.md` | Close the minimal fresh-install durable/healthy records and expose only `install` as a mutation command | 5, 7 |
| `CONTEXT.md`, `README.md`, `docs/architecture.md`, `docs/adr/0014-launcher-owned-signed-install-update.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/fresh-linux-install.md`, `AGENTS.md` | Update canonical vocabulary, lifecycle, CLI, test ownership, evidence and nonclaims | 9 |
| `artifacts/update-s2-fresh-install-verify.log` | Full canonical verification output (force-added because task artifacts are ignored by default) | 8–9 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Signed stable compatible release and fail-closed metadata/archive admission | Pass | Existing verifier reused before mutation; install cases cover forged, unsafe and incomplete input |
| Exact Linux/systemd/NVIDIA/assets/space host gate | Pass | `SystemHost` plus deterministic injected preflight cases |
| Canonical private XDG layout and no-follow custody | Pass | Fixed production derivation, isolated test identity only, permission/foreign/symlink cases |
| Safe first defaults and independent optional degradation | Pass | Mode-0600 V2/private files; local-only/no-fallback/no-capture; agent/Telegram disabled |
| Immutable release, durable records and generated user unit/linger | Pass | Exact extraction/inventory, release/install records, user unit and injected service transcript |
| Exact one-start readiness and failed-candidate truth | Pass | Exact release/build, 5 unique components, admission/listener tests; stop/disable/no selection on failure |
| Idempotence, conflict routing and minimal interruption recovery | Pass | Identical healthy probe, five durable interruption points, partial/failed state refusal |
| Deterministic behavioral test matrix and no leakage | Pass | 19 launcher tests pass inside `./verify`; no live host/service/network mutation |
| Authoritative documentation and bounded verification | Pass | Canonical docs updated; only repository-owned `./verify` used |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | 90-second canonical gate; launcher 19/19, Python 388, Vitest 88, builds and Firefox/LiveKit smoke pass; full output saved in `artifacts/update-s2-fresh-install-verify.log` |

## Unresolved uncertainty

- Production release signing authority/publication and real model/runtime acquisition are explicit non-goals, so the repository build stops before mutation when that authority is absent.
- Real Linux user-systemd/linger/NVIDIA/model/reboot/physical voice evidence remains in the separate platform/acceptance tier.

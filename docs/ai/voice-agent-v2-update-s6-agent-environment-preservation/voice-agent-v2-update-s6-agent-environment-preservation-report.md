# Do Report: voice-agent-v2-update-s6-agent-environment-preservation

**Source:** Firstmate AgentEnvironment/config-v2 preservation brief
**Parent:** Installation evolution I5
**Status:** ✅ pass

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `contracts/agent-config.v2.schema.json`, `config/agent-config-v2.example.yaml`, `src/voice_agent_v2/agent_environment_config.py` | Close optional capability configuration around coupled enablement, exact rootless endpoint, fixed spec/image, durable paths, credentials reference, network/resources and typed mounts; keep V1 upgrade disabled | 1 |
| `launcher/agent-environment.cjs`, `launcher/install.cjs`, `launcher/update.cjs`, `launcher/adopt.cjs`, `launcher/voice-agent.cjs` | Add inspection-only authority/inventory/preservation ownership, durable-state migration, before/after transaction custody, optional degradation and content-free status | 2–6 |
| `contracts/agent-environment-preservation.v1.schema.json`, `contracts/docker-endpoint.v1.schema.json`, `contracts/{update-transaction,legacy-adoption,launcher-status}.v1.schema.json`, `config/launcher-protocol-v1.json` | Define the closed preservation/authority/status/journal records | 3–6 |
| `launcher/test/*.test.cjs`, `tests/test_agent_environment.py` | Cover exact authority, production inspect parsing, disabled/degraded/stale states, success/rollback/recovery/adoption preservation, migration, GC exclusion, privacy and pre-I5 journal compatibility | 7 |
| `README.md`, `CONTEXT.md`, `AGENTS.md`, `contracts/README.md`, `docs/{architecture,roadmap,testing}.md`, `docs/adr/0015-preserve-agent-environment-across-release-transactions.md`, `docs/evidence/agent-environment-preservation.md` | Update authoritative lifecycle, glossary, CLI/contracts, evidence and explicit nonclaims | 8 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Complete optional config v2 and safe disabled defaults | Pass | Closed schema/example, coupled enablement, strict Python parsing and disabled V1 upgrade |
| Durable state outside releases and reconstructible cache | Pass | Canonical data-root layout, migration receipt classification and GC target denial |
| Exact rootless Docker authority without fallback | Pass | Owner-only socket, daemon/root-dir, namespace/cgroup and generated-service endpoint checks; explicit `--host` only |
| Content-free exact existing-container inventory | Pass | Selected ID/labels plus hashed rootfs/workspace/cache/mount/network/resource custody; no user contents or secrets read |
| Same identity through application lifecycle | Pass | Before/after receipts across update success, restoration/recovery and legacy adoption; mismatch aborts without container mutation |
| Optional safe status and transaction receipts | Pass | Closed disabled/ready/degraded/stale state/action contract and strict executable journal validators |
| Deterministic failure/privacy coverage | Pass | Node fixtures plus active Python config compatibility fixture; real Docker/systemd/live stand remain untouched |
| Proportionate authoritative documentation | Pass | ADR-0015, architecture §6.7/§9.14, I5 roadmap/testing/evidence and CLI/config docs |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| Focused Node/Python iteration checks and JSON parsing | Pass | 36 Node and 48 Python tests passed before the final delivery session; see handoff checkpoint |
| `./verify` | Pass | Sole final canonical run passed with `canonical_pr_gate: PASS deadline_seconds=90 unexpected_skips=0` and `RESULT: PASS`; full 147-line output: `artifacts/agent-environment-preservation-verify.log` (SHA-256 `976a127d4dce420d96dc183ad5daa3b1988d774cb884923a2e6518c582dc9280`) |

## Unresolved uncertainty

- No production signing/publication, live Docker/socket/container, real systemd/reboot/network/model/voice/full-stack/Raspberry Pi/macOS acceptance, credential/Telegram setup, environment lifecycle action or live-stand mutation is claimed.

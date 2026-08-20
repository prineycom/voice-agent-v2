# ADR-0016: Close v1 lifecycle deletion and local support authority

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Voice Agent v2 project architecture

## Context

The signed launcher already owns one exact XDG installation, an OS lock, durable release transaction, exact service readiness, active-plus-rollback retention, and installation-wide AgentEnvironment custody. Adding rollback, uninstall, or diagnostics through independent selectors, deletion helpers, Docker discovery, or raw log collection would bypass those invariants and create new authority over program bytes, durable user state, or secrets.

## Decision

`voice-agent rollback` is selector-free. It uses the existing update lock and transaction schema with `operation=rollback`, and admits only the recorded signed prior healthy release. Before quiesce it revalidates the immutable manifest/tree, release/build/platform and ready receipt, current config/data/host ranges, exact locally cached model/runtime custody, current exact-ready service/unit, and AgentEnvironment identity. It snapshots config/install/unit bytes, swaps `current` and `rollback`, starts once, and commits only after exact release/build, five unique ready components, admission, local/no-fallback facts, and owned loopback listener proof. Candidate failure restores and proves the release/config/unit healthy at entry; double failure retains journal, snapshot and both releases. Success keeps the displaced newer release as the sole rollback.

`voice-agent uninstall` begins with a preservation statement and exact dry-run inventory. Default removal is limited to the exact owned user unit/service, launcher plus its sole backup, verified program releases/pointers/metadata/transaction evidence, and product-owned program downloads/partials. It never disables linger. Config, secrets, models, application data/media/logs, the entire AgentEnvironment/Docker state, external mounts, and unknown files remain. Automation requires `--yes`; interactive operation requires exact typed phrases. Program-cache, models, AgentEnvironment, and all-data deletion are separate closed flags. AgentEnvironment deletion additionally requires the explicit data-loss flag and immediate exact rootless endpoint/container/owner reinspection. There is no arbitrary path, prefix/wildcard selection, ambient Docker context, system prune, unrelated container/volume, or external-mount deletion. A content-free uninstall journal makes selected-category retry idempotent; changed, linked, foreign, or unsupported targets fail closed.

`voice-agent support-bundle` first lists five fixed categories and writes one local mode-`0600` deterministic archive under diagnostics or a safe explicit regular-file destination. Its only entries are schema-validated status, doctor, normalized last-operation facts, redacted fixed service-policy facts, and bounded metadata-only transaction/journal codes. It excludes configuration, secret/environment values, user filenames/content, conversations/prompts/responses/media, token-bearing URLs, command lines/output, raw logs, model bytes, Docker inspect payloads, and credentials. A closed entry/path allowlist plus secret-name, known-value, token/private-key, URL and high-entropy scan runs before file creation; suspicion fails without an archive. The launcher has no upload action.

Stable lifecycle results and status/doctor recovery fields contain only operation/state/error and release/recovery facts. Machine fields remain owned by `contracts/` and `config/launcher-protocol-v1.json`. ADR-0014 continues to own release/install/update custody, and ADR-0015 continues to own AgentEnvironment preservation and rootless authority.

## Consequences

- Rollback reuses the proven transaction and readiness path instead of becoming historical downgrade/import.
- Default uninstall is availability-safe and preservation-first; destructive scope remains visible and mechanically closed.
- Support can receive a useful local artifact without turning diagnostics into secret or user-content collection.
- Deterministic injected fixtures can exhaust interruption, deletion and privacy cases without touching the live stand, real systemd/Docker, user data or the network.
- Production publication and real host/filesystem/Docker/reboot/physical acceptance remain separately gated.

## Alternatives considered

- **Arbitrary version rollback:** rejected because historical bytes do not carry recorded healthy/config/data custody.
- **One `--purge` switch or recursive XDG removal:** rejected because it conflates reconstructible program state with secrets and durable user/AgentEnvironment data.
- **Docker prune/name-prefix deletion:** rejected because it cannot prove exact installation ownership.
- **Raw logs/config/Docker inspect in support archives:** rejected because redaction after collection is not a closed privacy boundary.
- **Automatic support upload:** rejected because network disclosure requires a separate explicit user action outside launcher authority.

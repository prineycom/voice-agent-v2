# ADR-0015: Preserve the exact AgentEnvironment across application release transactions

- **Status:** Accepted
- **Date:** 2026-08-21
- **Decision owner:** Voice Agent v2 project architecture

## Context

The application and its one persistent Docker `AgentEnvironment` have different lifecycles. Application releases are signed, immutable and reconstructible; update keeps active plus one rollback generation and garbage-collects unreachable program bytes. The AgentEnvironment contains a writable rootfs, installed packages, durable workspace/cache, credential modes, mount authority, background-process state and controller receipts. Treating it as a release or ordinary cache would let an application update destroy user work or silently change authority.

The launcher must also avoid the opposite error: discovering ambient Docker authority or “repairing” optional agent tooling while changing an otherwise healthy voice release. Rootful, foreign, group-authorized or context-selected Docker may have broader host authority than the configured rootless service endpoint. An image/spec change can require deliberate maintenance but does not authorize automatic rebuild of the sole persistent environment.

## Decision

Classify the AgentEnvironment registry, rootfs-storage custody, workspace and durable cache beneath the installation's XDG data root. They are durable user state, not release payload, reconstructible model/runtime cache or release/asset-GC input. Additional-mount contents remain outside launcher ownership. `rootfs-storage` records the identity of the writable layer held by the rootless daemon; it does not claim that the launcher relocates Docker's storage.

`voice-agent.config.v2` explicitly couples AgentRun and AgentEnvironment enablement. Enabled input requires the exact verified `unix:///run/user/<uid>/docker.sock`, fixed allowed image/spec, stable installation ID, canonical registry/rootfs-storage/workspace/cache paths, private credential-store reference, network/resource policy and typed mounts. Fresh install, legacy adoption and V1 upgrade write a complete disabled shape and invent no endpoint, container, credential, recipient or mount.

The launcher admits only an explicitly addressed owner-only mode-`0600` Unix socket whose device/inode/UID, Docker daemon identity, rootless security mode and daemon-root ownership, rootless user namespace, cgroup v2/systemd driver, and generated-service endpoint agree. It uses an empty Docker home/config and never consults ambient context. Node has no portable Unix `SO_PEERCRED` interface, so socket custody plus the exact daemon response/root-directory identity is the honest peer proof. Rootful, foreign, group-authorized, mismatched or unverifiable authority is rejected; there is no fallback, Docker installation, group change, `sudo docker`, or daemon lifecycle action.

For a recorded selected container, the launcher may run only read-only `docker --host <exact> info` and `container inspect <recorded-id>`. It requires the exact container ID and managed/schema/owner/spec/generation labels. The content-free inventory hashes image/config-label, graph-driver/rootfs-storage, workspace/cache/additional-mount device/inode/owner/mode, network and resource identities. It never records mount source paths, workspace filenames/content, commands, process output, environment or credential values.

Every fresh install, already-current reconciliation, application update, candidate failure restoration, power-loss recovery and legacy adoption captures a content-free preservation receipt before and after service lifecycle work. Stable identity permits ordinary voice completion even when agent tools are disabled or the endpoint/container is unavailable, stopped or unhealthy. Desired image/config/spec drift becomes `stale_spec` with `maintenance_rebuild_required` and no mutation. Container/rootfs/workspace/cache/mount replacement becomes `degraded_identity_mismatch` and fails the surrounding release transaction. Application stop/start never creates, starts, stops, removes, adopts, commits, retires, prunes or rebuilds the container.

Migrate the historical cache-class durable tree by owner/type/no-symlink inventory and file hashes. Use atomic rename on the same filesystem. Across filesystems, create and fsync an exact staged copy, re-inventory it, atomically select the canonical target, and retain the old tree. The receipt marks the target durable, non-release-owned and non-GC-eligible. Interrupted pre-I5 update/adoption journals are accepted only in their exact historical field shape, expanded with null/false preservation fields, and given a fresh before receipt prior to further lifecycle action.

The closed capability states are `disabled`, `ready`, `degraded_endpoint_unavailable`, `degraded_identity_mismatch`, and `stale_spec`; each carries one explicit action. They affect agent-tool admission only and are not a sixth ordinary voice-readiness component.

ADR-0014 continues to own signed release provenance and the application transaction. This ADR narrows its durable-data and Docker authority behavior; it does not change the AgentEnvironment controller's separately confirmed lifecycle commands.

## Consequences

### Positive

- Application release GC and rollback cannot consume the user's environment.
- Update success proves preservation rather than assuming service shutdown is harmless.
- Optional Docker failure or stale desired spec cannot make healthy local voice unavailable.
- Rootful/ambient Docker never becomes an implicit repair path.
- Status, doctor and journals remain useful without exposing user content or secrets.
- Pre-decision interrupted transactions remain recoverable without discarding release custody.

### Costs and limits

- Install/update performs bounded extra Docker inspection when an environment is configured.
- Cross-filesystem migration temporarily needs space for a second exact tree and deliberately retains the old copy.
- The launcher can detect drift but cannot repair it automatically; confirmed environment maintenance remains separate.
- Deterministic fixtures prove the contract only. Real rootless Docker, live migration, daemon/reboot behavior, physical voice and production publication remain separate acceptance tiers.

## Alternatives considered

- **Keep AgentEnvironment under ordinary cache:** rejected because cache eviction and release maintenance would gain user-data deletion authority.
- **Rebuild automatically on image/spec drift:** rejected because it can kill processes, lose writable-rootfs state and change mounts/credentials without confirmation.
- **Use ambient Docker context or `/var/run/docker.sock` fallback:** rejected because authority and service behavior become host-dependent and may become rootful.
- **Make agent capability readiness part of five-component voice readiness:** rejected because optional tooling must not break ordinary local conversation.
- **Record full Docker inspection output for diagnosis:** rejected because it contains paths, command/environment details and potentially secret-bearing configuration.

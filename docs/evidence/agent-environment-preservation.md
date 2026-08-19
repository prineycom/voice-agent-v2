# I5 AgentEnvironment/config-v2 preservation evidence

> **Scope:** deterministic installation-manager evidence only
>
> **Date:** 2026-08-21

## Accepted outcome

The launcher treats the one existing AgentEnvironment as installation-owned durable user state. Fresh install, canonical update/reconciliation/failed-safe restoration/recovery and exact legacy adoption record content-free before/after custody without calling a container lifecycle operation. Optional disabled/unavailable/missing/stopped/unhealthy/stale states never change ordinary five-component voice readiness; an exact identity replacement fails the surrounding transaction.

Strict config v2 now contains coupled explicit enablement, the verified rootless endpoint, fixed allowed image/spec, stable installation identity, canonical durable registry/rootfs-storage/workspace/cache paths, one private credential-store reference, network/resources and typed mounts. Production install/adoption and the historical V1 upgrade remain disabled until the full authority is valid.

Historical cache-class durable state uses no-follow owner/type/hash inventory. Same-filesystem migration renames atomically; cross-filesystem migration stages, fsyncs, re-inventories and atomically selects an exact copy while retaining the old tree. Both receipts mark the canonical target durable, non-release-owned and non-GC-eligible.

## Deterministic evidence

The Node.js 26 launcher phase of the repository-owned `./verify` owns:

- owner-only explicit rootless socket/daemon/namespace/cgroup/generated-service endpoint acceptance and rootful/foreign/group/ambient/mismatch denial;
- production `docker info`/exact-ID inspect parsing without live Docker, plus content-free image/config/rootfs/workspace/cache/additional-mount/network/resource identity;
- disabled, ready, endpoint unavailable, missing, stopped, unhealthy, stale-spec and replacement behavior;
- unchanged exact identity through update candidate success, candidate failure/prior restoration, recovery and legacy adoption;
- every existing update/adoption durable interruption owner plus exact pre-I5 journal-shape compatibility;
- same- and forced-cross-filesystem migration with hash/CAS/fsync and old-tree retention;
- release reachability GC denial for data/config/model/AgentEnvironment targets;
- status/doctor/journal privacy and absence of container lifecycle calls.

The Python behavior phase validates the expanded strict schema, complete active compatibility fixture and disabled V1 upgrade while retaining the existing fake-Docker AgentEnvironment/AgentRun matrix.

The sole final canonical `./verify` run passed with `canonical_pr_gate: PASS deadline_seconds=90 unexpected_skips=0` and `RESULT: PASS`. Its complete 147-line output is stored at `artifacts/agent-environment-preservation-verify.log` (SHA-256 `976a127d4dce420d96dc183ad5daa3b1988d774cb884923a2e6518c582dc9280`).

## Explicit nonclaims

This evidence makes no claim about a live Docker daemon or socket, actual Docker peer credentials, a live container/image, real user-systemd or service restart, physical filesystem migration, daemon/host reboot, live background-process survival, model/network/credential/Telegram behavior, production signing/publication, the running review stand, physical voice/full-stack behavior, Raspberry Pi, or macOS runtime support.

The launcher does not create, adopt, start, stop, commit, remove, retire, prune, rebuild or upgrade an AgentEnvironment. Node does not expose portable Unix `SO_PEERCRED`; the accepted deterministic authority is exact socket custody plus explicit Docker daemon/root-directory/rootless/namespace/cgroup/service-endpoint proof. `rootfs-storage` is an identity/custody class and does not relocate Docker's writable-layer storage.

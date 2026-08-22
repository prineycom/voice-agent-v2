# ADR-0015: Pre-provision one namespace-root AgentEnvironment

- Status: Accepted
- Date: 2026-08-22

## Context

Issue #71 reached the first real `docker container create` only after the voice/action truth corrections. The canonical current-user rootless Docker 29.7 host rejected creation before an object existed. The product collapsed nonzero rejection and malformed success, and inspect-only status erased the failure.

One isolated task-owned create, after a privacy-safe classifier existed, identified the earliest incompatibility: the `--mount` form used an explicit writable token that Docker's bind-mount grammar does not admit. Docker documents bind mounts as writable by default and only `readonly`/`ro` as the read-only option. Removing that token allowed creation.

A subsequent read-only counterfactual separated helper behavior from rootless UID mapping. The current-user bind roots appeared as namespace-root-owned mode `0700`: container uid 1000 could not search/write them, while namespace root could. Pasha accepted namespace root rather than deferring for a separate idmapped-mount design.

## Decision

An enabled installation provisions, starts, and helper-validates its one persistent container during existing service startup, before full readiness or agent action admission. The same selected identity is reused across commands, turns, sessions, gateway/service restart, deploy at unchanged spec, and ordinary stop/start. Ordinary stop stops but never removes it. Explicit confirmed destructive lifecycle remains separate.

The authoritative image and container process identity is `0:0` inside the already validated current-user rootless Docker daemon. That namespace root maps to the ordinary host user, not host root. Image helper ledgers are namespace-root-owned. Writable bind mounts rely on Docker's default; only read-only mounts add `readonly`.

The sandbox remains: cap drop `ALL`, `no-new-privileges`, explicit allowlisted mounts, no Docker socket, no privileged/host mode, no ports, no rootful/remote/host/alternate-runtime fallback, and fixed `docker exec` helper routing. Unexpected process identity or spec drift fails closed and never causes implicit replacement or deletion.

Create failures retain public `agent_environment/environment_creation_failed` while private state distinguishes `docker_create_rejected` from `docker_create_response_invalid`. Only closed allowlisted scalars/enums persist; inspect-only status preserves unavailable truth. No Docker arguments, stderr text/hash, endpoint, mount, path, environment value, ID, credential, or content enters the failure record.

## Consequences

- Clean image preparation selects a new exact immutable context; prior prepared images are retained.
- Existing containers with the former identity become visible `stale_spec`/identity mismatch and are not silently changed, removed, or selected.
- Provision failure leaves the instance unready and action admission closed with zero retry/fallback.
- A future idmapped-mount design would require a separate accepted ADR and migration; it is not an implicit fallback.
- This decision makes no physical voice, reboot, pressure, soak, daemon-restart, or Docker Desktop/macOS claim.

# Simple versioned stands: published issue index

The captain-approved Grill breakdown was published in dependency order. There is no parent issue or sub-issue link.

GitHub `Task` type was requested as a best-effort follow-up after issue #65. The repository reports that issue types are not configured, so all nine issues remain untyped; this does not affect their execution classification or labels.

## 1. Diagnose clean Arch host requirements

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:** None - can start immediately
- **Created issue:** [#65 — Diagnose clean Arch host requirements](https://github.com/prineycom/voice-agent-v2/issues/65)

<details>
<summary>Full final issue body</summary>

## What to build

Deliver a read-only `stand doctor` path for a clean Arch Linux or EndeavourOS x86_64 host with NVIDIA. It inspects the prerequisites for a fully ready local Voice Agent v2 stand and reports exact, actionable installation commands without mutating the operating system.

## Acceptance criteria

- [ ] `stand doctor` performs only read operations and leaves OS packages, services, files, users, and configuration unchanged.
- [ ] It reports the supported-host decision for Arch Linux/EndeavourOS x86_64 with NVIDIA and explicitly identifies an unsupported or incomplete host.
- [ ] It checks and reports the exact missing or usable system packages, Node, Python, Git, user-systemd/linger, rootless Docker, required runtime binaries, selected model artifacts, and immutable runtime/model caches.
- [ ] Every unmet prerequisite has an actionable Arch-appropriate installation or remediation command, while the command itself is never run by `stand doctor`.
- [ ] It distinguishes a merely installed prerequisite from the rootless-Docker/user-systemd state required for a fully ready stand and does not claim readiness when required model/cache evidence is absent.
- [ ] Focused automated smoke coverage proves successful diagnosis, missing-prerequisite reporting, and no-mutation behavior.

## Blocked by

None - can start immediately.

</details>

## 2. Initialize and run one local dev release end to end

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:**
  - [#65 — Diagnose clean Arch host requirements](https://github.com/prineycom/voice-agent-v2/issues/65)
- **Created issue:** [#66 — Initialize and run one local dev release end to end](https://github.com/prineycom/voice-agent-v2/issues/66)

<details>
<summary>Full final issue body</summary>

## What to build

Deliver the first end-to-end local development release path. `stand init` creates an external dev instance and its private configuration; deployment from a local committed SHA builds and starts that instance through user systemd and exposes honest readiness, status, and journal logs.

## Acceptance criteria

- [ ] `stand init` creates external stand directories, two strict mode-0600 `KEY=VALUE` configuration files, unique generated LiveKit credentials, and the user-systemd template without placing mutable stand state in the controller clone.
- [ ] Private configuration is parsed as data rather than sourced as shell, and ports are explicitly configured rather than automatically allocated.
- [ ] `stand deploy dev --local <repo> <committed-sha>` accepts only a committed SHA, rejects dirty local changes, constructs an immutable release from that SHA, and selects it through `instances/dev/current` only after successful construction.
- [ ] The dev stack starts through the user-systemd unit, reaches readiness through its foreground launcher, and `stand status` reports the exact selected version and honest readiness.
- [ ] `stand logs dev` exposes the relevant journal records, and a failed setup/readiness path is visible rather than reported as started.
- [ ] Focused automated smoke coverage proves initialization, strict permissions, local committed-SHA deployment, pointer selection, start/readiness, status, and journal-log discovery.

## Blocked by

- [Diagnose clean Arch host requirements](https://github.com/prineycom/voice-agent-v2/issues/65)

</details>

## 3. Deploy immutable dev releases from private remote refs

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:**
  - [#66 — Initialize and run one local dev release end to end](https://github.com/prineycom/voice-agent-v2/issues/66)
- **Created issue:** [#67 — Deploy immutable dev releases from private remote refs](https://github.com/prineycom/voice-agent-v2/issues/67)

<details>
<summary>Full final issue body</summary>

## What to build

Deliver private-remote source deployment for immutable development releases. The controller owns one ordinary user Git clone and resolves a requested remote ref to one full immutable commit SHA before release construction or reuse.

## Acceptance criteria

- [ ] The controller clone is located at `~/.local/share/voice-agent-v2/source` and uses ordinary user Git authentication; the mechanism neither creates nor manages an access token.
- [ ] `stand deploy dev` fetches and resolves a permitted remote branch, tag, or SHA to one full commit SHA before it builds or selects a release.
- [ ] Release construction uses a full `git archive` snapshot without `.git`, produces the production frontend build and per-release Python environment from the dedicated production lock, records a release manifest, and promotes from a temporary directory only after success.
- [ ] A completed release for the exact resolved SHA is reused rather than rebuilt; frontend `node_modules` are removed after building when safe, and no automatic release deletion occurs.
- [ ] A failed fetch, unresolved ref, incomplete build, or failed manifest construction leaves existing releases and the selected dev pointer unchanged.
- [ ] Focused automated smoke coverage proves remote-ref resolution, immutable SHA identity, archive-only source, atomic promotion, manifest creation, and completed-release reuse.

## Blocked by

- [Initialize and run one local dev release end to end](https://github.com/prineycom/voice-agent-v2/issues/66)

</details>

## 4. Run isolated main and dev stands side by side

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:**
  - [#67 — Deploy immutable dev releases from private remote refs](https://github.com/prineycom/voice-agent-v2/issues/67)
- **Created issue:** [#68 — Run isolated main and dev stands side by side](https://github.com/prineycom/voice-agent-v2/issues/68)

<details>
<summary>Full final issue body</summary>

## What to build

Deliver side-by-side independent `main` and `dev` stands using the shared immutable release store. Each stand runs a complete loopback-local Voice Agent v2 stack while retaining separate mutable identity and Docker state.

## Acceptance criteria

- [ ] `main` and `dev` have independent explicit ports, configuration, data, cache/runtime paths, workspace, credentials, and Docker AgentEnvironment/container state.
- [ ] Each stand has an independent `current` pointer into shared immutable releases; only immutable releases and heavy immutable model/runtime caches may be shared.
- [ ] The instance-root contract makes current hard-coded user paths, including `~/.voice-agent` and shared AgentEnvironment paths, per-instance.
- [ ] Both stands bind only `127.0.0.1` and run independent LLM, LiveKit, gateway, STT, and TTS stacks concurrently without one stand reading, replacing, or deleting the other's mutable state.
- [ ] Status and logs identify the queried instance and exact release without mixing state between stands.
- [ ] Focused automated smoke coverage proves pointer, configuration, credential, mutable-path, Docker-state, and loopback-port isolation; resource behavior is measured rather than solved through shared inference.

## Blocked by

- [Deploy immutable dev releases from private remote refs](https://github.com/prineycom/voice-agent-v2/issues/67)

</details>

## 5. Deploy and manually roll back immutable SemVer releases

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:**
  - [#68 — Run isolated main and dev stands side by side](https://github.com/prineycom/voice-agent-v2/issues/68)
- **Created issue:** [#69 — Deploy and manually roll back immutable SemVer releases](https://github.com/prineycom/voice-agent-v2/issues/69)

<details>
<summary>Full final issue body</summary>

## What to build

Deliver immutable SemVer deployment and manual rollback for `main`. Operators select an exact release tag; the mechanism activates it only after validation and never selects a version automatically.

## Acceptance criteria

- [ ] `stand deploy main <tag>` accepts only `vMAJOR.MINOR.PATCH` tags, resolves each to one full commit SHA, and rejects branches, untagged SHAs, malformed tags, and automatic latest-version selection.
- [ ] Once a main tag has been observed, a changed target for the same tag is refused as a moved tag.
- [ ] Deployment validates the new version's external configuration before changing `instances/main/current`; failed validation leaves the prior selected release unchanged.
- [ ] Deploying an older accepted tag through the same `stand deploy main <tag>` command performs manual rollback; no automatic rollback occurs.
- [ ] A failed post-activation readiness check remains visibly selected for manual diagnosis or a deliberate older-tag deployment.
- [ ] Focused automated smoke coverage proves SemVer-only admission, moved-tag refusal, pre-switch validation, explicit activation, and manual rollback selection.

## Blocked by

- [Run isolated main and dev stands side by side](https://github.com/prineycom/voice-agent-v2/issues/68)

</details>

## 6. Complete explicit lifecycle and failure reporting

- **Execution:** `AFK`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-agent`
- **Blocked by:**
  - [#69 — Deploy and manually roll back immutable SemVer releases](https://github.com/prineycom/voice-agent-v2/issues/69)
- **Created issue:** [#70 — Complete explicit lifecycle and failure reporting](https://github.com/prineycom/voice-agent-v2/issues/70)

<details>
<summary>Full final issue body</summary>

## What to build

Complete the explicit lifecycle interface for versioned stands. Operators can start, stop, inspect, list, and read logs without losing instance state, and failure behavior remains bounded and actionable.

## Acceptance criteria

- [ ] `stand start`, `stand stop`, `stand status`, `stand logs`, and `stand list` provide the documented lifecycle and instance inventory for `main` and `dev`.
- [ ] `start` enables boot persistence; `stop` disables it and stops the instance container without deleting its data or rootfs.
- [ ] Deploy restarts only an instance that was already running; a stopped instance remains stopped after deployment.
- [ ] Expected configuration failures do not trigger restart, while repeated quick runtime failures reach a clear failed state instead of an unbounded restart loop.
- [ ] A bad activated release stays selected and visible until an operator manually corrects configuration or deploys another accepted release.
- [ ] The user-systemd `voice-agent-v2@.service` template uses linger and an explicit dependency on the same user's rootless `docker.service`; one foreground launcher owns application children/readiness and logs stay in journald.
- [ ] Focused automated smoke coverage proves lifecycle transitions, boot-persistence toggling, non-destructive stop, conditional deploy restart, configuration-failure behavior, and bounded runtime-failure reporting.

## Blocked by

- [Deploy and manually roll back immutable SemVer releases](https://github.com/prineycom/voice-agent-v2/issues/69)

</details>

## 7. Accept the real dev stand on the canonical host

- **Execution:** `HITL`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-human`
- **Blocked by:**
  - [#70 — Complete explicit lifecycle and failure reporting](https://github.com/prineycom/voice-agent-v2/issues/70)
- **Created issue:** [#71 — Accept the real dev stand on the canonical host](https://github.com/prineycom/voice-agent-v2/issues/71)

<details>
<summary>Full final issue body</summary>

## What to build

Perform the real canonical-host acceptance of the dev stand. This is a manual evidence gate for the end-to-end versioned-stand behavior, not a reboot test.

## Acceptance criteria

- [ ] A human verifies a real dev build and readiness on the canonical supported host, including exact selected version and status.
- [ ] A human verifies restart, stop/start, and journal visibility through the documented `stand` interface.
- [ ] A human verifies that the dev stand owns a rootless Docker AgentEnvironment and preserves the intended instance state across ordinary lifecycle actions.
- [ ] A human verifies that dev is isolated from main for configuration, ports, mutable state, credentials, and running stack behavior.
- [ ] The acceptance record distinguishes observed host facts from untested resource limits and does not claim a host reboot was performed.

## Blocked by

- [Complete explicit lifecycle and failure reporting](https://github.com/prineycom/voice-agent-v2/issues/70)

</details>

## 8. Add backed-up config and data migrations

- **Execution:** `HITL/future`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-human`
- **Blocked by:**
  - [#71 — Accept the real dev stand on the canonical host](https://github.com/prineycom/voice-agent-v2/issues/71)
- **Created issue:** [#72 — Add backed-up config and data migrations](https://github.com/prineycom/voice-agent-v2/issues/72)

<details>
<summary>Full final issue body</summary>

## What to build

When a concrete incompatible configuration or data schema exists, scope and implement the necessary migration as a separately reviewed change. The base deployment mechanism remains compatible-version-only and never mutates or copies user data.

## Acceptance criteria

- [ ] Work starts only after a concrete incompatible schema and affected instance data are identified; no speculative migration framework is added.
- [ ] Before any migration mutation, the implementation creates a recoverable backup and documents an explicit recovery path.
- [ ] The migration path is manually reviewed and verifies that the target version can use the migrated data; failure preserves a recoverable pre-mutation state.
- [ ] Compatible versions continue to deploy without copying or mutating user data, and existing legacy user data remains untouched unless this explicit migration is selected.
- [ ] Focused automated smoke coverage proves the identified schema transition, backup creation, failure handling, and recovery path; a human validates the concrete data case.

## Blocked by

- [Accept the real dev stand on the canonical host](https://github.com/prineycom/voice-agent-v2/issues/71)

</details>

## 9. Verify startup after a real host reboot

- **Execution:** `HITL/deferred`
- **GitHub issue type:** `Task` requested; unsupported (repository issue types are not configured)
- **Label:** `ready-for-human`
- **Blocked by:**
  - [#71 — Accept the real dev stand on the canonical host](https://github.com/prineycom/voice-agent-v2/issues/71)
- **Created issue:** [#73 — Verify startup after a real host reboot](https://github.com/prineycom/voice-agent-v2/issues/73)

<details>
<summary>Full final issue body</summary>

## What to build

Physically verify boot-time startup for an enabled stand after a real canonical-host reboot. This is deferred manual acceptance; it is not replaced by a service restart or synthetic test.

## Acceptance criteria

- [ ] A human performs a real host reboot with the selected stand enabled and verifies user-systemd linger and the same user's rootless Docker service are available after boot.
- [ ] The enabled stand starts without interactive login, reports its exact active release, and reaches honest readiness after the reboot.
- [ ] The acceptance record identifies the verified instance, exact release, readiness evidence, and any failure without exposing credentials or private content.
- [ ] No automated substitute, service-only restart, or unrelated host lifecycle event is recorded as reboot acceptance.

## Blocked by

- [Accept the real dev stand on the canonical host](https://github.com/prineycom/voice-agent-v2/issues/71)

</details>

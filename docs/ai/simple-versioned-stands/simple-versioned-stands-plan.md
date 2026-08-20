# Simple versioned stands: approved Grill plan

## Purpose

Provide a deliberately simple, operator-owned way to run two independent local Voice Agent v2 stands on one supported host. This plan is the accepted product boundary for the following issue sequence; it does not replace the existing application architecture.

## Supported target and prerequisites

The first release supports only Arch Linux or EndeavourOS x86_64 with NVIDIA. `stand doctor` is read-only: it reports exact missing prerequisites and actionable installation commands, but never changes the operating system. A fully ready stand requires user-systemd linger, rootless Docker, the required runtime binaries, and the selected model/runtime caches. It must report missing system packages, Node, Python, Git, Docker prerequisites, models, and caches honestly.

Two complete stacks are intentional: `main` and `dev`, each loopback-only. They include their own LLM, LiveKit, gateway, STT, and TTS stack. The known RTX 4070 resource-budget risk is accepted; the work measures it rather than adding shared-inference architecture.

## Ownership and layout

The controller has one ordinary Git clone at `~/.local/share/voice-agent-v2/source`. Stands, releases, private configuration, data, and runtime state are outside the Git repository.

```text
~/.local/share/voice-agent-v2/
  source/                         controller clone
  releases/<full-commit-sha>/      immutable release store
  instances/main/current -> ...    main selected release
  instances/dev/current -> ...     dev selected release
  instances/<name>/...             instance-private data, config, cache,
                                   runtime, workspace, credentials, and
                                   Docker AgentEnvironment state
```

A release is built from a full `git archive` snapshot of an exact committed SHA and contains no `.git`. It includes the production frontend build, a per-release Python virtual environment from a dedicated production lock, and a release manifest. Construction occurs in a temporary directory and promotes by rename only after success. Frontend `node_modules` are removed after a successful build when safe. Existing completed releases are reused; no automatic release deletion occurs.

`main` and `dev` each have a separate `current` pointer and separate configuration, explicit ports, data, cache/runtime paths, workspace, credentials, and Docker `AgentEnvironment`/container state. They share only immutable release directories and heavy immutable model/runtime caches. An explicit instance-root contract (for example `VOICE_AGENT_INSTANCE_ROOT`) replaces hard-coded shared user paths such as `~/.voice-agent` and shared AgentEnvironment paths.

Existing legacy user data is untouched. New instance roots start empty until a separately scoped migration slice.

## Version and source policy

`main` accepts only `vMAJOR.MINOR.PATCH` tags. A tag once observed must remain immutable: a moved tag is refused. There is no automatic latest-version selection.

`dev` accepts a tag, branch, SHA, or `--local <repo> <committed-sha>`. A local build packages only the named committed SHA; dirty local changes are never packaged. Both stands support only versions beginning with the first tag that contains this mechanism. No compatibility is built for PR 49 or earlier commits.

## Command interface and lifecycle

The single shell interface is:

```text
stand doctor
stand init
stand deploy
stand start
stand stop
stand status
stand logs
stand list
```

`init` creates external directories, two strict mode-0600 `KEY=VALUE` configuration files, unique generated LiveKit credentials, and the user-systemd template. The files are parsed as data, never sourced as shell. Ports are explicitly configured, never automatically allocated.

`deploy` resolves/builds or reuses one exact SHA, validates that version's external configuration before activation, changes only the target instance's `current` pointer, and restarts only if that stand was already running. A failed readiness check after activation leaves the selected bad release visible for manual diagnosis or rollback. For `main`, manual rollback is another `stand deploy main <older-tag>`; there is no automatic rollback.

A user-systemd `voice-agent-v2@.service` template uses linger and an explicit dependency on the same user's rootless `docker.service`. One foreground launcher owns all application child processes and readiness. Logs remain only in journald. `start` enables boot persistence; `stop` disables it and stops the instance container without deleting its data or rootfs. Expected configuration failure does not restart the service. Repeated quick runtime failure reaches a clear failed state.

All stand listeners bind only `127.0.0.1`. Exposure, Tailscale, reverse proxy, TLS, and firewall remain operator-owned and out of scope.

## Deliberate non-goals

The base mechanism does not add signing, an installer platform, automatic updates, automatic cleanup, automatic rollback, release-directory write sandboxing, network exposure management, data migration, backup behavior, or legacy-data adoption. It neither copies nor mutates user data during deployment.

## Verification and deferred work

Each implementation slice supplies one focused automated smoke path for each relevant behavior, followed by real manual dev acceptance on the canonical host. The manual dev acceptance does not require reboot. Schema/data migration is deferred until a concrete incompatible schema exists, and then requires pre-mutation backups and an explicit recovery route. Actual reboot acceptance is a separate deferred task: physically verify linger, rootless Docker, enabled startup, exact active release, and readiness after a real reboot.

# ADR-0014: Use one signed launcher-owned installation and update transaction

- **Status:** Accepted
- **Date:** 2026-08-20
- **Decision owner:** Voice Agent v2 project architecture

## Context

The current source-built operational path can select a new release while the exact healthy service still runs an older one, lose the older release from `previous` when a newer application validator rejects historical metadata, and block later work at a fixed release-count ceiling. The service correctly refused a restart without restoration custody, but the end-user operation boundary was split across deployment, service application, application-specific manifests, and manual release bookkeeping.

An end-user installation also must not trust a floating Git checkout, host-side dependency resolution, or transport security alone. Old application code cannot own interpretation of a new channel, and a new application manifest cannot erase custody of exact healthy running bytes. The persistent Docker AgentEnvironment is installation data, not a program release or disposable cache.

## Decision

Ship one separately versioned `voice-agent` launcher. The launcher, not an application release, is the sole owner of release provenance and the later install/update/rollback transaction. It contains no voice runtime, credentials, mutable product checkout, AgentEnvironment, or updater daemon. systemd continues to own the long-running application process.

The executable contract is closed and rooted in a documented offline Ed25519 public key: canonical `voice-agent.channel.v1` stable-channel metadata, detached signatures, monotonic sequence and expiry, exact artifact size/SHA-256/platform/protocol checks, `voice-agent.platform-artifact-manifest.v1`, and stable `voice-agent.release-record.v1`. Archive admission rejects absolute/traversing/duplicate paths, undeclared output, devices/FIFOs, non-directory ancestors, and escaping or undeclared links. Production signing-key provisioning is a separate release-maintainer responsibility; the repository carries deterministic signing tooling and public test material only.

Use the XDG layout defined once in [`architecture.md` §6.7](../architecture.md#67-launcher-owned-installation-and-update-boundary): launcher in `~/.local/bin`, immutable program and durable data under `~/.local/share/voice-agent`, non-secret/private configuration under `~/.config/voice-agent`, reconstructible downloads/models/runtimes under `~/.cache/voice-agent`, logs/diagnostics under `~/.local/state/voice-agent`, and only lock/ephemeral service state under `$XDG_RUNTIME_DIR/voice-agent`. One installation ID owns one user systemd unit and one durable AgentEnvironment. Additional mount contents remain outside launcher deletion authority.

`status` and `doctor` are read-only by default and report selected, running, exact-ready, rollback, transaction, optional AgentEnvironment, and explicit rootless Docker truth separately. A pointer never implies health. Legacy discovery can retain the exact running ready release as future rollback custody only after canonical-root/owner/inventory, regular-file/no-follow, installed-unit, MainPID/cwd/executable/argv, runtime release/build/readiness, and Docker endpoint evidence agree. An incompatible historical application manifest is reported as legacy/unsupported; no unrelated or unowned release is adopted.

Later install/update slices must use one durable journal and keep the prior exact running healthy release before quiescing. Success means selected == running == exact-ready. Failure after quiesce restores and proves the prior release; a double failure remains explicit. Reachability-based collection keeps active plus one verified rollback and transaction references, never deletes user data or AgentEnvironment state, and has no fixed release-count admission deadlock.

This ADR supersedes ADR-0013 only for installation/update provenance, layout, service-install ownership, release retention, rollback custody, and activation transaction semantics. ADR-0013 remains the current legacy application's bounded foreground/systemd lifecycle until migration. Machine fields are owned by [`contracts/`](../../contracts/README.md) and `config/launcher-protocol-v1.json`; this ADR does not duplicate them.

## Consequences

### Positive

- One command surface can eventually make install/update success identical to exact runtime readiness.
- New launcher code interprets new metadata while stable release records preserve historical rollback custody.
- Signed prebuilt payloads remove customer Git/npm build authority and make mirrors untrusted.
- Read-only diagnosis can describe the live selected-new/running-old/no-previous state without changing it or exposing content.
- Durable AgentEnvironment and user data are disjoint from immutable program GC.

### Costs and limits

- Release maintainers need offline-root/online-key custody and signed publication procedures before production release.
- A bridge launcher is required when a channel's minimum protocol exceeds the installed protocol.
- This first slice proves contracts, packaging, and read-only discovery only. It does not install, download, migrate, activate, restart, repair, self-replace, or touch the live stand.
- Linux x86_64 NVIDIA/systemd is the first product platform. macOS runtime support remains unclaimed.

## Alternatives considered

- **Repair only the legacy pointer rule:** rejected because it preserves split deploy/apply ownership, source builds, fixed-count refusal, and application-manifest custody.
- **Mutable Git checkout and in-place environment update:** rejected because provenance, reproducibility, rollback, and customer-host dependency resolution remain weak.
- **Updater daemon or release service:** rejected because one bounded CLI invocation, systemd, and a durable journal are sufficient.
- **New Rust/Go/Zig launcher toolchain:** rejected because Node.js 26 SEA produces a deterministic self-contained executable from the existing toolchain.
- **Infer readiness from `current` or systemd active:** rejected because neither proves exact release identity and five-component admission readiness.

# ADR-0013: Use one bounded systemd service and verified immutable releases

- **Status:** Accepted
- **Date:** 2026-08-16
- **Decision owner:** Voice Agent v2 project architecture

## Context

Slices 6–8 deliberately used a foreground development runner. It already owns the exact local-LFM, LiveKit, gateway/controller, Whisper, and two-worker Silero process tree, but it had no boot owner, durable release identity, bounded outer restart, cache preflight, or compatible rollback. Slice 9 must add those properties on the canonical Arch/systemd host without inventing a deployment control plane, process framework, model updater, fleet manager, or destructive cache manager.

The gateway/controller is already the correct owner of session admission, turn drain, STT/TTS worker custody, provider readiness, and terminal events. Splitting every logical role into a new network service or system unit would duplicate those contracts. The browser owns the avatar host and MVP eye, while kiosk/browser autostart remains excluded. The active provider is still only local LFM2.5; historical cloud mode must never be mistaken for a locally supervised process.

## Decision

Use the tracked [`voice-agent-v2.service`](../../ops/systemd/voice-agent-v2.service) as one system-level systemd service running as the existing non-root `priney` user. systemd owns the outer process group and boot activation. The existing foreground runner owns child startup and shutdown in this exact order:

- start: compatible configuration and artifacts → local LLM → LiveKit → gateway/controller/STT/TTS/provider adapter → application Serve route → signaling Serve route;
- stop: application route (close admission) → gateway/controller graceful turn and worker drain → signaling route → LiveKit → local LLM.

The gateway has 60 seconds to drain; systemd applies a 75-second hard group boundary. `KillMode=mixed` lets the main runner perform ordered normal shutdown, while systemd still kills every remaining cgroup process at the hard bound if the runner is lost. The unit is `Type=notify`: the main runner sends `READY=1` only after the gateway's fresh five-component report is ready and both exact tailnet routes have passed startup. Install/reconciliation and rollback then wait for that systemd boundary plus the exact release ID in `/api/status`; an active-but-old process is not reported as successful activation. The foreground runner treats a later gateway-owned STT/TTS/provider capability that remains unready for two seconds as a service failure rather than leaving an unobserved orphan/dead worker.

Restart is `on-failure`, delayed five seconds, with `StartLimitBurst=2` inside 600 seconds: the initial start plus one completed automatic recovery may run; another failure ends in an actionable failed unit. Exit status `2` means incompatible configuration/release/artifact and is never restarted. Inference requests themselves still have zero automatic retry and no provider/model/backend fallback.

`./voice-agent-ops deploy` accepts only a clean committed source, a mode-`0600` current-user server configuration, compatible pinned artifacts/runtimes, matching tailnet identity, and passing non-destructive disk/cache preflight. It builds the ordinary browser entry with the exact commit, archives only committed source, writes a content-free immutable release manifest, and atomically moves the `current` link under `~/.local/share/voice-agent-v2`. The payload inventory covers every file, symlink, and directory plus type, mode, symlink target, size, and file hash; release metadata is exact-key validated and cryptographically bound to the release-directory identity. Reapplying the same source/config locator/public configuration returns an explicit no-op. Release count/bytes and active cache roots have declared maxima; reaching a bound refuses the operation and deletes no existing cache or release.

LiveKit credentials stay only in that mode-`0600` configuration. Releases persist neither credentials nor any verifier derived from them, and no independent private revision key is introduced. Service execution reads and validates one configuration snapshot and passes those exact values into the runtime. A credential-only edit is therefore not detected automatically: the operator must explicitly revalidate/deploy, restart the unit, and check status; an already-running process never claims to observe secret rotation.

The release tree is not a runtime scratch directory. The service mount namespace exposes it read-only. The non-root service execution boundary creates and validates the private runtime subtree `/run/user/1000/voice-agent-v2`, then routes Python bytecode to its `pycache` directory; component subprocesses that construct minimal environments either use `-B` or name an explicit mutable runtime/cache prefix. The deterministic tracer similarly allocates HOME, cache, temp, output, and bytecode below an explicit external mutable root and removes its run directory. Detached diagnostic expiry helpers receive only their prior minimal non-secret environment plus a private runtime-tmpfs bytecode prefix. No runtime-generated path is omitted from release validation to make this work.

Activation retains only the prior verified release as `previous`. `rollback` revalidates that release's file inventory, operations schema, referenced server configuration, exact external artifacts/runtimes, disk/cache preflight, tailnet identity, and embedded client build before moving either link. A corrupt current release cannot block recovery to that verified prior target; it is reported as the incompatible replaced release and is never silently blessed. It cannot roll back to an arbitrary commit or to a configuration path whose public compatibility facts changed. Secret values are loaded only at execution, never copied into the release, hashed into its public fingerprint, printed, or tracked.

The service unit is installed idempotently only by the explicit `sudo -n` `install-service` operation. It changes no firewall, Tailscale node identity, public route, bootloader, browser session, or unrelated service.

Avatar-host and MVP-eye recovery remains a versioned browser-build/readiness responsibility: their exact contracts/module identity are compiled into the release, selected-renderer failure stays visibly degraded with no alternate representation, selector, or fallback module, and reconnect/reload obtains the same compatible build. No browser/kiosk autostart is introduced. A future explicitly authorized cloud deployment may report the external provider's readiness only; `cloud-llm` is declared `external-readiness-only`, is inactive in this fixed local deployment, and can never become a systemd-owned process or fallback.

## Consequences

### Positive

- Normal boot and process loss have one established Arch/systemd owner with a finite recovery bound.
- Existing turn/session and inference custody remains authoritative instead of being duplicated by a deployment framework.
- A release and browser can report exact build/release identities, provider mode, avatar contract/module, and no-fallback fact without exposing secrets.
- Identical source/config-locator/public-configuration deployment is a proven no-op; incompatible public config/artifacts and disk pressure fail before activation/admission. Credential-only changes require an explicit service restart after validation because they are intentionally absent from release identity.
- Full runtime startup and repeated recovered-release tracer runs leave the complete release inventory byte-identical; mutable bytecode/temp state has an explicit external owner.
- Rollback is narrow and evidence-based rather than an arbitrary Git checkout.

### Costs and limits

- One owned-child failure restarts the complete local stack, not only that process. This is intentionally simpler and prevents cross-generation rooms/inference from surviving a partial restart.
- The canonical service is host/user/path specific. A different host remains a new measured deployment, not fleet support.
- Three immutable releases are retained at most; no automatic cleanup chooses what to delete.
- Browser/avatar runtime liveness cannot be supervised without entering excluded kiosk/autostart scope.
- Controlled systemd, full-stack process loss, configuration, disk, and rollback rehearsals do not prove a physical reboot, physical microphone/audibility, second-device tailnet voice, Raspberry Pi frame rate, or a 20-turn physical full-stack soak. Those remain explicit acceptance gaps until actually performed.

## Alternatives considered

- **One unit per logical component:** rejected because STT/TTS are controller-owned resident workers and the provider adapter is not a separate daemon; splitting them would duplicate readiness, cancellation, and secret boundaries.
- **Containers or a custom deployment/control daemon:** rejected as unnecessary new control-plane and artifact surfaces on the established single Arch host.
- **Unlimited systemd restart:** rejected because it can create a GPU/model/admission loop.
- **Automatic cache deletion or model upgrade:** rejected because both are destructive or silently change artifact identity.
- **Arbitrary Git rollback:** rejected because it cannot prove artifact, configuration, contract, or client-build compatibility.
- **Supervise cloud LLM or start a browser/kiosk:** rejected because those are outside the host process boundary and current scope.

# E3.3 deterministic evidence — background processes, mounts, and lifecycle

## Scope and boundary

This slice extends the one installation-owned Docker `AgentEnvironment`; it adds no selectable environment, runtime, lifecycle target, mount target, port publication, scheduler, or host fallback. The architecture contract is [`architecture.md` §9.10](../architecture.md#910-e33-background-processes-explicit-mounts-and-lifecycle-ux).

- Background shell/code admission returns an opaque controller receipt. Private registry and rootfs claims bind it to installation owner, exact container/spec/generation, PID/start time, executable, process group, and current PID 1 start time. Bounded poll/logs/wait/write/TERM→KILL freshly reconciles all identity fields. A missing/tampered/stale/PID-reused claim returns stable failure/gone truth and cannot signal.
- Foreground call cancellation owns a separate fresh process identity. Ordinary AgentRun/turn/session/quit/idle/gateway/controller events perform no environment teardown and do not target background groups.
- Additional mounts are a strict restart-pinned typed list of absolute precreated user-owned non-symlink sources, fixed nonoverlapping destinations, and RO/RW mode. Custody and effective Docker mount facts enter the spec. Status explicitly warns that RO is readable/exfiltratable and RW is also mutable/deletable/encryptable.
- Lifecycle remains selector-free. Status is inspect-only. Reset/rebuild/remove/retire require confirmation and exact custody reinspection. Rebuild validates an unselected next generation before one atomic registry selection and retains the old stopped generation as nonselectable recovery material. Bind deletion remains separate and is not implemented.
- Port/listener, Git-lock, package-lock, concurrent-writer, process-state, stale-receipt, stopped/unhealthy/resource-stopped, and retained-generation conflicts are disclosed; unrelated work is never killed as repair.

## Deterministic coverage

`tests.test_agent_environment` uses fake Docker/container/process tables and disposable mount roots. It covers prompt background admission; opaque/no-PID receipt shape; controller-restart poll/log/write/wait/kill; foreground/background separation; container-stop process loss with same-ID recovery; tampered identity rejection; RO/RW `--mount` facts and authority disclosure; symlink, forbidden destination, raw-argument, custody drift, and spec-change rejection; no ports/socket; bounded resource stop-not-remove; confirmation; exact lifecycle; failed rebuild nonselection; retained old generation; and absence of prune/wildcard cleanup.

`tests.test_agent_environment_processes` invokes the fixed helper against disposable Linux `/proc` process groups. Independent helper processes reconcile one persistent receipt, write stdin, poll, wait, read bounded logs, and TERM→KILL the exact group. Separate cases tamper the rootfs claim and alter start-time identity, prove the owned group remains alive, restore the exact claim, and kill only that group. The canonical `./verify` behavior manifest owns both modules and its normal process/leak owner.

## Evidence limits

This is deterministic PR evidence only. It does not run Docker, stop an Engine/Desktop VM, validate native bind/file-sharing behavior, publish a port, use a live network/credential/model, reboot a host, exercise physical microphone/barge-in, or prove the complete application stack. It does not claim background survival across a real container/VM/host stop, automatic process restart, transactional writers, generic scheduling, or confidentiality of mounted/credential data from the agent. Linux Docker Engine, Docker Desktop/macOS, exact-model, real stop/daemon/Desktop, reboot, physical voice, and full-stack acceptance remain separately authorized tiers in [`testing.md`](../testing.md).

# E2.4 deterministic evidence — persistent AgentEnvironment

> Historical assumption note: this issue-era evidence required a separately published locked OCI index. The later personal-install correction replaced only that unavailable distribution assumption with explicit host-native locked-context preparation and a private exact local image ID. The AgentEnvironment identity, security, execution, persistence and lifecycle findings below remain unchanged; this historical run is not retroactively relabelled as real Docker evidence.

**Issue:** [#36](https://github.com/prineycom/voice-agent-v2/issues/36)  
**Tier:** deterministic PR evidence only  
**Command:** `./verify`

## Implemented contract

- `voice-agent.config.v2` is strict and single-environment. Historical V1 is accepted only by the one-way upgrade, which carries no former identity.
- `AgentRun` v1 binds the exact local LFM identity, full realtime identity, fixed decision/deadline budgets, controller-owned call IDs, cancellation and one final answer.
- One installation UUID derives one Docker owner key. An atomic private registry pins the endpoint fingerprint and selected exact ID/spec/generation; one Linux/macOS file lock serializes resolution and lifecycle transitions.
- Lookup uses exact managed+owner label filters followed by fresh inspection of every candidate. Duplicate, stale, partial, changed-endpoint and uncertain results fail closed.
- All terminal/file/search/write/edit/patch/code/process/receipt routes use `docker container exec` with the fixed image helper. No host operation or alternate execution path exists.
- The persistent rootfs ledger and private dispatch record prevent automatic replay after ambiguous acceptance. Only definite stopped-container rejection can start and retry the same ID/call ID once.
- Normal turn/session/controller events do not construct stop/remove. Explicit stopped recovery reuses the same ID. Resource reserve breach may stop, never remove, the exact selected ID.
- Selector-free confirmed reset/rebuild/remove/retire re-inspect endpoint and exact custody. Workspace/cache remain by default; no prune, wildcard or implicit cleanup is constructible.

## Deterministic observations

`tests.test_agent_environment` uses a process-safe fake Docker transcript and private filesystem fixtures to cover strict configuration, concurrent first creation, controller restart reuse, persistent markers, stopped same-ID recovery, endpoint/partial/duplicate/stale failure, all exec routes, bounded host reserve, at-most-once ambiguity and ledger loss, noncooperative cancellation, truthful status, rebuild/retention and exact lifecycle removal. Existing ordinary-voice and historical E2 tests remain in the same canonical gate.

The complete canonical output is saved as `artifacts/issue-36-verify.log`.

## Separate, still-open evidence

This report makes no claim about:

- Linux Docker Engine/client/server/rootless-or-rootful/cgroup/storage conformance;
- Docker Desktop/macOS/VMM/shared-root/disk-image behavior;
- the locked OCI index being present on a particular host;
- exact-model natural-task quality or network behavior;
- Docker daemon/Desktop restart, host reboot persistence, write-pressure overshoot;
- physical microphone, audible Kseniya, barge-in, full-stack soak or Raspberry Pi acceptance.

Those tiers require separate authorization and cannot borrow this deterministic result or one another's platform evidence.

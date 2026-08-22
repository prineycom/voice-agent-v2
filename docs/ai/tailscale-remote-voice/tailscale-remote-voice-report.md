# Do Report: tailscale-remote-voice

**Source:** captain-authorized Tailscale remote-voice launch brief and `remote-voice-scope.md`
**Parent:** direct PR over merged PR #95
**Status:** ✅ pass

## Changed files

| File or area | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/slice6_config.py`, `operations.py`, `slice6_gateway.py`, `scripts/run_slice6.py` | Validates one complete remote group, retains loopback internal signaling, renders the exact Tailscale ICE path, admits only the exact public Origin, and emits the exact WSS capability/CSP policy. | Exact HTTPS Origin; browser-reachable WSS; exact ICE candidate; internal/public separation; truthful application status. |
| `src/voice_agent_v2/stand_dev.py`, `scripts/stand.py` | Adds the dev-only mechanical Serve/configuration operation, private pre-mutation backups, narrow stale-rule removal, two independent HTTPS root proxies, and composed remote readiness. | Reproducible clean deployment; full LiveKit WSS namespace; no Funnel/LAN/wildcard exposure; honest readiness. |
| `contracts/`, `.env.slice6.example`, `README.md` | Extends public configuration truth and documents private topology ownership and operator commands. | Public origin/WSS/configuration contract; private host-specific ownership. |
| `tests/test_remote_voice.py`, `scripts/run_behavior_tests.py`, existing integration tests | Adds deterministic exact-origin, CSP, capability, Serve rendering/refusal, ICE, separation, backup, security and dishonest-readiness coverage to the canonical manifest. | Deterministic acceptance and refusal evidence. |
| `CONTEXT.md`, `docs/architecture.md`, `docs/testing.md`, `docs/roadmap.md`, ADR-0007/0013/0016, `docs/evidence/tailscale-remote-voice.md`, `AGENTS.md` | Records the bounded topology, owners, readiness semantics, security refusals and physical nonclaims. | Durable architecture/operations/testing authority. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Exact tailnet HTTPS application path and same-origin admission | ✅ implemented | Separate `:8443` Serve listener and exact Origin/CSP/session tests. |
| Browser-reachable TLS/SNI-valid WSS capability and full signaling path | ✅ implemented and host-proven | Same MagicDNS hostname on separate `:7443` root listener; exact capability; official LiveKit client joined through public WSS and observed the agent participant. |
| One tailnet-reachable LiveKit media candidate | ✅ implemented and host-proven | Private `tailscale0`/current Tailscale IPv4 group drives `node_ip`, interface include, IP allowlist and exact UDP bind; current LiveKit logs matched the configured and selected UDP host candidate. |
| Loopback-only internal signaling/control/inference | ✅ implemented | Gateway/agent URLs remain `127.0.0.1`; deterministic separation and listener checks. |
| Truthful composed readiness | ✅ implemented | `stand status dev` separately requires current identity, both Serve handlers, no Funnel, HTTPS probes, exact UDP bind and absence of the obsolete rule. |
| Tailnet-only bounded exposure and refusal policy | ✅ implemented | No Funnel, TURN, ICE/TCP, wildcard/LAN service bind, alternate candidate, generic handler or main topology path; exact refusal tests. |
| Retained dev state and clean mechanical deployment | ✅ host-proven | Selected release survived both bounded recovery actions; one canonical image refresh retained the prior image, `stand init` refreshed the repository launcher contract without hand-editing, and final status reported the AgentEnvironment running. |
| Intended second-device microphone/media/audio/cleanup | ⏳ Pasha acceptance | Explicitly outside host automation; exact manual observations remain open. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| Focused remote/config/startup/stand suites | ✅ pass | Continuation checkpoint records passing deterministic selections, including 5/5 final `tests.test_remote_voice`. |
| `git diff --check` | ✅ pass | Final pre-canonical tree. |
| `./verify` | ✅ pass | Exactly one invocation: 461 hermetic Python tests, 9 local-socket tests, 94 Vitest tests, TypeScript, both web builds, and Firefox/actual-LiveKit smoke; full output saved in `evidence/verify.txt`. |
| Initial selected-release start | ❌ closed prerequisite | Exact public readiness response identified only `image_preparation_stale`; build/release identity and gateway response matched. The unit was stopped before diagnosis and no topology workaround was applied. |
| `./stand agent-image prepare` | ✅ pass | Exactly once after the deployed merged PR #95 context truthfully reported stale; selected the current native context and retained the prior image without deletion. |
| Initial remote-apply restart | ❌ corrected deployment defect | The selected-release parser accepted the generated complete config, while the installed unit still invoked an older checkout parser and exited configuration-failed. No unit was hand-edited. |
| `./stand init`; `./stand start dev`; `./stand status dev` | ✅ pass | Canonical bootstrap replaced the stale checkout launcher contract while preserving the selected release; exactly one final readiness proof reported running/enabled/internal-ready/remote-ready and AgentEnvironment running. |
| HTTPS/Origin/WSS/ICE/cleanup host probe | ✅ pass | HTTP/2 app GET and exact CSP; exact-Origin capability with token omitted; official-client public WSS join; configured/selected Tailscale UDP host candidate; exact DELETE `204`; accepting/available cleanup. |
| Serve/firewall/listener inspection | ✅ pass | Separate root handlers on `:8443` and `:7443`, preserved unrelated `:443`, no Funnel, no media rich rule, loopback internals, Tailscale-only Serve and UDP media. |

## Unresolved uncertainty

- Pasha must still test the exact URL from a second tailnet device and observe browser load/connect, microphone permission/publication, remote participant/media, agent response audio, and disconnect cleanup.
- Chrome DevTools AXI found no supported Chrome binary on the host. The official-client host WSS/ICE proof makes no browser, microphone, remote-peer, audio, audibility, or playback claim.

# Do Report: tailscale-remote-voice

**Source:** captain-authorized Tailscale remote-voice launch brief and `remote-voice-scope.md`
**Parent:** direct PR over merged PR #95
**Status:** ⏳ host proof pending

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
| Browser-reachable TLS/SNI-valid WSS capability and full signaling path | ✅ implemented | Same MagicDNS hostname on separate `:7443` root listener; capability and Serve rendering tests; host upgrade proof pending. |
| One tailnet-reachable LiveKit media candidate | ✅ implemented | Private `tailscale0`/current Tailscale IPv4 group drives `node_ip`, interface include, IP allowlist and exact UDP bind; host proof pending. |
| Loopback-only internal signaling/control/inference | ✅ implemented | Gateway/agent URLs remain `127.0.0.1`; deterministic separation and listener checks. |
| Truthful composed readiness | ✅ implemented | `stand status dev` separately requires current identity, both Serve handlers, no Funnel, HTTPS probes, exact UDP bind and absence of the obsolete rule. |
| Tailnet-only bounded exposure and refusal policy | ✅ implemented | No Funnel, TURN, ICE/TCP, wildcard/LAN service bind, alternate candidate, generic handler or main topology path; exact refusal tests. |
| Retained dev state and clean mechanical deployment | ⏳ pending host proof | Deploy/restart preserves installation roots and preprovisioned AgentEnvironment by contract; live deploy/apply/status evidence remains to be recorded. |
| Intended second-device microphone/media/audio/cleanup | ⏳ Pasha acceptance | Explicitly outside host automation; exact manual observations remain open. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| Focused remote/config/startup/stand suites | ✅ pass | Continuation checkpoint records passing deterministic selections, including 5/5 final `tests.test_remote_voice`. |
| `git diff --check` | ✅ pass | Final pre-canonical tree. |
| `./verify` | ✅ pass | Exactly one invocation: 461 hermetic Python tests, 9 local-socket tests, 94 Vitest tests, TypeScript, both web builds, and Firefox/actual-LiveKit smoke; full output saved in `evidence/verify.txt`. |
| Dev deploy/apply and bounded host probes | ⏳ pending | Will be recorded after deployment of the committed task release. |

## Unresolved uncertainty

- The second tailnet-device path, browser microphone permission/publication, remote participant/media, agent response audio and physical playback remain Pasha-observed Acceptance.
- Host-side evidence cannot claim those physical observations and will not claim them after deployment.

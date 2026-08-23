# Do Report: persist-tailnet-media-firewall

**Source:** Firstmate launch brief backed by the private full-ICE trace and synchronized physical iPhone firewall A/B
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py`, `scripts/stand.py` | Adds strict private approved-peer `/32`, exact runtime/permanent firewalld ownership/reconcile/rollback/disable, effective-zone resolution, and truthful diagnostic readiness. | Exact narrow durable lifecycle; private ownership; transactional update/cleanup; truthful status. |
| `tests/test_remote_voice.py` | Adds fake-command coverage for exact bytes, idempotence, partial failures, unrelated preservation, peer/port/zone update, drift, broader rules, and disable. | Deterministic acceptance and refusal evidence. |
| `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/roadmap.md`, `docs/adr/0016-tailscale-remote-voice-topology.md`, `docs/evidence/tailscale-remote-voice.md`, `AGENTS.md` | Corrects the no-firewall premise, records the positive physical A/B/nonclaims, and documents operator/security/readiness ownership. | Durable architecture, operator and evidence correction. |
| `artifacts/persist-tailnet-media-firewall-verify.log` | Full output of the one bounded canonical verification run. | Exact saved verification evidence. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| One explicit approved peer `/32`, private only, never inferred | ✅ | Strict private config parser and required first `--peer`; tracked tests use reserved shared-address fixtures only. |
| Exact runtime and permanent effective-zone rule through `sudo -n firewall-cmd` | ✅ | Canonical rule builder, effective assigned/default zone resolver, exact query/add/remove transcript assertions; no reload/raw firewall path. |
| Idempotent transactional apply/update with owned-only rollback | ✅ | Surface snapshots, exact old/new reconciliation and rollback tests for runtime/permanent failure plus peer/port/zone change. |
| Exact disable/delete cleanup and unrelated preservation | ✅ | `remote-voice disable dev` removes/proves only ownership bytes and is repeatable; unrelated-rule assertions remain unchanged. |
| Truthful readiness | ✅ | Separate ownership, identity, zone, runtime, permanent, broader-lookalike, Serve, application, signaling and listener reasons; ready is labelled configured transport admission. |
| Preserve exact UDP topology and exclusions | ✅ | Existing exact `tailscale0` LiveKit configuration remains; no TCP/TURN/wildcard/interface trust/forwarding/masquerade/fallback command is introduced. |
| Correct durable docs and physical evidence/nonclaims | ✅ | ADR-0016, architecture, operator/testing/roadmap and Tailscale evidence updated. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src <pinned-test-python> -m unittest tests.test_remote_voice tests.test_stand_dev` | PASS | 25 focused tests; performed during implementation before the canonical gate. |
| `./verify` | PASS | Sole canonical run; 465 Python behavior tests, 94 Vitest tests, typecheck, both builds and actual-LiveKit Firefox smoke passed. Full output: `artifacts/persist-tailnet-media-firewall-verify.log`. |

## Unresolved uncertainty

- Deterministic tests do not claim live firewalld, reboot persistence, a future iPhone attempt, or physical audibility.
- The authorized exact-green `dev` deployment and host proof occur only after exact-head GitHub CI passes.

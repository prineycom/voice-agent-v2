# Do Report: persist-tailnet-media-firewall

**Source:** Firstmate launch brief backed by the private full-ICE trace and synchronized physical iPhone firewall A/B; targeted correction authorized after the first live update exposed an old installed-launcher/new-schema ordering defect
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py`, `scripts/stand.py` | Adds strict private approved-peer `/32`, exact runtime/permanent firewalld ownership/reconcile/rollback/disable, effective-zone resolution, truthful diagnostic readiness, and a durable selected-release systemd dispatcher installed/reloaded before deployment selection or remote schema mutation. | Exact narrow durable lifecycle; private ownership; transactional update/cleanup; launcher-before-schema compatibility; truthful status. |
| `tests/test_remote_voice.py`, `tests/test_stand_dev.py` | Adds fake-command coverage for exact bytes, idempotence, partial failures, unrelated preservation, peer/port/zone update, drift, broader rules, disable, and old installed-launcher → new schema ordering without a configuration-failed interval. | Deterministic acceptance, rollback and refusal evidence. |
| `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/roadmap.md`, `docs/adr/0016-tailscale-remote-voice-topology.md`, `docs/evidence/tailscale-remote-voice.md`, `AGENTS.md` | Corrects the no-firewall premise, records the positive physical A/B/nonclaims, and documents operator/security/readiness ownership. | Durable architecture, operator and evidence correction. |
| `artifacts/persist-tailnet-media-firewall-verify.log`, `artifacts/persist-tailnet-media-firewall-correction-verify.log` | Full output of the original and explicitly authorized correction canonical runs. | Exact saved verification evidence for both pushed heads. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| One explicit approved peer `/32`, private only, never inferred | ✅ | Strict private config parser and required first `--peer`; tracked tests use reserved shared-address fixtures only. |
| Exact runtime and permanent effective-zone rule through `sudo -n firewall-cmd` | ✅ | Canonical rule builder, effective assigned/default zone resolver, exact query/add/remove transcript assertions; no reload/raw firewall path. |
| Idempotent transactional apply/update with owned-only rollback | ✅ | Surface snapshots, exact old/new reconciliation and rollback tests for runtime/permanent failure plus peer/port/zone change. |
| Exact disable/delete cleanup and unrelated preservation | ✅ | `remote-voice disable dev` removes/proves only ownership bytes and is repeatable; unrelated-rule assertions remain unchanged. |
| Launcher-before-schema canonical handoff | ✅ | Init owns a stable selected-release dispatcher; deploy/apply atomically install and reload it before selection/schema mutation. Deterministic old-launcher upgrade and induced firewall failure prove old-schema rollback and no configuration-failed interval. |
| Truthful readiness | ✅ | Separate ownership, identity, zone, runtime, permanent, broader-lookalike, Serve, application, signaling and listener reasons; ready is labelled configured transport admission. |
| Preserve exact UDP topology and exclusions | ✅ | Existing exact `tailscale0` LiveKit configuration remains; no TCP/TURN/wildcard/interface trust/forwarding/masquerade/fallback command is introduced. |
| Correct durable docs and physical evidence/nonclaims | ✅ | ADR-0016, architecture, operator/testing/roadmap and Tailscale evidence updated. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src <pinned-test-python> -m unittest tests.test_remote_voice tests.test_stand_dev` | PASS | Original 25 focused tests plus 29 focused tests after the authorized launcher-order correction. |
| `./verify` at `9ff9afe` | PASS | Original authorized canonical run; 465 Python behavior tests, 94 Vitest tests, typecheck, both builds and actual-LiveKit Firefox smoke passed. Full output: `artifacts/persist-tailnet-media-firewall-verify.log`. |
| `./verify` after targeted correction | PASS | One additional explicitly authorized run; 469 Python behavior tests, 94 Vitest tests, typecheck, both builds and actual-LiveKit Firefox smoke passed. Full output: `artifacts/persist-tailnet-media-firewall-correction-verify.log`. |

## Unresolved uncertainty

- Deterministic tests do not claim live firewalld, reboot persistence, a future iPhone attempt, or physical audibility.
- The first green-head live apply was safely rolled back after exposing the installed-launcher ordering defect; no unrelated firewall state changed.
- The one authorized corrected exact-green `dev` deployment and host proof occur only after updated exact-head GitHub CI passes.

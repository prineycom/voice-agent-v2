# Do Report: 70-complete-explicit-lifecycle

**Source:** https://github.com/prineycom/voice-agent-v2/issues/70
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py` | Adds explicit start/stop/list inventory, persistence and failure status, conditional deploy restart, linger activation, and bounded user-unit policy. | Lifecycle/inventory; persistence; conditional restart; bounded/config failure; selected-release visibility; unit/launcher/journal contract. |
| `src/voice_agent_v2/agent_environment.py` | Adds locked, exact-identity non-destructive stop for the registered persistent container. | Non-destructive ordinary stop. |
| `scripts/stand.py` | Exposes `start`, `stop`, and `list`; maps launcher configuration failures to non-restartable exit status 2. | Complete CLI; expected configuration failure behavior. |
| `tests/test_stand_dev.py` | Adds deterministic main/dev lifecycle, preservation, exact container stop, conditional deploy, unit ownership, journal, and bounded failure smoke coverage. | All automated acceptance evidence. |
| `README.md` | Documents the complete operator lifecycle and conditional deployment behavior. | Documented lifecycle and inventory. |
| `docs/architecture.md` | Records lifecycle, persistence, exact container custody, linger/rootless Docker, launcher, journald, and bounded failure contracts. | Architecture/failure ownership. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| `start`, `stop`, `status`, `logs`, and `list` cover `main` and `dev` | ✅ | Shared strict instance functions plus deterministic two-instance lifecycle/inventory/log smoke. |
| Start enables persistence; stop disables it and preserves mutable/container state | ✅ | Linger + user-unit enable on start; disable/stop/reset on stop; exact container stop has no remove path and preservation sentinels remain. |
| Deployment restarts only a previously running instance | ✅ | Running state is captured before resolution/build; restart is conditional and stopped-instance smoke asserts no restart. |
| Configuration failure does not restart; quick runtime failure is bounded and reported | ✅ | Launcher configuration exits 2; unit prevents restart for 2 and limits runtime starts to three per 60 seconds; status reports suppression/bounded retries and restart count. |
| Bad activated release remains selected and visible | ✅ | Pointer switching has no rollback/compensation; existing failed-readiness smoke verifies exact bad SHA and not-ready status until deliberate selection. |
| Linger, same-user rootless Docker dependency, one launcher, readiness, and journald | ✅ | Generated user unit uses `default.target`, explicit `Requires/After=docker.service`, one notify `ExecStart`, control-group ownership, and journal stdout/stderr; start enables same-user linger. |
| Focused deterministic smoke coverage | ✅ | `tests.test_stand_dev` covers both instances and every required seam without real service/stand mutation. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | Canonical bounded PR gate; full output saved outside the repository in `/tmp/voice-agent-v2-lifecycle-70.verify.log`. |

## Unresolved uncertainty

- Physical Docker-platform, live-network, reboot, voice/full-stack, and issues #71–#73 manual acceptance remain explicitly unclaimed.

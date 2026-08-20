# Do Report: 66-initialize-local-dev-release

**Source:** https://github.com/prineycom/voice-agent-v2/issues/66
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/stand_dev.py` | External state, strict private config, immutable local release, user-systemd, readiness/status/log boundaries | All |
| `scripts/stand.py` | `init`, dev-local deployment, status/logs, and foreground launcher commands | All |
| `tests/test_stand_dev.py` | Deterministic filesystem/Git plus fake systemd/journal smoke coverage | All |
| `scripts/run_behavior_tests.py` | Registers the focused hermetic owner | Smoke coverage |
| `README.md` | Documents the first dev-only local stand interface and its boundaries | Operator use |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| External initialization, strict mode and unique LiveKit credentials | ✅ | `StandDevTests.test_init_creates_external_private_state_strict_configs_and_unique_credentials` |
| Data-only configuration and explicit ports | ✅ | `StandDevTests.test_private_configuration_is_strict_data_and_never_shell_input` |
| Exact clean SHA, immutable release, atomic selected pointer | ✅ | committed/dirty-SHA smoke tests; Git archive omits `.git` |
| User-systemd foreground start and exact readiness/status | ✅ | recording user-systemd seam tests successful and unready paths |
| Journal discovery and visible failure | ✅ | `StandDevTests.test_failed_readiness_stays_selected_and_status_and_logs_are_honest` |
| Focused deterministic coverage without host mutation | ✅ | fake systemd/journal boundary and disposable Git repository |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | Full output: `artifacts/issue-66-verify.log`; canonical 90-second PR gate passed. |

## Unresolved uncertainty

- Physical user-systemd/Docker/full-stack acceptance remains the separately scoped manual work; the automated seam makes no such claim.

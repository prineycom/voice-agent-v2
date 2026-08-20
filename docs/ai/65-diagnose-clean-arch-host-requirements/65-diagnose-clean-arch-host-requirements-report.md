# Do Report: 65-diagnose-clean-arch-host-requirements

**Source:** https://github.com/prineycom/voice-agent-v2/issues/65
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `stand` | Adds the `stand doctor` command entry point. | Read-only command and supported/incomplete/ready result. |
| `scripts/stand.py` | Dispatches the command without setup or lifecycle actions. | Read-only command. |
| `src/voice_agent_v2/stand_doctor.py` | Implements inspection-only host, package, runtime, Docker, artifact, and immutable-cache diagnosis. | All diagnostic and remediation criteria. |
| `tests/test_stand_doctor.py` | Deterministic ready, incomplete, and no-mutation smoke coverage. | Focused automated coverage. |
| `scripts/run_behavior_tests.py` | Includes the smoke coverage in the canonical Python manifest. | Focused automated coverage. |
| `README.md` | Documents command scope and its truthful Docker readiness boundary. | Operator-facing result/remediation clarity. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Read-only diagnosis | ✅ | `SystemProbe` exposes only subprocess queries and filesystem reads; the deterministic probe has no mutation capability. |
| Supported/incomplete/ready decision | ✅ | `Diagnosis` has explicit `unsupported`, `incomplete`, and `ready` states and exit codes. |
| Exact prerequisites | ✅ | Exact packages are declared in `stand_doctor.py`; selected artifacts, Python runtimes, and cache roots come from `config/operations-v1.json`. |
| Actionable remediation | ✅ | Every failed check renders a `remedy:` command; doctor only prints it. |
| Installed-but-not-ready state | ✅ | User-systemd service, linger, rootless Docker security option, NVIDIA Docker runtime, artifacts, and immutable caches independently gate `READY`. |
| Focused smoke coverage | ✅ | `tests.test_stand_doctor` is in the canonical hermetic manifest. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Canonical bounded PR gate; full output saved at `artifacts/issue-65-verify.log`. |

## Unresolved uncertainty

- —

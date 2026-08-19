# Do Report: 39-e3-3-background-mounts-lifecycle

**Source:** https://github.com/prineycom/voice-agent-v2/issues/39  
**Parent:** —  
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/agent_environment.py` | Private process claims, bounded operations, exact identity reconciliation, mount custody/spec/inspection, survival truth, atomic rebuild and exact lifecycle | background/process, mounts, lifecycle, conflicts, resources |
| `agent-environment/helpers/agent-helper` / `Dockerfile` | Persistent process ledger, bounded logs/stdin/wait/TERM→KILL, fresh `/proc` validation, foreground group proof | opaque receipts, restart reconciliation, PID reuse, cancellation separation |
| `src/voice_agent_v2/agent_environment_config.py`, config/contracts | Strict typed `additional_mounts`, process receipt/status contract and disclosures | restart-pinned RO/RW mounts, no selectors/raw args/ports |
| `tests/test_agent_environment.py` | Fake Docker/process/mount/lifecycle matrix | deterministic acceptance coverage |
| `tests/test_agent_environment_processes.py` | Disposable real `/proc` groups, tamper/PID reuse/cancellation evidence | signal safety and bounded background operations |
| `scripts/run_behavior_tests.py` | Adds the process owner to the canonical hermetic manifest | canonical PR gate ownership |
| `README.md`, `CONTEXT.md`, `AGENTS.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/e3-3-background-mounts-lifecycle.md` | Coherent active contract, terminology, test tiers, evidence/nonclaims | complete user/operator truth |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Prompt opaque background start plus poll/log/write/wait/exact-group kill | ✅ | fake-controller and disposable helper process cases |
| Survival across controller/call events; foreground separation | ✅ | replacement manager plus independent foreground/background process-group case |
| Tampered/stale/PID-reused receipt cannot signal | ✅ | private/rootfs claim mismatch and `/proc` start-time cases |
| Real-stop process loss with same-ID file/rootfs truth | ✅ deterministic contract | fake stop/start marks claims gone and preserves logs/binds; real platform tier remains separate |
| Typed RO/RW mounts, custody, spec drift and authority disclosure | ✅ | schema, explicit `--mount` transcript, symlink/overlap/custody/stale cases |
| No default ports/socket/host fallback and visible conflicts | ✅ | effective inspection/status and retained fixed construction |
| Selector-free confirmed exact reset/rebuild/remove/retire | ✅ | exact reinspection, lazy reset, failed-candidate nonselection, retained retirement cases |
| Resource failure stops, never removes | ✅ | existing reserve case retained and hardened with confirmed stop truth |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Full output saved in `artifacts/issue-39-verify.log` |

## Unresolved uncertainty

- Linux Docker Engine, Docker Desktop/macOS, exact-model/live-network, real daemon/Desktop/container stop, reboot, physical voice/barge-in, full-stack, and Raspberry Pi acceptance remain separate authorized evidence tiers; no result is inferred here.

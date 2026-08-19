# Review: 39-e3-3-background-mounts-lifecycle

**Source:** Issue #39, implementation report, and current diff  
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Important | `AgentEnvironment.execute` initially retained a reserved process slot after definite pre-dispatch credential failure or fixed-helper launch rejection | Bounded process admission could be exhausted by calls that created no usable process | Remove only the unissued private reservation on definite non-dispatch/rejection; retain ambiguous reservations as unknown | Fixed |
| Important | Fixed-helper process identity failures initially omitted process-state details | Status could continue showing an old last-reconciled state after tamper/PID-reuse rejection | Return content-free `process/unknown` detail and retain no signal authority | Fixed |
| Important | Additional-mount source rejection treated the canonical gate's private runtime test root as a forbidden control root | Deterministic mount acceptance could not run under the repository's owned runtime directory | Forbid exact runtime/system/control/socket surfaces while allowing a private owned non-socket fixture subtree subject to full custody | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Definite failed background reservations | `src/voice_agent_v2/agent_environment.py` | Reservation cleanup is bound to exact call/container/receipt and never runs after ambiguous dispatch |
| Content-free unknown process state | `agent-environment/helpers/agent-helper` | Process/receipt helper errors emit only fixed kind/state and stable code |
| Runtime-root overbreadth | `src/voice_agent_v2/agent_environment.py` | Exact `/run`, system trees, known control/socket markers, socket/device type and custody remain rejected; canonical mount cases pass |

## Skipped issues

| Finding | Reason |
| --- | --- |
| — | No remaining critical, important, or minor finding after fixes |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Canonical bounded PR gate; full final output in `artifacts/issue-39-verify.log` |

## Recommendations

- Keep Linux Engine, Docker Desktop/macOS, exact-model, real stop/daemon/Desktop, reboot, and physical/full-stack evidence separate as documented.
- Proceed to the explicit commit and direct-PR delivery boundary.

# Review: 36-e2-4-agent-environment

> Historical review note: the accepted local-image correction later retired the separately published OCI-index assumption recorded here. It did not weaken the exact-image or AgentEnvironment custody findings; active architecture now requires explicit native locked-context preparation and a private exact local Docker image ID.

**Source:** issue #36 and diff from `4af4b9271ad4e52951bf5c70ce9fb7a49b98935d`  
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Critical | `agent_run.py` originally accepted a noncooperative final decision without a second live-identity check | Cancelled work could publish late output | Check full identity immediately after every model decision and before callbacks | Fixed |
| Critical | Active V2 loader originally used direct `Path.read_bytes()` | Weakened landed no-follow owner/mode custody | Reuse `AgentConfigService` descriptor/tree custody for V2 | Fixed |
| Important | Endpoint fingerprint originally hashed the complete Docker server version document | Engine upgrade could be mislabeled as endpoint replacement | Pin context plus stable Engine ID; leave effective version/platform to inspection evidence | Fixed |
| Important | Safe status originally claimed persistence for every unavailable state | Unknown Docker truth could become a false persistence claim | Use nullable persistence truth for unavailable/conflict | Fixed |
| Important | AgentRun history originally reinjected complete bounded operation output | Several valid calls could exceed the decision-input bound | Hash/count and cap model-visible bytes; retain only four recent receipts | Fixed |
| Important | Private dispatch receipts could grow too broadly | Persistent control state needed an explicit bound | Bound dispatch records and mark oversized completed receipts non-replayable | Fixed |
| Important | Active V2 omitted the one credential/network configuration shape | Contract was incomplete even though live credentials/network evidence is excluded | Add one fixed empty credential object and fixed network/no-port object | Fixed |
| Minor | Locked OCI identities require separately published/platform-verified artifacts | Deterministic PR cannot prove registry availability | Keep explicit platform/image nonclaim and separate evidence tier | Documented |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Cancellation/live identity | `agent_run.py`, `agent_run_provider.py`, `tests/test_agent_environment.py` | Noncooperative cancellation test returns only `selected_provider_cancelled` |
| Secure V2 custody | `agent_environment_config.py`, `agent_runtime.py`, `operations_cli.py` | Active loader reuses no-follow descriptor/tree checks |
| Endpoint/status/history/receipt bounds | AgentEnvironment and AgentRun sources/contracts/tests | Fake-Docker endpoint, ambiguity, ledger-loss, persistence and resource cases |
| Complete fixed V2 shape | V2 model/schema/example | Strict parser and JSON Schema validation |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Native Docker Engine/Desktop and OCI availability | Explicitly belongs to separate authorized evidence tiers, not deterministic PR mutation |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | Sole repository PR gate; full output saved in `artifacts/issue-36-verify.log` |

## Recommendations

- Proceed to the explicit commit/push/PR boundary.
- Run Linux Docker Engine and Docker Desktop/macOS evidence only under separate authorization; do not reinterpret missing prerequisites as green.

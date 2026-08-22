# Do Report: 71-preprovision-agent-environment-fix-create

**Source:** https://github.com/prineycom/voice-agent-v2/issues/71 and the captain-authorized correction report dated 2026-08-22
**Parent:** issue #71 canonical-host acceptance
**Status:** ✅ pass

## Changed files

| File or area | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/agent_environment.py` | Persists a closed allowlisted create-failure record, preserves unavailable truth across inspect-only status, removes the unsupported writable mount token, admits namespace-root process identity under the sole rootless endpoint, and exposes prepared/provisioned lifecycle truth. | Safe create classification; privacy; zero retry/fallback; canonical Docker compatibility; persistent exact-container lifecycle. |
| `src/voice_agent_v2/agent_run_provider.py`, `src/voice_agent_v2/livekit_runtime.py` | Provisions/starts/helper-validates the exact AgentEnvironment during the existing selected-provider startup and withholds readiness/admission on failure. Later health reads inspect rather than create or retry. | Pre-ready provisioning; truthful failure; one startup attempt; gateway restart reuse. |
| `src/voice_agent_v2/agent_runtime.py`, `src/voice_agent_v2/slice6_gateway.py`, `src/voice_agent_v2/stand_dev.py` | Publishes prepared/provisioned/running/stopped/unavailable truth and stable public failure reasons; `stand status` freshly inspects the registered container. | Status/UI/CLI truth; public/private separation; ordinary start/stop reuse. |
| `agent-environment/Dockerfile`, `agent-environment/image-lock.v2.json`, `src/voice_agent_v2/agent_environment_image.py` | Pins image/helper custody and runtime inspection to namespace root `0:0` inside current-user rootless Docker while retaining the locked image identity and security constraints. | Smallest accepted bind-access compatibility correction; no rootful/host fallback. |
| `contracts/agent-environment-status.v2.schema.json`, `contracts/agent-runtime-status.v2.schema.json`, `contracts/public-operational-status.v2.schema.json`, fixture and `contracts/README.md` | Adds lifecycle state fields, keeps the closed create subclass only in private AgentEnvironment status, and exposes only the stable generic reason at the public gateway boundary. | Additive contract truth and allowlisted privacy. |
| `web/src/ui/stateMapping.ts`, `web/src/ui/stateMapping.test.ts` | Renders an `AGENT ENV` row separately from selected LLM and maps the generic typed environment failure to unavailable. | UI readiness truth without leaking the private create subclass. |
| `tests/test_agent_environment.py`, `tests/test_agent_environment_image.py`, `tests/test_agent_profile_runtime.py` | Adds deterministic create classification/preservation/privacy, canonical mount grammar, startup gating, public/private status and identity-drift regressions while retaining the existing concurrency, same-ID lifecycle, recovery and no-fallback matrices. | Behavioral seam proof. |
| `README.md`, `docs/architecture.md`, `docs/testing.md`, `docs/adr/0015-rootless-agent-environment-preprovisioning.md`, `docs/evidence/local-native-agent-environment-image.md` | Records lifecycle ownership, rootless namespace-root decision, bounded real evidence, exact security boundary and explicit nonclaims. | Operator/architecture/ADR/evidence consistency. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Nonzero rejection versus malformed success | ✅ implemented and covered | Private `last_create_failure.cause` is closed over `docker_create_rejected` and `docker_create_response_invalid`; public AgentRun remains `agent_environment/environment_creation_failed`. The restored two-case regression exercises both results. |
| Bounded private state and inspect-only preservation | ✅ implemented and covered | Registry validation accepts only seven named scalar/enum fields. Deterministic cases cover nonzero, malformed success, no container, repeated status, exact allowlist and raw-diagnostic exclusion scoped to the new failure record. |
| Zero hidden retry/fallback | ✅ implemented | A failed readiness call performs one create attempt; later health/status is inspect-only. Existing endpoint/runtime matrices reject alternate endpoint, runtime and fallback routes. |
| Earliest canonical divergence and smallest compatibility fix | ✅ evidenced | The single isolated classified create rejected an explicit writable `--mount` token before an object existed. Docker 29.7 bind-mount authority documents writable as the default and only `readonly`/`ro`; removing only the unsupported token disconfirmed image, endpoint, custody, duplicate-container and helper execution causes. |
| Rootless bind-access compatibility | ✅ decided and evidenced | A task-owned counterfactual showed uid 1000 could not search the exact private bind roots while namespace root could. The accepted `0:0` identity remains inside the current-user rootless daemon with cap-drop `ALL`, `no-new-privileges`, explicit mounts and no Docker socket/rootful/host path. |
| Exactly one startup-provisioned persistent container | ✅ implemented | Enabled startup calls the existing `ensure_running()` owner before readiness; exact selected identity is reused when running or stopped, including service/gateway reconstruction. No selector or parallel control plane was added. |
| Truthful failure/readiness/status | ✅ implemented | Provision/start/helper-readiness failure keeps agent actions closed and reports unavailable. Private status distinguishes image prepared and container provisioned; gateway/CLI/UI report running/stopped/unavailable with only the stable public reason. |
| Ordinary stop/start persistence and identity drift | ✅ implemented | Existing ordinary stop rechecks and stops the exact ID without remove/data deletion; startup restarts and validates it. Missing, duplicate, stale-spec or unexpected user identity fails closed without replacement. |
| Concurrency, transactionality and recovery | ✅ covered | Existing deterministic matrices cover single creation under concurrency, same-ID reuse, failed rebuild nonselection, exact lifecycle reinspection, retained generations and deployment pointer failure behavior; new startup and failure cases exercise the corrected entry seam. |
| Isolated real lifecycle proof | ✅ complete | All diagnostic state/workspace/cache used a distinct task owner. The corrected helper and direct namespace-root differential completed the same bounded workspace write; only task-owned diagnostic containers/directories were removed. Retained dev/main state and user data were not touched. |
| Canonical PR gate | ✅ pass | The authoritative post-race final tree passed with the full test restored and privacy matching scoped only to `last_create_failure`. Complete output: `artifacts/issue-71-preprovision-agent-environment-fix-create-verify.log`. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` (initial invocation) | ❌ fail | The original new assertion searched the full status document and collided with intentional pre-existing safe credential metadata; no product assertion failed. |
| `./verify` (superseded deletion tree) | discarded | It completed before the superseding decision was applied and is non-authoritative regardless of result. |
| `./verify` (Pasha-authorized post-race final tree) | ✅ pass | Authoritative final run: 456 hermetic Python tests, 9 local-socket tests, 94 Vitest tests, TypeScript, both web builds and Firefox/actual-LiveKit smoke passed; `RESULT: PASS`. Full output is saved at the ignored artifact path above. |

## Unresolved uncertainty

- No physical voice, microphone, loudspeaker, reboot, daemon-restart, pressure, soak, Docker Desktop/macOS, Raspberry Pi or complete issue #71 acceptance is claimed.
- The task-owned Linux/rootless proof does not mutate or replace the retained canonical dev/main environments. A new post-merge acceptance remains required for full issue #71 closure.

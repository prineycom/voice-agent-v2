# Do Report: 71-agent-environment-final-live

**Source:** promoted issue #71 exact PR #92 live AgentEnvironment first-use diagnosis
**Parent:** https://github.com/prineycom/voice-agent-v2/issues/71
**Status:** ✅ pass

## Live finding promoted into this correction

Exact merged PR #92 produced parser-valid operation decisions, but the retained product-private AgentEnvironment state root was mode `0755`. Inspect-only status returned `absent` without validating that root. First use then rejected it as `environment_state_unsafe` before registry/container/helper creation, and `AgentRun` surfaced the raw exception as generic controller `inference_runner_failure`.

This delivery changes no live stand, Docker object, profile, image or Silero behavior. It provides only the bounded private-root custody and typed-error correction required before a fresh physical acceptance.

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/agent_environment.py` | Adds one descriptor-checked owner for the product-private state root and nested Docker-client directory; status, first use, lock and atomic registry writes share the invariant. | Missing/safe-broad roots become exact `0700`; unsafe custody fails before registry/Docker mutation; absent status stays non-creating for identity. |
| `src/voice_agent_v2/agent_run.py` | Translates `ensure_running()` failures through the existing typed AgentEnvironment boundary. | Stable `agent_environment/<code>` terminal instead of generic runner failure. |
| `tests/test_agent_environment.py` | Covers absent-root/permissive-umask bootstrap, nested client custody, historical `0755` hardening, unsafe path/identity cases, first fake creation and realtime typed failure. | Required deterministic regressions 1–5. |
| `tests/test_stand_dev.py` | Makes the standalone lifecycle fixture preserve the real exact-private-parent invariant. | Existing exact-ID stop custody regression remains representative and green. |
| `README.md`, `docs/architecture.md` | Documents status/root ownership, bounded hardening and typed first-use failure. | Operator and architecture truth. |
| `docs/evidence/local-native-agent-environment-image.md` | Records deterministic owners and explicit live nonclaims. | Current evidence/nonclaims. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Absent root under permissive umask | ✅ | `DockerCLICustodyTests` starts beneath an exact `0700` installation root, proves state root and nested empty Docker client are `0700`, status is `absent`, then fake first use creates one registry/container. |
| Existing affected `0755` root recovery | ✅ | Same-user, non-writable owner-accessible root is descriptor-opened, narrowed to `0700`, fsynced/rechecked; status creates no registry/container and later first use succeeds. |
| Unsafe custody rejection | ✅ | Symlink, non-directory, simulated foreign owner, cross-device, identity drift and writable modes return `environment_state_unsafe` before Docker/registry calls; no `fchmod` follows them. |
| Status truth | ✅ | Status owns/validates the root before image/registry inspection, but no-registry remains `absent` with null identity/generation and no container creation. |
| Nested Docker client stays private/empty | ✅ | Docker client no longer creates parents or applies path-based chmod; it consumes the same owner and carries only the fixed endpoint environment. |
| First-use typed error | ✅ | `ensure_running()` `AgentEnvironmentError` becomes `StageFailure("agent_environment", code)`; realtime terminal is exact `agent_environment/environment_state_unsafe`. |
| Zero mutation/readiness effects on failure | ✅ | Regression proves zero helper, TTS, Docker/container/registry calls and unchanged provider health fact. No retry/fallback is added. |
| Existing endpoint/image/environment/lifecycle contract | ✅ | Focused 141-test matrix retains rootless endpoint, prepared image, security, files/network/process/report/research, stand stop and resident readiness owners. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src python -m unittest tests.test_agent_environment tests.test_agent_environment_files tests.test_agent_environment_network tests.test_agent_environment_processes tests.test_agent_report_delivery tests.test_agent_research tests.test_stand_dev tests.test_slice6_realtime tests.test_resident_lifecycle tests.test_verify_architecture` | ✅ pass | 141 focused deterministic tests. |
| `python -m compileall -q src tests` | ✅ pass | Sources compile. |
| `git diff --check` | ✅ pass | No whitespace errors. |
| `./verify` | ✅ pass | Exactly once: 445 hermetic Python, 9 local-socket, Vitest 91, typecheck, production/review builds and Firefox/actual-LiveKit smoke; `RESULT: PASS`. Complete ignored log: `artifacts/agent-environment-final-live-71-verify.log`. |

## Boundaries and remaining acceptance

- No live dev deployment/restart, Docker/image/profile/environment mutation or physical turn occurred in this Ship.
- The two observed Silero synthesis failures are separate evidence and are not changed or conflated here.
- Deterministic fake-Docker coverage does not prove the retained live root has recovered or that a real environment/container exists.
- Issue #71 still requires a post-merge, newly authorized physical acceptance beginning with exactly one first turn, followed only on success by same-ID reuse and the documented single stop/start persistence sequence.

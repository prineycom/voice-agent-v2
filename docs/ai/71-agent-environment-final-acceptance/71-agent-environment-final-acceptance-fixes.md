# Fix Report: 71-agent-environment-final-acceptance

**Source:** promoted live acceptance report at `/home/priney/Projects/mymate/data/voice-agent-v2-agent-environment-final-acceptance-71/report.md`  
**Status:** ✅ pass  
**Scope stayed small:** yes

## Clarification decisions

- The only runtime endpoint is derived from the effective UID as the current-user rootless Unix socket. It is not a public configuration, ambient context, or fallback selector.
- The Docker client keeps an empty private `HOME`/`DOCKER_CONFIG`; it carries only the explicit endpoint and fixed locale/path environment.
- Endpoint and daemon failures use content-free `docker_endpoint_unavailable`, `docker_endpoint_incompatible`, or `docker_endpoint_changed`; a reachable daemon that lacks the exact prepared image remains `prepared_image_missing`.

## Changed behavior

- Before: the private Docker client discarded the host context but supplied no explicit endpoint, so it selected inaccessible system `default` `/var/run/docker.sock` and misreported a valid retained image as missing.
- After: every Docker call targets only `unix:///run/user/<effective-uid>/docker.sock`, after validating root-owned runtime ancestors, the private same-user runtime directory/socket, Linux/rootless daemon facts, and stable socket/daemon identity.
- Ambient `DOCKER_HOST`, `DOCKER_CONTEXT`, host Docker config/auth/helpers, remote/system/rootful endpoints, endpoint replacement, and fallback remain unavailable.

## Files changed

| File | Change |
| --- | --- |
| `src/voice_agent_v2/agent_environment.py` | Adds explicit current-user rootless endpoint custody, private fixed Docker environment, socket/daemon validation, stable identity checks, and endpoint-specific failure normalization. |
| `tests/test_agent_environment.py` | Adds deterministic private-client endpoint, status progression, actual-image-missing distinction, ambient override, missing/foreign/rootful/replaced socket, and changed-daemon coverage; updates runtime fake endpoint facts. |
| `tests/test_stand_dev.py` | Keeps explicit-stop fake daemon identity aligned with the validated rootless endpoint contract. |
| `README.md` | Documents the fixed current-user rootless endpoint and no ambient context/credential/fallback behavior. |
| `docs/architecture.md` | Records endpoint derivation, custody, daemon validation, failure isolation, and reason boundary. |
| `docs/evidence/local-native-agent-environment-image.md` | Records deterministic endpoint-custody coverage and remaining live acceptance boundary. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `PYTHONPATH=src python -m unittest -v tests.test_agent_environment tests.test_agent_environment_image tests.test_stand_dev` | ✅ pass | 47 focused config/image/environment/stand tests. |
| `./verify` | ✅ pass | Run exactly once: 431 hermetic Python + 9 local-socket tests, installed contract, Vitest 91, typecheck, both builds, and actual-LiveKit Firefox smoke; `RESULT: PASS`. Full ignored log: `artifacts/agent-environment-final-acceptance-71-verify.log`. |

## Follow-ups

- After merge, repeat the separately authorized live strict-profile AgentRun → same-ID reuse → one ordinary dev stop/start acceptance from issue #71.
- This correction makes no live Docker, image prepare/build, AgentRun, dev mutation, registry/container, credential, Web/provider, reboot, #86, migration, main, SemVer, or deployment claim.

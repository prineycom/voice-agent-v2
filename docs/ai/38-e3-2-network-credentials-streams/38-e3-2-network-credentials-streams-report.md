# Do Report: 38-e3-2-network-credentials-streams

**Source:** https://github.com/prineycom/voice-agent-v2/issues/38
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/agent_environment_config.py`, `contracts/agent-config.v2.schema.json`, `config/agent-config-v2.example.yaml` | Strict one-object credential names/modes plus network and bounded-stream configuration | One configuration; no selectors/raw values; network-none fixture; bounds |
| `src/voice_agent_v2/agent_environment_credentials.py` | Exact mode-`0600` private installation value custody and keyed create-time fingerprinting | Private sources; per-exec rotation; create-time stale spec; no ambient lookup |
| `src/voice_agent_v2/agent_environment.py`, `agent-environment/helpers/agent-helper` | Network policy, credential injection/cleanup, exact stream helpers, acknowledgement/unknown/no-retry, display redaction, truthful status | Credential bytes/modes; inbound/outbound hashes; ack truth; no host path/fallback; readiness preservation |
| `src/voice_agent_v2/agent_run.py` | Redact only final human display after raw model/tool use | Raw bytes unchanged; accidental display redaction |
| `agent-environment/Dockerfile` | `curl`, CA, SSH/Git client support and persistent helper roots | DNS/TLS/curl and remote Git capability |
| `tests/test_agent_environment_network.py`, `scripts/run_behavior_tests.py` | Synthetic fake-Docker/network/transport coverage in the canonical manifest | All deterministic acceptance cases |
| `contracts/agent-environment-status.v2.schema.json`, `contracts/README.md` | Fixed exposures, authority/no-secret, network and retry/rollback truth | Safe status and explicit threat model |
| `README.md`, `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `docs/evidence/e3-2-network-credentials-streams-remote-git.md` | Coherent E3.2 contract, evidence tiers, authority, and nonclaims | Documentation and tier separation |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| DNS/TLS/curl with no published port; disabled fails closed | ✅ | Bridge/none fake transcript and create-argument assertions |
| Exact synthetic token and display-only redaction | ✅ | Two endpoint captures, raw AgentRun history, redacted final copy |
| HTTP/download/upload/multipart and binary controller streams | ✅ | Exact lengths/SHA-256 and persisted binary fixture |
| Per-exec rotation/process inheritance/`0600` tmpfs files | ✅ | Mutable private store, old-process capture, fixed put/cleanup transcript |
| Create-time fingerprint/stale/rebuild/no public digest | ✅ | Restart controller and explicit retained-generation rebuild assertions |
| No ambient credentials/profile selection | ✅ | Strict schema, exact private store, helper environment test |
| No domain/payload binding | ✅ | Same token deliberately reaches two controlled endpoints and documented nonclaim |
| Remote Git clone/fetch/push | ✅ | Synthetic controlled remote transcript using declared per-exec token |
| Persistence and cancellation non-rollback truth | ✅ | Restarted controller observes downloaded hash; status/documentation deny rollback |
| Exact acknowledgement/unknown/no auto-repeat | ✅ | Single-invocation transport callbacks and status contract |
| Failure preserves one environment/no host fallback | ✅ | Existing E2.4 suite plus network-none and stream path rejection |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ pass | Full output: `artifacts/issue-38-verify.log` |

## Unresolved uncertainty

- Real Linux Docker Engine, Docker Desktop/macOS, live network/remote Git, exact-model, reboot, voice/full-stack, and Raspberry Pi evidence remain separate and pending.

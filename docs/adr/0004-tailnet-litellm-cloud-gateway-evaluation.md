# ADR-0004: Evaluate one tailnet LiteLLM gateway after local LLM failure

- **Status:** Accepted for Slice 2 evaluation; not yet an approved provider selection
- **Date:** 2026-08-10
- **Decision owner:** Voice Agent v2 project architecture

## Context

The only preregistered local LLM, official `LiquidAI/LFM2.5-2.6B` native BF16 on vLLM, failed hard Slice 2 quality, visible-output, isolation, and context-pressure gates. Its failed result remains authoritative and is not retried or erased. ADR-0003 permits a separately approved cloud evaluation after documented local failure; it does not permit automatic fallback.

The user selected one gateway path for that evaluation: LiteLLM at `http://rpi:4000`. This is a narrow topology exception to ADR-0001 because the gateway is a user-operated tailnet service, although STT, TTS, LiveKit, session control, and all local inference remain on the canonical Arch host. The gateway is not yet evidence of an underlying provider/model selection.

The URL uses plaintext HTTP. Before any prompt or context transfer, discovery proved that `rpi` resolved to a Tailscale CGNAT address, the kernel selected `tailscale0`, the peer was online, and `tailscale ping` established a direct WireGuard path. Unauthenticated liveliness and readiness returned HTTP 200. `/v1/models` returned HTTP 401 `auth_error`, so no model alias was discovered and no prompt was sent.

A separately authorized, read-only lookup inspected only likely LiteLLM credential material in the legacy project. No credential value was present. `infra/pi/litellm/config.yaml` and `infra/pi/agent/deploy/litellm.service` reference `LITELLM_MASTER_KEY`; the service points to `/etc/voice-agent/litellm.env`. The user authorized one exact non-echoing SSH read of that variable using remote `sudo -n`, corrected the SSH port to `2222`, and independently confirmed its ED25519 host fingerprint. A fresh scan matched the exact fingerprint; only that key was written to a task-private `known_hosts` file with mode `0600`, leaving global trust unchanged. Because no configured `.pub` or agent identity existed, the user authorized a dedicated persistent ED25519 identity at `~/.ssh/id_ed25519_rpi_litellm`; it was created without replacement, with private/public modes `0600`/`0644`, and a source-bound `restrict,from="100.78.238.32"` authorized-keys line. After the user confirmed installation, BatchMode public-key authentication and remote `sudo -n` succeeded. The exact authorized source `/etc/voice-agent/litellm.env` was absent (`FileNotFoundError`), so no other remote path/value was inspected.

For this closed-circuit Slice 2 test only, the user explicitly accepted the disclosure risk and placed the credential out-of-band in the approved task-private `litellm.token` file with mode `0600`. The harness did not expose it. An authenticated `GET /v1/models` over the re-proven Tailscale route returned six aliases. The user selected exactly `deepseek-v4-flash`, personally attested that it is the intended DeepSeek route, and directed the project to stop further RPi mapping/provenance/cost/privacy investigation. The active config did not prove the mapping; that fact remains explicit. No prompt was sent before the superseding preregistration.

## Decision

Evaluate exactly the allowlisted LiteLLM gateway endpoint `http://rpi:4000` as the only cloud-gateway candidate. Do not retry LFM, contact another gateway/provider directly, use an implicit LiteLLM default, or configure automatic/per-request fallback.

Plain HTTP to this endpoint is permitted only while all of these conditions are freshly proven for the measurement run:

1. `rpi` resolves exclusively to a Tailscale address;
2. the kernel route uses `tailscale0`;
3. the named Tailscale peer is online and a WireGuard direct or DERP path succeeds;
4. the client rejects redirects and connects only to the exact allowlisted scheme, host, and port.

If any condition fails, cloud readiness fails before prompt/context transfer. This conditional exception applies only to the host-to-gateway hop; the gateway's onward provider transport and provider region/privacy facts still require explicit evidence.

Gateway authentication must come from a user-authorized, dedicated, untracked secret source. The benchmark must not inspect ambient environment variables, shell history, password stores, unrelated token files, or gateway configuration. A missing credential is a hard readiness failure, not permission to search for one.

After authorized authentication, discovery may query only the model-list surface and privacy-safe usage/error metadata. The user selected exactly `deepseek-v4-flash`. For this temporary public-synthetic benchmark only, operator attestation replaces provider/model mapping provenance; the route is recorded as opaque and the active config is not claimed as proof. The cloud preregistration must supersede the local preregistration, be approved and committed before any completion request, and fix:

- exact endpoint and alias plus the explicit operator-attested opaque-routing limitation;
- public synthetic prompt/context fixtures and a permitted-field allowlist;
- concurrency `1/2/4`, TTFT, visible-content, completion, throughput, fairness, cancellation, sustained, and repeat gates;
- redirect, endpoint, model-echo, context-isolation, error, and no-fallback checks;
- the explicit test-only exception that leaves retention/training, region, privacy terms, pricing, and provider provenance unevaluated and therefore forbids private/live or production use;
- content-free tracked observations and cache-local raw output handling.

Only public synthetic fixtures may be used in Slice 2. Raw audio, private transcripts, conversation history, local files, credentials, and environment values are forbidden. STT and TTS remain local and separate; no cloud STT/TTS is authorized.

## Consequences

### Positive

- The documented local failure remains visible while the architecture can evaluate the explicit cloud option.
- Tailscale protects the otherwise plaintext host-to-gateway HTTP payload without opening a LAN/public fallback route.
- One alias, one endpoint, and no fallback preserve explainable privacy, cost, latency, and failure behavior.
- Authentication and content filtering fail closed before external transfer.

### Costs and risks

- The user-operated LiteLLM gateway becomes an additional availability, configuration, credential, and observability boundary if cloud mode later passes and is selected.
- Tailscale route proof does not establish the gateway's onward provider encryption, provider/model mapping, retention, training, region, or cost policy.
- LiteLLM aliases can hide provider/model changes unless the mapping is separately recorded and readiness verifies the expected echoed identity.
- Operator attestation permits the selected alias benchmark but cannot prove the gateway's hidden route, onward endpoint, privacy, region, or cost. Even a passing synthetic benchmark cannot activate private/live or production cloud mode without a later explicit provider/privacy decision.

## Alternatives considered

- **Accept the failed local selection:** declined by the user.
- **Retry or retune LFM:** rejected for this decision; further LFM inference is explicitly stopped.
- **Call a cloud provider directly:** not authorized; only the exact LiteLLM gateway path may be investigated.
- **Allow gateway default/fallback routing:** rejected because it hides provider/model, privacy, cost, and failure behavior.
- **Send prompts over unproven plaintext HTTP:** rejected; transport must fail closed before content transfer.

## Evidence and supersession

This decision narrows ADR-0003's explicit cloud option and supersedes ADR-0001 only where it prohibited every required user-managed service outside the canonical host. It does not restore the legacy Pi/Desktop inference topology: the tailnet node is limited to the selected cloud gateway boundary and may not host STT/TTS or become a general control/inference plane.

Transport and unauthenticated discovery evidence is recorded in `benchmarks/evidence/litellm-discovery.v1.json`; raw headers/bodies and network diagnostics remain under `/home/priney/.cache/voice-agent-v2/slice-2/discovery/litellm-rpi-4000/`. No cloud completion result exists yet.

# Do Report: 3-measured-host-model-and-llm-provider-budget

**Source:** https://github.com/prineycom/voice-agent-v2/issues/3
**Parent:** —
**Status:** ⚠️ operator-fixed checkpoint complete with measured limitations; cumulative delivery continues to Slice 5

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `benchmark-slice2`, `benchmarks/slice2/` | Added cache-guarded local measurement plus a commit-gated LiteLLM runner with exact fixture/alias/request allowlists, Tailscale proof, concurrency, cancellation, and cache-only content. | Repeatable harness; local evidence and precommitted cloud measurement contract. |
| `benchmarks/config/`, `benchmarks/fixtures/`, `benchmarks/schemas/` | Preserved local v1/cloud v2 and added fixed-stack v3: 90% Whisper CPU delivery exception plus exact pinned-legacy Qwen3 artifact/runtime/config and overlap/repeat gates. | Precommitted new TTS/overlap evidence; transparent result-aware delivery exceptions. |
| `benchmarks/evidence/host-live.v1.json` | Recorded whitelisted canonical-host facts. | Host/driver/resource identity. |
| `benchmarks/evidence/litellm-discovery.v1.json` | Recorded content-free Tailscale transport, unauthenticated LiteLLM health/model-list, and scoped credential-lookup evidence. | Endpoint/transport/privacy gate before cloud inference. |
| `benchmarks/results/` | Recorded privacy-safe local results plus fixed-stack cloud repeat, Qwen TTS, Whisper override, and overlap aggregates. | Machine-readable successes, failures, operator selections, and pending human gates. |
| `benchmarks/selection/` | Recorded failed selection and human-readable rationale. | Exactly one provider mode or failed selection; no fallback. |
| `docs/roadmap.md`, Issue #3, `docs/adr/0001`, `0003`, `0004` | Preserved the local failure and recorded selected alias `deepseek-v4-flash` as operator-attested/opaque for synthetic testing without claiming config proof or production privacy approval. | Authoritative cloud scope, topology exception, security and dependency gates. |
| `tests/test_slice2_benchmark.py` | Added offline schema, privacy, acquisition, fixture, and candidate-policy tests. | Reproducibility and safety guards. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Numeric thresholds committed before candidate results | ✅ | Approved preregistration committed at `f11f6caa42301376b96063cea61a617b94ecaad1`; every raw/result record names it. |
| Exact artifact/runtime/driver/context/concurrency identity | ✅ | Candidate manifest, runtime lock, host capture, and three tracked result files. |
| At least two STT and two TTS candidates; explicit one-LLM exception | ✅ | Five acquired candidate IDs: one LFM, two Whisper, two Piper; no alternate LLM. |
| Cold, warm, sustained, cancellation, recovery, and resource measurement | ✅ | Cache-local raw evidence and tracked aggregates. |
| LLM concurrency `1/2/4`, fairness, pressure, isolation, cancellation | ✅ | 108 marked parallel requests, four pressure requests, cancellation/replacement case. |
| Required selected-stack overlap and repeat | ⛔ | Correctly stopped: LFM and both STT candidates failed hard component gates, so no eligible stack/winner existed. |
| STT selection | ❌ | Turbo WER passed but CPU p95 failed; large-v3 CPU/latency passed but WER failed. |
| TTS selection | ⛔ | Automated timing passed; blind listening was not requested after upstream hard failures stopped selection. |
| Exactly one LLM mode or failed selection, no automatic fallback | ✅ | `selection.v1.json` preserves mode `none`; LiteLLM is an investigation candidate only and no default/fallback is allowed. |
| LiteLLM endpoint transport protected before content | ✅ | `rpi` resolved to Tailscale CGNAT, the kernel route used `tailscale0`, and a direct WireGuard path succeeded. |
| Safe gateway discovery only | ✅ | Liveliness/readiness and authenticated model-list succeeded over Tailscale; zero prompt requests and no private content were sent. |
| Explicit model alias/provider and superseding preregistration | ✅ | `deepseek-v4-flash`/operator-attested opaque routing committed before completions at `f698c002…`; no other alias or fallback was requested. |
| No secrets, raw content, or weights in Git | ✅ | Raw outputs/audio/models/runtimes and discovery bodies remain only in authorized cache; tracked evidence contains aggregates/hashes and no credential. |
| No cancelled alternate LLM artifact | ✅ | Acquisition manifest contains no alternate LLM; artifact cache has exactly the five approved directories. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./benchmark-slice2 validate` | ✅ pass | Validates local evidence plus cloud discovery and superseding preregistration schemas. |
| `./benchmark-slice2 measure cloud deepseek-v4-flash` before commit | ✅ blocked | Exited 2 before transport/token/request; primary/repeat ran only after commit. |
| Cloud primary/repeat | ❌ measured | Primary failed 17 automated gates; repeat failed 9. The delivery override fixes the alias without changing thresholds or claiming pass. |
| Qwen3 TTS primary/repeat | ✅ automated | Load ~3.6 s, first-signal p95 ~161 ms, RTF p95 ~0.402, VRAM peak 5,205 MiB, repeat stable; listening remains pending. |
| Fixed-stack overlap | ⚠️ mixed | Handoff 196 ms and resource/admission/stop gates passed; TTS/cloud regression and Whisper finalization-regression gates failed. |
| `./verify` | ✅ pass | Slice 1 behavioral verification remains green; 27 tests at checkpoint. |
| Runtime import/CUDA probe | ✅ pass | vLLM 0.26.0 / Torch 2.11 CUDA 13 and faster-whisper/CTranslate2/Piper imported; RTX 4070 visible. |
| Hash-checked acquisition | ✅ pass | LFM/STT/TTS artifact sizes and SHA-256 values matched manifests. |
| Qwen-artifact audit | ✅ pass | `qwen_acquired=False`; no matching model-cache path outside generic vLLM package code. |

## Failed gates

- LFM: at least five fixed prompts had empty visible answers; all 108 marked concurrency answers lacked visible markers; all four pressure answers were empty; concurrency-1 visible TTFT p95 exceeded its threshold.
- Whisper large-v3-turbo: CPU p95 `88.04% > 83.33%`.
- Whisper large-v3: WER `13.3127% > 12%`.

## Cloud discovery and preregistration gate

- The user authorized only LiteLLM at `http://rpi:4000`; transport is proven Tailscale-protected and no prompt was sent.
- `/v1/models` requires a bearer credential.
- The narrowly scoped legacy-project lookup found no credential value. It found only `LITELLM_MASTER_KEY` references in `infra/pi/litellm/config.yaml` and `infra/pi/agent/deploy/litellm.service`; the latter references `/etc/voice-agent/litellm.env`.
- The user independently confirmed the `rpi:2222` ED25519 fingerprint. A fresh scan matched exactly; only that host key was stored in task-private `known_hosts` mode `0600`, with global trust unchanged.
- BatchMode SSH then failed public-key authentication before remote `sudo -n` or file access. SSH configuration listed only default paths with no corresponding `.pub`/agent identity, and no private key was read or derived.
- The user authorized a dedicated persistent `~/.ssh/id_ed25519_rpi_litellm` ED25519 identity. It was created only after both paths were absent, with modes `0600`/`0644`, fingerprint `SHA256:85c6cJp2Ybv+otOTuID2l1zDxOOkFtd6sb498wWMKi4`, and a `restrict,from="100.78.238.32"` installation line.
- After installation confirmation, the dedicated key authenticated and remote `sudo -n` succeeded, but the exact authorized `/etc/voice-agent/litellm.env` source raised `FileNotFoundError`. No alternate remote path/value was inspected.
- For this closed-circuit test only, the user accepted the disclosure risk and placed the credential out-of-band in the task-private mode-`0600` file. The harness authenticated only `GET /v1/models` over re-proven Tailscale, exposed no credential material, and sent zero prompts.
- The active Docker service is LiteLLM `1.87.0`. Its mounted config contains no `deepseek-v4-flash` route, so config proof is absent. The user nevertheless selected that alias and accepts its DeepSeek route as operator-attested/opaque; further RPi mapping inspection is stopped.
- Superseding cloud preregistration v2 fixed the exact alias and numeric gates before both 132-request runs. Both runs remain failed evidence.
- Fixed-stack v3 records the selected Whisper exception and exact Qwen3 CustomVoice configuration from pinned legacy commit `93c5c397…`; no private clone audio or legacy code was copied.

## Fixed-stack evidence

- Whisper large-v3-turbo: WER `11.4551%`, component finalization p95 `294.39 ms`, original CPU p95 `88.04% > 83.33%`; delivery ceiling is transparently adjusted to `90%`. Combined CPU p95 `66.67%` passed, while finalization regression `59.56% > 20%` failed.
- Qwen3 CustomVoice/`ryan`: automated component and repeat timing/resource/cancellation gates passed. Recovery first signal around `5.6 s` is retained as an unpreregistered cold-first-request diagnostic. Twelve playable WAVs are outside Git under the authorized cache.
- Cloud→TTS: `196.48 ms` handoff passed; TTS regression `23.56% > 20%` and cloud completion regression `612.89% > 20%` failed.
- Barge-in: STT submission `113.66 ms`, old TTS stop `113.65 ms`, zero stale output, GPU reserve `4,788 MiB`, CPU p95 `66.67%`; STT finalization regression failed.

## Unresolved uncertainty

- Qwen3 Russian intelligibility/pronunciation/naturalness listening is pending and must not be fabricated.
- Physical microphone acceptance is deferred to the single final Slice 5 command under the cumulative delivery update.
- Provider cost, retention/training, region, onward endpoint, and config-proven model provenance remain intentionally unevaluated; failed cloud gates remain failed.

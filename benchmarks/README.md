# Slice 2 measured host/model budget

This directory is the evidence-and-selection harness for [Issue #3](https://github.com/prineycom/voice-agent-v2/issues/3). It does **not** implement production inference. Slice 1 contracts and required `./verify` remain intact.

## Gate state

`config/preregistration.v1.json` is the immutable local-plan preregistration committed before acquisition/inference. The official LFM BF16/vLLM candidate and both STT candidates later failed at least one hard gate; `results/` and `selection/` preserve that failed evidence. Further LFM inference is stopped.

The user then authorized only the LiteLLM gateway at `http://rpi:4000` for cloud investigation. DNS, `tailscale0` routing, and a direct WireGuard path prove that the plaintext HTTP hop is carried inside Tailscale. The user-confirmed `rpi:2222` ED25519 host key is isolated in task-private trust, and the dedicated source-restricted identity authenticates with remote `sudo -n`; the referenced remote env file was absent and no alternate remote path was searched. For this closed-circuit test only, the user accepted the disclosure risk and placed a credential out-of-band in the task-private mode-`0600` file. Authenticated `/v1/models` returned six aliases without prompts; generic `owned_by=openai` metadata does not establish underlying provider/model. `evidence/litellm-discovery.v1.json` records the privacy-safe state.

`config/preregistration.cloud.v2.json` now clearly supersedes v1 for exactly alias `deepseek-v4-flash`. The user accepts its DeepSeek routing as operator-attested and opaque, stops further mapping/provenance/cost/privacy investigation, and limits the exception to committed public synthetic fixtures. The preregistration must be committed before the first completion request; its harness refuses network I/O while uncommitted. This is not private/live or production approval.

The local runtime-priority decision and compatibility evidence remain in [`vllm-compatibility.md`](vllm-compatibility.md).

### Completed local candidate

- official `LiquidAI/LFM2.5-2.6B` native BF16 checkpoint;
- immutable revision `ab00687315bc1298e9d54e9c4b611dde9867ccc2`;
- two weight files, `5,394,427,456` bytes total, SHA-256 values recorded in `config/candidates.v1.json`;
- complete repository `5,412,390,391` bytes;
- official architecture `Lfm2ForCausalLM`, native in vLLM `0.26.0`;
- LFM Open License v1.0: notice obligations and a USD 10 million annual-revenue condition for commercial use.

Under local preregistration v1, GGUF/llama.cpp, community GPTQ, every alternate local LLM, and cloud were excluded. LFM failure correctly produced no LLM selection; the later LiteLLM decision is a separate gated path and does not rewrite v1.

## Local preregistration v1

vLLM is pinned to `0.26.0` / commit `568afb3…`, PyTorch `2.11.0`, cache-local CUDA 13, `max_model_len=4096`, `max_num_seqs=4`, and `gpu_memory_utilization=0.58`. The analytical 12 GB preflight is plausible but is not a result: the cap leaves `5,158 MiB` outside vLLM and about `3,103 MiB` after current display use plus the `1,536 MiB` reserve. Full STT/TTS overlap must prove the remainder.

| Gate | Proposed hard threshold |
| --- | --- |
| Complete local stack | GPU reserve `≥1,536 MiB`; host RAM available `≥6,144 MiB`; CPU p95 `≤83.33%`; no swap consumption; sustained growth `≤64 MiB VRAM` / `≤128 MiB RAM` per cycle. |
| LLM parallel workload | Concurrency `1/2/4`; three rounds of 12 fixed marked requests per level plus four 3584-input-token pressure requests. Raw TTFT p95 `≤1.5/2/3 s`; visible-content TTFT p95 `≤3/4/6 s`; completion p95 `≤10/12/16 s`; per-request p05 `≥15/10/8 tok/s`. |
| LLM throughput/fairness | Aggregate concurrency-2 throughput `≥1.5×` concurrency 1; concurrency 4 `≥2.25×`; Jain fairness `≥0.90`; slowest/median rate `≥0.75`; inter-token-gap p95 `≤750 ms`; queue p95 `≤250 ms`; zero starvation or marker leakage. |
| LLM quality | Blind 14-prompt Russian rubric, 0–4: overall `≥3.1`; relevance/correctness/Russian `≥3.0`; concision `≥2.75`; safety/honesty `≥3.5`; zero critical failure. Hidden reasoning is timed but not scored as visible output. |
| LLM cancellation/recovery | At concurrency 4, affected output stops `≤300 ms`; replacement admitted `≤250 ms`; unaffected-peer regression `≤15%`; zero stale/cross-request output; no runtime/provider switch. |
| STT | Public 24-item Russian LibriSpeech subset: WER `≤12%`, no empty result, finalization p95 `≤1.5 s`, RTF p95 `≤0.50`, cold load `≤30 s`. |
| TTS | Blind 12-item set: intelligibility `≥3.25`, pronunciation/naturalness `≥3.0`, no critical failure; first audio p95 `≤500 ms`, RTF p95 `≤0.50`, cold load `≤10 s`. |
| Required overlap | Four LLM requests remain active during TTS startup; component regression `≤20%`. During affected LLM/TTS cancellation, new resident-STT admission `≤250 ms`, old output stops `≤300 ms`, unaffected peers regress `≤15%`, zero stale output, and complete resource gates remain active. |
| Repeat | All `1/2/4` levels, context pressure, quality, sustained, overlap, and cancellation repeat: latency/throughput regression `≤15%`; VRAM peak `+≤256 MiB`; RAM peak `+≤512 MiB`; quality decline `≤0.25`; every gate still passes. |

Selection was gate-first. LFM failed and `selection/selection.v1.json` records no provider. Any future cloud result must retain that failure, use exactly one explicit LiteLLM alias, and prohibit defaults/fallback.

## Fixed public/generated material

- `fixtures/stt-russian-ruls.v1.json`: 24 preregistered public Russian LibriSpeech rows; audio stays in the authorized cache.
- `fixtures/llm-russian.v1.json`: 14 freshly authored CC0 Russian quality prompts.
- `fixtures/llm-parallel-russian.v1.json`: 12 freshly authored CC0 marked concurrency requests plus deterministic context-pressure construction.
- `fixtures/tts-russian.v1.json`: 12 freshly authored CC0 listening utterances.

Candidate text/audio output and randomized blind mappings stay under `/home/priney/.cache/voice-agent-v2/slice-2/`. Git stores only public fixture text, numeric aggregates, hashes, identities, limits, gate outcomes, and safe host fields. Result schemas reject content-bearing, environment, user, hostname, and UUID fields.

## Candidate and runtime screen

`config/candidates.v1.json` pins every source, revision, primary hash, format/quantization, license, runtime, context, concurrency, size, and compatibility rationale. It contains:

- two local STT candidates;
- approved official native LFM BF16 on vLLM as the only LLM;
- two local Piper TTS candidates.

`config/runtime-artifacts.v1.json` records current runtime resolution sizes and hashes. The live redacted host capture found RTX 4070 `12,282 MiB`, driver `610.57.04`, 6 cores / 12 threads, 31.2 GiB RAM, Vulkan 1.4.357, and no system CUDA compiler/CMake. All benchmark runtimes therefore remain user-space/cache-local; no Arch package or service mutation is proposed.

## Staged download boundary

| Stage | Incremental bytes | Cumulative bytes |
| --- | ---: | ---: |
| Cache-local vLLM plus STT/TTS runtimes | 5,482,352,882 | 5,482,352,882 |
| Approved LFM BF16 repository | 5,412,390,391 | 10,894,743,273 |
| Two STT plus two TTS repositories | 4,838,913,920 | 15,733,657,193 |
| Fixed STT audio upper bound | 20,000,000 | **15,753,657,193** |

Required maximum: **15,753,657,193 bytes (14.67 GiB)**. No alternate-LLM or GGUF allowance remains.

Planning is read-only and safe before approval:

```sh
./benchmark-slice2 validate
./benchmark-slice2 plan-download \
  llm-preferred-lfm25-26b-vllm-bf16 \
  stt-whisper-large-v3-turbo stt-whisper-large-v3 \
  tts-piper-denis-medium tts-piper-dmitri-medium
```

The local v1 acquisition/measurement sequence stopped at its fail-closed component gate, as preserved in [`selection/README.md`](selection/README.md). Later public-synthetic cloud, Qwen, repeat, and fixed-stack overlap measurements are owned by `results/fixed-stack-delivery.v1.json`; the cumulative delivery exception is owned by [ADR-0005](../docs/adr/0005-operator-fixed-slices-2-5-model-stack.md). Root `./verify` continues to own Slice 1 verification rather than the Slices 2–5 acceptance state.

## E2.1 operation-proposal measurement

E2.1 is independently owned by:

- `fixtures/tool-proposals-russian.v1.json`: 240 CC0 public-synthetic Russian fixtures in exact 80 positive / 80 ordinary / 40 ambiguous / 40 injection classes;
- `config/tool-proposals-orders.v1.json`: five materialized fixed permutations;
- `config/tool-proposals-preregistration.v1.json`: exact candidate formats, ADR-0008 identity, hashes, thresholds, two-slot/600-second bounds, result contract, and no-tuning selection rule;
- `tool_proposals/`: benchmark-only parsers, scoring, pure test handlers, ancestry/hash/privacy validation, and no production import path;
- `evidence/tool-proposals-lfm2.5-q4.v1.json`: counts-only machine outcome.

The preregistration is commit-pinned at `49af77a7c1b5c8b7ca839348f1a99dc207e39fa4`. Its one authorized `./verify-local-lfm --tool-proposals-only` invocation failed before artifact/runtime or fixture admission because the nested CLI received and rejected the outer selector. Consequently every mechanism records `attempted=0`, `not_attempted=1200`, `passed=false`; exact identity was not verified, the matrix is incomplete, and the decision is `model_operation_proposals_unavailable`. No completion or private content was retained, no retry/model/provider substitution is permitted, and zero is not presented as a measured safety/accuracy score.

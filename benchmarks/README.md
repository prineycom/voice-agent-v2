# Slice 2 measured host/model budget

This directory is the evidence-and-selection harness for [Issue #3](https://github.com/prineycom/voice-agent-v2/issues/3). It does **not** implement production inference. Slice 1 contracts and required `./verify` remain intact.

## Gate state

`config/preregistration.v1.json` is approved for the official LFM BF16/vLLM primary as the only LLM and for staged LFM/STT/TTS acquisition. No runtime/model installation, large download, candidate inference, or candidate result occurred before approval. Acquisition commands require the approved proposal to be committed; later results must name that ancestor commit.

The runtime-priority decision preserves the LFM2.5-2.6B family and requires strong parallel behavior. The target is now the smallest provenance-safe artifact with verified native vLLM support, not the formerly mandatory GGUF quantization. Full compatibility evidence is in [`vllm-compatibility.md`](vllm-compatibility.md).

### Approved priority #1

- official `LiquidAI/LFM2.5-2.6B` native BF16 checkpoint;
- immutable revision `ab00687315bc1298e9d54e9c4b611dde9867ccc2`;
- two weight files, `5,394,427,456` bytes total, SHA-256 values recorded in `config/candidates.v1.json`;
- complete repository `5,412,390,391` bytes;
- official architecture `Lfm2ForCausalLM`, native in vLLM `0.26.0`;
- LFM Open License v1.0: notice obligations and a USD 10 million annual-revenue condition for commercial use.

GGUF/llama.cpp, community GPTQ, every alternate local LLM, and cloud are excluded. If LFM fails any preregistered gate, no LLM is selected and the dependent path stops for a new explicit user decision.

## Compact preregistration proposal

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

Selection is gate-first. LFM is selected only if it passes every gate. If it fails, select no LLM and block Slice 4. Exactly local provider mode, one identity, maximum four active requests, no alternate candidate, no cloud, and no fallback are recorded.

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

After the approved preregistration commit, the controlled sequence is runtime preparation, staged LFM acquisition and measurement, STT/TTS candidate acquisition and measurement, blind review, selected-stack worst-case overlap, and complete repeat. The root `./verify` remains the final required repository check.

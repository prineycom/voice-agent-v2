# Verified vLLM/LFM2.5 compatibility proposal

> Research cutoff: 2026-08-10. At the time of this compatibility screen no inference/download had occurred. The target was later measured and failed preregistered visible-output/isolation/pressure gates; its result is preserved and further LFM inference is stopped.

## Finding

**vLLM supports the LFM2.5-2.6B model family through its native Hugging Face architecture, but the supplied single-file GGUF is not a verified or credible vLLM target.**

Evidence was checked at immutable/current identities rather than inferred from a generic throughput claim:

- vLLM `v0.26.0`, commit `568afb3a13806beb53bb2e6bd518269357b237c0`, lists `Lfm2ForCausalLM` in the supported-model table and has a native `vllm/model_executor/models/lfm2.py` implementation. The official LiquidAI vLLM guide says dense LFM2.5 architectures ship in vLLM `0.23.0` and later.
- Official `LiquidAI/LFM2.5-2.6B` revision `ab00687315bc1298e9d54e9c4b611dde9867ccc2` declares architecture `Lfm2ForCausalLM`, native BF16 safetensors, Russian, and the LFM Open License v1.0. Its model card explicitly assigns the native checkpoint to Transformers/vLLM/SGLang and GGUF to llama.cpp-compatible local tools.
- vLLM `0.26.0` can attempt single-file GGUF only through out-of-tree `vllm-gguf-plugin`. Core documentation calls the path **highly experimental and under-optimized**, warns of incompatibility with other features and unstable tokenizer conversion, and recommends an external base tokenizer/config.
- `vllm-gguf-plugin` `0.0.5`, commit `d358f564fc8f470cddd7c141a149b4ebafafa01f`, has a generic weight adapter but its tested-family table does not contain LFM2. The plugin explicitly says appearing in vLLM’s general supported-model list does not guarantee GGUF compatibility. No LFM2 plugin test, issue, or PR was found.
- The supplied official GGUF is well identified—revision `b421ad1d549afeda6a0fb2ad3a697cb5a7879adc`, Q4_K_M file SHA-256 `79fdf00351b46cf26f020aead28d01889886be87c55fa0eb907e6f9b00bfee14`, `1,674,454,848` bytes—but the evidence supports llama.cpp, not a strong vLLM parallel-serving claim.

Therefore the GGUF/vLLM pairing is recorded as **generic experimental possibility, exact pairing unverified**, not as “supported.” It is not the target runtime.

## Smallest credible vLLM artifact

The recommended target is the official native BF16 checkpoint:

| Field | Value |
| --- | --- |
| Repository | `LiquidAI/LFM2.5-2.6B` |
| Revision | `ab00687315bc1298e9d54e9c4b611dde9867ccc2` |
| Architecture | `Lfm2ForCausalLM` |
| Weight format | Two native BF16 safetensors; no weight quantization |
| Weight bytes | `5,394,427,456` (`5,144.53 MiB`) |
| Complete pinned repository bytes | `5,412,390,391` |
| Weight SHA-256 #1 | `05be188e6570a7642b4690545ab22ebf4d0a6068775dd776a452eebf738854cc` |
| Weight SHA-256 #2 | `532f803a9e6f127adc124d977503b438d8cc9c18f96e0862bf6835e38867bca8` |
| License | LFM Open License v1.0 |

No official LiquidAI AWQ, GPTQ, FP8, or CUDA-vLLM quantized LFM2.5-2.6B repository was found. Official alternatives are native, GGUF, ONNX, and MLX; the latter three are not native vLLM CUDA checkpoints.

A smaller community checkpoint, `pierjoe/LFM2.5-2.6B-W4A16-GPTQ` revision `530bf5c99b0d98a7a945f0fec35528f83b7f91f9`, contains a `2,328,191,336`-byte compressed-tensors weight file. It is **screened out**, despite vLLM bundling compressed-tensors, because it has no model card, no declared license, only two recorded downloads, excludes all LFM convolution projections from quantization, and has no authoritative LFM/vLLM compatibility result. Choosing it would invent provenance and compatibility.

LFM Open License v1.0 permits the benchmark and redistribution with notice obligations; commercial use is conditioned on the user/legal entity remaining below USD 10 million annual revenue. This record is not legal advice.

## Runtime proposal

- vLLM `0.26.0` / Apache-2.0 / commit `568afb3a13806beb53bb2e6bd518269357b237c0`.
- PyPI x86_64 wheel SHA-256 `adb1e4c9b46d0dfdb094121ae5aad670a42412dd813ed4e5db069ed6a15006de`; current resolution is 195 packages and `3,854,486,734` download bytes.
- PyTorch `2.11.0`, Transformers `5.15.0`, cache-local CUDA 13 wheels. vLLM supports Python `>=3.10,<3.15`, including the live Python 3.14.6. Driver `610.57.04` reports CUDA UMD 13.3.
- Server hard limits: `max_model_len=4096`, `max_num_seqs=4`, `gpu_memory_utilization=0.58`. No second provider/model is loaded as fallback.
- LFM sampling is fixed at temperature `0.1`, top-k `50`, repetition penalty `1.1`, fixed seed, and the runtime's reasoning parser; hidden thinking is timed but excluded from human-scored visible output.
- The explicit single-LLM exception permits no alternate local model and no cloud contact. Failure of LFM produces no selection rather than a runtime/model substitution.

## 12 GB analytical preflight—not a benchmark result

- Physical VRAM: `12,282 MiB`; current display/system capture: `519 MiB`.
- A `0.58` vLLM cap is `7,123.56 MiB`, leaving `5,158.44 MiB` outside vLLM.
- LFM BF16 weights are `5,144.53 MiB`. The model has eight GQA attention layers; four full 4096-token BF16 KV caches are approximately `256 MiB` total before allocator overhead. Hybrid convolution state is much smaller. The cap is therefore plausible for weights, four contexts, activations, and runtime overhead.
- After current display use and the preregistered `1,536 MiB` reserve, about `3,103 MiB` remains outside vLLM for resident STT, CPU TTS, and transients. This is tight enough that only measured full-stack overlap can pass it; artifact sizes are not substituted for peak runtime measurements.

This establishes **credible test feasibility**, not selection. Startup failure, runtime peak, four-request context pressure, or STT overlap can still reject BF16 LFM.

## Parallel-request gates

Every LLM candidate is measured at `1`, `2`, and `4` concurrent streaming requests, three complete rounds of 12 fixed public synthetic requests per level, plus four simultaneous 3584-input-token pressure requests. Hard gates include:

- raw TTFT p95: `≤1.5/2/3 s`; first visible content p95: `≤3/4/6 s`; completion p95: `≤10/12/16 s` for concurrency `1/2/4`;
- per-request decode p05: `≥15/10/8 tok/s` at `1/2/4`;
- aggregate throughput: concurrency 2 at least `1.5×` concurrency 1, concurrency 4 at least `2.25×` concurrency 1;
- Jain fairness `≥0.90`, slowest/median decode rate `≥0.75`, inter-token gap p95 `≤750 ms`, queue p95 `≤250 ms`, zero starvation;
- zero cross-request marker leakage, stale output, or correlation failure;
- cancellation at concurrency four stops the affected output within `300 ms`, admits a replacement within `250 ms`, and degrades unaffected peers by at most `15%` without server/provider switch;
- KV capacity for four 4096-token sequences, complete VRAM/RAM/CPU reserve, and no sustained growth beyond the preregistered bounds;
- worst-case four-request LLM decode overlaps TTS startup, then cancellation plus new resident-STT admission, while every original resource gate remains active.

## Staged download boundary

| Stage | Incremental bytes | Cumulative bytes |
| --- | ---: | ---: |
| Cache-local vLLM plus STT/TTS runtime resolutions | 5,482,352,882 | 5,482,352,882 |
| Approved official LFM BF16 repository | 5,412,390,391 | 10,894,743,273 |
| Two STT plus two TTS repositories | 4,838,913,920 | 15,733,657,193 |
| Fixed STT audio upper bound | 20,000,000 | **15,753,657,193** |

The required maximum is **15,753,657,193 bytes (14.67 GiB)**. It contains exactly one LLM and no GGUF/llama.cpp, alternate-model, or cloud allowance.

## Recommendation

Use the approved official native BF16 `LiquidAI/LFM2.5-2.6B@ab006873…` on vLLM `0.26.0` as the only LLM candidate. Select it only if it passes every quality, concurrency, cancellation, resource, overlap, and repeat gate. If it fails, record no LLM selection and stop the dependent path for a new explicit user decision.

Authoritative references:

- <https://docs.vllm.ai/en/v0.26.0/models/supported_models/>
- <https://docs.vllm.ai/en/v0.26.0/features/quantization/gguf/>
- <https://github.com/vllm-project/vllm/tree/568afb3a13806beb53bb2e6bd518269357b237c0>
- <https://github.com/vllm-project/vllm-gguf-plugin/tree/d358f564fc8f470cddd7c141a149b4ebafafa01f>
- <https://docs.liquid.ai/deployment/gpu-inference/vllm>
- <https://huggingface.co/LiquidAI/LFM2.5-2.6B/tree/ab00687315bc1298e9d54e9c4b611dde9867ccc2>
- <https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/tree/b421ad1d549afeda6a0fb2ad3a697cb5a7879adc>

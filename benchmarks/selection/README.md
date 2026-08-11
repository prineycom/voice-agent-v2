# Slice 2 local-v1 failed-selection rationale

This file owns the fail-closed outcome under local preregistration v1, commit `f11f6caa42301376b96063cea61a617b94ecaad1`; it is not the current cumulative-delivery selection. The later operator-fixed stack is owned by `../results/fixed-stack-delivery.v1.json` and [ADR-0005](../../docs/adr/0005-operator-fixed-slices-2-5-model-stack.md). All content-bearing raw evidence remains under `/home/priney/.cache/voice-agent-v2/slice-2/`; tracked results contain aggregates and hashes only.

## Local v1 outcome

**Under local preregistration v1, no LLM and no complete stack were selected, so Slice 4 was blocked.** The explicit single-LLM rule permitted only official `LiquidAI/LFM2.5-2.6B` native BF16 on vLLM. It failed hard component gates, so no alternate local model or cloud provider was contacted, acquired, run, or substituted under that plan. Dependent overlap and winning-configuration repeat were not run.

## Hard failures

### LLM

`LiquidAI/LFM2.5-2.6B@ab00687315bc1298e9d54e9c4b611dde9867ccc2`, vLLM `0.26.0`:

- five of 14 fixed quality requests produced no visible answer before the fixed 256-token completion cap, so critical failures were at least `5` versus allowed `0`;
- all 108 marked concurrency requests produced no visible marker-bearing answer, so isolation/correlation failures were `108` versus allowed `0`;
- all four 3584-input-token pressure requests completed without a visible answer, so pressure failures were `4` versus allowed `0`;
- concurrency-1 visible-content p95 was `3,236.54 ms` versus `3,000 ms` maximum.

The server did pass several independent gates: startup `22,445.47 ms`, VRAM peak `7,765 MiB` leaving `4,517 MiB`, aggregate throughput scaling above the concurrency-2/4 thresholds, low inter-token gaps, cancellation, replacement admission, and resource release. These passes cannot override any hard failure.

vLLM's FlashInfer sampling JIT could not compile with the resolved CUDA 13.0 headers, NVCC 13.3, and host GCC 16. The measured server therefore used vLLM's native top-k/top-p sampler via `VLLM_USE_FLASHINFER_SAMPLER=0`; sampling values and all preregistered limits remained unchanged. This runtime detail is disclosed in the result configuration.

### STT

- `stt-whisper-large-v3-turbo`: WER `11.4551%` passed, but host-wide CPU p95 `88.04%` exceeded the `83.33%` hard limit.
- `stt-whisper-large-v3`: CPU and latency passed, but WER `13.3127%` exceeded the `12%` hard limit.

Therefore no STT candidate is eligible under the unchanged gates.

### TTS

Both fixed voices completed automated timing/cancellation measurement:

- Denis: first-audio p95 `173.23 ms`, RTF p95 `0.0345`;
- Dmitri: first-audio p95 `197.35 ms`, RTF p95 `0.0437`.

Blind listening quality was not requested after upstream LLM and STT hard failures made a complete selection impossible. No TTS is selected.

## Acquisition boundary

Only the approved LFM, two STT candidates, two TTS candidates, public STT subset, and pinned cache-local runtimes were acquired. The approved maximum download boundary was `15,753,657,193` bytes (`14.67 GiB`). The acquisition manifest contains exactly those five candidate IDs and no alternate LLM.

## Required next decision

Any retry requires a new explicit user decision and revised preregistration before new model/provider contact, download, or inference. The existing failed result must not trigger automatic fallback.

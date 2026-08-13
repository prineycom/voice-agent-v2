# Accelerated VoxCPM2 optional TTS experiment

> **Status:** deployable experimental backend, **no-go for the current full resident stack**
>
> Qwen remains the default. This experiment makes no physical voice-quality or audibility claim.

This branch replaces only the selected TTS adapter when
`VOICE_AGENT_TTS_BACKEND=voxcpm2-fast`. LiveKit, the session controller, microphone/VAD,
interruption, local LFM, Whisper, ports, and security configuration are unchanged. Backend failure
is explicit and never falls back to Qwen or cloud.

## Why this runtime

The pinned official VoxCPM source revision
`ee8161e9e1b7b082cb5721a3a9980da4204401e6` recommends Nano-vLLM for accelerated production
serving and reports about 0.13 RTF on RTX 4090 versus about 0.30 for ordinary PyTorch. The selected
Nano-vLLM release is `2.0.4` / commit
`0cc522ca22971213f5fda5d4b2c4457f294aab85`; it uses FlashAttention, Triton, CUDA graphs, a
resident model, streaming chunks, and request cancellation. vLLM-Omni's current upstream recipe
names a 24 GB RTX 4090 as its minimum recommended GPU and allocates about 22 GiB, so it is not a
credible 12 GB path. The official VoxCPM documentation reports llama.cpp-omni Q8 at about 1.76 RTF
on Apple M4 Pro; it is not the fastest supported path for this host.

The previous legacy `voxcpm 2.0.3` streaming result is historical evidence only. V2 has no A2F,
and this branch remeasures the accelerated runtime directly through the V2 TTS contract.

## Reproducible setup and checks

All downloads, generated public samples, logs, and evidence stay under
`/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts/`. The model revision, every model
file hash, CPython archive, uv wheel, PyTorch/FlashAttention/Nano-vLLM artifacts, runtime lock, and
public reference metadata are pinned by [`config/voxcpm2-fast-tts-v1.json`](../../config/voxcpm2-fast-tts-v1.json)
and [`requirements-voxcpm2-fast.lock`](../../requirements-voxcpm2-fast.lock).

```sh
./setup-voxcpm2-fast
./verify-voxcpm2-fast
```

The real verifier holds the cross-experiment GPU lock for its whole process lifetime. It performs a
real load, discard-only warm-up, three public Russian utterances, cold/warm timing, resource
sampling, process-reuse checks, cooperative cancellation, stale-PCM rejection, and recovery. It
stores content-free JSON evidence and cache-local WAVs for a later human listening review; a pass
is not a quality claim.

The isolated candidate application entry point is:

```sh
./run-voxcpm2-fast-candidate
```

It verifies the prepared cache, holds
`/home/priney/.cache/voice-agent-v2/experiments/gpu-bakeoff.lock` for the complete app lifetime,
and selects only `voxcpm2-fast`. It otherwise executes the existing `./run-slice6` path and uses the
same ignored `.env.slice6` configuration and ports.

## Voice/reference truth

The accelerated runtime preserves **Ultimate Cloning**. This task deliberately does not access a
private reference. It pins Russian LibriSpeech test row 999, reader path prefix `4471`, revision
`a519c986bb3342cc8136d3d14e5ad8a4f1e1a2bd`, with its exact transcript and WAV hash. The dataset
card declares CC-BY-4.0 and upstream OpenSLR SLR96 states public domain in the USA; attribution is
recorded in the manifest.

The resulting public reference is only an evidence-backed test voice. It is not Pasha's voice, and
no cloning similarity or quality is claimed until Pasha listens. Substituting a private recording is
outside this branch and cannot happen through an environment variable or implicit file lookup.

## Measured result and gate

On the RTX 4070 12 GB host, Nano-vLLM was real-time after warm-up: three public cloned Russian
sentences measured 69–74 ms time-to-first PCM and 0.180–0.186 RTF. Cancellation measured 27 ms,
with zero post-cancel chunks, and the same resident adapter process served warm-up, turns,
cancellation, and recovery. Peak total VRAM was 8,406 MiB and peak VoxCPM2 process VRAM was
8,184 MiB; the isolated run retained 3,494 MiB minimum free VRAM.

The required full-stack reserve fails before Whisper is added: VoxCPM2 peak plus the current
approximately 2,900 MiB LFM process projects to 11,306 MiB used and only 976 MiB free, below the
non-negotiable 1,536 MiB reserve. Current Whisper evidence adds a further 2,388 MiB above its
measurement baseline. Starting the full overlap would knowingly violate the gate, so it was not
done. Runner readiness requires 9,728 MiB free before load and fails before GPU allocation when the
resident stack cannot preserve the reserve.

**Recommendation: no-go for Pasha's manual full-app voice test on the current stack.** The branch
is kept deployable and fail-closed for later re-evaluation if an upstream accelerated runtime
materially reduces residency; the latency result alone must not override the memory gate.

## Rollback

Qwen is unchanged and remains the default:

```sh
./run-slice6
```

Do not set `VOICE_AGENT_TTS_BACKEND`, or set it explicitly to `qwen`. Stopping the foreground
candidate command uses the existing `run-slice6` cleanup; it does not change service supervision,
firewall, Tailscale Serve, LiveKit credentials, or tracked/untracked product configuration.

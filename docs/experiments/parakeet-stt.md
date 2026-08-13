# Optional Parakeet STT experiment

> **Status:** deployable experiment, **no-go for replacing Whisper from automated evidence**
>
> **Physical Russian microphone recognition:** not tested and not claimed.

This branch adds `nvidia/parakeet-tdt-0.6b-v3` as an explicit local STT alternative while leaving Whisper as the default and rollback. It does not change LiveKit, microphone/VAD/reconnect, interruption publication, LFM, Qwen/`ryan`, Tailscale, firewall, or active ports.

## Runtime choice

Official evidence was rechecked on 2026-08-14 at the immutable revisions in [`config/parakeet-stt-v1.json`](../../config/parakeet-stt-v1.json):

- the model card declares Russian, 16 kHz mono input, punctuation/capitalization, 600M parameters, CC-BY-4.0, and official Russian FLEURS/CoVoST2 WER of 5.51%/3.00%;
- NVIDIA NeMo is the full Python/PyTorch path and is substantially heavier to install and operate;
- official NeMo-Speech.cpp supports this exact Q8 GGUF and offline final transcription, but explicitly rejects streaming because the model is not cache-aware trained.

NeMo-Speech.cpp's stable C ABI was selected. The one authorized Vulkan build retry stopped at a further cache-local SentencePiece build dependency, so the mandated CPU fallback was built and measured. It consumes no attributable VRAM and keeps the GPU reserve large, but its measured quality/latency do not justify replacing Whisper.

## Setup and isolated run

```sh
./setup-parakeet-stt
./verify-parakeet-stt
./verify-parakeet-overlap
./run-parakeet-candidate
```

`./run-parakeet-candidate` selects Parakeet only for that foreground run. The shared `./run-slice6` launch boundary acquires `/home/priney/.cache/voice-agent-v2/experiments/gpu-bakeoff.lock` for the complete product/GPU process lifetime whenever Parakeet is selected, including direct configuration. The candidate never changes the default deployment.

Direct configuration is closed:

```sh
VOICE_AGENT_STT_BACKEND=parakeet  # explicit experiment
VOICE_AGENT_STT_BACKEND=whisper   # explicit rollback
unset VOICE_AGENT_STT_BACKEND     # default rollback
```

Unknown values fail startup. There is no automatic/cloud/Whisper fallback behind a successful Parakeet turn.

## Automated result

The fixed 24-sample, 168.9-second Russian LibriSpeech set produced:

| Arm | WER | final p95 | RTF p95 |
| --- | ---: | ---: | ---: |
| Parakeet Q8 clean source | 12.384% | 1551 ms | 0.1217 |
| Parakeet through the exact current repository Silero endpoint | 17.647% | 1400 ms | 0.1216 |
| Existing Whisper baseline, clean source | **11.455%** | **294 ms** | **0.0510** |

The repository-path arm is the current `SileroSpeechEndpoint` over exact 16 kHz mono PCM. It intentionally makes no RTP/Opus or physical microphone claim. The delta demonstrates why model quality must not be conflated with endpoint/input loss.

Other measured facts:

- resident CPU runner peak: 865.5 MiB RSS, 0 MiB attributable VRAM;
- the same process served all successful clean/repository turns;
- exact and near digital silence produced no direct-runtime transcript;
- real cancellation stopped the process in 7.38 ms, removed temporary WAV input, and recovered with a new warmed process;
- isolated resident Parakeet + LFM + Qwen turn completed at 7799 MiB peak total VRAM, leaving 4483 MiB—well above the 1536 MiB reserve;
- diagnostics retain no audio, transcript, prompt, or response content.

The machine-readable evidence is intentionally outside Git under `/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt/results/`.

## Recommendation and rollback

**No-go for automatic promotion or claiming Parakeet is better than Whisper.** Keep this branch deployable only if Pasha deliberately wants a manual challenger despite the failed clean-corpus comparison. A manual test must be described only as a physical acceptance attempt, never as pre-established recognition success.

Stop the foreground candidate normally, then run `./run-slice6` without `VOICE_AGENT_STT_BACKEND`; Whisper is restored. No model deletion, service migration, port change, or shared-service restart is required.

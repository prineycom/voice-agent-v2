# Third-party and model notices

Voice Agent source code is licensed under MIT; see [`LICENSE`](LICENSE). That license does **not** relicense the separately identified components or model assets below. The accepted distribution scope is Pasha's private personal noncommercial use.

| Component | Exact authority | License / notice |
| --- | --- | --- |
| Silero TTS `v5_5_ru` / `kseniya` | `snakers4/silero-models@d9355348e2781dc8fa25a135d1602c530afae24c`, `v5_5_ru.pt`, 145,420,684 bytes, SHA-256 `50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437` | **CC BY-NC-SA 4.0**, Silero Models by Silero Team / snakers4. Private personal noncommercial use only. <https://github.com/snakers4/silero-models/blob/d9355348e2781dc8fa25a135d1602c530afae24c/LICENSE> |
| Faster Whisper | `SYSTRAN/faster-whisper@65882eee9f5cdbeeb2d877f1131d48cf241b327d` / 1.2.1 | MIT. <https://github.com/SYSTRAN/faster-whisper/blob/65882eee9f5cdbeeb2d877f1131d48cf241b327d/LICENSE> |
| Whisper conversion | `mobiuslabsgmbh/faster-whisper-large-v3-turbo@0e7a9a46fd2ec6300297f1665cd1b1f2c8b15c11` | MIT model-card declaration; exact files and hashes are in the signed asset receipt. <https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/tree/0e7a9a46fd2ec6300297f1665cd1b1f2c8b15c11> |
| Silero VAD v6 | blob SHA-256 `4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2` from Faster Whisper 1.2.1 / Silero VAD `806dcba3f0b5d95282d0889a074954a2f8c6397b` | MIT. <https://github.com/snakers4/silero-vad/blob/806dcba3f0b5d95282d0889a074954a2f8c6397b/LICENSE> |
| LFM model | `LiquidAI/LFM2.5-2.6B-GGUF@b421ad1d549afeda6a0fb2ad3a697cb5a7879adc`, `Q4_K_M`, SHA-256 `79fdf00351b46cf26f020aead28d01889886be87c55fa0eb907e6f9b00bfee14` | LFM Open License v1.0; retain the revision-pinned license/model card. <https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/tree/b421ad1d549afeda6a0fb2ad3a697cb5a7879adc> |
| llama.cpp | commit `689e227db485c6b33d061555e74034c93a867649` (`b10357`) | MIT. <https://github.com/ggml-org/llama.cpp/blob/689e227db485c6b33d061555e74034c93a867649/LICENSE> |
| LiveKit server | 1.13.5 / commit `3b9f118327b257301083a7c4aa46076c8012918a` | Apache-2.0. <https://github.com/livekit/livekit/blob/3b9f118327b257301083a7c4aa46076c8012918a/LICENSE> |
| CPython | 3.12.13, pinned python-build-standalone input | PSF-2.0 and bundled notices from the exact archive. <https://docs.python.org/3.12/license.html> |
| Node.js | 26.7.0, build/SEA input only; not in the application runtime | MIT and Node bundled third-party notices. <https://github.com/nodejs/node/blob/v26.7.0/LICENSE> |
| CMake | 4.1.1, exact build-only archive in `release/inputs/runtime-sources.v1.json`; not in the application runtime | BSD-3-Clause and bundled notices. <https://github.com/Kitware/CMake/blob/v4.1.1/Copyright.txt> |
| patchelf | 0.18.0, exact build-only archive in `release/inputs/runtime-sources.v1.json`; not in the application runtime | GPL-3.0-or-later. <https://github.com/NixOS/patchelf/blob/0.18.0/COPYING> |
| PyAV / bundled FFmpeg | `av==18.0.0`, exact `cp311-abi3-manylinux_2_28_x86_64` wheel SHA-256 `ae56b40b6f8b067a8ad2dac664fbfbabac7f7a55b9a7bb031eb99289252bc017` | PyAV BSD-3-Clause; preserve the exact wheel's FFmpeg/codecs license inventory and source offer/notices. Do not infer a different FFmpeg configuration from package metadata. |
| CUDA 12.9 / cuBLAS / cuDNN | exact components in the runtime receipt; assembler builder `nvidia/cuda:12.9.1-devel-rockylinux8` is pinned by manifest digest and is not shipped | NVIDIA CUDA EULA and redistributable manifests; host provides only kernel/driver and `libcuda.so.1`. <https://docs.nvidia.com/cuda/eula/index.html> |
| PyTorch CPU | `torch==2.8.0+cpu`, exact CPython 3.12 manylinux wheel | BSD-3-Clause plus the wheel's bundled notices. |
| AgentEnvironment | OCI index `sha256:8a5a972b25f7c203c71b8e28af17f756d9daf39bc74ebbc5d86eaf6c9f3da421`; source is this repository's `agent-environment/` tree | Separately digest-pinned installation-owned OCI image. Its exact generated OCI SBOM/license inventory is a publication receipt and the image is never copied into the application artifact. |

The release candidate includes an SPDX 2.3 document and exact source/license receipt hashes. Notices must accompany the exact bytes; missing or changed identity is a hard refusal. This inventory does not grant commercial rights for Kseniya or override any upstream terms.

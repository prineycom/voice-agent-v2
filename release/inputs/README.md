# Production runtime inputs

This directory is the closed input authority for Pasha's private personal noncommercial Linux `x86_64` release.

- `runtime-sources.v1.json` pins relocatable CPython 3.12.13, build-only Node 26.7.0, LiveKit 1.13.5 and llama.cpp `689e227…`. Node builds the web/SEA launcher and must not appear below the application `runtime/` tree.
- `python-wheelhouse.v1.json` records the exact filename, immutable URL, size, SHA-256 and license metadata for every wheel admitted by `requirements-release/{gateway,stt,tts}.lock`. Acquire into an empty wheelhouse, verify every row, then install with `pip --require-hashes --no-index --find-links` under the pinned CPython only.
- `asset-license-receipts.v1.json` owns exact model/AgentEnvironment provenance and license separation. Model bytes remain separately signed content-addressed assets and are not copied into the application archive.

The normalized runtime tree contains one relocatable CPython/package closure, static LiveKit, minimal rebuilt llama.cpp `llama-server`/GGML libraries against CUDA 12.9, and only their recursively reached native dependencies. Every file is regular, link-free and mode `0444` or `0555`; RUNPATH is empty or `$ORIGIN`-relative. The host may provide only Linux, glibc `>=2.28`, NVIDIA driver `>=575.51.03`, and `libcuda.so.1`. Do not copy a live venv, use host Python/Node, resolve from ambient caches, ship CUDA build tools, or include a Node runtime.

After two isolated builds compare byte-for-byte, generate the exact complete receipt:

```sh
./release-candidate runtime-receipt \
  --runtime-root <normalized-runtime> \
  --source-commit "$(git rev-parse HEAD)" \
  --output <runtime-receipt.json>
```

The command walks every byte, verifies Linux x86_64 ELF identity, records `DT_NEEDED`/SONAME/RUNPATH, binds every committed wheel/source input, rejects Node/links/special files/modes, and emits the aggregate digest consumed by `candidate`. Candidate assembly additionally requires the production faster-whisper runner, exact licenses/notices, actual SPDX 2.3 and host-path absence.

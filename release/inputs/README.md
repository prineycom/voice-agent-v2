# Production runtime inputs

This directory is the closed input authority for Pasha's private personal noncommercial Linux `x86_64` release.

- `runtime-sources.v1.json` pins the CUDA-12.9.1 Rocky-8 builder manifest, relocatable CPython 3.12.13, build-only Node 26.7.0, CMake, patchelf, LiveKit 1.13.5 and llama.cpp `689e227…`. Node builds web/SEA inputs and must not appear below the application `runtime/` tree.
- `python-wheelhouse.v1.json` records the exact filename, immutable URL, size, SHA-256 and license metadata for every wheel admitted by `requirements-release/{gateway,stt,tts}.lock`. Acquire into an empty wheelhouse, verify every row, then install with `pip --require-hashes --no-index --find-links` under the pinned CPython only.
- `model-assets.v1.json` owns the exact four model sets: five named STT files plus LFM, Kseniya and VAD members, with individual and aggregate identities.
- `asset-license-receipts.v1.json` owns exact model/AgentEnvironment provenance and license separation. Model bytes remain separately signed content-addressed assets and are not copied into the application archive.

The normalized runtime tree contains one relocatable CPython/package closure, static LiveKit, minimal rebuilt llama.cpp `llama-server`/GGML libraries against CUDA 12.9, and only their recursively reached native dependencies. Every file is regular, link-free and mode `0444` or `0555`; RUNPATH is empty or `$ORIGIN`-relative. The host may provide only Linux, glibc `>=2.28`, NVIDIA driver `>=575.51.03`, and `libcuda.so.1`. Do not copy a live venv, use host Python/Node, resolve from ambient caches, ship CUDA build tools, or include a Node runtime.

Run the real build only as an explicitly authorized maintainer action, outside canonical `./verify` and outside the checkout output path:

```sh
./release-candidate assemble-runtime \
  --cache <isolated-reconstructible-cache> \
  --output <new-output-directory> \
  --fetch
./release-candidate assemble-runtime \
  --cache <same-cache> \
  --output <second-new-output-directory> \
  --compare-with <new-output-directory>
```

`--fetch` may acquire only the accepted immutable inputs and exact npm lock into that cache. The real assembly runs in the pinned OCI with network disabled and emits `runtime/`, `web/`, `runtime-receipt.json` and `assembly-receipt.json`. The second invocation proves receipt equality; retain physical byte comparison, size/file-count and recursive ELF review as separate evidence. For an independently normalized runtime, `runtime-receipt` remains available:

```sh
./release-candidate runtime-receipt --runtime-root <normalized-runtime> --source-commit "$(git rev-parse HEAD)" --output <runtime-receipt.json>
```

Receipt generation walks every byte, verifies Linux x86_64 ELF identity and the GLIBC-2.28 ceiling, records `DT_NEEDED`/SONAME/RUNPATH/`libcuda`, binds every committed wheel/source/builder input, rejects Node/links/special files/modes, and emits the aggregate digest consumed by `candidate`. Candidate assembly additionally requires the production faster-whisper runner, closed model sets, exact licenses/notices, actual SPDX 2.3 and host-path absence.

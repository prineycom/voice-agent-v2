# Production runtime inputs

This directory is the closed input authority for Pasha's private personal noncommercial Linux `x86_64` release.

- `runtime-sources.v1.json` pins the CUDA-12.9.1 Rocky-8 builder manifest, relocatable CPython 3.12.13, build-only Node 26.7.0, its matching build-only Rocky `libatomic` 8.5.0-26 RPM, CMake, patchelf, LiveKit 1.13.5 and llama.cpp `689e227…`. Node uses the official exact Linux x64 `.tar.gz`; its accepted size/SHA-256, signed-checksum identities, signer and MIT receipt are closed. The unchanged Node ELF requires `libatomic.so.1`, which the pinned image lacks. The exact matching Rocky RPM is bound by URL/size/SHA-256/signing-key/source-RPM/license receipt, extracted only under `--network=none`, and supplies one hash-checked regular build-tool library. The llama.cpp row uses only the direct canonical `codeload.github.com/ggml-org/llama.cpp/tar.gz/<full-commit>` locator. Node and `libatomic` build web/SEA inputs only and must not appear below the application `runtime/` tree.
- `builder-tools.v1.json` carries the closed `voice-agent.builder-tools.v3` invoked-tool authority. Normal CLIs declare exact argv, exit status, bounded output prefix/version and output-receipt hashing. Non-public compiler children, RPM extraction and the glibc loader have exact parent-bound custody. The Node compatibility receipt additionally binds ELF64/x86_64/interpreter, `DT_NEEDED`, complete GLIBC/GLIBCXX/CXXABI sets, exact loader/library bytes, the bounded raw exit-127 missing-library stderr hash, corrected output and denial of `LD_LIBRARY_PATH`/`LD_PRELOAD`. Preflight creates an otherwise empty `/build/tool-bin`, rejects all differing facts together before npm acquisition/output, and prevents ambient PATH fallback.
- `python-wheelhouse.v1.json` records the exact filename, immutable URL, size, SHA-256 and license metadata for every wheel admitted by `requirements-release/{gateway,stt,tts}.lock`. Acquire into an empty wheelhouse, verify every row, then install with `pip --require-hashes --no-index --find-links` under the pinned CPython only.
- `model-assets.v1.json` owns the exact four model sets: five named STT files plus LFM, Kseniya and VAD members, with individual and aggregate identities.
- `asset-license-receipts.v1.json` owns exact model/AgentEnvironment provenance and license separation. Model bytes remain separately signed content-addressed assets and are not copied into the application archive.

The normalized runtime tree contains one relocatable CPython/package closure, static LiveKit, minimal rebuilt llama.cpp `llama-server`/GGML libraries against CUDA 12.9, and only their recursively reached native dependencies. Every file is regular, link-free and mode `0444` or `0555`; RUNPATH is empty or `$ORIGIN`-relative. The host may provide only Linux, glibc `>=2.28`, NVIDIA driver `>=575.51.03`, and `libcuda.so.1`. Do not copy a live venv, use host Python/Node, resolve from ambient caches, ship CUDA build tools, or include a Node runtime.

Run the real build only as an explicitly authorized maintainer action, outside canonical `./verify` and outside the checkout output path:

```sh
./release-candidate preflight-runtime \
  --cache <isolated-reconstructible-cache> \
  --fetch
./release-candidate assemble-runtime \
  --cache <isolated-reconstructible-cache> \
  --output <new-output-directory> \
  --fetch
./release-candidate assemble-runtime \
  --cache <same-cache> \
  --output <second-new-output-directory> \
  --compare-with <new-output-directory>
```

After that exact cache, including npm cache bytes, has completed once, deployment may repeat the full no-output proof without any acquisition:

```sh
./release-candidate preflight-runtime --cache <same-complete-cache>
```

This is not a builder-only selector: every builder/content/npm/config row still must pass, every admitted tarball must resolve from the read-only cache, no acquisition container runs, and the cache tree must remain byte-identical.

`--fetch` first requires local rootless Podman 6 with netavark and an installed working `pasta`; there is no network selector, slirp or host-network fallback. The builder digest and per-tool evidence closure are checked before input acquisition. Only the separately content-addressed tool inputs are then cached and extracted under `--network=none`; Node is executed through the exact builder loader with `--inhibit-cache` and a fixed private library path; the complete reachable lock-v3 package inventory and every SHA-512 integrity are validated before exact npm tarballs can be acquired. Distinct empty owner-only single-link npm user/global configs and separate owner-only home/cache/prefix/temp directories are identity-checked with npm 11 after ambient package-manager/Node/registry/auth/token/certificate/proxy removal. A complete cache is checked read-only and skips networking; only missing admitted bytes use `--network=pasta`, while offline install uses the same cache read-only and proves it byte-identical. Only after the complete closure succeeds may remaining immutable runtime inputs be fetched or output be created. Every container has empty pull auth and no ambient credential/socket authority. Compilation, offline wheel installation, llama/CUDA build, normalization and candidate bytes remain `--network=none`. The no-output `preflight-runtime` returns content-free probe/hash/provenance receipts; it accepts no probe override. Assembly emits `runtime/`, `web/`, `runtime-receipt.json` and an `assembly-receipt.json` binding the same tool authority and observations. The second invocation proves receipt equality; retain physical byte comparison, size/file-count and recursive ELF review as separate evidence. For an independently normalized runtime, `runtime-receipt` remains available:

```sh
./release-candidate runtime-receipt --runtime-root <normalized-runtime> --source-commit "$(git rev-parse HEAD)" --output <runtime-receipt.json>
```

Receipt generation walks every byte, verifies Linux x86_64 ELF identity and the GLIBC-2.28 ceiling, records `DT_NEEDED`/SONAME/RUNPATH/`libcuda`, binds every committed wheel/source/builder input, rejects Node/links/special files/modes, and emits the aggregate digest consumed by `candidate`. Candidate assembly additionally requires the production faster-whisper runner, closed model sets, exact licenses/notices, actual SPDX 2.3 and host-path absence.

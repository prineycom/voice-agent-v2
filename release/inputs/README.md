# Production runtime inputs

This directory is the closed input authority for Pasha's private personal noncommercial Linux `x86_64` release.

- `runtime-sources.v1.json` pins the CUDA-12.9.1 Rocky-8 builder manifest, relocatable CPython 3.12.13, build-only Node 26.7.0, CMake, patchelf, LiveKit 1.13.5 and llama.cpp `689e227…`. Node uses the official exact Linux x64 `.tar.gz` published alongside the former `.tar.xz`; its accepted 62,014,253-byte size/SHA-256, signed `SHASUMS256.txt`/detached-signature identities, Node release signer fingerprint and MIT receipt are closed, and extraction therefore needs only the pinned builder's gzip/tar rather than an undeclared `xz`. The llama.cpp row uses only the direct canonical `codeload.github.com/ggml-org/llama.cpp/tar.gz/<full-commit>` locator: owner, repository, path and full receipt commit are exact, query/userinfo/fragment variants fail, and codeload is never admitted through a redirect. Its accepted size/SHA-256, archive filename, source commit and MIT receipt remain unchanged. Node builds web/SEA inputs and must not appear below the application `runtime/` tree.
- `builder-tools.v1.json` carries the closed `voice-agent.builder-tools.v2` invoked-tool authority. Normal CLIs declare exact argv, exit status, bounded output prefix/version and output-receipt hashing. GCC/CUDA children without a public version interface declare canonical no-link root-owned executable custody, exact mode/SHA-256 and an exact successfully probed parent compiler/toolkit version. `false` and other unused image inventory are excluded. Preflight creates an otherwise empty `/build/tool-bin`, validates every declared path/evidence/provenance, rejects missing/tampered/linked/wrong-parent/extra rows in one complete content-free report, and prevents ambient PATH fallback.
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

This is not a builder-only selector: every builder/content/npm row still must pass, and a missing cache byte fails closed.

`--fetch` first requires local rootless Podman 6 with netavark and an installed working `pasta`; there is no network selector, slirp or host-network fallback. The builder digest and per-tool evidence closure are checked before input acquisition. Only the separately content-addressed tool inputs are then cached and extracted under `--network=none`; exact npm tarballs are acquired as integrity-bound cache bytes by the web acquisition container under `--network=pasta`, then installed and preflighted under `--network=none`. Only after the complete closure succeeds may remaining immutable runtime inputs be fetched or output be created. Every container has an empty private home/config, empty registry auth and no ambient proxy/credential/socket authority. Compilation, offline wheel installation, llama/CUDA build, normalization and candidate bytes remain `--network=none`. The no-output `preflight-runtime` returns content-free probe/hash/provenance receipts; it accepts no probe override. Assembly emits `runtime/`, `web/`, `runtime-receipt.json` and an `assembly-receipt.json` binding the same tool authority and observations. The second invocation proves receipt equality; retain physical byte comparison, size/file-count and recursive ELF review as separate evidence. For an independently normalized runtime, `runtime-receipt` remains available:

```sh
./release-candidate runtime-receipt --runtime-root <normalized-runtime> --source-commit "$(git rev-parse HEAD)" --output <runtime-receipt.json>
```

Receipt generation walks every byte, verifies Linux x86_64 ELF identity and the GLIBC-2.28 ceiling, records `DT_NEEDED`/SONAME/RUNPATH/`libcuda`, binds every committed wheel/source/builder input, rejects Node/links/special files/modes, and emits the aggregate digest consumed by `candidate`. Candidate assembly additionally requires the production faster-whisper runner, closed model sets, exact licenses/notices, actual SPDX 2.3 and host-path absence.

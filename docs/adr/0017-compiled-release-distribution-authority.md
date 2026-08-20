# ADR-0017: Compile release authority into the launcher and keep production signing offline

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Voice Agent v2 project architecture

## Context

ADR-0014 defines signed launcher-owned installation, but the merged I1–I7 executable deliberately used an unprovisioned production source and deterministic in-memory artifacts. A distributable launcher must not obtain its trust root from the channel, an installed cache, environment, CLI, or another mutable authority. A release publisher also must not turn the existing host-oriented Python caches and license-limited model evaluation into an apparently self-contained production artifact.

The initial bridge correctly refused the host-oriented runtime. Pasha subsequently fixed the distribution scope to a private personal noncommercial release, selected MIT for Voice Agent source, and retained exact Silero `v5_5_ru` / `kseniya` separately under CC BY-NC-SA 4.0. Production inputs must therefore close the host-neutral runtime and notices for that scope without inventing public/commercial authority.

## Decision

A production SEA launcher is built only from explicit version/protocol/source commit/reproducible timestamp, one canonical Ed25519 SPKI public key, and fixed private GitHub owner/repository/ref/channel path. The GitHub API origin and closed asset-store origins are compiled constants. The executable admits no runtime override for them. A build lacking any exact input remains inert for install/update network authority.

`HttpsReleaseSource` is the production implementation of the same source interface used by lifecycle fixtures. It reads one fixed XDG private token file only after owner/mode-`0600`/regular-file custody checks. The token is absent from release identity, CLI, environment, URL, binary, logs, status, journal and support output. Exact GitHub content API responses bind repository/ref/path. Artifact URLs bind numeric private release asset IDs. Exactly one API asset redirect may reach a compiled GitHub asset-store host, with Authorization stripped; all other redirect/query/host ambiguity fails. TLS, byte/time/range/validator and content-free error rules remain strict. Channel signatures are checked only with the compiled key.

The Linux artifact is canonical zstd-compressed ustar. Admission checks exact compressed size/SHA-256 and manifest SHA-256, bounded expansion/count/path/file sizes, canonical checksum/numeric/ownership metadata, complete end markers, and a single closed manifest. PAX/GNU extensions, sparse/device/FIFO/socket types, undeclared metadata/output, duplicates, unsafe paths, special bits, and unsafe links fail before stage extraction. Launcher-owned extraction and final inventory/fsync/read-only promotion remain the ADR-0014 lifecycle boundary.

`./release-candidate` is the only publisher command surface:

- `assemble-runtime` requires a clean exact commit and consumes only `release/inputs`: a digest-pinned CUDA-12.9.1 Rocky-8 `linux/amd64` builder, exact relocatable CPython, wheel/source/tool inputs and the exact Node/npm lock. Before any fetch it requires local rootless Podman 6, the netavark backend and a working `pasta` helper; this fixed rootless `pasta` mode is used only by the receipt-bound web acquisition container and cannot be selected or overridden by the caller. Builder-image and immutable-input acquisition retain exact digest/URL/size/license authority. Containers receive a private empty home/config, no inherited proxy/credential environment or socket mount, and the builder pull receives an explicit empty auth file. The actual compilation, offline wheel installation, llama/CUDA build, normalization and output assembly are `--network=none`, read-only, and have no host Python, Node, venv, CUDA overlay or ambient mount. The pinned llama.cpp archive is fetched directly from the one canonical codeload owner/repository/tar.gz/full-commit path bound to its receipt; codeload accepts no query, userinfo, fragment, alternate path or redirect-derived authority and receives no credential. This does not broaden the existing closed unauthenticated input redirect policy or the launcher's separate private release-source boundary. The assembler emits a link-free mode/timestamp-normalized runtime, static web tree, complete runtime receipt and assembly receipt. Static ELF is not rewritten; dynamic ELF receives only relative RUNPATH, recursively reached CUDA/llama providers are de-duplicated into one closure, and the host boundary remains kernel/glibc loader family/`libcuda.so.1` only;
- `candidate` requires the assembler output, exact npm integrity, the three CPython-3.12 hash locks and closed wheelhouse receipt, and a Linux x86_64 glibc-2.28/CUDA-12.9 runtime receipt with CPython, LiveKit, rebuilt minimal llama.cpp and complete file/ELF/license inventories. Node 26.7.0 is an exact web/SEA build input only and is absent from the application runtime. The production faster-whisper runner is mandatory. Output includes actual SPDX 2.3 and an unsigned channel template;
- `verify` reindexes and hashes the complete candidate without network and validates SPDX identity/relationships;
- `publish-assets` is the confirmed first phase: it uploads collision-free immutable version assets through `gh-axi`, obtains exact numeric release/asset IDs, verifies sizes/hashes, and emits a canonical receipt;
- `finalize-channel` accepts only that receipt, replaces every program/launcher/dependency locator with the exact private GitHub API asset URL, and writes canonical stable bytes. Changed/missing/colliding IDs refuse;
- `sign-channel` rejects an unfinalized candidate, reads only an explicit current-user mode-`0400`/`0600` Ed25519 private-key file, and emits only a detached signature plus content-free receipt;
- final `publish` checks the already-staged immutable release and exact `version:sequence`, then atomically CAS-advances only `stable.json`/signature on `release-channel` and verifies both readbacks.

Production keys are never generated, stored, copied, printed, accepted from workflow input, or available to pull requests. Key rotation is a separately downloaded bridge-launcher release authorized by the old trust root. A channel or cache can never silently replace the compiled root.

The accepted input authority is `requirements-release/`, `release/inputs/`, top-level `LICENSE`/`THIRD_PARTY_NOTICES.md`, and the assembler-generated runtime receipt supplied to `candidate`. Four closed model sets bind STT (the exact five relative files), LFM, Kseniya and VAD individual hashes/sizes plus an aggregate identity. The launcher copies only verified single-link content-addressed members through no-follow file descriptors into atomic owner-only aggregate views. It generates the sole absolute runtime descriptor from the immutable release and XDG view/state/temp/log/agent-data roots; production code discovers neither a home directory nor an upstream cache. Kseniya remains a separately signed content-addressed CC BY-NC-SA asset with explicit noncommercial notice; MIT applies only to project source. No model weight is downloaded or republished by repository verification.

## Consequences

- HTTPS compromise, network/cache/environment key injection, redirects, response smuggling ambiguity, and permissive archive tools do not grant execution authority.
- Repeated launcher/candidate assembly with identical accepted inputs can be compared byte-for-byte, with canonical receipts identifying the exact source and trust-root fingerprint.
- Signing stays offline and publication cannot be triggered by a pull request or untrusted workflow parameter.
- A production candidate is buildable through the repository-owned opt-in assembler only when all accepted immutable inputs and the pinned builder are available. Canonical verification exercises its orchestration and refusal contracts with bounded fixtures, never runs the real OCI/network build, and makes no physical-runtime claim.
- Initial acquisition is download → out-of-band digest/release-page verification → owner-only install; `curl | bash` is not supported.

## Alternatives rejected

- **Network- or environment-supplied public key:** mutable data cannot define its own verifier.
- **Public raw GitHub or a floating branch:** unavailable for the private repository and not exact artifact authority. Only the fixed content API identity and closed stripped-auth asset redirect are admitted.
- **Host Python/Node or customer dependency resolution:** violates offline runtime closure and reproducibility.
- **Test stubs labelled as a production runtime:** deterministic format evidence is not application readiness.
- **Online CI production signing:** pull-request workflows do not receive production release authority.

# Compiled distribution bridge evidence

**Evidence date:** 2026-08-22  
**Platform under deterministic test:** Linux `x86_64`, Node.js 26 SEA; injected HTTPS/service/filesystem facts only  
**Authority:** ADR-0017 and `docs/architecture.md` §6.7

## Deterministic evidence

The canonical `./verify` launcher phase owns:

- one explicit compile-time Ed25519 public key and stable HTTPS URL, inert missing/invalid authority, and pinned-over-network/cache/environment/CLI key selection;
- TLS/DNS/URL/port/path, redirect, timeout, transfer/content length, validator/range and byte-bound refusals with content-free errors;
- exact zstd artifact size/SHA and canonical ustar checksum/ownership/type/path/count/size/ratio/end-marker admission, including traversal, duplicate, PAX/device and undeclared output refusal;
- repeated byte-identical SEA builds, canonical SHA-256/provenance receipts and checkout/home/token absence from the executable;
- reproducible synthetic application archive/manifest/SBOM/provenance/unsigned-channel bytes through the same indexer consumed by install/update fixture transactions;
- dirty/floating/unhashed/undeclared/wrong-platform/license/host-path refusal;
- owner-only offline Ed25519 channel signing without key/path output;
- publication confirmation, tag/sequence collision and SHA-256 readback mismatch refusal;
- a read-only PR workflow using only the committed public test key and no signing/publication authority.

The complete canonical output is stored in the branch artifact named by the implementation report. No production key, external URL, GitHub release, model weight, user systemd unit, Docker object, XDG installation or live stand participates.

## Private personal release closure

The prior production-input blocker is resolved for the accepted private personal noncommercial scope:

1. `requirements-release/{gateway,stt,tts}.lock` and `release/inputs/python-wheelhouse.v1.json` identify exact CPython-3.12 Linux wheels, including PyAV/FFmpeg, CUDA-12.9 and Torch CPU inputs.
2. `release/inputs/runtime-sources.v1.json` fixes relocatable CPython 3.12.13, build-only Node 26.7.0, LiveKit 1.13.5 and llama.cpp commit `689e227…`; a candidate runtime receipt must enumerate every byte and ELF edge. Host authority is only kernel/glibc/NVIDIA driver/`libcuda.so.1`.
3. Production code derives paths from immutable release and XDG roots. The faster-whisper runner is included and Node cannot enter the application runtime.
4. `LICENSE`, `THIRD_PARTY_NOTICES.md` and exact asset receipts separate MIT project source from CC BY-NC-SA Kseniya and other upstream terms. Kseniya is explicit private-personal-noncommercial only.
5. The launcher binds private GitHub API identity and a mode-`0600` token file outside release identity. One closed asset redirect strips Authorization. Two-phase publication obtains immutable numeric IDs before stable bytes are finalized and signed.
6. Candidate SBOM output is actual SPDX 2.3 rather than a custom file using an SPDX filename.

No production key/token was created, no model weight was downloaded or republished, and no live installation was changed.

## Nonclaims

- No production application bytes were assembled or signed in this task; the exact external runtime receipt and physical runtime proof are supplied only when an operator builds a candidate.
- Synthetic archive bytes do not prove application startup, GPU/CUDA behavior, five-component readiness, voice, service or rollback on a physical host.
- Private GitHub logic was tested with injected API/content/redirect/range responses; no production token or authenticated endpoint was used.
- Offline signing tests use disposable test keys only; production key generation/storage remains an operator decision.
- Publication tests use a fake remote boundary/dry-run only; no authentication or upload occurred.
- VM power loss, reboot, real systemd/Docker/network, model redistribution/legal approval and physical acceptance remain pending.

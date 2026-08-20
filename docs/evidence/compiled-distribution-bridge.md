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

## Production assembly blocker

A production `release-candidate candidate` invocation deliberately cannot succeed from this repository state:

1. `requirements-slice6.lock` pins versions but contains no exact artifact `--hash=sha256:` entries.
2. There is no accepted complete Linux `x86_64` redistributable runtime-bundle receipt containing bundled Python, Node and all native dependency closure with exact file hashes, immutable source/license receipts, glibc/CUDA compatibility and affirmative redistribution authority.
3. Active configuration still contains canonical-host cache locators; the release leak gate refuses them.
4. Silero `v5_5_ru` / `kseniya` is explicitly private noncommercial evaluation-only and requires legal review before production/commercial redistribution.

The tool therefore neither falls back to host Python/Node nor emits a stub labelled as working. Resolution requires new accepted inputs/evidence, not weakening a guard. No model weight was invented, downloaded or republished.

## Nonclaims

- No working production application artifact or signed production candidate exists.
- Synthetic archive bytes do not prove application startup, GPU/CUDA behavior, five-component readiness, voice, service or rollback on a physical host.
- HTTPS logic was tested with injected responses; no live production endpoint or DNS/TLS service was contacted.
- Offline signing tests use disposable test keys only; production key generation/storage remains an operator decision.
- Publication tests use a fake remote boundary/dry-run only; no authentication or upload occurred.
- VM power loss, reboot, real systemd/Docker/network, model redistribution/legal approval and physical acceptance remain pending.

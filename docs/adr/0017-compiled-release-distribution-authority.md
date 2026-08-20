# ADR-0017: Compile release authority into the launcher and keep production signing offline

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Voice Agent v2 project architecture

## Context

ADR-0014 defines signed launcher-owned installation, but the merged I1–I7 executable deliberately used an unprovisioned production source and deterministic in-memory artifacts. A distributable launcher must not obtain its trust root from the channel, an installed cache, environment, CLI, or another mutable authority. A release publisher also must not turn the existing host-oriented Python caches and license-limited model evaluation into an apparently self-contained production artifact.

The repository currently has no accepted production runtime-bundle receipt. `requirements-slice6.lock` pins versions but has no artifact hashes, active configuration contains canonical-host cache paths, and Silero is explicitly private noncommercial evaluation-only with legal review required. These are release-blocking facts, not permission to use host Python/Node, omit dependency closure, invent locators, or claim redistribution authority.

## Decision

A production SEA launcher is built only from explicit version/protocol/source commit/reproducible timestamp, one canonical Ed25519 SPKI public key, and one canonical HTTPS DNS URL ending in `/stable.json`. Those values are embedded as SEA assets. The executable admits no runtime override for them. A build lacking any exact input remains inert for install/update network authority.

`HttpsReleaseSource` is the production implementation of the same source interface used by lifecycle fixtures. It uses validated TLS, public DNS names on the standard HTTPS port, exact URLs, no credentials/query/fragment/redirect/content encoding or transfer-length ambiguity, bounded deadlines/bytes, exact strong ETag and range semantics, and content-free failures. Channel signatures are checked only with the compiled key. Signed artifact and asset URLs remain exact authorities, never redirect suggestions.

The Linux artifact is canonical zstd-compressed ustar. Admission checks exact compressed size/SHA-256 and manifest SHA-256, bounded expansion/count/path/file sizes, canonical checksum/numeric/ownership metadata, complete end markers, and a single closed manifest. PAX/GNU extensions, sparse/device/FIFO/socket types, undeclared metadata/output, duplicates, unsafe paths, special bits, and unsafe links fail before stage extraction. Launcher-owned extraction and final inventory/fsync/read-only promotion remain the ADR-0014 lifecycle boundary.

`./release-candidate` is the only publisher command surface:

- `candidate` requires a clean exact commit, exact npm locks, hash-bearing Python release lock, a closed Linux x86_64 glibc/CUDA runtime-bundle receipt, real bundled Python and Node ELF executables, complete dependency files, immutable source/license receipts, and redistribution authority; it builds the web UI and canonical artifact/SBOM/provenance/unsigned channel;
- `verify` reindexes and hashes the complete candidate without network;
- `sign-channel` reads only an explicit current-user mode-`0400`/`0600` Ed25519 private-key file, signs already canonical reviewed channel bytes, and emits only a detached signature plus content-free receipt;
- `publish --dry-run` requires those signed bytes, the public key matching launcher provenance, and exact `version:sequence` confirmation; real publication uses only `gh-axi`, refuses an existing tag or non-older signed remote sequence, uploads exact immutable release assets, verifies their downloads, then atomically CAS-advances the `release-channel` Git ref containing only `stable.json`/signature and verifies both readbacks. The compiled URL is the exact raw `release-channel/stable.json` path.

Production keys are never generated, stored, copied, printed, accepted from workflow input, or available to pull requests. Key rotation is a separately downloaded bridge-launcher release authorized by the old trust root. A channel or cache can never silently replace the compiled root.

The production assembler intentionally remains blocked until an accepted runtime bundle, hash-complete Python lock, host-neutral application inputs, and redistribution evidence exist. Deterministic PR tests assemble a clearly synthetic bounded archive to exercise the format only; they are not a shippable application runtime. No model weight is downloaded or republished.

## Consequences

- HTTPS compromise, network/cache/environment key injection, redirects, response smuggling ambiguity, and permissive archive tools do not grant execution authority.
- Repeated launcher/candidate assembly with identical accepted inputs can be compared byte-for-byte, with canonical receipts identifying the exact source and trust-root fingerprint.
- Signing stays offline and publication cannot be triggered by a pull request or untrusted workflow parameter.
- There is no production release candidate yet. This is an honest blocker caused by missing hash/licensing/runtime-closure evidence, not an implementation fallback.
- Initial acquisition is download → out-of-band digest/release-page verification → owner-only install; `curl | bash` is not supported.

## Alternatives rejected

- **Network- or environment-supplied public key:** mutable data cannot define its own verifier.
- **GitHub redirects or a floating branch:** redirects and mutable source are not artifact authority.
- **Host Python/Node or customer dependency resolution:** violates offline runtime closure and reproducibility.
- **Test stubs labelled as a production runtime:** deterministic format evidence is not application readiness.
- **Online CI production signing:** pull-request workflows do not receive production release authority.

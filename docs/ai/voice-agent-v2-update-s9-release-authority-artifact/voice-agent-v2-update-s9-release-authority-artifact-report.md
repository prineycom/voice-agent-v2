# Do Report: voice-agent-v2-update-s9-release-authority-artifact

**Source:** firstmate launch brief (distribution bridge vertical slice)  
**Parent:** merged launcher lifecycle PRs #50–#56  
**Status:** ⚠️ partial — strict bridge/tooling implemented; production runtime assembly blocked by repository inputs

## Changed files

| Area | Change | Acceptance criteria |
| --- | --- | --- |
| `launcher/release-source.cjs`, launcher loaders/install/update/adoption | Compile-time pinned production authority, strict HTTPS/range acquisition, zstd/ustar indexing and same lifecycle source seam | 1–2, 9 |
| `launcher/build`, launcher protocol/tests | Mandatory reproducible SEA inputs, embedded assets, SHA/provenance/compare, standalone embedded module closure | 3, 7, 9 |
| `release-candidate`, `release/` | Clean exact candidate assembly/refusal, manifest/SBOM/provenance/channel, offline signing, dry-run/confirmed collision/readback publication | 4–6, 9 |
| `.github/workflows/release-candidate.yml`, `scripts/verify.py` | Public-test-root-only unsigned release verification with no production signing/publishing authority | 7, 9 |
| `contracts/`, ADR-0017, architecture/roadmap/testing/README/evidence/AGENTS | Authoritative distribution, initial acquisition, rotation, blocker and nonclaim documentation | 8, 10 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Pinned HTTPS production source | Pass | Source tests cover missing/invalid root, network/cache/environment/CLI key injection, URL/TLS/redirect/length/deadline/range failures |
| Exact artifact and safe extraction | Pass within accepted bounded-buffer source interface | zstd/ustar parser plus existing owner-only fsynced extraction and complete inventory tests |
| Reproducible SEA launcher | Pass | Two/compare builds, receipts, embedded module closure and host/token leak scan |
| Production application assembly | **Blocked, fail closed** | Python lock lacks hashes; runtime closure/redistribution receipt absent; host-bound inputs and Silero license gate refuse assembly |
| Manifest/SBOM/provenance/assets/channel | Pass for deterministic format; production output blocked | Canonical reproducibility tests and external asset evidence gate |
| Offline signing/publication | Pass deterministically, not executed externally | Owner-only ephemeral key test; fake remote collision/sequence/readback mismatch; `gh-axi` production adapter/dry-run |
| CI isolation | Pass structurally | Read-only workflow, committed public key only, no secret/input/sign/upload step |
| Initial UX/key rotation | Pass | README and ADR-0017 reject `curl | bash`; old-key-authorized bridge required |
| Install/update/rollback fixture path | Pass | Existing real lifecycle fixture artifact matrices consume the unchanged interface and new extraction fsync path |
| Docs/contracts | Pass | ADR-0017 plus architecture §6.7 remain the authority; roadmap/testing/README/contracts updated proportionately |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS at checkpoint; final rerun required | `artifacts/update-s9-release-authority-artifact-verify.log`; 75 launcher/distribution tests, 388 hermetic Python tests, 9 local-socket tests, 88 Vitest tests and production Firefox/LiveKit smoke passed. Subsequent bounded publication-tag/channel-CAS edits require the next worker to rerun the sole canonical gate. |

## Unresolved uncertainty

- A production candidate and PR delivery are blocked exactly as ADR-0017/evidence state. Per the task's blocker clause, do not publish a PR or represent this as a working release until hash-complete locks, an accepted bundled runtime closure, host-neutral inputs and redistribution authority exist.
- Real DNS/TLS, GitHub publication, Linux service/GPU/runtime, VM power-loss/reboot and physical voice evidence remain separate authorized tiers.

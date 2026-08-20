# Do Report: voice-agent-v2-update-s10-runtime-assembler-assets

**Source:** Private S10 runtime assembler/assets brief (task authority; not committed)
**Parent:** ADR-0017 distribution correction
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/runtime-assembler.cjs`, `release/assemble-runtime.sh`, `release/release.cjs`, `release/release-candidate` | Add the bounded pinned-builder assembly command, normalized runtime/web output, recursive ELF and assembly/runtime receipts, and candidate integration. | 1–3 |
| `release/inputs/*`, `requirements-release/*`, `THIRD_PARTY_NOTICES.md` | Close immutable builder/tool/runtime/wheel/model identities and private-personal-noncommercial notices. | 1–4, 9 |
| `contracts/distribution-release.v1.schema.json`, `contracts/platform-artifact-manifest.v1.schema.json`, `contracts/model-sets.v1.schema.json`, `contracts/runtime-config.v1.schema.json` | Define expanded runtime/ELF/builder, four model-set and generated runtime-config contracts. | 2, 4–6 |
| `launcher/asset-cache.cjs`, `launcher/install.cjs`, `launcher/update.cjs`, `launcher/adopt.cjs`, `launcher/release-source.cjs` | Materialize no-follow atomic model views, implement staged production closure checks, generate runtime config, retain active/rollback reachability and recover exact prior views/config. | 4, 6, 7 |
| `src/voice_agent_v2/runtime_config.py`, production runtime modules and `scripts/run_slice6.py` | Make production runtime/model/path selection launcher-descriptor-owned and move the STT runner out of benchmark ownership. | 2, 5 |
| `launcher/test/*`, `release/*.test.cjs`, `tests/test_runtime_config.py`, verification manifests | Add bounded deterministic assembler, model-view, host-preflight, lifecycle recovery and host-neutral-path coverage. | 3, 6–8 |
| `README.md`, `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/testing.md`, `release/inputs/README.md` | Document the single opt-in real assembler command, runtime/model authority, recovery semantics, evidence tiers and remaining nonclaims. | 9 |
| `docs/evidence/runtime-assembler-model-views.md` | Record deterministic evidence and explicit physical/runtime/publication nonclaims. | 8–9 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| 1. Repository-owned bounded assembler | ✅ | `assemble-runtime` validates exact input cache/builder custody, separates acquisition, then performs a network-disabled build with no ambient host tool fallback. |
| 2. Relocatable closed runtime | ✅ deterministic contract | CPython/wheels, LiveKit, rebuilt llama.cpp/CUDA, static web, production STT runner, normalized modes/timestamps, recursive ELF/RUNPATH/GLIBC receipts and Node exclusion are enforced. Real bytes remain a physical tier. |
| 3. Bounded fixture instead of PR-time real CUDA build | ✅ | Canonical orchestration injects a small no-network fixture; real builder/input hashes remain committed production authority and fixture output is never labelled production. |
| 4. Closed model sets and atomic views | ✅ | Exactly four aggregate sets, exact five-file STT membership, resumable raw custody, descriptor-through-fd copy, atomic promotion, interruption/race/tamper/link/extra and reachability behavior. |
| 5. Host-neutral launcher-owned production paths | ✅ | `runtime.json` binds release/executables/views/state/temp/log/web/agent data; production modules consume `VOICE_AGENT_RUNTIME_CONFIG`; production STT runner is under `src/`. |
| 6. Real staged production compatibility | ✅ deterministic contract | `SystemHost.inspectCompatibility` checks signed descriptor identities, runtime receipt/files, exact views, application protocol and GLIBC after base platform/NVIDIA/VRAM checks; lifecycle callers fail before mutation. |
| 7. Transaction recovery and retention | ✅ | Views are built before service mutation; active/rollback raw bytes and aggregates are protected; rollback/recovery rematerializes recorded prior views and rewrites prior config before exact start/readiness. |
| 8. Deterministic tests and zero live mutation | ✅ | Canonical `./verify` passed: 85 launcher/release tests, 390 hermetic Python tests, 9 local-socket tests, 88 Vitest tests, typecheck/builds and Firefox/LiveKit smoke under the 90-second gate. |
| 9. Authority docs and delivery workflow | ✅ repository scope | ADR/architecture/roadmap/testing/README/input docs/evidence updated; exact-head CI is the remaining delivery check after push. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Sole canonical repository verification; `canonical_pr_gate: PASS`, no unexpected skips. Full output: `evidence/verify.txt`. |

## Unresolved uncertainty

- The real pinned OCI/network assembler has not been executed. Two isolated physical assemblies, measured archive/expanded closure, recursive native review and disposable-host NVIDIA/systemd/voice/reboot acceptance remain separate authorized tiers.
- Production signing keys/tokens/publication remain deliberately absent.

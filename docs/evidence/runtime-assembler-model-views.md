# S10 runtime assembler and model-view closure

## Scope

This evidence records deterministic repository proof for the private personal noncommercial Linux `x86_64` distribution correction. It does not record a real OCI build, model download, signing, publication, live installation, Docker/GPU/voice/reboot exercise, or live-stand mutation.

## Implemented closure

- `./release-candidate assemble-runtime` consumes the closed `release/inputs` authority, an exact content-addressed cache and the pinned CUDA-12.9.1 Rocky-8 builder. It fails before fetch/output unless local rootless Podman 6 reports netavark and a working `pasta` helper. Only the receipt-bound web acquisition container uses fixed `--network=pasta`; private empty home/config, empty pull auth, stripped ambient proxy/credential state and no socket mounts remain explicit. Compilation, offline wheel installation, llama/CUDA build and normalization stay read-only and `--network=none`, with no selector, host network, removed slirp or fallback. The llama.cpp commit archive uses its direct canonical codeload owner/repository/tar.gz/full-commit locator with unchanged accepted bytes; deterministic fixtures reject alternate owner/repository/commit/path/query/host and every redirect into or out of codeload while proving credential-free direct acquisition, digest refusal and repeat cache custody.
- The assembler bundles CPython 3.12.13 and hash-locked wheels, static LiveKit, rebuilt server-only llama.cpp/CUDA, normalized static web output and recursively closed ELF dependencies. Dynamic objects receive only relative RUNPATH; static ELF is not passed to patchelf; reached llama/NVIDIA providers are moved into one common closure. Node, CMake, patchelf and the CUDA compiler remain build-only.
- Runtime receipts bind the builder manifest, source/wheel inputs, every file, recursive ELF `DT_NEEDED`, SONAME, RUNPATH, GLIBC floor and `libcuda` use. Candidate construction requires the production-owned faster-whisper runner, exact notices and SPDX 2.3 output.
- `voice-agent.model-sets.v1` binds exactly four aggregates: the exact five-file faster-whisper directory, LFM GGUF, Kseniya and VAD. Raw members retain resumable content-addressed custody; the launcher copies them through no-follow source/target descriptors into owner-only atomic aggregate views and rejects links, extra files, tampering, interrupted stages and race losers.
- The launcher generates `voice-agent.runtime-config.v1` from the immutable release, exact aggregate views and canonical XDG paths. The production wrapper exports only `VOICE_AGENT_RUNTIME_CONFIG`; production LiveKit/STT/LLM/TTS/VAD/trace path selection derives from it.
- Fresh install, update and legacy adoption prove staged runtime/model closure before service mutation. Update/rollback recovery re-materializes exact recorded prior views, regenerates prior runtime configuration before start and retains active plus rollback raw/view reachability.

## Deterministic evidence

Canonical `./verify` exercises bounded assembler authority/input-cache/orchestration fixtures rather than the real OCI/network build. Those network-free fixtures cover exact rootless Podman-6/netavark/`pasta` facts and command construction, missing helper, rootful/wrong backend/unsupported engine, arbitrary override, removed slirp, ambient proxy/credential stripping, network-disabled build phases and downloaded digest mismatch. It also exercises release/receipt refusal, exact four-set materialization, interruption/race/tamper/link/extra/reachability behavior, production `SystemHost` positive/tamper parsing, host-neutral runtime config, and install/update/adoption/rollback recovery fixtures under disposable XDG roots.

The complete canonical output is retained at `docs/ai/voice-agent-v2-update-s10-runtime-assembler-assets/evidence/verify.txt`. The final result and exact branch head are recorded in the do/review reports and pull request.

## Nonclaims and remaining acceptance

- No real builder image or immutable runtime input was fetched in canonical verification.
- No production runtime/archive byte size, expanded file count, physical GLIBC/driver/CUDA behavior or byte-identical second real assembly is claimed.
- No model byte was downloaded into canonical user paths.
- No production key/token was read or generated; nothing was signed or published.
- No live service, live stand, canonical XDG installation, Docker object or AgentEnvironment was started, stopped, changed or inspected.
- Disposable-host user-systemd/NVIDIA/model/voice, power-loss/VM, reboot and physical full-stack acceptance remain pending under `docs/testing.md`.

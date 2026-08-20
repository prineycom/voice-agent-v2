# Do Report: voice-agent-v2-update-s12-podman-pasta-network

**Source:** Private S12 Podman acquisition-network correction brief (task authority; not committed)
**Parent:** D1 compiled distribution bridge
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `release/runtime-assembler.cjs`, `release/assemble-runtime.sh`, `release/release.cjs` | Require local rootless Podman 6/netavark/working `pasta` before acquisition; use fixed `--network=pasta` only for web acquisition; keep assembly `--network=none`; isolate Podman/container environment and pull auth; retain actionable assembly error codes. | 1–3 |
| `release/runtime-assembler.test.cjs` | Add network-free facts, refusal, command, environment, auth, cache/digest and pre-output coverage. | 4 |
| `README.md`, `release/inputs/README.md`, `docs/adr/0017-compiled-release-distribution-authority.md`, `docs/architecture.md`, `docs/testing.md`, `docs/evidence/runtime-assembler-model-views.md` | Record the supported rootless acquisition boundary, immutable authority, network-disabled build phases, deterministic evidence and unchanged physical-build nonclaim. | 5 |
| `docs/ai/voice-agent-v2-update-s12-podman-pasta-network/evidence/verify.txt` | Preserve the complete sole canonical verification output. | 4–5 |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Rootless Podman 6/netavark/`pasta` preflight before fetch/output | ✅ | One local non-remote `podman info` document and a bounded `pasta --version` probe fail with `runtime_acquisition_network_unsupported` before cache preparation or output creation. |
| Network limited to admitted acquisition | ✅ | The web acquisition container has literal `--network=pasta`; compilation, offline wheel installation, llama/CUDA build and normalization share the sole `assemble` invocation with `--network=none`. Builder/input identities remain digest-pinned and revalidated. |
| No ambient home/config/credential/proxy/socket authority | ✅ | Podman receives an allowlisted host environment, uses local mode and explicit empty pull auth; containers disable host/proxy inheritance, use private tmpfs-backed home/config, clear credential/proxy names and mount no engine socket. |
| Required deterministic refusal/orchestration cases | ✅ | Network-free Node fixtures cover exact pasta construction/rootless facts, missing helper, rootful engine, wrong backend, unsupported engine, removed slirp, arbitrary override, ambient stripping, network-disabled build, explicit empty auth and digest mismatch. |
| Documentation and bounded validation | ✅ | Authority/runtime/testing/evidence text updated; sole `./verify` result is `PASS`. No real assembler, live stand, signing, token or publication action ran. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | ✅ PASS | 91 Node tests, 390 hermetic Python tests, 9 local-socket tests, 88 Vitest tests, typecheck, production/review builds and bounded Firefox/LiveKit smoke passed within the canonical deadline. Full output: `evidence/verify.txt`. |

## Unresolved uncertainty

- Physical two-build Podman/network/runtime acceptance remains the separately authorized post-merge tier; this change deliberately did not run it.

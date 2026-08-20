# Review: voice-agent-v2-update-s9-release-authority-artifact

**Source:** continuation of the distribution-bridge slice at checkpoint `22c42a0af105624964178257997ff2a58e9fb3a9`
**Status:** ✅ pass

## Findings

No critical, important, or minor findings remain after the continuation fixes.

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Public/raw GitHub assumption and credential forwarding risk | `launcher/build`, `launcher/release-source.cjs`, tests | Fixed private repository/ref/path and API/store hosts; owner-mode-0600 token custody; one stripped-auth asset redirect |
| Circular publish/sign authority | `release/release.cjs`, contracts/tests/docs | Immutable upload/ID receipt precedes channel finalization and offline signing; final publish is channel-only CAS |
| Incomplete/host-bound runtime and false SBOM | `requirements-release/`, `release/inputs/`, application paths, release tooling | CPython 3.12 hash closure, CUDA 12.9/ELF receipt, STT runner, no application Node, actual SPDX 2.3 |
| Source/model license ambiguity | `LICENSE`, `THIRD_PARTY_NOTICES.md`, ADR-0009/0017 and evidence | MIT source remains separate from exact CC BY-NC-SA Kseniya private-personal-noncommercial authority |

## Skipped issues

- Physical authenticated GitHub, GPU/service/reboot/voice acceptance is an explicit later tier; no production token/key/publication/live mutation was authorized here.

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | PASS | 78 launcher/distribution, 388 hermetic Python, 9 local-socket, 88 Vitest, typecheck/build, and production Firefox/LiveKit smoke; full log in `artifacts/update-s9-release-authority-artifact-verify.log` |

## Recommendations

- Commit, push, open the PR, and require exact-head CI before delivery-ready status.

# Review: 7-slice-6-livekit-media-interruption

**Source:** `docs/ai/7-slice-6-livekit-media-interruption/7-slice-6-livekit-media-interruption-report.md` and Issue #7
**Status:** ✅ implementation review pass; physical acceptance remains explicitly pending

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| — | No critical, important, or minor implementation finding remained after reviewing the controller/media lifecycle, capability/origin boundary, child-process configuration, React event/media gates, contracts, tests, CI and evidence claims. | The diff is ready for the commit boundary. This does not convert the documented physical-browser gates into passes. | Keep the pending Pasha checklist open and do not proceed to Design Gate V until it is recorded. | — |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Pre-review stale completion could race a replacement listening event | `src/voice_agent_v2/realtime.py`, `tests/test_slice6_realtime.py` | Completion/context commit and next-turn admission share the session lock; deterministic ordering test passes. |
| Pre-review reconnect could briefly reattach/replay old media or retain unheard provider context | `realtime.py`, `livekit_runtime.py`, `web/src/state.ts`, `voiceClient.ts`, `playback.ts` and tests | Old epochs are suppressed before media actions; playout is suspended; cleanup is serialized; context resets before the next epoch. |
| Pre-review unrelated child processes inherited server configuration | `scripts/run_slice6.py`, startup boundary test | Tailscale children receive no LiveKit/LiteLLM server values; LiveKit gets only its dedicated config/key pair. |
| Pre-review public URL configuration did not prove the current tailnet host | `slice6_config.py`, `run_slice6.py`, tests | Exact HTTPS origins share one host and startup matches DNS/IP/online state against `tailscale status --json`. |
| Pre-review capability endpoint admitted cross-site POST consumption | `slice6_config.py`, `slice6_gateway.py`, tests | Capability issuance requires exact loopback or configured tailnet application `Origin`; missing/cross-site origin smoke returns 403. |

## Skipped issues

| Finding | Reason |
| --- | --- |
| Real loopback microphone/listening, second tailnet browser, 250 ms physical interruption, endpoint-to-playout timing and combined resources are absent | These are mandatory human/device acceptance evidence, not defects that can be fabricated or closed by code review. The exact checklist is documented. |
| Vite reports a single-bundle size advisory | The deliberately simple one-page Slice 6 client is within scope; premature code splitting/design-system work would not improve an acceptance boundary. The advisory remains visible. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify-slice6` | Historical pre-fix checkpoint | The earlier checkpoint recorded 83 network-denied Python tests and 8 browser tests; later lifecycle regressions require fresh pipeline results, so these counts are not evidence for the current code. |
| Restricted LiveKit start plus `ss` | PASS | Loopback TCP/7880; redacted tailnet UDP/7882; no TCP media. |
| Gateway status/security/origin smoke | PASS | Loopback only, safe headers/status, provider endpoint absent, missing/cross-site origin denied. |
| `sh -n ...`, Python compile, `git diff --check` | PASS | Scripts, Python syntax and whitespace pass. |
| Local Markdown file-link check | PASS | New README/architecture/roadmap/ADR/evidence links resolve. |

## Recommendations

- Proceed to `/skill:gca #7` for the explicit commit boundary.
- Let firstmate start `/no-mistakes` only after the brief's committed `done` gate.
- Do not claim final Slice 6 acceptance or start Design Gate V until Pasha records the physical checklist in the evidence report.

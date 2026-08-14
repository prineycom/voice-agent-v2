# Review: silero-kseniya-tts-v2

**Source:** current diff on `fm/voice-agent-v2-silero-kseniya-48k`
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| Important | `src/voice_agent_v2/realtime.py` detached obsolete-TTS cleanup originally restored an LFM snapshot after replacement admission. | A scheduling race could erase the replacement's newly committed local-LFM context. | Drop, but do not restore, a pre-commit obsolete TTS handoff snapshot. | Fixed |
| Important | `src/voice_agent_v2/silero_tts.py` pool startup originally had no concurrent-start gate. | Two startup callers could exceed the exact two-process invariant. | Serialize pool startup and shutdown around `_starting`. | Fixed |
| Important | `src/voice_agent_v2/silero_tts.py` retained affinity/lock/invalidation state after terminal turns. | A long-running private session could grow task-local scheduler state without a turn bound. | Add explicit terminal `release_turn` and wire it through delivered, failed, interrupted, reset, and close paths. | Fixed |
| Important | `src/voice_agent_v2/livekit_runtime.py` reset originally cleared every session snapshot. | Reconnecting one room could disturb another room's in-flight rollback state. | Clear only keys belonging to the requested session. | Fixed |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Obsolete rollback race | `src/voice_agent_v2/realtime.py`, `src/voice_agent_v2/livekit_runtime.py` | Detached cleanup uses `discard_obsolete_turn`; cooperative STT/LFM paths still restore and drain. |
| Concurrent third-worker risk | `src/voice_agent_v2/silero_tts.py`, `tests/test_silero_tts.py` | A barrier-backed concurrent-start test observes exactly two created/live workers. |
| Per-turn scheduler retention | `src/voice_agent_v2/silero_tts.py`, `src/voice_agent_v2/livekit_runtime.py`, `tests/test_silero_tts.py` | `retained_turn_count` returns to zero after release. |
| Cross-session reset | `src/voice_agent_v2/livekit_runtime.py` | Snapshot/correlation cleanup is filtered by `session_id`. |

## Skipped issues

None.

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `PYTHONPATH=src python -m unittest discover -s tests -q` | Pass | 171 tests, 2 skipped after review fixes. |
| `cd web && npm run typecheck && npm test` | Pass | Typecheck and 27 Vitest tests. |
| `./verify-silero-kseniya` | Pass | Exact cache, two-worker overlap, native-48 totals, stale discard, VAD/Whisper coexistence, and explicit recovery; no physical claims. |
| `NO_MISTAKES_EVIDENCE_DIR=$PWD/tmp/silero-kseniya-firefox-evidence ./verify-slice6` | Pass | Contract/runtime, 171 Python tests, web build/tests, and real Firefox/official LiveKit with fake inference. |
| `git diff --check` | Pass | No whitespace errors. |

## Recommendations

- Commit and push only this private branch.
- Open the PR but do not merge until Pasha completes physical listening/barge-in/tailnet/full-stack resource acceptance and licensing/legal review separately approves the boundary.

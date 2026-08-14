# Review: voxcpm2-fast-tts

**Source:** current diff on `fm/voice-agent-v2-voxcpm2-fast-tts`
**Status:** ✅ pass

## Findings

No unresolved findings remain.

## Fixed issues

| Finding | Files changed | Evidence |
| ------- | ------------- | -------- |
| Accelerated inference could be started without proof that the cross-experiment lock wrapper owned the process lifetime. | `run-voxcpm2-fast-candidate`, `scripts/voxcpm2_fast_runner.py`, `src/voice_agent_v2/voxcpm2_tts.py`, `tests/test_voxcpm2_fast_tts.py` | The wrapper exports the lock-held sentinel, the adapter passes it through its isolated environment, and the executable runner rejects startup before cache/GPU access without it. Real locked verification passed. |
| Public reference discovery addressed the dataset's moving default revision before checking the returned asset. | `scripts/acquire_voxcpm2_fast.py` | The rows request now supplies the immutable manifest revision and still validates row metadata, asset revision, size, and SHA-256. |
| Verify-only setup checked the tracked lock digest and critical imports but not the complete installed distribution set. | `setup-voxcpm2-fast` | Verify-only now compares normalized pinned requirements with the cache-local runtime's full `uv pip freeze` output before imports. |

## Skipped issues

None.

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify-voxcpm2-fast` | PASS | Real pinned model, public Ultimate Cloning, resident-process reuse, warm latency/RTF, cooperative cancellation, recovery, and resources under the global GPU lock. |
| `./setup-voxcpm2-fast --verify-only` | PASS | Model/reference hashes, runtime lock identity, complete package set, and critical versions verified offline. |
| `./verify-slice6` (includes complete test discovery) | PASS | 163 Python behavioral tests, installed-runtime checks, 24 Vitest tests, web build, and deterministic Firefox/LiveKit regression. |
| `./verify-slice6` | PASS | Existing Python, installed-runtime, TypeScript, Vitest, build, and deterministic Firefox/LiveKit contracts remained green. |
| `git diff --check`; JSON/shell/Python syntax checks | PASS | No whitespace or syntax failures. |

## Recommendations

- Keep the no-go decision: the candidate is real-time in isolation but cannot preserve the 1,536 MiB reserve with the current resident LFM, even before Whisper.
- Run the required no-mistakes delivery gate, then review and merge the resulting PR; do not deploy the candidate on the current full stack.

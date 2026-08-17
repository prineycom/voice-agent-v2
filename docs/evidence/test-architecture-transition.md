# Lean test-architecture transition evidence

- **Source baseline:** `872ee20630723b544ddfaf395e3647c0262e3301`
- **Scope:** test ownership and orchestration only; accepted product contracts and physical Acceptance status are unchanged
- **Decision:** short real Firefox + actual local LiveKit remains a required PR blocker; LiteLLM/Qwen and executable Slices 2–5 are frozen historical evidence

## Bounded transition parity

Before any old command was removed, the legacy baseline passed on the audited source head under per-command `TERM`/`KILL` outer timeouts:

| Command | Result |
| --- | --- |
| `./verify` | pass, 380 executions including 9 environment skips |
| `./verify-slice6` | pass, including 380 Python executions, 88 Vitest cases, both builds, Firefox and actual local LiveKit |
| `./verify-slice8` | pass, 90 Python executions, validator, 69 repeated Vitest cases |
| `./verify-slice9` | pass, 98 Python executions, validator, 50 repeated Vitest cases |

On the transition working tree, before deleting the wrappers, the new `./verify` and the still-present `./verify-slice6`, `./verify-slice8`, and `./verify-slice9` all passed. The new gate ran 306 hermetic current-behavior tests plus 9 runnable local-socket tests with zero skips, all 88 Vitest cases once, one typecheck, one build per entry mode, and the short Firefox/LiveKit smoke. The compatibility wrappers then passed their historical focused selections and validators. Process/listener scans after both parity runs found no task-owned survivor. The intentionally retired differences are the duplicate checkpoint collection, historical Slice 2 benchmark module, inactive LiteLLM/Qwen runtime tests, hardware/cache-dependent cases, and long browser lifecycle cases now owned by another tier.

## Retired-owner traceability

| Retired owner/invariant | One current owner |
| --- | --- |
| Checkpoint A realtime behavior, previously collected three times | `tests.test_slice6_realtime.RealtimeCheckpointTests`, once in the explicit PR manifest |
| Checkpoint B persistent publication and per-frame freshness, previously collected three times | `tests.test_slice6_livekit_runtime.PersistentLiveKitTrackTests`, once in the explicit PR manifest |
| Qwen warmup checkpoint | frozen dated Slice 2–6 evidence; active equivalent is Silero two-worker warmup/readiness in `tests.test_silero_tts` and `./verify-silero-kseniya` |
| Slice 8 disposable process loss/recovery | `SupervisorLifecycleTests.test_real_process_loss_allows_one_recovery_then_stays_unready` |
| Slice 8 capture deletion | `CaptureAndResourceTests.test_public_capture_delete_command_removes_private_content` |
| Slice 8 failure matrix/readiness/privacy/resource calculations | observability, diagnostics, realtime, and operations owners in the explicit Python manifest |
| Slice 9 three-process ordered cleanup | `SupervisorLifecycleTests.test_three_real_processes_stop_in_declared_order_without_survivors` |
| Slice 9 bounded recovery, disk pressure, and sustained threshold calculations | startup and operations behavior tests |
| Slice 9 fresh parent/listener/readiness behavior that formerly skipped under root discovery | `tests.test_slice9_runtime` in the local-socket PR subphase |
| `verify-slice6/7/8/9` cumulative orchestration and Vitest subsets | `./verify` for PR behavior; `./verify-extended` for long browser/lifecycle behavior |
| `verify-slice3` | `./verify-real-stt` canonical-host extended command |
| Slice 4/5 and `verify-real-streaming` LiteLLM/Qwen runtime entrypoints | frozen evidence through `./verify-evidence`; active LFM/Silero exact checks remain separate |
| `tests/test_slice2_benchmark.py` in blind PR discovery | explicit/path-triggerable `./verify-evidence` only |
| Long mute, reconnect, diagnostics, and interruption lifecycle | `./verify-extended` with a 600-second outer timeout |
| Exact-cache LFM, Silero, STT, and user-systemd host behavior | the four separately bounded canonical-host commands in [`../testing.md`](../testing.md) |
| Microphone audibility, barge-in, resident soak, reboot, rollback voice turn, Raspberry Pi, licensing | physical Acceptance only; still open |

The checkpoint move places shared fakes in `tests/support/realtime_fakes.py`, whose filename cannot be collected by unittest discovery. Twelve active realtime/LiveKit checkpoint behaviors now run once; the thirteenth historical Qwen warmup is intentionally retired rather than disguised as active Silero coverage. This removes the 26 proven exact duplicate executions without weakening any active-generation behavior.

# Do Report: 68-isolated-main-dev-stands

**Source:** https://github.com/prineycom/voice-agent-v2/issues/68
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/instance_runtime.py` | Adds the fail-closed selected-instance path, shared-cache, and listener-port contract. | Per-instance mutable paths; no selected-instance legacy fallback; explicit ports. |
| `src/voice_agent_v2/stand_dev.py`, `scripts/stand.py` | Enables main/dev local and remote deployments, independent pointers/services, isolated launcher environments, exact status/log identity, and cross-instance credential/port validation. | Independent pointers, configuration, credentials, paths, ports, status, and logs. |
| `scripts/run_slice6.py`, `src/voice_agent_v2/slice6_config.py`, `src/voice_agent_v2/local_lfm.py` | Propagates explicit instance ports through llama.cpp, LiveKit, gateway, readiness, and loopback configuration; records process-tree RSS/counts with no shared inference. | Concurrent complete loopback stacks and measured duplicated resources. |
| `src/voice_agent_v2/agent_config.py` | Roots selected agent profiles below the instance rather than `~/.voice-agent`. | Hard-coded user-profile path isolation. |
| `src/voice_agent_v2/agent_run_provider.py`, `src/voice_agent_v2/livekit_runtime.py`, `src/voice_agent_v2/operations_cli.py` | Routes workspace, cache, credentials, controller registry, Docker client, and AgentEnvironment identity through the selected instance. | Docker/container, workspace, cache, and credential isolation. |
| `src/voice_agent_v2/silero_tts.py` | Routes the mutable two-worker TTS runtime below the instance while retaining the shared immutable model/runtime artifact. | TTS mutable-state isolation with allowed immutable-cache reuse. |
| `tests/test_stand_dev.py`, `tests/test_slice6_startup.py` | Adds deterministic two-release/two-instance smoke coverage and synthetic process-tree resource measurement. | All focused smoke requirements. |
| `README.md`, `docs/architecture.md` | Documents the two-stack interface, instance-root boundary, nonclaims, and deferred SemVer/lifecycle scope. | Approved architecture and issue-boundary traceability. |
| `artifacts/issue-68-verify.log` | Preserves full canonical verification output. | Delivery evidence. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Independent ports, configuration, data, mutable cache/runtime, workspace, credentials, and Docker state | ✅ | Launcher exports disjoint instance-owned paths; strict configs reject overlap/shared LiveKit credentials; AgentEnvironment receives distinct state/workspace/cache/credential roots. |
| Independent `current` pointers with only immutable release/cache sharing | ✅ | Generic main/dev deployment selects separate symlinks into one release store; deterministic smoke selects different exact commits while sharing the release parent and immutable cache root. |
| Explicit instance root replaces `~/.voice-agent` and shared AgentEnvironment paths | ✅ | `VOICE_AGENT_INSTANCE_ROOT` drives agent profile, diagnostics, inference runtime, and AgentEnvironment composition; selected ports fail if omitted. |
| Complete independent loopback-only stacks | ✅ | Both units receive non-overlapping explicit llama.cpp, LiveKit TCP/UDP, and gateway ports; all bind/allowlist values remain `127.0.0.1`; each launcher starts its own LLM, LiveKit, gateway, STT, and two TTS workers. |
| Status/log identity remains instance/release exact | ✅ | Output headers contain requested instance and full selected SHA; systemd and journal commands target only `voice-agent-v2@<instance>.service`. |
| Focused deterministic isolation/resource coverage | ✅ | `test_main_and_dev_have_independent_complete_loopback_stack_contracts`, `test_selected_instance_never_falls_back_to_legacy_mutable_paths_or_ports`, and `test_complete_stack_resource_measurement_keeps_inference_independent`. Runtime journals report process-tree count/RSS and `shared_inference=false`. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `./verify` | PASS | Canonical 90-second PR gate; 400 Python tests, 88 Vitest tests, typecheck/builds, and actual-LiveKit Firefox smoke passed. Full output: `artifacts/issue-68-verify.log`. |

## Unresolved uncertainty

- Physical concurrent-stack resource capacity and canonical-host acceptance remain separate manual evidence gates; this slice measures each running process tree and makes no host-capacity claim.

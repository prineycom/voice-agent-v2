# Do Report: 71-agent-environment-live-retry

**Source:** promoted issue #71 exact-runtime compatibility diagnosis  
**Parent:** https://github.com/prineycom/voice-agent-v2/issues/71  
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| ---- | ------ | ------------------- |
| `src/voice_agent_v2/local_lfm.py` | Builds a fresh generation-only V4 clone, removes only final `answer.maxLength`, and keeps decision-content failures turn-local. | Exact b10357 compatibility; unchanged strict admission/readiness split. |
| `tests/test_local_lfm.py` | Proves the one-path semantic diff, retained closed structure, unchanged valid parsing, turn-local invalid/content failures and global capability rejection. | Required deterministic coverage 1–6. |
| `tests/test_agent_environment.py` | Carries malformed/empty/oversized/unknown/citation decisions through the turn controller with zero environment/tool/TTS mutation. | Required deterministic coverage 5. |
| `README.md`, `docs/architecture.md`, `contracts/README.md` | Documents generation/admission separation and unchanged no-retry/global-rejection boundaries. | Operator and architecture accuracy. |
| `docs/evidence/agent-decision-structured-output.md` | Records the source-backed b10357 threshold diagnosis, deterministic owners and live nonclaims. | Required deterministic coverage 7. |

## Acceptance coverage

| Criterion | Status | Evidence |
| --------- | ------ | -------- |
| Tracked V4 unchanged | ✅ | No diff; SHA-256 remains `63b4c368e01ecb44413290b070984c40bf21c7580cc5af5240864be526a87f7f`. |
| Strict parser unchanged | ✅ | `src/voice_agent_v2/agent_run.py` has no diff. |
| Only generation final `answer.maxLength` omitted | ✅ | Structural-difference test requires the sole path `oneOf/1/properties/answer/maxLength`. |
| Metadata/oneOf/closed variants/tools/arguments/citations retained | ✅ | Captured HTTP payload assertions compare retained structures to tracked V4. |
| Ordinary voice payload unchanged | ✅ | Existing fixed voice-payload test remains green; response format is decision-only. |
| Invalid decisions are turn-local before mutation | ✅ | Local-LFM readiness and real-turn controller matrices cover empty, oversized, malformed, unknown field/tool and invalid citation. |
| Genuine structured-output rejection remains global | ✅ | HTTP 400 compatibility regression remains green. |
| No forbidden runtime/live changes | ✅ | No deployment, browser/live turn, Docker/image/profile or runtime artifact change performed. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| `PYTHONPATH=src python -m unittest -v tests.test_local_lfm tests.test_agent_environment tests.test_resident_lifecycle` | ✅ pass | 64 focused tests. |
| `PYTHONPATH=src python -m unittest -v tests.test_local_lfm` | ✅ pass | 32 tests after final content-failure coverage. |
| `python -m compileall -q src tests` | ✅ pass | Sources compile. |
| `git diff --check` | ✅ pass | No whitespace errors. |
| `./verify` | ✅ pass | Exactly once; 441 hermetic Python + 9 local-socket tests, Vitest 91, typecheck, both builds and Firefox/LiveKit smoke; `RESULT: PASS`. Ignored log: `artifacts/agent-environment-live-retry-71-verify.log`. |

## Unresolved uncertainty

- Live generated decision quality and the real AgentRun/AgentEnvironment lifecycle remain separate post-merge acceptance; no live output was inspected or claimed in this Ship.

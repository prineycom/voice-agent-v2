# Fix Report: 71-agent-environment-real-run-acceptance

**Source:** promoted live acceptance report at `/home/priney/Projects/mymate/data/voice-agent-v2-agent-environment-real-run-acceptance-71/report.md`  
**Status:** ✅ pass  
**Scope stayed small:** yes

## Clarification decisions

- Use pinned llama.cpp b10357's supported OpenAI-compatible `response_format.type=json_schema` shape with the exact active `agent-decision.v4` schema.
- Keep strict `AgentDecision.parse()` admission unchanged; do not repair, coerce, retry, replay, or fall back.
- Treat malformed model content as request-local, while transport, identity, protocol, and structured-output rejection remain provider-global readiness failures.

## Changed behavior

- Before: AgentRun requested free-form decision text; malformed JSON marked LocalLFM unready and amplified one turn failure into resident service recovery.
- After: decision generation is schema-constrained to operation/final and 14 fixed tools; malformed content yields only `agent_decision_invalid` before environment/TTS mutation and leaves a healthy provider ready for a fresh independent turn.

## Files changed

| File | Change |
| ---- | ------ |
| `src/voice_agent_v2/local_lfm.py` | Adds the exact decision-only structured-output payload and separates request-local invalid content from provider-global failures. |
| `contracts/agent-decision.v4.schema.json` | Closes the operation tool field over the existing 14 fixed tools. |
| `tests/test_local_lfm.py` | Captures exact payload/schema, valid documents, local malformed failure, fresh next request, and global transport/identity/protocol/capability failures. |
| `tests/test_agent_environment.py` | Proves invalid decision causes one content-free turn failure with zero environment/helper/container/TTS mutation. |
| `tests/test_resident_lifecycle.py` | Proves malformed decision preserves resident readiness while real protocol loss blocks admission. |
| `README.md`, `docs/architecture.md`, `contracts/README.md`, `docs/evidence/agent-decision-structured-output.md` | Documents current behavior, ownership, evidence, and nonclaims. |

## Validation

| Command | Result | Notes |
| ------- | ------ | ----- |
| Focused unittest modules | ✅ pass | Local LFM, AgentRun/AgentEnvironment, and resident lifecycle regressions pass. |
| `python -m compileall -q src tests` | ✅ pass | Python sources compile. |
| `git diff --check` | ✅ pass | No whitespace errors. |
| `./verify` | ✅ pass | Run exactly once: 438 hermetic Python + 9 local-socket tests, installed contract, Vitest 91, typecheck, both builds, and actual-LiveKit Firefox smoke; `RESULT: PASS`. Complete ignored log: `artifacts/agent-environment-real-run-acceptance-71-verify.log`. |

## Follow-ups

- Repeat the separately authorized real #71 AgentRun/AgentEnvironment acceptance only after merge; this Ship performs no live session or deployment.

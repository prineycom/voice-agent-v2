# Deterministic evidence — closed AgentRun decisions and turn-local failure

**Tier:** deterministic PR evidence only  
**Command:** `./verify`

## Contract

- The production decision-only llama.cpp request uses `response_format.type=json_schema` and supplies the exact active `agent-decision.v4` schema. Its operation variant enumerates the 14 fixed tools and both operation/final variants reject unknown top-level fields.
- Ordinary voice generation keeps its existing payload. `AgentDecision.parse()` remains strict defense in depth; no extraction, repair, coercion, retry, replay, fallback model/provider, or unconstrained request exists.
- Malformed decision bytes from an otherwise reachable, exact, compatible endpoint terminate only that AgentRun/turn as content-free `agent_decision_invalid`, before `AgentEnvironment.ensure_running`, helper execution, visible answer, or TTS.
- Transport, endpoint identity, response protocol, and structured-output rejection remain provider-global readiness failures and retain the resident supervisor's recovery semantics.

## Deterministic coverage

`tests.test_local_lfm` captures the real decision HTTP payload, compares its nested schema to the tracked contract, admits unchanged valid operation/final documents, proves malformed bytes preserve ready health and allow a fresh request, and proves structured-output rejection becomes the stable compatibility code `agent_decision_structured_output_unsupported`. `tests.test_agent_environment` carries an invalid strict decision through the real turn controller and proves one content-free failure with zero environment/helper/container/TTS mutation. `tests.test_resident_lifecycle` proves that request-local failure keeps the resident stack ready while a genuine protocol loss makes selected-LLM readiness incompatible and blocks admission.

## Separate evidence

These tests use only fake HTTP/Docker/model/media surfaces. They do not claim exact-model decision quality, a real llama.cpp request, Docker creation/reuse, browser/systemd behavior, physical voice, network, reboot, or completion of issue #71 live acceptance. That acceptance remains a separate authorized follow-up after merge.

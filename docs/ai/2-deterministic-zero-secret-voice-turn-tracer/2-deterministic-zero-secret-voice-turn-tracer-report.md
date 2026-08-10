# Do Report: 2-deterministic-zero-secret-voice-turn-tracer

**Source:** https://github.com/prineycom/voice-agent-v2/issues/2
**Parent:** —
**Status:** ✅ pass

## Changed files

| File | Change | Acceptance criteria |
| --- | --- | --- |
| `src/voice_agent_v2/` | Dependency-free contracts, deterministic PCM generation, fake STT/LLM-provider/TTS, controller lifecycle, cancellation, normalization, and artifact writer | Complete correlated success turn; hard failures; cancellation; repeatability |
| `contracts/` | Versioned schemas, ownership/limits/terminal notes, producer-consumer examples, canonical traces, and declared PCM hashes | Checked-in contracts/fixtures and ownership evidence |
| `tests/test_tracer.py` | Behavioral, schema/fixture, no-fabrication, correlation, terminal, cancellation, and timestamp-normalization checks | All behavioral acceptance criteria |
| `verify`, `scripts/verify.py` | One root verification command with isolated empty caches, socket denial, two-run byte comparison, and deterministic report | Clean network-denied verification; nonzero on mismatch |
| `.github/workflows/behavioral.yml`, `.no-mistakes.yaml` | Required pull-request behavioral CI; removed documentation-only `no_ci` opt-out | Behavioral CI requirement |
| `README.md`, `AGENTS.md` | Root-command and slice-local toolchain pointers without selecting the production stack | Documented reproducible command and scope boundary |
| `docs/ai/2-deterministic-zero-secret-voice-turn-tracer/evidence/` | Two byte-identical verification outputs and evidence explanation | Deterministic evidence; no legacy use |
| `docs/ai/voice-agent-v2-roadmap/voice-agent-v2-roadmap-issues.md` | Complete local index for the eleven published roadmap nodes | Approved issue publication phase |

## Acceptance coverage

| Criterion | Status | Evidence |
| --- | --- | --- |
| Clean checkout needs no network, secrets, GPU, models, or cache | ✅ | `./verify`; standard-library-only toolchain, empty temporary home/cache, socket-denial audit policy |
| Fixed Russian transcript/response, ordered lifecycle, generated declared PCM, one completion | ✅ | canonical success trace, PCM hashes, `TracerBehaviorTests.test_success_is_correlated_ordered_and_complete` |
| Session/turn/sequence/version correlation | ✅ | event schema plus per-event behavioral assertions |
| Repeated normalized trace and PCM bytes are identical | ✅ | two internal clean runs per command and byte-identical checked-in `run-1.txt` / `run-2.txt` |
| Hard STT/selected-provider-LLM/TTS failure has no downstream fabrication | ✅ | table-driven public-scenario behavior test and canonical failure traces |
| Cancellation yields one interruption and no post-cancel chunks | ✅ | cancellation behavior test and canonical cancellation trace |
| Root command fails on any mismatch | ✅ | assertions, contract validation, fixture comparison, and unittest result control the exit status |
| Pull requests require behavioral CI and `no_ci` is absent | ✅ | `.github/workflows/behavioral.yml`; `.no-mistakes.yaml` contains no opt-out |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `./verify` | ✅ pass | 11 behavioral/contract tests; all five public scenarios |
| `./verify > .../run-1.txt` and `./verify > .../run-2.txt` | ✅ pass | Each command performs two isolated empty-cache runs under socket denial |
| `cmp .../run-1.txt .../run-2.txt` | ✅ pass | Complete command output is byte-identical |
| `sha256sum .../run-1.txt .../run-2.txt contracts/fixtures/traces/success.jsonl` | ✅ pass | Output hash is `f2f6c1c4…41a2904` twice; normalized trace hash is `9b607f8f…f305f` |

## Unresolved uncertainty

- The first green GitHub Actions run is necessarily obtained after this branch enters the PR path; the required workflow is checked in and local root verification is green.
- GitHub issue type `Task` is unavailable because repository issue types are not configured. All roadmap issues remain open and correctly labeled.
- No legacy material was inspected or used.

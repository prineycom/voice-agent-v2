# Review: Voice Agent v2 architecture comments

**Source:** Current diff implementing three authorized inline comments on PR #1
**Status:** ✅ pass

## Findings

| Severity | Evidence | Acceptance impact | Recommendation | Fix status |
| --- | --- | --- | --- | --- |
| — | README, glossary, architecture, roadmap, and ADR cross-check found no unresolved contradiction | All three authorized decisions are represented consistently | Proceed to commit and no-mistakes | — |

## Fixed issues

| Finding | Files changed | Evidence |
| --- | --- | --- |
| Local-only LLM boundary contradicted the allowed cloud option | `README.md`, `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, ADR-0001, ADR-0003 | Local candidates are evaluated first; cloud requires measured local failure, explicit selection, and provider/credential/privacy/observability controls with no automatic fallback. |
| Legacy provenance lacked a precise inspection target/procedure | `README.md`, `AGENTS.md`, `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, all ADRs | Pinned `93c5c397` tree, exact source-path evidence, minimal migration, and fresh V2 validation are explicit. |
| Accepted architecture incorrectly committed the MVP to Live2D | `README.md`, `CONTEXT.md`, `docs/architecture.md`, `docs/roadmap.md`, renamed ADR-0002 | Renderer-agnostic module boundary, deterministic MVP eye behavior, future Grill/design gate, later optional Live2D/3D modules, and no LLM frame control are explicit. |

## Skipped issues

| Finding | Reason |
| --- | --- |
| — | No review finding was excluded. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| GitHub-flavored Markdown parse | Pass | All changed Markdown renders through `cmark-gfm`. |
| Heading/link/anchor validator | Pass | All local document links and fragments resolve. |
| `git diff --check` | Pass | No whitespace errors. |
| Stale-decision search | Pass | No old ADR link, accepted Live2D-first statement, local-only cloud prohibition, or stale visual-control contract remains. |
| Pinned legacy `git cat-file -e` checks | Pass | Every referenced legacy ADR exists at the pinned commit. |

## Recommendations

- Commit the documentation correction without adding runtime code.
- Run the full no-mistakes path with the complete updated intent and trusted docs-only `no_ci` policy.
- Use the PR as the manual review surface; do not merge it.

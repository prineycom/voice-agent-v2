# Fix Report: Voice Agent v2 architecture review

**Source:** Three authorized inline comments on [PR #1](https://github.com/prineycom/voice-agent-v2/pull/1)
**Status:** ✅ pass
**Scope stayed small:** yes — documentation and project foundation only

## Clarification decisions

- Prefer a local LLM initially, but allow one explicitly selected cloud LLM only after measured local candidates fail preregistered VRAM, latency, or quality gates. Never guess the user's preferred model or fail over providers automatically.
- Pin legacy inspection to `prineycom/voice-agent@93c5c397`, keep it read-only, and selectively revalidate only the smallest slice-needed contract, component, or test.
- Replace the Live2D-first MVP decision with a renderer-agnostic avatar boundary and deterministic custom MVP eye. Defer the detailed visual/module contract to a future Grill/design gate; keep Live2D and 3D as optional later modules and keep LLM output away from frames.

## Changed behavior

- Local-only LLM architecture → local-first measured LLM selection with an explicit cloud-provider/privacy/credential/observability boundary when local evidence fails.
- Generic legacy provenance mention → pinned repository/tree, exact inspection procedure, and migration evidence rule.
- Accepted Live2D-first visual → deterministic custom AI eye behind a pluggable renderer module boundary, with a required pre-implementation design gate.

## Files changed

| File | Change |
| --- | --- |
| `README.md` | Align vision, scope/non-goals, MVP eye, cloud LLM option, and pinned legacy procedure. |
| `CONTEXT.md` | Replace renderer-specific visual-control terms with stable provider, avatar-module, MVP-eye, speech-envelope, and legacy-reference vocabulary. |
| `AGENTS.md` | Point future agents to the exact pinned legacy tree and authoritative selective-revalidation procedure. |
| `docs/architecture.md` | Define provider, privacy, credential, observability, avatar-module, MVP-eye, and pinned legacy boundaries. |
| `docs/roadmap.md` | Gate cloud LLM selection on local failure; add avatar Grill/design prerequisite and deterministic MVP-eye slice. |
| `docs/adr/0001-clean-v2-single-host.md` | Clarify the single self-hosted compute plane and explicit managed-cloud-LLM exception. |
| `docs/adr/0002-renderer-agnostic-avatar-boundary.md` | Replace the unmerged Live2D-specific ADR with the accepted renderer-agnostic/MVP-eye decision. |
| `docs/adr/0003-local-first-llm-with-explicit-cloud-option.md` | Record the local-first/cloud-option trade-off and provider boundaries. |

## Validation

| Command | Result | Notes |
| --- | --- | --- |
| `cmark-gfm ... --to html` for every Markdown document | Pass | All documents parse as GitHub-flavored Markdown. |
| Dependency-free heading/link/anchor validator | Pass | 8 pre-report Markdown files and 42 links validated. |
| `git diff --check` | Pass | No whitespace errors. |
| Stale architecture search | Pass | No old ADR path, accepted Live2D-first statement, local-only cloud prohibition, or stale visual-control contract remains. |
| `git cat-file -e 93c5c397:<path>` in the read-only legacy checkout | Pass | Pinned commit contains all six linked legacy ADR targets. |

## Follow-ups

- Run the roadmap's dedicated `/skill:grill-docs` Design Gate V before implementing the MVP eye or detailed avatar-module contract.
- Record the user's preferred local LLM candidate only when its exact identifier is supplied to Slice 2.
- Implement any later Live2D or 3D module as a separate post-MVP vertical slice against the accepted renderer-agnostic boundary.

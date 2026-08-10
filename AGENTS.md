# Project agent memory

Voice Agent v2 is pre-implementation; do not infer a runnable stack or selected model/framework from this repository.

- Use [`CONTEXT.md`](CONTEXT.md) for stable terminology.
- Use [`docs/architecture.md`](docs/architecture.md) for boundaries, contract ownership, lifecycle, failure policy, security, and resource gates.
- Use [`docs/roadmap.md`](docs/roadmap.md) for the mandatory dependency order and slice acceptance evidence.
- Use [`docs/adr/`](docs/adr/) only for accepted decisions that meet the project's ADR bar.
- Inspect legacy evidence only at pinned [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Follow the read-only selective-revalidation procedure in [`docs/architecture.md`](docs/architecture.md#34-pinned-legacy-reference); never copy legacy source or assumptions wholesale.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

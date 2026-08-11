# Project agent memory

Voice Agent v2 has cumulative Slices 1–5 plus the Slice 6 development LiveKit/React path; physical-browser Slice 6 acceptance remains pending. Run root verification with `./verify`, and use [`README.md`](README.md#slice-6-development-application) plus [`docs/evidence/slice-6-livekit-media-interruption.md`](docs/evidence/slice-6-livekit-media-interruption.md) for the installed runtime and remaining manual gates.

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

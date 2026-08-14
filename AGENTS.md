# Project agent memory

Voice Agent v2 has cumulative Slices 1–6 plus the Issue #9 Slice 7 renderer-agnostic avatar host, deterministic SVG eye, and portrait-first UI shell; physical browser/full-stack/Raspberry Pi acceptance remains pending. Active LLM is only pinned cache-local LFM2.5 Q4_K_M/llama.cpp per [`config/local-lfm-v1.json`](config/local-lfm-v1.json) and ADR-0008. Active TTS is fixed directly to exact Silero `v5_5_ru` / `kseniya` with native 48-kHz output and two workers; historical Qwen/TTS v1 stays inactive, with no selector or fallback. Run `./verify-slice6`, `./verify-local-lfm`, `./verify-silero-kseniya`, and `./verify-slice7`; use [`docs/evidence/silero-kseniya-48k-private-evaluation.md`](docs/evidence/silero-kseniya-48k-private-evaluation.md) and [`docs/evidence/slice-7-ui-avatar.md`](docs/evidence/slice-7-ui-avatar.md) for the remaining physical/manual boundaries.

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

# Project agent memory

Voice Agent v2 has cumulative Slices 1–9 plus E2.4–E3.2's production multi-step path through exactly one installation-owned persistent Docker `AgentEnvironment`; [`docs/architecture.md` §§9.7–9.9](docs/architecture.md#97-e24-production-agentrun-and-agentenvironment) own identity/lifecycle, ordinary persistence, network, one credential declaration, binary streams, acknowledgement truth, and remote Git. [`docs/evidence/e3-2-network-credentials-streams-remote-git.md`](docs/evidence/e3-2-network-credentials-streams-remote-git.md) owns the latest deterministic evidence/nonclaims. Physical Docker-platform/live-network/reboot/voice/full-stack/Raspberry Pi acceptance remains pending. Active LLM is only pinned cache-local LFM2.5 Q4_K_M/llama.cpp per [`config/local-lfm-v1.json`](config/local-lfm-v1.json) and ADR-0008. E2.1's single preregistered proposal benchmark attempt remains consumed as `model_operation_proposals_unavailable`; E2.2/E2.3 remain historical synthetic contracts. Active TTS is fixed directly to exact Silero `v5_5_ru` / `kseniya` with native 48-kHz output and two workers; historical Qwen/TTS v1 stays inactive, with no selector or fallback. `./verify` is the only PR gate; [`docs/testing.md`](docs/testing.md) owns all separate tiers. Use [`docs/evidence/test-architecture-transition.md`](docs/evidence/test-architecture-transition.md) for retired-invariant traceability and [`docs/evidence/slice-9-single-host-reliability.md`](docs/evidence/slice-9-single-host-reliability.md) for operational evidence/nonclaims. `./run-review-stand` is the real full local runtime at the stable manual-test URL; the synthetic `ReviewStand` is test-only output under `web/review/dist` and must never be deployed there.

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

# Grill: Proactive Agent — Results

> **Date:** 2026-08-16
> **Mode:** Epic-definition grill (product questions only, technical details deferred to implementation)
> **Reference architecture:** Hermes Agent (https://hermes-agent.nousresearch.com/docs/)

## Context

Voice Agent v2 is a local-first voice companion: STT (Whisper) → LLM (LFM2.5 2.6B Q4_K_M on llama.cpp) → TTS (Silero/Kseniya) over LiveKit. Slices 1–7 in various stages of completion. Current state: voice chat, no tools, no memory, no skills, no terminal, no proactivity.

Goal: transform the voice agent into a proactive agent modeled on Hermes architecture.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Worker = cloud API model, conversation = local LFM2.5 | 2.6B local already uses VRAM with Whisper+Silero; can't fit another 7B+ on 12GB. Cloud API gives smart worker without VRAM conflict. Same as Hermes main loop + delegate_task pattern. |
| 2 | Approvals via Telegram gateway (text/buttons), future — standalone app | Voice confirmation for `rm -rf` is impractical. Telegram is the approval surface, like Hermes `/approve` `/deny`. |
| 3 | Terminal = direct shell on host + MCP for Linux management | Single user, tailnet is the security boundary. Docker sandbox complicates git/SSH/system tasks. Safeguards via approval flow + pattern-blocking, not isolation. |
| 4 | Delegation is asynchronous, UI indicators for background tasks | Strong model may think for minutes. Agent says "working on it" and stays available. Result re-enters conversation as new turn. Like `delegate_task(background=true)` in Hermes. |
| 5 | Web search: hybrid (quick = conversation model, research = worker) | LFM 2.6B is weak at complex tool calling with result parsing. Quick `web_search` snippets are fine. Deep multi-page research → delegate to worker. |
| 6 | Skills: worker writes, conversation model triggers | LFM 2.6B can't write quality SKILL.md. Worker (Claude/GPT) handles structure, pitfalls, steps. Conversation model decides "I should remember this." |
| 7 | Memory: post-session batch via background process | Continuous transcript monitoring = constant cloud API calls for few facts/hour. Post-session: one API call reads full session, extracts facts, writes to memory. Like Hermes memory tool but automatic. |
| 8 | Compression: auto + voice command. New session: voice + auto per config | Compression is technical, auto. New session is product decision, voice-controlled. Same as Hermes `/compress` and `/new`. |
| 9 | Config and SOUL.md: only worker modifies, hot-reload | Config is critical. LFM 2.6B may generate broken YAML. Worker does `patch` via delegation. Hot-reload picks up changes. |
| 10 | Proactivity = initiative + suggestions + cron | Agent initiates conversation (result ready, fact found), proposes actions (skill suggestion, code check), runs scheduled tasks. Difference between "voice chat" and "agent." |
| 11 | Input — voice only. Telegram — only for approvals | All dialog and commands via voice. Approvals via Telegram text/buttons. Two confirmation paths don't conflict. |
| 12 | CLI tools: dual purpose (agent via tool calling, user via shell) | Like Hermes `hermes config set`. Debug, control, escape hatch. Docs for user and worker. Skill describes procedures. |
| 13 | Delegation protocol: firstmate-style document exchange | Internal delegation between conversation and worker models uses firstmate pattern: document exchange, backlog, artifacts after each task. Not integration with firstmate — pattern reuse. |
| 14 | Herdr integration: agent reads and interacts with herdr sessions | Separate from delegation. Agent looks into herdr panes, reads output, sends text. What's inside doesn't matter. |
| 15 | All access levels configurable via config, not hardcoded | Terminal, herdr, files, MCP, and any tool with granular access — all levels (read-only / read+write / full / disabled) set in config.yaml. Architectural principle. |

## Epics

### E1 — Agent Config & File System Foundation
Copy Hermes approach: config.yaml with hot-reload, file structure (~/.voice-agent/ with skills/, sessions/, memory/, SOUL.md). Study Hermes config parameters, select applicable ones, add voice-agent-specific params. **Architectural principle: all access levels to tools and functions are configurable, not hardcoded.** Each tool has a config section with access level (read-only / read+write / full / disabled). Only essential params for current epics.

### E2 — Tool-Calling Framework
OpenAI-format tool schemas, dispatch, result injection into conversation model (LFM2.5). Base loop for all tools.

### E3 — Terminal Tool + Approvals
Shell execution on host, command classification (read-only / destructive / blocked), Telegram gateway for approvals. MCP for Linux management. Access level via config.

### E4 — Web Search
web_search + web_extract tools. Quick searches — conversation model directly. Deep research — via delegation to worker.

### E5 — Delegation System
Delegation to cloud worker model, async execution, UI indicators for background tasks. Internal protocol between conversation and worker models follows firstmate pattern: document exchange, backlog, artifacts after each task.

### E6 — Skills System
SKILL.md storage, context loading, worker creates skills, conversation model triggers creation.

### E7 — Memory System
Persistent memory store, injection into turns, background post-session process for fact extraction via worker.

### E8 — Session Management
Context compression (auto + voice), new session (voice + auto per config), persistence/search.

### E9 — SOUL.md + Config/SOUL Self-Modification
Worker modifies config and SOUL.md via delegation. Hot-reload picks up changes.

### E10 — Herdr Integration
Read and interact with herdr sessions. Access level via config (read-only / read+write / full).

### E11 — CLI Tools + Docs + Skill
CLI commands for all operations (agent config, agent skills, agent memory, agent cron). Dual purpose: agent via tool calling, user via shell. Documentation. Hermes-style skill for self-management.

## Deferred

- **Proactivity Layer** — initiative, suggestions, cron. Deferred until agent capabilities (E2–E8) are stable.

## Phases

**Phase 1 — Foundation** (base without which nothing works)
- E1 → E2

**Phase 2 — Agent Capabilities** (agent starts acting)
- E3 → E4 → E5

**Phase 3 — Agent Intelligence** (agent learns and remembers)
- E6 → E7 → E8

**Phase 4 — Self-Management & External** (agent manages itself and environment)
- E9 → E10 → E11
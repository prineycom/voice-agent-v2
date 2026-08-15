# Design Gate V — Grill session results

> **Date:** 2026-08-14
> **Issue:** [#8](https://github.com/prineycom/voice-agent-v2/issues/8)
> **Mode:** Product decisions by Pasha, technical implementation by agent
> **Status:** All decisions accepted

## Summary

Grill session expanded the original avatar-only Design Gate V scope to cover the entire browser client UI. 14 product-level questions were resolved. Two new ADRs are created: ADR-0011 (UI shell architecture) and ADR-0012 (visual design system). This document is the authoritative grill protocol.

## Decisions

### D1 — Interface language

**Decision:** English for all UI labels, statuses, and controls. Dialogue content (user speech, agent response) in the conversation language (Russian by default).

**Rationale:** Cyberpunk aesthetic fits English labels (CONNECTING, LISTENING, SPEAKING). Short English labels are more compact for a minimalist screen. Content stays native.

### D2 — Main screen layout

**Decision:** Full-screen avatar-first. Avatar canvas occupies 100vw × 100vh. UI elements (status, speech text, microphone, menu) are compact overlays with z-index above canvas.

**Rationale:** Maximum immersion. Avatar is the visual hero. UI chrome stays minimal and edge-anchored. Works naturally on portrait-vertical small screens (Raspberry Pi display, mobile).

### D3 — Conversation history panel

**Decision:** Slide-in overlay from right edge. Semi-transparent with blur over avatar. Does not replace main screen. Toggle via menu or edge swipe.

**Rationale:** Preserves avatar context. Cyberpunk slide-in panel is a natural pattern. Compact on small screens.

### D4 — Detailed status panel

**Decision:** Tabbed panel (slide-in overlay). Tab 1: System — live component status (STT, LLM, TTS, LiveKit, avatar), configuration overview. Tab 2: Timeline — per-turn breakdown (endpoint → STT → LLM → TTS → completion) + diagnostics download.

**Rationale:** Per-turn timeline for debugging, live status for "what's happening now". Tabs are compact. Config is part of system tab, not a separate panel.

### D5 — Connection status overlay behavior

**Decision:** Minimal. Startup: only connection status ("CONNECTING…" → "READY" → disappears). Disconnect: "CONNECTION LOST" + reconnect button. Compact corner indicator only when connected and healthy.

**Rationale:** No clutter. Startup is transient, disconnect is actionable. Corner indicator is the steady-state.

### D6 — Microphone button

**Decision:** Icon-only floating button, bottom-center. State by color and pulse: neutral=idle, cyan-pulse=listening, dimmed=muted, red-flicker=error. No text label.

**Rationale:** Minimalist. State conveyed visually. Natural touch target. No text = less screen space.

### D7 — Menu structure

**Decision:** Minimal dropdown from hamburger icon (top corner). Items: DISCONNECT, HISTORY (toggle), STATUS (toggle), REDUCE MOTION (toggle). Diagnostics download lives in status panel.

**Rationale:** 4 items = minimal. Power features one tap away but not on main screen.

### D8 — Agent speech output on main screen

**Decision:** Full current turn response text as semi-transparent bottom overlay. Scrolls if long. Fades out when turn completes. Full history in history panel, not on main screen.

**Rationale:** User sees the full current answer, not just last fragment. Already supported by `state.response`. History doesn't clutter the main screen.

### D9 — Visual design language

**Decision:** Neon minimal. Dark base (`#0a0e1a`), neon accents (cyan `#00f0ff`, magenta `#ff00aa`), glow effects on active elements, monospace uppercase labels, subtle scanlines (opacity 0.03). CSS-only effects.

**Rationale:** Cyberpunk without clutter. Glow + monospace = instantly readable as cyberpunk. CSS-only = no performance concern. See ADR-0012 for full palette and state map.

### D10 — Avatar viewport

**Decision:** Full-viewport canvas (100vw × 100vh). Avatar host owns the entire viewport. UI overlays on top.

**Rationale:** Avatar gets maximum space for ambient effects, glow, pulse. UI elements don't compete.

### D11 — Avatar host / UI shell boundary

**Decision:** UI shell provides a container `<div>`. Avatar host mounts into it and owns everything inside. UI communicates only through avatar host API. Zero UI knowledge of avatar internals.

**Rationale:** Directly enforces ADR-0002. Replacing eye → Live2D → 3D = zero UI shell changes. Clean contract boundary.

### D12 — Reduced motion

**Decision:** Two levels. (1) System `prefers-reduced-motion: reduce` — suppresses scanlines, glow pulsing, panel animations, avatar ambient motion. Colors remain with instant transitions. (2) UI toggle (menu → REDUCE MOTION) — suppresses avatar animation entirely (static frame). Both can be active. UI toggle persists in localStorage.

**Rationale:** Accessibility baseline (system) + user preference (toggle). Some want cyberpunk aesthetics without avatar motion.

### D13 — Target viewport

**Decision:** Mobile-first, portrait-vertical primary. Canonical desktop target: Raspberry Pi with small vertical display. Landscape desktop secondary — layout scales, no separate design.

**Rationale:** Deployment is a private single-user voice agent, potentially on a Raspberry Pi with a small attached display in portrait orientation. Full-screen avatar-first layout naturally fits vertical screens.

### D14 — Diagnostics

**Decision:** Diagnostics download button lives inside the status panel (Timeline tab), not on the main screen or in the menu.

**Rationale:** Diagnostics data is related to per-turn timeline. Keeps menu minimal.

## New ADRs

- [ADR-0011: UI shell architecture](../adr/0011-ui-shell-architecture.md) — component hierarchy, panel model, avatar host integration
- [ADR-0012: Visual design system](../adr/0012-visual-design-system.md) — neon minimal palette, state map, reduced motion, performance budget

## Updated issue

Issue #8 updated with expanded scope covering avatar module + full UI. See: https://github.com/prineycom/voice-agent-v2/issues/8

## Slice 7 implications

The grill results define the design boundary for Slice 7 implementation:

- UI shell: full-screen avatar container + overlay components (status, speech, mic, menu)
- Two slide-in panels: history (right edge), status (tabbed, can stack)
- Avatar host: mounts into full-viewport container, owns rendering
- Visual system: neon minimal, CSS-only, tokenized palette, monospace labels
- State mapping: existing `VoiceState` + `voiceReducer` drive UI transitions
- Reduced motion: two-level (system + UI toggle)
- Mobile-first portrait layout, no separate desktop design

## Manual-acceptance amendment — explicit real-session admission

Pasha's PR #20 manual test showed that a synthetic review entry at the stable URL could falsely appear ready while bypassing microphone permission, LiveKit, and genuine history. The live entry therefore starts `DISCONNECTED` with one `CONNECT` action; only that gesture may request the capability/microphone and advance through `CONNECTING` to `READY`. Synthetic fixture data remains test-build-only and is never served as the manual acceptance stand. This amendment supersedes D5's automatic startup transition for the live product without changing its minimal overlay language.
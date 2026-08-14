# ADR-0011: UI shell architecture — full-screen avatar-first, panel overlays

- **Status:** Accepted
- **Date:** 2026-08-14
- **Decision owner:** Voice Agent v2 project architecture

## Context

Slice 6 shipped a linear text-first React UI: a header, a 2-column grid with connection card and conversation history, a controls section with inline buttons, and a footer. The UI has no avatar, no menu, no collapsible panels, and all controls are permanently visible on the main screen.

Design Gate V expands the scope to the entire browser client UI. The product target is a compact, minimalist, cyberpunk-styled interface where the avatar is the primary visual presence and everything else recedes into overlays and panels.

The deployment context is mobile-first, portrait-vertical primary: a small Raspberry Pi display in vertical orientation is the canonical desktop target, with mobile browsers as the primary design target. Landscape desktop is secondary.

## Decision

### Main screen layout

The main screen is a **full-viewport avatar canvas** (100vw × 100vh). The avatar host owns the entire viewport; UI elements are compact overlays positioned at screen edges with z-index above the canvas.

Overlay elements on the main screen:

- **Connection status indicator** — compact dot or short text in a top corner. Only visible when connected (ready). Replaced by full-screen overlay on startup and disconnect.
- **Agent speech output** — the full current turn response text, displayed as a semi-transparent bottom overlay. Scrolls if long. Fades out when the turn completes. Not the full conversation history — only the current turn.
- **Microphone button** — icon-only floating button, bottom-center. No text label. State conveyed by color and pulse: neutral=idle, pulsing-cyan=listening, dimmed=muted, red-flicker=error.
- **Menu button** — icon (hamburger) in top corner. Opens a minimal dropdown.

### Startup and disconnect overlays

- **Startup overlay**: minimal — only connection status ("CONNECTING…", then "READY"). Disappears on ready. Full-screen, cyberpunk-styled, but no progress indicators or extra text.
- **Disconnect overlay**: "CONNECTION LOST" + reconnect button. Full-screen, replaces the main screen.

### Menu structure

Minimal dropdown, triggered by hamburger icon in top corner. Items:

- DISCONNECT (action)
- HISTORY (toggle — opens/closes conversation history panel)
- STATUS (toggle — opens/closes detailed status panel)
- REDUCE MOTION (toggle — suppresses avatar animation)

Diagnostics download lives inside the status panel, not in the menu.

### Conversation history panel

Slide-in overlay from the right edge. Semi-transparent background with blur over the avatar. Does not remove the main screen. Toggle via menu HISTORY item or right-edge swipe (touch). Close via swipe-back or menu toggle.

### Detailed status panel

Tabbed panel, also slide-in overlay (can stack or replace history panel):

- **Tab 1: System** — live status of each component (STT, LLM, TTS, LiveKit, avatar module): ready/degraded/failed. Current configuration overview (provider, model, TTS profile). Resource indicators if available.
- **Tab 2: Timeline** — per-turn breakdown for the last selected turn: endpoint → STT final → LLM first token → TTS first audio → completion. Plus a DOWNLOAD DIAGNOSTICS button.

### Avatar host integration

The UI shell knows nothing about avatar internals. The UI provides a full-viewport `<div>` container element. The avatar host mounts into this container and owns everything inside it. The UI communicates only with the avatar host API (lifecycle state, speech envelope, cancellation). Replacing the MVP eye with Live2D or 3D requires zero changes to the UI shell.

This directly enforces the ADR-0002 boundary: "the UI shell owns layout; the avatar host owns the avatar viewport only."

### Responsive behavior

Primary target: mobile-first, portrait-vertical. The full-screen avatar-first layout naturally adapts to small vertical viewports. Overlay elements are compact and anchored to edges. On larger landscape screens, the layout scales up — avatar stays full-viewport, overlays remain edge-anchored. No separate desktop layout is designed.

### State-to-UI mapping

Control events from `realtime-control.v2` map to UI state transitions through the existing `voiceReducer`. The UI shell consumes `VoiceState` and derives:

- Main screen phase indicator (idle/listening/thinking/speaking) → avatar state + ambient color
- `state.response` → bottom overlay text (current turn response)
- `state.history` → history panel content
- `state.connection` → startup overlay / corner indicator / disconnect overlay
- Per-turn metrics from `TurnHistoryItem` → timeline tab in status panel
- Component health (forward-looking from Slice 8) → system tab in status panel

## Consequences

### Positive

- Avatar is the visual hero; UI chrome stays out of the way.
- Panel overlays preserve context — the avatar is never fully hidden.
- Avatar module swappability is clean: UI shell is renderer-agnostic by construction.
- Mobile-first portrait layout works on Raspberry Pi vertical displays.
- Menu stays minimal; power features (diagnostics, status, history) are one tap away but not cluttering.

### Costs and risks

- Full-viewport canvas + overlay UI requires careful z-index and pointer-event management.
- Slide-in panels on small screens may feel tight; blur/transparency must be tested for readability.
- Two-level reduced-motion (system + UI toggle) adds a small state surface.
- Per-turn timeline in status panel depends on metrics already collected but may need additional correlation for the system tab (Slice 8 observability).

## Alternatives considered

- **Card-based layout (current Slice 6):** rejected — not immersive, too much visible chrome, not cyberpunk.
- **Split screen (avatar top, controls bottom):** rejected — wastes vertical space on portrait screens, breaks immersion.
- **UI knows about canvas rendering (shared canvas):** rejected — couples UI shell to avatar renderer, breaks swappability.
- **Full-screen menu panel:** rejected — too heavy for 4 items; dropdown is sufficient.

## Provenance

- Slice 6 UI: `web/src/App.tsx`, `web/src/state.ts`, `web/src/VoiceSessionContext.tsx`
- ADR-0002: renderer-agnostic avatar boundary
- Architecture §5.2: avatar-boundary constraints
- Architecture §3.2: browser client and avatar module logical roles
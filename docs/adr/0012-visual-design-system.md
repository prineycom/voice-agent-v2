# ADR-0012: Visual design system — neon minimal cyberpunk

- **Status:** Proposed
- **Date:** 2026-08-14
- **Decision owner:** Voice Agent v2 project architecture

## Context

The Slice 6 UI uses a dark theme (`#080b13` background, `#eef2ff` text, `#101521` cards) with blue accents. It is clean but visually neutral — no cyberpunk identity. Design Gate V requires a minimalist cyberpunk aesthetic for the full-screen avatar-first UI.

The deployment target is mobile-first, portrait-vertical (small Raspberry Pi display or phone). The visual system must be readable on small screens, performant on limited hardware, and accessible.

## Decision

### Visual language: neon minimal

Cyberpunk expressed through neon accents, glow, and monospace typography — not through clutter, glitch, or heavy animation.

#### Palette

| Token | Value | Usage |
| --- | --- | --- |
| `--bg-base` | `#0a0e1a` | Full-screen background |
| `--bg-overlay` | `rgba(10, 14, 26, 0.85)` | Panel backgrounds (semi-transparent) |
| `--text-primary` | `#eef2ff` | Primary text |
| `--text-secondary` | `#8490aa` | Secondary text, labels |
| `--accent-cyan` | `#00f0ff` | Primary accent: ready, listening, active states |
| `--accent-magenta` | `#ff00aa` | Secondary accent: thinking, processing |
| `--accent-amber` | `#ffd166` | Warning: connecting, reconnecting |
| `--accent-red` | `#ff3b5c` | Error: failed, disconnected, muted |
| `--accent-green` | `#49d79c` | Success: ready, connected |
| `--border-subtle` | `rgba(0, 240, 255, 0.15)` | Thin dividers, panel edges |
| `--glow-cyan` | `0 0 20px rgba(0, 240, 255, 0.4)` | Glow for active elements |
| `--glow-magenta` | `0 0 20px rgba(255, 0, 170, 0.4)` | Glow for processing elements |

#### Typography

- **UI labels / status text**: monospace font (system monospace stack: `'JetBrains Mono', 'Fira Code', ui-monospace, monospace`). Uppercase, letter-spacing `0.08em`. Small sizes (0.7–0.8rem).
- **Agent/user speech content**: sans-serif (system sans stack). Readable, 1rem–1.1rem. Not monospace — content should feel natural.
- **Language**: all UI labels in English. Dialogue content (user speech, agent response) in the language of the conversation (Russian by default).

#### Effects

- **Glow**: `box-shadow` and `text-shadow` using accent colors at low opacity. Applied to status indicators, active buttons, active phase labels. Not applied to body text.
- **Scanlines**: subtle `::before` overlay on the root container, `repeating-linear-gradient` at `opacity: 0.03`. Adds atmosphere without harming readability.
- **Thin borders**: 1px solid `--border-subtle` for panel edges and dividers. No thick borders, no rounded cards with heavy backgrounds.
- **Panel blur**: `backdrop-filter: blur(12px)` on slide-in panels for depth.

#### State-specific visual language

| State | Color | Glow | Motion |
| --- | --- | --- | --- |
| idle | `--text-secondary` | none | none |
| listening | `--accent-cyan` | cyan glow | pulse (slow, 2s) |
| thinking | `--accent-magenta` | magenta glow | pulse (fast, 1s) |
| speaking | `--accent-green` | green glow | pulse synced to speech envelope |
| error/failed | `--accent-red` | red glow | flicker (short, 0.5s) |
| reconnecting | `--accent-amber` | amber glow | pulse (medium, 1.5s) |
| degraded | `--accent-amber` | amber glow (dim) | none |

#### Microphone button visual states

| State | Color | Motion |
| --- | --- | --- |
| idle (mic on, not listening) | `--accent-cyan` (dim) | none |
| listening | `--accent-cyan` | pulsing glow |
| muted | `--text-secondary` | none, icon dimmed |
| error | `--accent-red` | short flicker |

### Performance budget

- All effects are CSS-only (box-shadow, text-shadow, backdrop-filter, linear-gradient). No WebGL for UI chrome.
- Scanline overlay is a single pseudo-element, not a render loop.
- `backdrop-filter: blur()` is applied only to visible panels, not full-screen.
- Avatar rendering budget is owned by the avatar module, not the UI shell.
- Target: UI chrome renders at 60fps on Raspberry Pi browser. Effects must degrade gracefully — if `backdrop-filter` is unsupported, panels fall back to `--bg-overlay` solid color.

### Reduced motion: two levels

1. **System level** (`prefers-reduced-motion: reduce`): suppresses scanlines, glow pulsing, panel slide animations, avatar ambient motion. Colors remain (instant transitions, no animation). Static glow halos stay as box-shadow without keyframe animation. This is the accessibility baseline.

2. **UI toggle** (menu → REDUCE MOTION): suppresses avatar animation entirely (static eye/frame). Glow halos remain as static shadows. UI transitions are instant. Scanlines off. This is user preference, independent of system setting.

Both levels can be active simultaneously. The UI toggle is persistent across sessions (localStorage).

## Consequences

### Positive

- Cyberpunk identity without visual clutter — neon + monospace + glow is immediately readable as cyberpunk.
- CSS-only effects = no performance concern from UI chrome.
- Two-level reduced motion covers both accessibility and user preference.
- Palette is tokenized — easy to adjust or theme later.
- Monospace labels are compact and fit the minimalist screen.

### Costs and risks

- `backdrop-filter` support must be verified on the Raspberry Pi browser target.
- Glow effects on low-DPI small screens may look different than designed — visual review required on target hardware.
- Monospace fonts may render differently across browsers — a web font (JetBrains Mono) should be bundled for consistency if system monospace is unreliable.

## Alternatives considered

- **Glitch/HUD (option B from grill):** rejected — adds visual noise, HUD frames compete with avatar for attention. Can be added later as optional.
- **Dark industrial / retro terminal (option C from grill):** rejected — green-amber palette and CRT effects are a different aesthetic direction; neon minimal is more modern cyberpunk.
- **No scanlines:** considered — scanlines at 0.03 opacity add atmosphere without harming readability; removing them loses subtle cyberpunk texture.

## Provenance

- Grill session 2026-08-14, question 9 (visual language), question 12 (reduced motion)
- Slice 6 styles: `web/src/styles.css`
- Architecture §5.2: avatar-boundary constraints
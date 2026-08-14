# Slice 7 UI/avatar bounded validation

> **Scope:** automated renderer/UI boundaries plus a short headless portrait browser review. This is not Raspberry Pi, physical audio, tailnet second-client, or complete local-inference acceptance.

## Contract and deterministic evidence

Run from the repository root:

```sh
./verify-slice7
```

The focused command covers:

- `voice-agent.avatar-host.v1` capability/compatibility, monotonic input, cancellation, explicit degraded fallback, and bounded invalid-signal behavior;
- deterministic eye replay, pupil/blink/ring bounds, thinking loader, tracking/idle precedence, decoded-playout-only speech pulsing, interruption-to-neutral, and both motion levels;
- reducer-to-UI state mapping and exactly four steady overlay responsibilities;
- exact four-item menu, history/status panels, icon-only microphone state, and persisted reduced-motion choice;
- actual published-track microphone mute/unmute, rapid-toggle coalescing, effective failure state, assistant-playback independence, and muted transient reconnect;
- decoded PCM time-domain envelope normalization plus the existing reconnect/interruption/playback contract tests;
- TypeScript production build.

The normalized fixed eye fixture has FNV-1a 32-bit replay hash `acba732d`. This is a small regression identifier for the serialized render-state fixture, not a cryptographic asset identity.

## Portrait browser review

The review fixture is the production shell/components with a clearly labelled in-memory fixture; it does not request a LiveKit capability or claim a voice turn. Chrome for Testing `152.0.7977.42` was attached through `chrome-devtools-axi` / the Chrome DevTools protocol at `480 × 800`.

Observed:

- viewport and document were both exactly `480 × 800`, with no page overflow;
- the avatar viewport filled the full viewport;
- exactly one compact connection indicator, speech overlay, microphone control, and menu overlay were present;
- the menu exposed exactly `DISCONNECT`, `HISTORY`, `STATUS`, and `REDUCE MOTION`;
- history and status alternated as right-edge panels; status exposed `SYSTEM` and `TIMELINE` tabs;
- microphone state changed from `Mute microphone` to `Unmute microphone`;
- the user reduced-motion preference persisted as `voice-agent.reduce-motion.v1=true` and reached the static module input;
- no application JavaScript exception appeared. A missing favicon response from the temporary standard-library HTTP server and one browser-internal subsystem log were non-product observations.

The committed portrait capture is [`slice-7-review-portrait.png`](slice-7-review-portrait.png) (`480 × 800`, SHA-256 `ce97967b5f86522f74a8b16f6442b21e637d6e19b100085b3fb12c16819adfdc`) and renders implementation commit `4f5c377b6ea424c9e5a5f7d88422a5a3181189fa`. The active review stand shows `REVIEW <12-char-head>` in the corner and the complete compiled head in **STATUS → SYSTEM → BUILD**.

## Review stand

The isolated launcher builds the current clean commit, binds the static server only to `127.0.0.1:4177`, and owns one reversible foreground Tailscale Serve route on HTTPS `8447`. It does not start inference, reuse the production gateway, change the existing HTTPS/443 mapping, modify firewalld, or expose a public route.

```sh
./run-review-stand start
./run-review-stand status
./run-review-stand restart
./run-review-stand stop
```

Tailnet URL: `https://priney-arch.darter-smoot.ts.net:8447/?review=1`

## Explicit gaps

Not tested or claimed here:

- physical Raspberry Pi browser 60-fps or resource performance;
- physical microphone, decoded-speaker audibility, or Kseniya quality;
- complete llama.cpp + Whisper + Silero + LiveKit session and real speech-envelope visual review;
- physical rapid barge-in/return-to-neutral timing;
- second tailnet browser acceptance;
- human approval beyond the recorded headless visual inspection.

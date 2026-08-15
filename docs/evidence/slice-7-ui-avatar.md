# Slice 7 UI/avatar bounded validation

> **Scope:** automated renderer/UI boundaries plus functional browser/LiveKit checks. Historical portrait fixture evidence is retained below but is not the live acceptance entry. This is not Raspberry Pi, physical audio, or second-client acceptance.

## Contract and deterministic evidence

Run from the repository root:

```sh
./verify-slice7
```

The focused command covers:

- `voice-agent.avatar-host.v1` capability/compatibility, monotonic input, cancellation, explicit degraded fallback, and bounded invalid-signal behavior;
- deterministic eye replay, pupil/blink/ring bounds, thinking loader, tracking/idle precedence, decoded-playout-only speech pulsing, interruption-to-neutral, and both motion levels;
- reducer-to-UI state mapping and exactly four steady overlay responsibilities;
- exact four-item menu, history/status panels, icon microphone control with a visible lifecycle label, and persisted reduced-motion choice;
- actual published-track microphone mute/unmute, rapid-toggle coalescing, effective failure state, assistant-playback independence, and muted transient reconnect;
- decoded PCM time-domain envelope normalization plus the existing reconnect/interruption/playback contract tests;
- explicit disconnected/CONNECT admission, empty pre-session history, microphone permission/publication lifecycle, and separated production/test-fixture builds;
- TypeScript production and dedicated fixture builds.

The normalized fixed eye fixture has FNV-1a 32-bit replay hash `acba732d`. This is a small regression identifier for the serialized render-state fixture, not a cryptographic asset identity.

## Historical test-only portrait fixture

This evidence predates Pasha's real-session acceptance feedback. The fixture uses production shell components with a clearly labelled in-memory state; it does not request a LiveKit capability or claim a voice turn. It now builds only under ignored `web/review/dist` and is never exposed at the stable review URL. Chrome for Testing `152.0.7977.42` was attached through `chrome-devtools-axi` / the Chrome DevTools protocol at `480 × 800`.

Observed:

- viewport and document were both exactly `480 × 800`, with no page overflow;
- the avatar viewport filled the full viewport;
- exactly one compact connection indicator, speech overlay, microphone control, and menu overlay were present;
- the menu exposed exactly `DISCONNECT`, `HISTORY`, `STATUS`, and `REDUCE MOTION`;
- history and status alternated as right-edge panels; status exposed `SYSTEM` and `TIMELINE` tabs;
- microphone state changed from `Mute microphone` to `Unmute microphone`;
- the user reduced-motion preference persisted as `voice-agent.reduce-motion.v1=true` and reached the static module input;
- no application JavaScript exception appeared. A missing favicon response from the temporary standard-library HTTP server and one browser-internal subsystem log were non-product observations.

The committed historical capture is [`slice-7-review-portrait.png`](slice-7-review-portrait.png) (`480 × 800`, SHA-256 `ce97967b5f86522f74a8b16f6442b21e637d6e19b100085b3fb12c16819adfdc`) and renders implementation commit `4f5c377b6ea424c9e5a5f7d88422a5a3181189fa`. It is fixture evidence only, not proof of the active voice path.

## Live review stand

The launcher builds the current clean commit's ordinary production entry, verifies its embedded SHA, copies it to an immutable ignored per-run directory, then reuses the safe Slice 6 foreground runtime: local LFM, LiveKit, gateway/controller, STT/TTS, loopback gateway `8000`, and explicit owned Tailscale Serve routes. The stable application route overrides only the configured application origin/port to HTTPS `8447`; LiveKit's public signaling URL remains operator-configured and is returned through the typed capability. It does not change HTTPS/443, firewalld, public exposure, or unrelated services.

The page starts `DISCONNECTED`; **CONNECT** is the user gesture that requests the same-origin session capability, joins the capability's validated public LiveKit endpoint, requests microphone permission, and publishes the microphone. History is empty until genuine server control events. The microphone overlay reports disconnected, permission, publication, live/listening, muted, or error state explicitly.

```sh
./run-review-stand start
./run-review-stand status
./run-review-stand restart
./run-review-stand stop
```

Tailnet URL: `https://priney-arch.darter-smoot.ts.net:8447/`

## Explicit gaps

Not tested or claimed here:

- physical Raspberry Pi browser 60-fps or resource performance;
- physical microphone, decoded-speaker audibility, or Kseniya quality;
- physical end-to-end llama.cpp + Whisper + Silero + LiveKit speech/listening acceptance (the deployed runtime is real, while automation uses synthetic audio/inference fixtures);
- physical rapid barge-in/return-to-neutral timing;
- second tailnet browser acceptance;
- human approval beyond the recorded headless visual inspection.

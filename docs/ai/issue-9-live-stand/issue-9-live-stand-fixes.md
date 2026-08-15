# Fix Report: Issue #9 live review stand

**Source:** Pasha's manual acceptance feedback on PR #20
**Status:** ⚠️ implementation and live runtime validated; no-mistakes/CI follow-up pending
**Scope stayed small:** yes — deployment entry/session admission and microphone-state repair on the existing branch/PR

## Reproduction and diagnosis

### Trigger

`./run-review-stand` built Vite in `review` mode and exposed the dedicated `ReviewStand` through a standard-library static server on the stable tailnet URL. That entry constructs a synthetic `ready` `VoiceState`; it never mounts `VoiceSessionProvider` or `VoiceClient`.

### Masking condition

The synthetic state pre-populated history, response, TTS/LLM metadata, and `microphoneEnabled=true`. It therefore made the UI look connected while bypassing `/api/session`, LiveKit join, `createLocalAudioTrack`, publication, server control events, and assistant media attachment.

### Visible symptoms reproduced

On `https://priney-arch.darter-smoot.ts.net:8447/` before the fix:

- title and entry were `Voice Agent v2 Review` / the synthetic review bundle;
- status was `READY · REVIEW 25a762e38cc2`;
- history contained four user/agent strings from `ReviewStand` that Pasha never sent or received;
- microphone permission remained `prompt` and no permission request occurred;
- browser resources contained only HTML/JS/CSS, with no `/api/session` or LiveKit request;
- `POST /api/session` returned static-server HTTP 501 and `GET /api/status` returned 404;
- only the static listener `127.0.0.1:4177` existed; gateway `8000`, LiveKit `7880`, and local LFM `18080` were absent.

### Smallest counterfactual and disconfirming evidence

The ordinary production entry (`web/src/main.tsx`) mounts `VoiceSessionProvider` and `App`; `VoiceClient.start()` performs same-origin `POST /api/session`, joins the capability's validated public LiveKit URL, creates/publishes the microphone, consumes genuine control events, and attaches current-generation response media. The existing Firefox/official-LiveKit test exercises that path successfully with fake media/inference. Required local runtime/config/artifacts were present. This disconfirms an eye-renderer, reducer-history, or microphone-toggle cause: the deployment selected the wrong entry and omitted the entire gateway/runtime.

## Changed behavior

- Stable review URL: synthetic static review entry → exact-head production bundle served by the real gateway/controller/LiveKit/local-inference runtime.
- Startup: automatic/synthetic READY → modal `DISCONNECTED` with explicit `CONNECT`.
- Admission: no API/media work before a user gesture; `CONNECT` requests the same-origin capability, joins its validated public LiveKit endpoint, then requests and publishes the microphone.
- History: fixture messages → empty until accepted server control events.
- Microphone: ambiguous icon state → explicit `MIC DISCONNECTED`, `MIC PERMISSION`, `MIC PUBLISHING`, `MIC LIVE`/`MIC LISTENING`, `MIC MUTED`, or `MIC ERROR` state alongside the icon-only control.
- Fixture build: overwrote `web/dist` and was tailnet-routed → separate `web/review/dist` test-only build, never used by the live stand.

## Files changed

| File | Change |
| --- | --- |
| `web/src/App.tsx` | Remove automatic connection; preserve explicit reconnect/disconnect intent. |
| `web/src/ui/overlays/ConnectionOverlays.tsx` | Add explicit disconnected CONNECT modal. |
| `web/src/state.ts`, `web/src/voiceClient.ts`, `web/src/VoiceSessionContext.tsx` | Carry permission/publication/live/muted/error microphone lifecycle. |
| `web/src/ui/stateMapping.ts`, `web/src/ui/controls/MicrophoneButton.tsx`, `web/src/styles.css` | Render unambiguous microphone status without changing renderer ownership. |
| `web/vite.config.ts`, `verify-slice7` | Separate production/review outputs and assert generated-entry divergence. |
| `run-slice6`, `run-review-stand` | Reuse the safe full runtime at stable HTTPS/8447 with exact-head production build. |
| focused browser/unit tests | Cover connect gating, empty history, permission/publication states, and real path behavior. |

## Validation

| Command/check | Result | Notes |
| --- | --- | --- |
| `./verify-slice7` | PASS | Focused browser/unit checks; production and isolated fixture builds; generated entry divergence. |
| `./verify-slice6` | PASS | Existing deterministic controller plus Firefox/official-LiveKit microphone/VAD, control events, reconnect, response PCM/playout, history/status, and diagnostics. Uses synthetic microphone/audio and deterministic inference; no physical audibility claim. |
| Stable URL pre-CONNECT functional check | PASS | Production title/entry, explicit CONNECT, empty history, `MIC DISCONNECTED`, permission `prompt`, and no pre-connect API/LiveKit request. No aesthetic visual judgment. |
| Stable API/runtime check | PASS | HTTPS 200; exact candidate bundle; gateway/status available; scoped capability returned public `wss://priney-arch.darter-smoot.ts.net:7443`, verified LLM/TTS identities, and a token without exposing it; unjoined capacity recovered. |
| Runtime listeners/routes | PASS | Gateway `127.0.0.1:8000`, LiveKit `127.0.0.1:7880`, local LFM `127.0.0.1:18080`, owned tailnet HTTPS `8447` application and `7443` signaling. HTTPS/443/firewall unchanged. |
| no-mistakes and CI | PENDING | Run after the follow-up commits, then redeploy exact final PR head. |

No aesthetic visual review was performed.

## Follow-ups

- Pasha must perform the final physical microphone and audible-response check; synthetic audio can prove server events/PCM plumbing but not real microphone acoustics or physical audibility.

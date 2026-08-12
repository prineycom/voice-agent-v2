# Slice 6 LiveKit media and interruption evidence

- **Issue:** [#7](https://github.com/prineycom/voice-agent-v2/issues/7)
- **Checkpoint date:** 2026-08-11
- **Status:** Partial — deterministic/runtime/bind evidence passes; physical-browser acceptance remains open
- **Legacy source:** None inspected or used

This report distinguishes executable evidence from the microphone, listening, remote-device, timing, and resource observations that do not yet exist. It does not claim final Slice 6 acceptance.

## Implemented stack and boundaries

| Boundary | Pinned implementation | Evidence |
| --- | --- | --- |
| Local media server | official LiveKit Server `1.13.5`; release archive SHA-256 `c020fac437b7cc9b776eef1ad5ea8af77be9acfa07602eca20a3a44930dfbc70` | Cached binary reports `livekit-server version 1.13.5`; current generated restricted config started successfully. |
| Server RTC/capability | official Python `livekit==1.1.14`, `livekit-api==1.2.0` | `scripts/verify_slice6_runtime.py` verifies installed versions and decodes the generated JWT without opening a socket. |
| Application glue | `fastapi==0.141.1`, `uvicorn==0.52.1` | Loopback-only gateway smoke returned exactly `{"available":true,"session_limit":1}` with no-store, CSP, microphone Permissions-Policy, and no provider endpoint. |
| Browser | React `19.2.8`, TypeScript `7.0.2`, Vite `8.2.1`, official `livekit-client` `2.21.0` | Locked npm install, typecheck, then-current Vitest suite, and production build passed at the recorded checkpoint. |
| Inference | Existing `RealTurnController`, Whisper large-v3-turbo, only LiteLLM alias `deepseek-v4-flash`, Qwen3 CustomVoice/`ryan` | No replacement pipeline was added. Deterministic tests use fakes; no new private transcript was sent for this checkpoint. |

`./setup-slice6` keeps the LiveKit binary/Python environment in the ignored user cache and browser dependencies/build output in ignored directories. `.env.slice6`, signing material, the provider token, models, model caches, recordings, transcripts, generated audio, and runtime logs remain outside Git. The foreground runner gives the LiveKit process only its config/signing pair, gives the gateway/controller the server configuration it owns, and removes LiveKit/LiteLLM server configuration from Tailscale Serve child environments.

The browser capability is limited to one generated room, one generated browser identity, microphone publication, agent subscription, and data publication for 300 seconds. Issuance requires the exact loopback or configured tailnet application `Origin`; this blocks cross-site session consumption without adding a second identity system. Decoded grants contain no room create/list/admin/record capability. The browser response has a closed seven-field shape: `session_id`, `stream_epoch`, `livekit_url`, `token`, `expires_in_seconds`, `admission_timeout_ms`, and `control_version`; it cannot contain the LiteLLM endpoint, provider token, or LiveKit signing secret.

## Automated verification

### Root network-denied suite

Command:

```sh
./verify
```

Result: **PASS** at the recorded checkpoint. Python's audit policy denied IP sockets while allowing only asyncio's local AF_UNIX wake-up pair. The then-current suite included fake-inference realtime cases for:

- correlated success through audio delivery;
- disconnect cancellation without completion;
- duplicate, late, wrong-session, wrong-turn, wrong-version, out-of-order, oversized, and malformed control rejection;
- barge-in with one old `turn.interrupted`, source clear, serialized cancellation, correlated fresh-media readiness before replacement inference, a replacement completion, and no post-replacement old event;
- completion/new-turn admission ordering;
- reconnect epoch advance, stale-media discard, in-memory context reset, duplicate/malformed client-control rejection, and closed-session degradation;
- control-publication failure cancellation, worker drain, context rollback, and transport-session closure.

The deterministic interruption payload declares `drain_bound_ms=250`; the measured fake sink drain is asserted not to exceed that bound. This is not a physical microphone-to-speaker timing result.

### Installed runtime and browser boundary

Command:

```sh
./verify-slice6
```

Result: **PASS** after `./setup-slice6`:

- installed versions: `livekit=1.1.14`, `livekit-api=1.2.0`, `fastapi=0.141.1`, `uvicorn=0.52.1`;
- decoded capability: one room, microphone publish, subscribe/data, no management grants;
- Python runtime check: no socket opened and no model/provider/microphone/browser used;
- React/TypeScript typecheck: pass;
- the then-current Vitest suite passed;
- Vite production build: pass.

Browser tests cover the typed reducer/parser, exact capability response, lifecycle/epoch/sequence/turn gates, reconnect suppression, stale event rejection, cancellable startup, bounded readiness, agent loss, cleanup failure, fresh-subscription media invalidation, React shell, autoplay failure boundary, and explicit autoplay recovery. The installed runtime check also exercises cancellation during model startup, unclaimed-room expiry, and capacity retention after incomplete cleanup. The app intentionally remains one small bundle; Vite's size advisory is visible but is not hidden as a failure.

## Measured bind and port evidence

A current host smoke generated the server config through `livekit_server_config`, started the pinned binary, and inspected listeners with `ss`:

| Path | Configured address | Smoke result |
| --- | --- | --- |
| LiveKit signaling/API | `127.0.0.1:7880/tcp` | **Observed listening only on loopback.** |
| WebRTC UDP media | host Tailscale IPv4 (redacted) `:7882/udp`, interface `tailscale0` | **Observed listening on the tailnet address.** |
| ICE/TCP media | disabled (`tcp_port: 0`; conventional `7881` absent) | **No `7881` or `7882` TCP listener observed.** |
| Gateway | production config `127.0.0.1:8000/tcp` | Gateway behavior/security smoke passed on temporary loopback port `18006`; full runner port remains part of the physical gate. |
| Application HTTPS | explicit Tailscale Serve port, example `8443/tcp` | Configured, **not yet exercised by a second browser**. |
| Signaling HTTPS | explicit Tailscale Serve port, example `7443/tcp` | Configured, **not yet exercised by a second browser**. |
| TURN/public fallback | none | No implementation or listener added. |

The LiveKit JSON startup observation reported version `1.13.5`, loopback bind, the redacted tailnet node IP, and UDP start `7882`. This proves the server bind, not firewall traversal, certificate behavior, remote ICE selection, or audible media.

## Interruption and reconnect policy

- Endpointing uses 20 ms 16 kHz mono frames, 100 ms speech start, 600 ms trailing silence, 200 ms pre-roll, and a 15 s utterance maximum. It is a bounded CPU energy detector, not an unmeasured claim that Silero is unnecessary.
- Barge-in first terminalizes the old turn, clears the 100 ms LiveKit source queue, invalidates the browser's old audio subscription and admits only a freshly subscribed track, cancels the selected provider and process-isolated STT/TTS, rolls back undelivered LLM context, and serializes replacement work.
- During transport reconnect, the browser detaches audio and drops all old-epoch data. The server cancels/drains active work, resets all in-memory conversation context, advances the epoch, and only then reports ready. Cleanup/reset timeout produces `session.degraded` and closes turn admission.
- Provider failure or empty output remains a hard `turn.failed`; no alternate model, endpoint, alias, transport redirect, local provider, or fabricated answer exists.

## Preserved failed evidence and limitations

Nothing in this slice rewrites historical machine-readable benchmark results. The failed `deepseek-v4-flash` latency, reliability, fairness, isolation/empty-response, privacy/provenance, and cost gates remain in the existing Slice 2–5 evidence and architecture summaries. The current operator-authorized HTTP route is still plaintext above the application layer and operator-opaque; ADR-0006 permits only private Slice 6 testing and does not approve production security/privacy.

The Slice 5 observed peak of `7,600 MiB` VRAM and preserved reserve of `4,682 MiB` remain the ceiling. Deterministic/runtime/bind smokes did not load the full inference stack with a real browser, so they provide **no new combined VRAM/RAM/CPU or latency pass**.

Other open limits:

- one room/session at a time;
- no TURN or ICE/TCP fallback;
- foreground development orchestration only, with no restart/reboot/systemd claim;
- simple energy endpoint requires real microphone validation;
- no physical loopback audio, remote browser, reconnect, or barge-in capture yet;
- Tailscale HTTPS application/signaling ports have not yet been accepted from a second client.

## Exact remaining Pasha acceptance

Keep all captures outside Git and do not paste secrets or conversation content into the issue/PR.

1. On the canonical host, fill ignored `.env.slice6`, confirm the provider token file is user-owned mode `0600`, run `./setup-slice6`, then run `./run-slice6`. Observe the printed loopback/tailnet URLs and no startup traceback.
2. In a host browser, open `http://127.0.0.1:8000`, choose **Подключить микрофон**, and allow microphone/audio if prompted. Speak one bounded Russian utterance. Observe ordered listening → transcribing → thinking → speaking → completed state, the matching transcript/response, and an audible `ryan` response. A provider empty/latency failure must appear as a failure, not be retried through another route.
3. While a second response is audibly playing, begin a new utterance. Capture control/audio timing. Observe the old turn become `turn.interrupted`, old sound cease within 250 ms of server speech-start detection, a new correlated turn complete, no old words resume, and the dropped-event count not increase for valid traffic.
4. During another response, interrupt the browser network long enough to show **Переподключение…**, then restore it. Observe no old response play after reconnection, state return to ready only after the epoch reset, and a fresh utterance complete without stale transcript/response/audio.
5. Disconnect the host browser so the one-session limit is free. From a second device that is already a tailnet member, open `SLICE6_APP_PUBLIC_URL`, connect, speak, see the correlated transcript/response/state, and hear the agent. Confirm no public/non-tailnet path was added and capture the selected WebRTC path as tailnet UDP `7882` plus WSS signaling on the configured port.
6. During the loopback and remote cases, capture endpoint-to-first-playout/completion timing and host VRAM/RAM/CPU. Confirm LiveKit/browser/transient overhead fits inside the `4,682 MiB` reserve above the Slice 5 `7,600 MiB` peak without exhausting the `12,282 MiB` device, and confirm the preregistered latency budget. Report any provider timeout/empty response as the known failure, not as a passing retry.
7. Save one redacted browser capture showing transcript/turn state and successful barge-in, plus a redacted listener/capability review. Only those observations can close the pending roadmap evidence.

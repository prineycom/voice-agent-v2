# Slice 6 LiveKit media and interruption evidence

- **Issue:** [#7](https://github.com/prineycom/voice-agent-v2/issues/7)
- **Checkpoint date:** 2026-08-12
- **Status:** Failed manual attempt on 2026-08-12; authorized correction passes deterministic/runtime/Firefox boundaries, and a new physical-browser/full-stack acceptance remains open
- **Legacy source:** None inspected or used

This report distinguishes executable evidence from physical observations. Pasha's first Firefox attempt failed: background/silence triggered turns and no useful visible/audible response was accepted. The diagnosis is preserved outside the repository; none of the corrections below relabel that failed test or claim final Slice 6 acceptance.

## Implemented stack and boundaries

| Boundary | Pinned implementation | Evidence |
| --- | --- | --- |
| Local media server | official LiveKit Server `1.13.5`; release archive SHA-256 `c020fac437b7cc9b776eef1ad5ea8af77be9acfa07602eca20a3a44930dfbc70` | Cached binary reports `livekit-server version 1.13.5`; current generated restricted config started successfully. |
| Server RTC/capability | official Python `livekit==1.1.14`, `livekit-api==1.2.0` | `scripts/verify_slice6_runtime.py` verifies installed versions and decodes the generated JWT without opening a socket. |
| Application glue | `fastapi==0.141.1`, `uvicorn==0.52.1` | Loopback-only gateway smoke returned exactly `{"available":true,"session_limit":1}` with no-store, CSP, microphone Permissions-Policy, and no provider endpoint. |
| Browser | React `19.2.8`, TypeScript `7.0.2`, Vite `8.2.1`, official `livekit-client` `2.21.0` | Locked npm install, typecheck, then-current Vitest suite, and production build passed at the recorded checkpoint. |
| Inference | Existing `RealTurnController`, Whisper large-v3-turbo, fixed local official LFM2.5 Q4_K_M on llama.cpp, Qwen3 CustomVoice/`ryan` | `LiveTurnRunner` reuses the controller with the new provider-neutral local adapter; no second pipeline or cloud fallback was added. |

`./setup-slice6` keeps the LiveKit binary/Python environment in the ignored user cache and browser dependencies/build output in ignored directories. It verifies but never downloads/replaces the exact local LFM/llama.cpp cache when present. `.env.slice6`, signing material, models, model caches, recordings, transcripts, generated audio and runtime logs remain outside Git. The foreground runner verifies model/runtime hashes, starts llama.cpp on loopback, gives LiveKit only its config/signing pair, gives the gateway/controller only server configuration it owns, and strips secrets from Tailscale Serve children. No provider token exists in the active stack.

The browser capability is limited to one generated room, one generated browser identity, microphone publication, agent subscription, and data publication for 300 seconds. Issuance requires the exact loopback or configured tailnet application `Origin`; this blocks cross-site session consumption without adding a second identity system. Decoded grants contain no room create/list/admin/record capability. The browser response has a closed seven-field shape: `session_id`, `stream_epoch`, `livekit_url`, `token`, `expires_in_seconds`, `admission_timeout_ms`, and `control_version`; it cannot contain the loopback LFM endpoint, model-management access, provider credential, or LiveKit signing secret.

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

Browser tests cover the typed reducer/parser, incremental `llm.visible`, exact capability response, lifecycle/epoch/sequence/turn gates, reconnect suppression, stale event rejection, cancellable startup, bounded readiness, agent loss, cleanup failure, exact fresh-publication attachment and retirement acknowledgement, redacted diagnostics download, React shell, autoplay failure boundary, and explicit autoplay recovery. A separate real headless Firefox test joins an actual pinned LiveKit server with deterministic fake inference and observes official data/track/publication lifecycle. These lifecycle tests deliberately do not use RTP/Web Audio counters as evidence of speaker output: they prove only correlation, subscription invalidation, bounded acknowledgement, and cleanup. The installed runtime check also exercises cancellation during model startup, unclaimed-room expiry, capacity retention after incomplete cleanup, and local idempotent publication retirement after confirmed room disconnect. The app intentionally remains one small bundle; Vite's size advisory is visible but is not hidden as a failure.

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

## Local LFM / Issue #15 evidence

The tracked [`config/local-lfm-v1.json`](../../config/local-lfm-v1.json) freezes the official model/runtime identity, loopback endpoint, two-slot/65,536-token shape, full GPU offload, flash attention/Jinja/reasoning parser, bounded Russian voice prompt and sampling/visible-output contract. `./setup-slice6` and `./run-slice6` verify the exact 1,674,454,848-byte model SHA-256 `79fdf003…bfee14` and llama.cpp binary SHA-256 `08625d7c…ac11`; they never acquire an alternate model.

Focused real `./verify-local-lfm` and `./verify-real-streaming` runs on 2026-08-12 passed. The streaming check used the exact LFM endpoint plus resident Qwen and observed first PCM at about 11.07 s before total completion at about 13.16 s (17 chunks, no content logged); this proves real component overlap, not speaker audibility.

The provider check recorded:

- health/model identity and loopback-only `127.0.0.1:18080`;
- llama.cpp tag `b10357` / commit `689e227db`, exact binary/model hashes;
- exactly two `/slots`, each reporting `n_ctx=32768` after `--ctx-size 65536 --parallel 2`;
- two simultaneous public Russian requests completed successfully;
- repeated focused pairs completed in about 1.8–2.0 s total; visible TTFT ranged about 0.91–1.73 s and completion about 1.21–1.98 s;
- bounded visible answers (roughly 130–190 characters), hidden reasoning present but excluded from returned/TTS content;
- mid-request cancellation returned `selected_provider_cancelled`, and replacement requests completed in about 0.53–0.74 s;
- observed focused llama.cpp process VRAM was about 2,900 MiB, with no other inference model resident.

This focused measurement proves the provider/service contract, not full-stack coexistence. Resident llama.cpp plus real Whisper, Qwen3, LiveKit, gateway and browser VRAM/RAM/CPU, contention, sustained multi-turn and physical response quality are still pending. CI runs deterministic local-provider behavior and manifest wiring without requiring the 1.67 GB cache; the real host check fails closed if the exact cache is absent.

## Interruption and reconnect policy

- Endpointing uses the checksum-pinned Silero v6 ONNX artifact on CPU: 20 ms input, 32 ms inference windows, 96 ms speech confirmation at probability 0.60, 0.35 continuation hysteresis, 640 ms trailing silence, 256 ms pre-roll, and a 15 s maximum. Silence, stationary background and intermittent high-energy/AGC-like candidates have executable no-admission tests; a public Russian LibriSpeech fixture confirms speech detection when the authorized cache is present. RMS is diagnostics only.
- Barge-in first terminalizes the old turn, clears the 100 ms LiveKit source queue, invalidates the browser's old audio subscription and admits only a freshly subscribed track, cancels the selected provider and process-isolated STT/TTS, rolls back undelivered LLM context, and serializes replacement work.
- During transport reconnect, the browser detaches audio and drops all old-epoch data. The server cancels/drains active work, resets all in-memory conversation context, advances the epoch, and only then reports ready. Cleanup/reset timeout produces `session.degraded` and closes turn admission.
- Each complete visible LFM sentence is published immediately and handed to Qwen while SSE continues; each Qwen PCM chunk enters the correlated LiveKit source immediately. Hidden reasoning never crosses either boundary. Provider/TTS failure stops future output and is explicit; valid visible text is not hidden by TTS failure. A late failure cannot retract already delivered visible/PCM prefix, while barge-in prevents any later old prefix from escaping. No alternate model, endpoint, alias, redirect or fabricated answer exists.
- Normal teardown retires the exact publication while room transport is available. After confirmed room disconnect, cleanup retires it locally and closes the source without another unpublish request; repeated cleanup joins that same operation.

## Preserved failed evidence and limitations

Nothing in this slice rewrites historical machine-readable benchmark results. The failed `deepseek-v4-flash` latency, reliability, fairness, isolation/empty-response, privacy/provenance and cost gates remain in the existing Slice 2–5 evidence and architecture summaries. ADR-0006 remains the historical temporary authorization; ADR-0008 removes that route from the active app. The earlier LFM BF16/vLLM failure also remains failed; selecting a separately measured Q4_K_M/llama.cpp runtime does not relabel it.

The Slice 5 cloud-provider stack observed a `7,600 MiB` local Whisper/Qwen peak with `4,682 MiB` then free. The focused local-LFM server alone observed about `2,900 MiB` process VRAM. These measurements are not additive proof and do not establish a new combined ceiling; deterministic/runtime/bind smokes did not load llama.cpp, Whisper, Qwen, LiveKit and a real browser together, so they provide **no combined VRAM/RAM/CPU or latency pass**. Final acceptance requires a safe non-OOM reserve on the 12,282 MiB device under real overlap and barge-in.

Automated RTP counters, Web Audio callbacks, media-element state, LiveKit subscription events, and server queue drain are not physical evidence. They do not establish that the microphone captured speech, the speaker was audible, the response tail played, or barge-in stopped sound within 250 ms. Only the remaining Pasha-owned browser/listening procedure can establish those facts.

Other open limits:

- one room/session at a time;
- no TURN or ICE/TCP fallback;
- foreground development orchestration only, with no restart/reboot/systemd claim;
- the corrected Silero thresholds still require real canonical-microphone false-positive/miss validation;
- no physical loopback audio, remote browser, reconnect, or barge-in capture yet;
- Tailscale HTTPS application/signaling ports have not yet been accepted from a second client.

## Exact remaining Pasha acceptance

Keep all captures outside Git and do not paste secrets or conversation content into the issue/PR.

1. On the canonical host, use the already running corrected stack or fill ignored `.env.slice6`, run `./setup-slice6`, then `./run-slice6`. Observe the printed loopback/tailnet URLs, no credential requirement, no startup traceback, and a new content-free session JSONL under `~/.cache/voice-agent-v2/slice-6/diagnostics/`.
2. In a host browser, open `http://127.0.0.1:8000`, choose **Подключить микрофон**, and allow microphone/audio if prompted. Speak one bounded Russian utterance. Observe ordered listening → transcribing → thinking → speaking → completed state, the matching concise local-LFM response, and an audible `ryan` response. A local model timeout, hidden-only, truncated, empty or resource failure must appear explicitly with no cloud/fallback request.
3. While a second response is audibly playing, begin a new utterance. Capture control/audio timing. Observe the old turn become `turn.interrupted`, old sound cease within 250 ms of server speech-start detection, a new correlated turn complete, no old words resume, and the dropped-event count not increase for valid traffic.
4. During another response, interrupt the browser network long enough to show **Переподключение…**, then restore it. Observe no old response play after reconnection, state return to ready only after the epoch reset, and a fresh utterance complete without stale transcript/response/audio.
5. Disconnect the host browser so the one-session limit is free. From a second device that is already a tailnet member, open `SLICE6_APP_PUBLIC_URL`, connect, speak, see the correlated transcript/response/state, and hear the agent. Confirm no public/non-tailnet path was added and capture the selected WebRTC path as tailnet UDP `7882` plus WSS signaling on the configured port.
6. During the loopback and remote cases, capture local-LFM visible TTFT/completion, endpoint-to-first-playout/completion and total-board/process VRAM, RAM and CPU with resident llama.cpp plus real Whisper, Qwen3, LiveKit, gateway and browser. Confirm the 12,282 MiB device keeps a safe reserve under overlap/barge-in and the preregistered latency budget. Report every local-provider timeout/empty/truncated response as failure, never as a passing retry or cloud fallback.
7. Before disconnecting after any error, capture the exact UI alert and press **Скачать диагностику**. Save that redacted JSONL, one browser capture showing transcript/turn state and successful barge-in, and a redacted listener/capability review outside Git. Only those observations can close the pending roadmap evidence.

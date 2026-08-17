# Slice 6 LiveKit media and interruption evidence

- **Issue:** [#7](https://github.com/prineycom/voice-agent-v2/issues/7)
- **Checkpoint date:** 2026-08-12
- **Status:** Historical 2026-08-12 checkpoint; the failed manual attempt remains evidence, while the active Silero/Kseniya branch is documented separately
- **Legacy source:** None inspected or used

This report preserves the pre-ADR-0009 Slice 6/Issue #15 checkpoint and distinguishes its executable evidence from physical observations. Pasha's first Firefox attempt failed: background/silence triggered turns and no useful visible/audible response was accepted. The diagnosis is preserved outside the repository; nothing below relabels that failed test or claims final Slice 6 acceptance. The active TTS v2/native-48 composition, current verification limits, and remaining acceptance procedure are owned by [`silero-kseniya-48k-private-evaluation.md`](silero-kseniya-48k-private-evaluation.md).

## Implemented stack and boundaries

| Boundary | Pinned implementation | Evidence |
| --- | --- | --- |
| Local media server | official LiveKit Server `1.13.5`; release archive SHA-256 `c020fac437b7cc9b776eef1ad5ea8af77be9acfa07602eca20a3a44930dfbc70` | Cached binary reports `livekit-server version 1.13.5`; current generated restricted config started successfully. |
| Server RTC/capability | official Python `livekit==1.1.14`, `livekit-api==1.2.0` | `scripts/verify_runtime_contract.py`, owned by `./verify`, verifies installed versions and decodes the generated JWT without opening a socket. |
| Application glue | `fastapi==0.141.1`, `uvicorn==0.52.1` | Loopback-only gateway smoke returned exactly `{"available":true,"session_limit":1}` with no-store, CSP, microphone Permissions-Policy, and no provider endpoint. |
| Browser | React `19.2.8`, TypeScript `7.0.2`, Vite `8.2.1`, official `livekit-client` `2.21.0` | Locked npm install, typecheck, then-current Vitest suite, and production build passed at the recorded checkpoint. |
| Inference at this checkpoint | Existing `RealTurnController`, Whisper large-v3-turbo, fixed local official LFM2.5 Q4_K_M on llama.cpp, Qwen3 CustomVoice/`ryan` | Historical pre-ADR-0009 composition; the active branch replaces only this TTS leg as documented in the current evidence report. |

`./setup-slice6` keeps the LiveKit binary/Python environment in the ignored user cache and browser dependencies/build output in ignored directories. It verifies but never downloads/replaces the exact local LFM/llama.cpp cache when present. `.env.slice6`, signing material, models, model caches, recordings, transcripts, generated audio and runtime logs remain outside Git. The foreground runner verifies model/runtime hashes, starts llama.cpp and LiveKit on loopback, and gives the gateway/controller only server configuration it owns. It starts no external-exposure child. No provider token exists in the active stack.

The browser capability is limited to one generated room, one generated browser identity, microphone publication, agent subscription, and data publication for 300 seconds. Issuance requires the exact loopback or explicitly configured operator-owned application `Origin`; this blocks cross-site session consumption. Decoded grants contain no room create/list/admin/record capability. The browser response has a closed nine-field shape: `session_id`, `stream_epoch`, `livekit_url`, `token`, `expires_in_seconds`, `admission_timeout_ms`, `control_version`, `llm_profile`, and `tts_profile`. The two profiles expose only the verified active local LLM identity and the fixed public TTS profile; the response cannot contain the loopback LFM endpoint, model-management access, provider credential, or LiveKit signing secret.

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
./verify
./verify-extended
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
| WebRTC UDP media | loopback `127.0.0.1:7882/udp` in the active configuration | Current application boundary; the original external-address observation is historical. |
| ICE/TCP media | disabled (`tcp_port: 0`; conventional `7881` absent) | **No `7881` or `7882` TCP listener observed.** |
| Gateway | production config `127.0.0.1:8000/tcp` | Gateway behavior/security smoke passed on temporary loopback port `18006`; full runner port remains part of the physical gate. |
| External application/signaling exposure | none owned by the application | Optional and entirely operator-owned; not an acceptance requirement. |
| TURN/public fallback | none | No implementation or listener added. |

The historical LiveKit JSON startup observation reported version `1.13.5` and UDP start `7882`. The active configuration restricts signaling/media to loopback. Neither observation proves audible media, and external traversal/certificates are outside application acceptance.

## Local LFM / Issue #15 evidence

The tracked [`config/local-lfm-v1.json`](../../config/local-lfm-v1.json) freezes the official model/runtime identity, loopback endpoint, two-slot/65,536-token shape, full GPU offload, flash attention/Jinja/reasoning parser, bounded Russian voice prompt and sampling/visible-output contract. `./setup-slice6` and `./run-slice6` verify the exact 1,674,454,848-byte model SHA-256 `79fdf003…bfee14` and llama.cpp binary SHA-256 `08625d7c…ac11`; they never acquire an alternate model.

Focused real `./verify-local-lfm` and the now-retired `./verify-real-streaming` run on 2026-08-12 passed. That historical streaming check used the exact LFM endpoint plus resident Qwen and observed first PCM at about 11.07 s before total completion at about 13.16 s (17 chunks, no content logged); it remains dated evidence, not supported current tooling or speaker audibility.

The provider check recorded:

- health/model identity and loopback-only `127.0.0.1:18080`;
- llama.cpp tag `b10357` / commit `689e227db`, exact binary/model hashes;
- exactly two `/slots`, each reporting `n_ctx=32768` after `--ctx-size 65536 --parallel 2`;
- two simultaneous public Russian requests completed successfully;
- repeated focused pairs completed in about 1.8–2.0 s total; visible TTFT ranged about 0.91–1.73 s and completion about 1.21–1.98 s;
- bounded visible answers (roughly 130–190 characters), hidden reasoning present but excluded from returned/TTS content;
- mid-request cancellation returned `selected_provider_cancelled`, and replacement requests completed in about 0.53–0.74 s;
- observed focused llama.cpp process VRAM was about 2,900 MiB, with no other inference model resident.

This focused measurement proved the provider/service contract, not full-stack coexistence. At this checkpoint, resident llama.cpp plus real Whisper, Qwen3, LiveKit, gateway and browser VRAM/RAM/CPU, contention, sustained multi-turn and physical response quality were still pending. The current Silero/full-stack resource gate is owned by the active evidence report.

## Historical interruption and reconnect policy

The following describes the preserved pre-ADR-0009 implementation. Endpointing and reconnect ownership remain relevant, but its Qwen track-retirement and browser-subscription rules are not the active TTS/media contract. ADR-0009 and the [current evidence report](silero-kseniya-48k-private-evaluation.md) own request/turn/media-generation invalidation, persistent-publication attachment, and Silero cancellation behavior.

- Endpointing uses the checksum-pinned Silero v6 ONNX artifact on CPU: 20 ms input, 32 ms inference windows, 96 ms speech confirmation at probability 0.60, 0.35 continuation hysteresis, 640 ms trailing silence, 256 ms pre-roll, and a 15 s maximum. Silence, stationary background and intermittent high-energy/AGC-like candidates have executable no-admission tests; a public Russian LibriSpeech fixture confirms speech detection when the authorized cache is present. RMS is diagnostics only.
- Barge-in terminalized the old turn, cleared the LiveKit source queue, invalidated the old browser subscription, cancelled provider/STT/TTS work, rolled back undelivered LLM context, and serialized replacement work.
- During transport reconnect, the browser detached audio and dropped old-epoch data. The server cancelled/drained active work, reset in-memory conversation context, advanced the epoch, and only then reported ready.
- Complete visible LFM sentences were handed to Qwen while SSE continued; Qwen PCM chunks entered the correlated LiveKit source immediately. Hidden reasoning did not cross either boundary, and no alternate model, endpoint, alias, redirect, or fabricated answer existed.

## Preserved failed evidence and limitations

Nothing in this slice rewrites historical machine-readable benchmark results. The failed `deepseek-v4-flash` latency, reliability, fairness, isolation/empty-response, privacy/provenance and cost gates remain in the existing Slice 2–5 evidence and architecture summaries. ADR-0006 remains the historical temporary authorization; ADR-0008 removes that route from the active app. The earlier LFM BF16/vLLM failure also remains failed; selecting a separately measured Q4_K_M/llama.cpp runtime does not relabel it.

The Slice 5 cloud-provider stack observed a `7,600 MiB` local Whisper/Qwen peak with `4,682 MiB` then free. The focused local-LFM server alone observed about `2,900 MiB` process VRAM. These historical measurements are not additive proof and do not establish a new combined ceiling. The active composition's full-stack resource gate is defined in the current evidence report.

Automated RTP counters, Web Audio callbacks, media-element state, LiveKit subscription events, and server queue drain are not physical evidence. They do not establish that the microphone captured speech, the speaker was audible, the response tail played, or barge-in stopped sound within 250 ms. Only the remaining Pasha-owned browser/listening procedure can establish those facts.

Other open limits:

- one room/session at a time;
- no TURN or ICE/TCP fallback;
- foreground development orchestration only, with no restart/reboot/systemd claim;
- the corrected Silero thresholds still require real canonical-microphone false-positive/miss validation;
- no physical loopback audio, remote browser, reconnect, or barge-in capture yet;
- Optional external exposure is not exercised or required by the application contract.

## Remaining acceptance

The authoritative current checklist is [Pasha manual acceptance in the Silero/Kseniya evidence report](silero-kseniya-48k-private-evaluation.md#pasha-manual-acceptance--still-required). This historical Qwen/`ryan` checklist is intentionally not retained as parallel active guidance.

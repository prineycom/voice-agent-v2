# Voice Agent v2 implementation roadmap

> **Status:** Authoritative implementation order
>
> **Owner:** Voice Agent v2 product delivery
>
> **Last updated:** 2026-08-10

This roadmap is a sequence of independently deliverable vertical slices. It contains no calendar estimates. A slice starts only when its dependencies and incoming evidence gate are satisfied; it finishes only with the stated user-visible behavior and evidence.

The architecture and contract ownership are defined in [`architecture.md`](architecture.md). Stable terminology is defined in [`../CONTEXT.md`](../CONTEXT.md).

## Delivery rules for every slice

- Exercise the thinnest end-to-end user path; do not build an unused horizontal platform first.
- Keep LiveKit, STT, and TTS local. Evaluate only explicitly approved LLM candidates; use a cloud LLM only after documented local-gate failure and a separate explicit model/provider/privacy approval. No automatic fallback may make an acceptance test pass.
- Never commit secrets, private recordings, conversation content, model weights, caches, generated engines, provider credentials, or local environment values.
- Give each new wire contract one owner, a version, limits, terminal semantics, and executable producer/consumer tests.
- Record model and artifact identity by revision/hash and provenance.
- Treat subjective hardware behavior as unproven until the slice captures measurements on the canonical host.
- Inspect legacy material only at the pinned read-only [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6) tree when a slice needs it. Record the source commit/path, bring only the smallest useful unit, and prove it again under V2 contracts.
- A unit suite alone cannot close a vertical slice; the validation method below must observe the described public behavior.

## Dependency order

```text
1 Deterministic zero-secret tracer
└─ 2 Measured host/model and LLM-provider budget
   └─ 3 Real local STT turn
      └─ 4 Selected LLM-provider response
         └─ 5 Real local-STT/TTS conversation
            └─ 6 LiveKit media and interruption
               └─ V Avatar/module Grill and design gate
                  └─ 7 Deterministic MVP eye module
                     └─ 8 Failure semantics and observability
                        └─ 9 Single-host operational reliability  ← core MVP exit
                           └─ 10 Optional wake activation
```

## Slice 1 — Deterministic zero-secret voice-turn tracer

### User-visible outcome

A developer can run one documented root command on a clean checkout and observe one complete synthetic voice turn: fixed transcript, fixed response text, a deterministic audible tone artifact, ordered lifecycle state, and one terminal result tied to the same turn.

### Included scope

- Minimal executable session-controller seam and versioned event envelope.
- Deterministic fake STT, LLM, and TTS implementations behind the intended contracts.
- Programmatically generated PCM input and output; no recording or model download.
- Session/turn correlation, ordered lifecycle events, terminal outcomes, and a cancellation seam; no avatar contract is designed in this slice.
- One root verification command that exercises the tracer and contract tests.
- Behavioral CI that runs the root verification command and replaces the documentation-only `no_ci` opt-out.
- Repository/toolchain declarations only as needed to run this slice reproducibly.

### Excluded scope

- Real models/providers, GPU inference, microphones, LiveKit, WebRTC, browser UI/avatar modules, wake, deployment, and legacy source migration.
- Claims about production latency or quality.

### Dependencies

- This documentation foundation only.

### Acceptance criteria

- A clean checkout completes the tracer without network access, secrets, GPU access, or pre-existing model/cache state.
- Generated input deterministically yields a fixed Russian transcript, fixed response, ordered lifecycle events, declared PCM output, and exactly one `completed` terminal event.
- Session ID, turn ID, sequence, and contract versions correlate every emitted observation.
- Repeated runs are byte-identical after explicitly nondeterministic diagnostic timestamps are normalized.
- Injected hard STT, selected-provider LLM, and TTS failures each yield exactly one documented failed outcome with no downstream fabrication.
- Cancellation produces one interrupted outcome and no post-cancel chunks.
- The root verification command exits nonzero on any contract or tracer failure.
- Pull requests run that command as required behavioral CI; the documentation-only `no_ci` opt-out is removed in the same slice.

### Validation method

Run the root verification command from an empty-cache, network-denied environment twice; compare normalized trace and PCM hashes. Run table-driven failure/cancellation cases through the same public tracer interface.

### Evidence required before Slice 2

- Checked-in contract schemas/fixtures and ownership notes.
- Root verification output showing all deterministic success/failure cases.
- A green behavioral CI run with the documentation-only opt-out absent.
- Normalized trace and PCM hashes from two clean runs.
- A short statement of any legacy material considered; expected evidence is “none” for this slice.

## Slice 2 — Measured host/model and LLM-provider budget

> **Cumulative-delivery override (2026-08-11):** Pasha fixed Whisper large-v3-turbo, LiteLLM `deepseek-v4-flash`, and pinned-legacy Qwen3 CustomVoice/`ryan` for Slices 2–5 despite recorded hard-gate misses. Failed gates remain failed and are never relabelled. The branch proceeds through Slice 5 for one final human microphone/listening test; intermediate microphone acceptance, review, no-mistakes, PR, and merge are deferred.

### User-visible outcome

The project has repeatable measurements and a transparent operator-fixed delivery stack. LFM remains a failed local candidate. `deepseek-v4-flash` failed primary/repeat automated cloud gates but is fixed with no fallback. Whisper large-v3-turbo keeps its passed WER/latency and uses an explicit postmeasurement CPU ceiling of 90% after its 88.04% result missed the original 83.33% gate. Qwen3 CustomVoice/`ryan` passed automated timing/resource/repeat gates, with listening pending. Overlap failures remain recorded; this checkpoint is not an evidence-backed all-gates-pass claim.

### Included scope

- A repeatable benchmark harness that records the host, driver, runtime, artifact/provider identity, configuration, postmeasurement exceptions, and failed gates without rewriting them.
- A preregistered Russian STT corpus/rubric, LLM response rubric, and TTS listening set that contain no private recordings in Git.
- Preserve both measured Whisper/Piper candidate sets and failed official LFM evidence. The delivery override selects Whisper large-v3-turbo and freshly validates pinned-legacy `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` with `faster-qwen3-tts` 0.2.6, speaker `ryan`, Russian, chunk 4, temperature 0.8, top-p 0.9, top-k 50, repetition penalty 1.05, max 2048, and no fixed seed.
- One explicit cloud-gateway investigation: LiteLLM at `http://rpi:4000`, carried only over a proven Tailscale WireGuard path, with no redirect, implicit model default, alternate endpoint, or fallback.
- Before cloud inference, authenticated alias discovery, explicit selection of only `deepseek-v4-flash`, recorded operator-attested opaque DeepSeek routing, and an approved committed superseding preregistration. Cost/privacy/provider provenance are explicitly not evaluated under this temporary exception rather than silently assumed.
- Cloud measurement with public synthetic fixtures only: permitted-field filtering, concurrency `1/2/4`, TTFT/visible/completion/throughput/fairness, cancellation, sustained behavior, safe usage/error observations, and local TTS overlap only after component gates pass.
- Cold, warm, and sustained local measurement of load time, latency, throughput, VRAM, RAM, CPU, GPU use, cancellation, and recovery.
- Required overlap measurements: local-LLM decode with TTS startup, plus new STT admission while prior response work is being cancelled.
- A fail-closed LLM outcome: the failed LFM result cannot be erased or disguised. Select cloud mode only if the one explicit LiteLLM alias passes every transport, identity, privacy, cost, quality, latency, concurrency, cancellation, and no-fallback gate; otherwise retain no LLM selection.
- Selection report for local STT/TTS and one LLM provider mode, with explicit context/concurrency/admission limits and no fallback route.

### Excluded scope

- Production inference services, LiveKit, browser/avatar work, deployment automation, wake training, cloud STT/TTS, LFM retry, alternate gateways/models/providers, automatic provider fallback, and model-quality claims beyond the fixed evaluation sets.
- Committing model weights, provider/registry tokens, private samples, or raw conversational content.

### Dependencies

- Slice 1 contracts and deterministic tracer are green.
- Local artifact licenses/acquisition paths remain reviewable without exposing credentials. The task-private test credential authenticated over the proven transport. The user selected only `deepseek-v4-flash` and accepts an operator-attested opaque route; completion requests remain blocked until the superseding preregistration is committed. Defaults, other aliases, fallback, private/live content, and production use remain forbidden.

### Acceptance criteria

- Numeric resource, latency, and quality pass/fail thresholds and scoring rules are committed before candidate results are viewed.
- Every local result identifies artifact revision/hash, quantization, serving runtime/version, driver, context, concurrency, and benchmark input revision.
- Measurements include idle and peak VRAM/RAM, cold and warm latency, sustained behavior, and all required overlap/cancellation cases from [`architecture.md`](architecture.md#113-measurements-required-before-modelprovider-selection).
- Local LLM evidence contains exactly the approved failed `LiquidAI/LFM2.5-2.6B` native BF16/vLLM identity; it is never retried or replaced by another local LLM.
- Cloud discovery is limited to `http://rpi:4000`. Before prompt/context transfer, DNS must resolve to Tailscale, the route must use `tailscale0`, a WireGuard path must succeed, redirects must be rejected, and credential discovery must remain explicitly scoped and non-echoing.
- Cloud inference contains exactly one explicitly selected LiteLLM alias, `deepseek-v4-flash`, and records the underlying route as operator-attested/opaque rather than config-proven; gateway default, alias substitution, alternate alias, and observed fallback are hard failures.
- A clearly superseding cloud preregistration with transport, permitted-field/privacy-safe handling, concurrency `1/2/4`, quality, latency, fairness, cancellation, sustained, and repeat gates is approved and committed before any completion request. It explicitly records that provider cost/retention/training/region/provenance are outside this temporary synthetic scope.
- Selected local STT/TTS and the selected LLM mode pass their quality/latency gates. Cloud mode reports its local resource envelope and network/provider budget separately, and records content-free usage/cost/error observations plus provider privacy facts.
- Repeating the winning configuration produces results inside the declared tolerance.
- Exactly one provider mode is selected, is visible in evidence/configuration, and has no automatic failure-triggered route to another provider.
- If neither a local LLM nor an approved cloud candidate passes, the slice ends with a failed selection report and Slice 4 cannot start.

### Validation method

Preserve the completed LFM and local STT/TTS runs. Re-prove the Tailscale route, commit the superseding preregistration, then run only `deepseek-v4-flash` with committed public synthetic quality, concurrency `1/2/4`, pressure, cancellation, sustained, permitted-field, safe-usage/error, and repeat cases. Do not inspect further RPi mapping, test another alias, or send private/live content. Any measured identity, transport, request-privacy, or performance failure remains fail-closed; provider policy/cost remain intentionally unclaimed.

### Evidence required before Slice 3

- Machine-readable local benchmark results and a human-readable selection rationale.
- Precommitted thresholds/rubrics and repeat-run comparison.
- Selected local STT/TTS artifact manifests and measured residency/admission constraints.
- A synthetic-test result for the operator-attested `deepseek-v4-flash` alias, or an explicit failed-selection record. Slice 4 remains blocked until a later private/live provider privacy/provenance approval even if the synthetic benchmark passes.
- Explicit list of hypotheses that component/provider benchmarking has not tested.

## Slice 3 — Real local STT voice turn

### User-visible outcome

A user can speak Russian to a host-local capture path and see an accurate final transcript produced by the selected local STT model; deterministic substitutes finish the rest of the turn.

### Included scope

- The selected STT artifact and local inference-service contract.
- Live microphone use for manual acceptance and a fixed, licensed/generated corpus for reproducible validation.
- Audio-format negotiation, streaming or bounded utterance input, partial/final results, cancellation, readiness, and correlation.
- Replacement of only the fake STT leg in the Slice 1 tracer.

### Excluded scope

- Real LLM/TTS, LiveKit/WebRTC, avatar, persistent recordings, speaker identification, wake, and broad multilingual optimization.

### Dependencies

- Slice 2 operator-fixed Whisper large-v3-turbo artifact and transparent 90% CPU delivery exception; original failed 83.33% gate remains recorded.

### Acceptance criteria

- The fixed Russian corpus meets the preregistered accuracy and finalization thresholds on the canonical host.
- Automated/public-corpus capture-contract coverage produces exactly one final transcript; physical microphone acceptance is deferred to the single final Slice 5 command and remains pending here.
- Audio format mismatch, model unavailable, cancellation, and process loss have the architecture's explicit terminal behavior.
- No raw audio is retained by default; diagnostic capture requires an explicit bounded mode outside Git.
- STT identity/readiness and per-turn latency/resource observations are available without transcript content in default logs.
- The unchanged deterministic LLM/TTS legs still complete a correlated turn after real STT finalization.

### Validation method

Run corpus scoring, public tracer success/failure cases, capture-format/non-retention checks, and cancellation during transcription. Compare resource/latency results with the transparent Slice 2 delivery limits. Defer physical microphone acceptance to Slice 5.

### Evidence required before Slice 4

- Corpus score and latency/resource report tied to model/runtime hashes.
- Contract test results for controller and STT producer/consumer.
- Privacy check showing default non-retention.
- One redacted trace proving a real STT result completes the tracer.

## Slice 4 — Selected LLM-provider response

### User-visible outcome

After a real local transcript, the user receives a relevant response from the one measured LLM provider mode. The app/operator can identify whether that mode is local or approved cloud; deterministic TTS still makes completion audible without pretending it is speech quality.

### Included scope

- Provider-neutral LLM request/result contract owned by the session controller/provider adapter.
- The Slice 2 selected local artifact or explicitly approved cloud provider/model/endpoint—never both as runtime fallbacks.
- Bounded conversation context for one realtime session.
- Streaming text if supported, cancellation, readiness, correlation, response limits, provider identity, and external-transfer/usage observations.
- Cloud credential injection/redaction and permitted-context filtering if cloud mode was selected.
- Replacement of the fake LLM leg after real STT.

### Excluded scope

- Real TTS, tools/agent delegation, long-term memory, retrieval, automatic/per-request provider fallback, avatar control, prompt-content logging, cloud STT/TTS, and broad assistant-feature expansion.

### Dependencies

- Slice 3 real STT path is green.
- Slice 2 operator-fixed `deepseek-v4-flash` alias, context/admission limits, failed measurement record, operator-attested opaque routing, and no-fallback policy. Cumulative delivery authorizes implementation/final user test but not a claim that the failed cloud gates passed.

### Acceptance criteria

- The fixed response set meets the preregistered relevance, language, safety, latency, and response-length rubric.
- First-token, completion, context, resource/network, usage/cost where available, and cancellation observations remain within the selected budget.
- Provider mode/model identity is explicit in safe configuration, readiness, and metadata while credentials and prompt/response content remain absent.
- In cloud mode only final transcript and permitted context cross the allowlisted TLS endpoint; raw audio, local files, environment values, and secrets do not.
- Session context never crosses session IDs and is not durably persisted by default.
- Selected-provider failure yields no fabricated answer, alternate-provider request, or downstream TTS request.
- Real STT → selected LLM provider → deterministic TTS completes one correlated turn through the public tracer.

### Validation method

Run the fixed evaluation set, multi-turn session-isolation cases, permitted-field filtering, secret-redaction checks, mid-generation cancellation, provider failure with no fallback, and the real-STT/selected-provider tracer. In cloud mode, also verify the endpoint allowlist and external-transfer observations without recording content.

### Evidence required before Slice 5

- Fixed response-rubric results tied to local artifact/runtime or approved cloud provider/model identity.
- Context isolation, permitted-field, secret-redaction, no-fallback, and cancellation contract results.
- One privacy-safe trace showing STT final through selected-provider terminal output.
- Updated overlap/resource or cloud-network budget using observed provider behavior.

## Slice 5 — Real local-STT/TTS spoken conversation

### User-visible outcome

A user can speak through a host-local path and hear a locally synthesized spoken answer from real local STT, the selected LLM provider, and local TTS—the first complete real-inference voice conversation, before LiveKit.

### Included scope

- Selected TTS artifact and local streaming-service contract.
- Declared output audio format, ordered chunks, first-audio behavior, terminal semantics, and cancellation.
- Safe overlap of selected-provider response streaming and local TTS startup within the measured local/network budget.
- Text remains visible if synthesis fails.
- Replacement of the deterministic TTS leg in the tracer.

### Excluded scope

- LiveKit/WebRTC, remote clients, avatar rendering, voice cloning from private samples, multiple voice personas, wake, and deployment supervision.

### Dependencies

- Slice 4 real-STT/selected-provider path is green.
- Slice 2 operator-fixed Qwen3 CustomVoice artifact/runtime/configuration, Apache-2.0 provenance, automated timing/resource evidence, and pending human listening rubric.

### Acceptance criteria

- Fixed listening material meets the preregistered Russian intelligibility/naturalness and first-audio/real-time-factor thresholds.
- A live microphone turn produces an audible answer with exactly one correlated completion.
- Required selected-provider→TTS overlap remains within the measured GPU/RAM reserve in local mode or the measured network/TTS reserve in cloud mode; sustained turns do not show unbounded memory growth.
- Cancellation stops synthesis and output within the declared bound and emits no stale chunks.
- TTS failure preserves valid text but cannot mark the spoken turn successfully completed.
- Default operation retains neither microphone input nor synthesized audio.

### Validation method

Run blind/fixed listening review, repeated and sustained real-inference tracer turns, overlap/resource capture, failure injection, and mid-synthesis cancellation on the canonical host.

### Evidence required before Slice 6

- Listening/latency/resource report tied to the selected artifact.
- Sustained-run and cancellation traces.
- Complete real-inference turn trace and declared audio format.
- Updated headroom calculation for LiveKit and browser addition.

## Slice 6 — Local LiveKit media and interruption

### User-visible outcome

A user opens the private web app, speaks over LiveKit, hears the locally synthesized answer, sees transcript/turn state, and can interrupt the answer by speaking again.

### Included scope

- Local LiveKit server, minimal web gateway, and minimal browser conversation UI.
- Tailscale/loopback HTTPS entry, scoped room capability, WebRTC microphone/agent audio, and versioned control events.
- Session reconnect behavior and full barge-in cancellation across controller, selected LLM provider, TTS, published audio, and queued client output.
- Headless deterministic media test plus real browser validation.
- Minimum tailnet port/exposure documentation based on actual results.

### Excluded scope

- Avatar host/modules, wake, kiosk, public internet exposure, a separate auth subsystem, cloud TURN chosen without evidence, and general UI polish.

### Dependencies

- Slice 5 real local conversation and current headroom evidence are green.

### Acceptance criteria

- Loopback browser and a second tailnet browser can connect using only tailnet membership plus protocol-required room capability.
- STT/TTS/local-model inference and all management endpoints remain host-local; an explicitly selected cloud LLM is outbound-only to its allowlisted endpoint. LiveKit and cloud-provider credentials never reach the browser.
- Published microphone audio yields one correlated transcript and audible agent response over LiveKit.
- Barge-in marks the prior turn interrupted, stops prior audio within the declared bound, discards old queued events, and completes a new turn without correlation leaks.
- Disconnect/reconnect cannot replay a stale answer as a new turn.
- Media/control timing and resource use stay within the Slice 5 reserve and preregistered latency budget.
- A deterministic headless test covers success, disconnect, duplicate/late event, and interruption without requiring real models or secrets.

### Validation method

Run network-denied deterministic headless media tests, then selected-provider real-inference loopback and tailnet browser sessions. Capture endpoint-to-playout timing and a scripted/observed barge-in trace. Review actual bind addresses and tailnet exposure.

### Evidence required before Design Gate V

- Headless success/fault/interruption results.
- Real loopback and second-tailnet-client latency traces.
- Redacted bind/port and scoped-capability review.
- Browser capture showing conversation, transcript state, and successful barge-in.

## Design Gate V — Grill the avatar module and MVP eye

This is a required design task, not an implementation slice. Run a dedicated `/skill:grill-docs` session after Slice 6 evidence is available and before Slice 7 starts.

### Required decisions

- Avatar-host/module capabilities, versioning, selection, fallback, and failure contract.
- MVP eye visual grammar: pupil bounds, blink timing, palette/state map, thinking loader, speech-pulse mapping, and return-to-neutral.
- Input precedence and staleness rules for lifecycle, actual-playout speech envelope, external tracking target, seeded idle movement, interruption, and reduced motion.
- Determinism/replay rules and browser performance/accessibility budgets.
- Whether any future LLM semantic visual input is useful. The MVP does not require it; if admitted later, it must remain bounded, renderer-neutral, validated, and never contain frame/renderer data.

### Gate acceptance and evidence

- Accepted updates to [ADR-0002](adr/0002-renderer-agnostic-avatar-boundary.md), the architecture boundary, glossary, and a versioned contract plan with one owner.
- Original design references/provenance and preregistered visual-review scenarios without proprietary/copied assets.
- Explicit non-goals for the MVP module, including no Live2D/3D implementation and no camera-tracking producer.
- Slice 7 acceptance fixtures and visual/performance rubric are defined before runtime implementation.

## Slice 7 — Renderer-agnostic host and deterministic MVP eye

### User-visible outcome

During the private LiveKit conversation, an original animated AI eye visibly listens, thinks, speaks, handles interruption, and returns smoothly to neutral. Its pupil uses bounded external targeting or bounded idle movement, it blinks, its outer ring/pattern pulses with actual speech, and its palette/pupil representation reflects state.

### Included scope

- The Gate V renderer-agnostic avatar host/module contract and capability/fallback behavior.
- One original custom MVP eye module with documented authorship/license/provenance.
- Bounded external pupil target input plus seeded deterministic idle movement when no valid target exists; the tracking producer itself is not included.
- Deterministic blink, state-specific pupil behavior including a thinking loader, palette changes, actual-playout speech-envelope pulsing, interruption, return-to-neutral, reduced motion, and render health.
- Offline fixture replay and real-conversation visual review.

### Excluded scope

- Live2D or 3D module implementations, A2F, A2E, camera/object tracking, LLM visual output, proprietary/copied assets, full-body animation, and wake.

### Dependencies

- Slice 6 correlated LiveKit state/playout path and interruption behavior are green.
- Design Gate V is accepted with the detailed contract, fixtures, precedence rules, and visual rubric.
- Original eye asset/design provenance is approved for repository use.

### Acceptance criteria

- The avatar host/module boundary has one owner, version/capabilities, bounded validated inputs, cancellation, fallback, and executable producer/consumer tests.
- The same fixtures, lifecycle timing, speech envelope, tracking targets, and idle seed yield the same normalized render-state sequence.
- Missing/stale/malformed targets use the designed bounded idle behavior; no unvalidated input reaches render state.
- Blink and pupil motion remain within the approved bounds; thinking uses the approved loader representation.
- The outer ring/pattern follows actual audio playout rather than generated text or LLM timing.
- Palette/state transitions and interruption return to a safe state within declared bounds.
- The LLM emits no renderer parameters, keyframes, executable content, or frames and is not required for visual control.
- Reduced-motion mode avoids nonessential motion while preserving understandable state.
- Human visual review accepts the original eye as deliberately simple, readable, and not a copy of another character.
- Browser frame timing and host resource use stay within preregistered limits during a real voice session.

### Validation method

Run module-contract bounds/property tests, seeded deterministic fixture replay, stale/malformed tracking cases, speech-envelope replay against actual audio timing, browser performance capture, and recorded visual review for neutral, listening, thinking, speaking, failure, tracking, idle, reduced-motion, and interruption states.

### Evidence required before Slice 8

- Gate V decisions, versioned module contract/fixtures, and producer/consumer test report.
- Asset/design provenance and approved visual captures.
- Deterministic replay hash/normalized state report across tracking, idle, speech, palette, and lifecycle inputs.
- Browser performance/resource results and interruption capture.

## Slice 8 — Failure semantics and privacy-safe observability

### User-visible outcome

When any core capability fails, the app tells the user whether it is unavailable, degraded, retrying, or interrupted; an operator can diagnose the correlated turn without exposing conversation content.

### Included scope

- Liveness/readiness for LiveKit, controller, STT, selected LLM provider, TTS, avatar host, and active module.
- Structured session/turn timing, selected-provider identity/external-transfer/usage metadata, terminal outcomes, queue/drop/cancellation counts, and GPU/CPU/RAM measurements from the architecture.
- User-visible degraded states and the complete hard/soft failure matrix.
- Fault injection for process/provider loss, cloud credential/allowlist failure when applicable, GPU allocation failure, malformed avatar inputs, network interruption, and browser render failure.
- Explicit diagnostic-capture opt-in and retention boundary.

### Excluded scope

- A hosted telemetry vendor, raw-content logging by default, public status pages, wake observability, and automated deployment/upgrade.

### Dependencies

- Slice 7 complete product path is green.

### Acceptance criteria

- One turn produces one correlated timeline from utterance through terminal delivery without logging raw audio, transcript, prompt, or response by default.
- Every failure row in [`architecture.md`](architecture.md#7-failure-semantics) has an executable or controlled validation and the documented user-visible state.
- Readiness differs from process liveness and identifies incompatible/unloaded model or contract state.
- No fault silently switches LLM provider/model, moves STT/TTS to cloud, changes host/auth/wake behavior, or selects another avatar module.
- Failure retries are bounded; GPU/process crash cannot create an admission or restart loop.
- Operators can compute the preregistered latency/resource percentiles and identify the slow stage from emitted observations.
- Diagnostic content capture is off by default, bounded when enabled, outside Git, and has an exercised deletion path.

### Validation method

Run the deterministic fault matrix, real-process kill/recovery cases, resource-pressure test inside safe limits, tailnet disconnect, browser render fault, and a privacy review of default logs. Reconstruct a turn timeline using only emitted metadata.

### Evidence required before Slice 9

- Fault-matrix results and user-visible-state captures.
- Example redacted correlated timeline and percentile report.
- Readiness compatibility report.
- Default-log privacy review and diagnostic-retention exercise.

## Slice 9 — Single-host operational reliability

### User-visible outcome

After a normal host boot or a bounded service failure, the private app reaches an honest ready/degraded state without manual process archaeology and sustains normal conversation reliably. This is the core MVP exit gate.

### Included scope

- Reproducible, idempotent local service supervision and configuration validation.
- Declared start/stop ordering, graceful turn drain, bounded restart policy, and no orphan inference.
- Artifact manifest verification, disk/cache bounds, version/build reporting, and rollback to the last known compatible configuration.
- LiveKit, web gateway, controller, local STT/TTS, any selected local LLM, provider-adapter state, avatar host, and MVP eye recovery on the one host; explicit cloud mode reports external provider readiness without pretending to supervise it.
- Sustained voice/avatar session, reboot validation, and rollback rehearsal.

### Excluded scope

- Kiosk/browser autostart, multi-host failover, high availability, public exposure, fleet management, automatic model upgrades, destructive cache cleanup, and wake.

### Dependencies

- Slice 8 failure and observability evidence is green.

### Acceptance criteria

- From a normal reboot, services reach the correct ready or explicit degraded state with artifact/config compatibility checked before turn admission.
- Reapplying the same operational configuration is a no-op; partial failure is reported and never followed by a false success.
- Graceful shutdown/interrupted restart leaves no unbounded inference process or stale room response.
- Each core process can fail once and recover within a declared bound or stop in an actionable failed state; no infinite restart loop.
- A sustained preregistered session passes turn success, latency, VRAM/RAM growth, interruption, and avatar frame-health thresholds.
- A deliberately incompatible configuration is rejected before admission.
- Rollback restores the prior compatible stack and completes the deterministic tracer plus one real voice turn.
- No operation requires or reveals a secret in command output or tracked files.

### Validation method

Perform idempotence checks, controlled reboot, one-at-a-time service failure/recovery, sustained real conversation/animation run, incompatible-config rejection, disk-pressure preflight, and rollback rehearsal. Finish with root verification and one tailnet voice turn.

### Evidence required for core MVP completion and before Slice 10

- Reboot/readiness and service-recovery report.
- Sustained-run latency/resource/turn-success summary.
- Configuration/artifact verification output and rollback trace.
- Root verification and tailnet conversation evidence from the recovered stack.
- Explicit core MVP sign-off; wake remains absent and non-blocking.

## Slice 10 — Optional wake activation

### User-visible outcome

If explicitly authorized after the core MVP, a user can activate the ready voice app hands-free with chosen wake behavior that meets measured privacy, false-accept, latency, and resource limits.

### Included scope

- A fresh product decision that defines whether wake is still valuable, the phrases/languages, and whether an existing model is sufficient.
- Local-only wake inference, visible/listenable activation state, manual activation fallback, stop/deactivation, and integration with current session/turn admission.
- Fixed positive/negative/noise evaluation and sustained false-accept measurement.
- Custom-model training only if existing candidates fail a preregistered gate and the user separately approves private-data handling and GPU work.
- Artifact identity/provenance, thresholds, rollback, and resource coexistence with the core stack.

### Excluded scope

- Making wake a prerequisite for core MVP, silent fail-open to always listening, cloud wake services, committing raw user recordings, kiosk behavior, and training a custom model merely because legacy work attempted one.

### Dependencies

- Slice 9 core MVP evidence is green.
- Explicit post-MVP scope decision and approved recording-retention policy if custom training is considered.

### Acceptance criteria

- Wake-disabled remains a supported configuration and manual activation remains available.
- Recall, false accepts per measured duration, activation latency, CPU/GPU/RAM cost, and phrase/noise coverage meet thresholds committed before evaluation.
- Missing, corrupt, or incompatible wake artifacts report degraded wake capability; they never silently enable always-listening behavior.
- Activation/deactivation cannot leak audio into an unadmitted turn and respects current interruption/session lifecycle.
- Private evaluation/training audio is never committed and follows an explicit retention/deletion policy.
- The complete core voice/MVP-eye path still meets its Slice 9 thresholds with wake enabled.
- Disabling or rolling back wake restores the unchanged core MVP.

### Validation method

Run fixed positive/negative/noise corpora, a sustained ambient false-accept session, latency/resource capture, artifact corruption, enable/disable rollback, and a real tailnet hands-free conversation. If custom training occurs, reproduce the model from pinned inputs/tooling while separately verifying private-data deletion.

### Evidence required to close the optional slice

- Scope decision and preregistered thresholds.
- Recall/false-accept/latency/resource report tied to artifact hash.
- Privacy/retention evidence and corruption/fail-safe results.
- Core MVP regression evidence with wake enabled and then rolled back.

## What is intentionally not on this roadmap

Live2D and 3D are optional later avatar modules, not MVP paths; each requires a separate post-MVP vertical slice against the renderer-agnostic contract. A2F, A2E, kiosk mode, additional user-managed compute hosts, public deployment, a separate auth project, cloud STT/TTS, automatic LLM fallback, and wholesale legacy migration have no active slices. Adding one requires a new product decision, architecture impact review, and dependency-ordered slice rather than reopening stale legacy backlog assumptions.

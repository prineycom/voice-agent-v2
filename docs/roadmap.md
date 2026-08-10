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
- Keep inference local. No cloud fallback may make an acceptance test pass.
- Never commit secrets, private recordings, conversation content, model weights, caches, generated engines, or local environment values.
- Give each new wire contract one owner, a version, limits, terminal semantics, and executable producer/consumer tests.
- Record model and artifact identity by revision/hash and provenance.
- Treat subjective hardware behavior as unproven until the slice captures measurements on the canonical host.
- Migrate legacy material only when the slice needs it. Record the source commit/path, bring only the smallest useful unit, and prove it again under V2 contracts.
- A unit suite alone cannot close a vertical slice; the validation method below must observe the described public behavior.

## Dependency order

```text
1 Deterministic zero-secret tracer
└─ 2 Measured host/model budget
   └─ 3 Real local STT turn
      └─ 4 Real local LLM response
         └─ 5 Real local TTS conversation
            └─ 6 LiveKit media and interruption
               └─ 7 Live2D eyes and semantic intent v1
                  └─ 8 Failure semantics and observability
                     └─ 9 Single-host operational reliability  ← core MVP exit
                        └─ 10 Optional wake activation
```

## Slice 1 — Deterministic zero-secret voice-turn tracer

### User-visible outcome

A developer can run one documented root command on a clean checkout and observe one complete synthetic voice turn: fixed transcript, fixed response text, a deterministic audible tone artifact, one bounded animation-intent fixture, and one terminal result tied to the same turn.

### Included scope

- Minimal executable session-controller seam and versioned event envelope.
- Deterministic fake STT, LLM, and TTS implementations behind the intended contracts.
- Programmatically generated PCM input and output; no recording or model download.
- Session/turn correlation, ordered lifecycle events, terminal outcomes, cancellation seam, and neutral animation fallback.
- One root verification command that exercises the tracer and contract tests.
- Behavioral CI that runs the root verification command and replaces the documentation-only `no_ci` opt-out.
- Repository/toolchain declarations only as needed to run this slice reproducibly.

### Excluded scope

- Real models, GPU inference, microphones, LiveKit, WebRTC, browser UI, Live2D assets, wake, deployment, and legacy source migration.
- Claims about production latency or quality.

### Dependencies

- This documentation foundation only.

### Acceptance criteria

- A clean checkout completes the tracer without network access, secrets, GPU access, or pre-existing model/cache state.
- Generated input deterministically yields a fixed Russian transcript, fixed response, valid bounded intent fixture, declared PCM output, and exactly one `completed` terminal event.
- Session ID, turn ID, sequence, and contract versions correlate every emitted observation.
- Repeated runs are byte-identical after explicitly nondeterministic diagnostic timestamps are normalized.
- Injected invalid animation intent yields the neutral fallback while the voice turn still completes.
- Injected hard STT, LLM, and TTS failures each yield exactly one documented failed outcome with no downstream fabrication.
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

## Slice 2 — Measured host and model budget

### User-visible outcome

The project has an evidence-backed local STT/LLM/TTS candidate set that can fit and perform on the RTX 4070 host under the voice product's real overlap, rather than a model list chosen by assertion.

### Included scope

- A repeatable benchmark harness that records the host, driver, runtime, artifact identity, and configuration.
- A preregistered Russian STT corpus/rubric, LLM response rubric, and TTS listening set that contain no private recordings in Git.
- At least two credible candidates per inference role, unless a documented compatibility/license screen leaves only one testable candidate.
- Cold, warm, and sustained measurement of load time, latency, throughput, VRAM, RAM, CPU, GPU use, cancellation, and recovery.
- Required overlap measurements: LLM decode with TTS startup, plus new STT admission while prior response work is being cancelled.
- Selection report for one compatible initial STT/LLM/TTS set, including quantization/context/concurrency limits and a measured safety margin.

### Excluded scope

- Production inference services, LiveKit, browser/avatar work, deployment automation, wake training, and model-quality claims beyond the fixed evaluation sets.
- Committing model weights, registry tokens, private samples, or raw conversational content.

### Dependencies

- Slice 1 contracts and deterministic tracer are green.
- Candidate licenses and acquisition paths are reviewable without exposing credentials.

### Acceptance criteria

- Numeric pass/fail thresholds and scoring rules are committed before candidate results are viewed.
- Every result identifies artifact revision/hash, quantization, serving runtime/version, driver, context, concurrency, and benchmark input revision.
- Measurements include idle and peak VRAM/RAM, cold and warm latency, sustained behavior, and all required overlap/cancellation cases from [`architecture.md`](architecture.md#113-measurements-required-before-model-selection).
- The selected trio passes the preregistered STT, LLM, and TTS quality gates and the latency gates without out-of-memory recovery or an unmeasured unload trick.
- The complete measured peak leaves an explicit, evidence-derived reserve for display/rendering and transient allocations.
- Repeating the winning configuration produces results inside the declared tolerance.
- If no trio passes, the slice ends with a failed selection report and the next slice does not start; documentation does not promote a “least bad” model to proven.

### Validation method

Run the harness on the canonical host from cold load through sustained and overlap scenarios. Independently review the blind/fixed quality scoring and rerun the winning configuration. Verify the report can be reproduced from artifact identities without embedding secrets.

### Evidence required before Slice 3

- Machine-readable benchmark results and a human-readable selection rationale.
- Precommitted thresholds/rubrics and repeat-run comparison.
- Selected artifact manifests and measured residency/admission constraints.
- Explicit list of hypotheses that remain untested by component benchmarking.

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

- Slice 2 selected STT artifact, limits, and quality/latency thresholds.

### Acceptance criteria

- The fixed Russian corpus meets the preregistered accuracy and finalization thresholds on the canonical host.
- A manual microphone turn displays partial feedback if supported and exactly one final transcript.
- Audio format mismatch, model unavailable, cancellation, and process loss have the architecture's explicit terminal behavior.
- No raw audio is retained by default; diagnostic capture requires an explicit bounded mode outside Git.
- STT identity/readiness and per-turn latency/resource observations are available without transcript content in default logs.
- The unchanged deterministic LLM/TTS legs still complete a correlated turn after real STT finalization.

### Validation method

Run corpus scoring, public tracer success/failure cases, live microphone acceptance, and a cancellation during transcription. Compare resource/latency results with Slice 2 tolerances.

### Evidence required before Slice 4

- Corpus score and latency/resource report tied to model/runtime hashes.
- Contract test results for controller and STT producer/consumer.
- Privacy check showing default non-retention.
- One redacted trace proving a real STT result completes the tracer.

## Slice 4 — Real local LLM response

### User-visible outcome

After a real local transcript, the user receives a relevant local text response from the selected LLM; deterministic TTS still makes completion audible without pretending it is speech quality.

### Included scope

- Selected local LLM artifact and service contract.
- Bounded conversation context for one realtime session.
- Streaming text if supported, cancellation, readiness, correlation, and response limits.
- A minimal neutral animation-intent candidate compatible with the existing tracer invariants; expressive vocabulary remains deferred to Slice 7.
- Replacement of the fake LLM leg after real STT.

### Excluded scope

- Real TTS, tools/agent delegation, long-term memory, retrieval, cloud fallback, avatar rendering, prompt-content logging, and broad assistant-feature expansion.

### Dependencies

- Slice 3 real STT path is green.
- Slice 2 selected LLM artifact, context/admission limits, and rubric.

### Acceptance criteria

- The fixed response set meets the preregistered relevance, language, safety, latency, and response-length rubric.
- First-token, completion, context, resource, and cancellation observations remain within the selected budget.
- Invalid structured output cannot bypass the controller's response and intent validation; valid text can use the neutral intent fallback.
- Session context never crosses session IDs and is not durably persisted by default.
- Model/process failure yields no fabricated answer or downstream TTS request.
- Real STT → real LLM → deterministic TTS completes one correlated turn through the public tracer.

### Validation method

Run the fixed evaluation set, multi-turn session-isolation cases, malformed-output injection, mid-generation cancellation, and the real-STT/real-LLM tracer on the canonical host.

### Evidence required before Slice 5

- Fixed response-rubric results tied to artifact/runtime identity.
- Context isolation, malformed output, and cancellation contract results.
- One privacy-safe trace showing STT final through LLM terminal output.
- Updated overlap budget using observed LLM runtime behavior.

## Slice 5 — Real local spoken conversation

### User-visible outcome

A user can speak through a host-local path and hear a locally synthesized spoken answer from real STT, LLM, and TTS—the first complete real-inference voice conversation, before LiveKit.

### Included scope

- Selected TTS artifact and local streaming-service contract.
- Declared output audio format, ordered chunks, first-audio behavior, terminal semantics, and cancellation.
- Safe overlap of LLM generation and TTS startup within the measured budget.
- Text remains visible if synthesis fails.
- Replacement of the deterministic TTS leg in the tracer.

### Excluded scope

- LiveKit/WebRTC, remote clients, avatar rendering, voice cloning from private samples, multiple voice personas, wake, and deployment supervision.

### Dependencies

- Slice 4 real STT/LLM path is green.
- Slice 2 selected TTS artifact, voice/license provenance, listening rubric, and resource limits.

### Acceptance criteria

- Fixed listening material meets the preregistered Russian intelligibility/naturalness and first-audio/real-time-factor thresholds.
- A live microphone turn produces an audible answer with exactly one correlated completion.
- Required LLM→TTS overlap remains within the measured GPU/RAM reserve; sustained turns do not show unbounded memory growth.
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

A user opens the private web app, speaks over LiveKit, hears the local answer, sees transcript/turn state, and can interrupt the answer by speaking again.

### Included scope

- Local LiveKit server, minimal web gateway, and minimal browser conversation UI.
- Tailscale/loopback HTTPS entry, scoped room capability, WebRTC microphone/agent audio, and versioned control events.
- Session reconnect behavior and full barge-in cancellation across controller, LLM, TTS, published audio, and queued client output.
- Headless deterministic media test plus real browser validation.
- Minimum tailnet port/exposure documentation based on actual results.

### Excluded scope

- Live2D rendering, wake, kiosk, public internet exposure, a separate auth subsystem, cloud TURN chosen without evidence, and general UI polish.

### Dependencies

- Slice 5 real local conversation and current headroom evidence are green.

### Acceptance criteria

- Loopback browser and a second tailnet browser can connect using only tailnet membership plus protocol-required room capability.
- Inference services and management endpoints remain host-local; the signing secret never reaches the browser.
- Published microphone audio yields one correlated transcript and audible agent response over LiveKit.
- Barge-in marks the prior turn interrupted, stops prior audio within the declared bound, discards old queued events, and completes a new turn without correlation leaks.
- Disconnect/reconnect cannot replay a stale answer as a new turn.
- Media/control timing and resource use stay within the Slice 5 reserve and preregistered latency budget.
- A deterministic headless test covers success, disconnect, duplicate/late event, and interruption without requiring real models or secrets.

### Validation method

Run network-denied deterministic headless media tests, then real-model loopback and tailnet browser sessions. Capture endpoint-to-playout timing and a scripted/observed barge-in trace. Review actual bind addresses and tailnet exposure.

### Evidence required before Slice 7

- Headless success/fault/interruption results.
- Real loopback and second-tailnet-client latency traces.
- Redacted bind/port and scoped-capability review.
- Browser capture showing conversation, transcript state, and successful barge-in.

## Slice 7 — Original Live2D eyes and semantic intent v1

### User-visible outcome

During the private LiveKit conversation, an original Live2D AI-eyes avatar visibly attends, thinks, speaks, reacts with bounded semantic cues, handles interruption, and returns smoothly to neutral.

### Included scope

- Original AI-eyes design and Live2D asset with documented authorship/license/provenance.
- `AnimationIntent` v1 vocabulary, bounds, size/cue limits, semantic timing anchors, schema, fixtures, and compatibility rules.
- LLM production of candidate v1 intent; controller validation and neutral fallback; realtime delivery tied to turn state.
- Deterministic avatar mapping, interpolation, blink/gaze/idle, cue scheduling, cancellation, reduced-motion mode, and render health.
- Offline fixture replay and real-conversation visual review.

### Excluded scope

- Proprietary or copied character assets, 3D, A2F, A2E, per-frame LLM output, arbitrary Live2D parameter output, a second renderer, advanced full-body animation, and wake.

### Dependencies

- Slice 6 correlated LiveKit control path and interruption behavior are green.
- Original asset provenance is approved for repository use.

### Acceptance criteria

- Contract v1 meets every invariant in [`architecture.md`](architecture.md#51-animation-intent-invariants) and has executable producer, validator, transport, and consumer tests.
- An explicit hard limit bounds serialized size, cue count, vocabulary, intensity, and duration-like values.
- Unknown, malformed, late, duplicate, and out-of-range intent cannot reach render parameters; neutral fallback preserves voice.
- Replaying the same fixture and lifecycle timing yields the same normalized render-state sequence.
- The LLM never emits or controls Live2D parameter names, keyframes, or frames.
- Interruption cancels old cues and returns within the declared motion bound; the new turn is visually distinct by correlation.
- Reduced-motion mode avoids nonessential motion while preserving understandable state.
- Human visual review accepts the original design as deliberately simple, readable, and not a copy of the inspiration.
- Browser frame timing and host resource use stay within preregistered limits during a real voice session.

### Validation method

Run schema/property/bounds tests, deterministic fixture replay, malformed/late-event fault cases, browser performance capture, and recorded A/B visual review for neutral, listening, thinking, speaking, emphasis, failure, and interruption states.

### Evidence required before Slice 8

- V1 schema, fixtures, compatibility policy, and contract-test report.
- Asset provenance/license record and approved visual captures.
- Deterministic replay hash/normalized state report.
- Browser performance/resource results and interruption capture.

## Slice 8 — Failure semantics and privacy-safe observability

### User-visible outcome

When any core capability fails, the app tells the user whether it is unavailable, degraded, retrying, or interrupted; an operator can diagnose the correlated turn without exposing conversation content.

### Included scope

- Liveness/readiness for LiveKit, controller, STT, LLM, TTS, and avatar runtime.
- Structured session/turn timing, terminal outcomes, queue/drop/cancellation counts, and GPU/CPU/RAM measurements from the architecture.
- User-visible degraded states and the complete hard/soft failure matrix.
- Fault injection for process loss, GPU allocation failure, malformed contracts, network interruption, and browser render failure.
- Explicit diagnostic-capture opt-in and retention boundary.

### Excluded scope

- A hosted telemetry vendor, raw-content logging by default, public status pages, wake observability, and automated deployment/upgrade.

### Dependencies

- Slice 7 complete product path is green.

### Acceptance criteria

- One turn produces one correlated timeline from utterance through terminal delivery without logging raw audio, transcript, prompt, or response by default.
- Every failure row in [`architecture.md`](architecture.md#7-failure-semantics) has an executable or controlled validation and the documented user-visible state.
- Readiness differs from process liveness and identifies incompatible/unloaded model or contract state.
- No fault silently switches model, host, cloud, auth mode, wake behavior, or visual architecture.
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
- LiveKit, web gateway, controller, STT, LLM, and TTS recovery on the one host.
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
- The complete core voice/avatar path still meets its Slice 9 thresholds with wake enabled.
- Disabling or rolling back wake restores the unchanged core MVP.

### Validation method

Run fixed positive/negative/noise corpora, a sustained ambient false-accept session, latency/resource capture, artifact corruption, enable/disable rollback, and a real tailnet hands-free conversation. If custom training occurs, reproduce the model from pinned inputs/tooling while separately verifying private-data deletion.

### Evidence required to close the optional slice

- Scope decision and preregistered thresholds.
- Recall/false-accept/latency/resource report tied to artifact hash.
- Privacy/retention evidence and corruption/fail-safe results.
- Core MVP regression evidence with wake enabled and then rolled back.

## What is intentionally not on this roadmap

3D, A2F, A2E, kiosk mode, a multi-host topology, public deployment, a separate auth project, and wholesale legacy migration have no active slices. Adding one requires a new product decision, architecture impact review, and dependency-ordered vertical slice rather than reopening stale legacy backlog assumptions.

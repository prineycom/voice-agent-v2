# Verification tiers and invariant ownership

This document is the command authority for tests. Historical slice reports remain dated evidence; they do not create additional current gates.

## Canonical PR tier

Provision dependencies once, without running tests, typecheck, or builds:

```sh
./setup-test-runtime
```

Then run the only PR command:

```sh
./verify
```

`./verify` owns a 90-second monotonic deadline and runs these phases in order:

1. the deterministic zero-secret tracer;
2. the Node.js 26 signed-release/launcher phase: canonical Ed25519 channel freshness and sequence, artifact/manifest/platform/protocol binding, unsafe archive denial, exact legacy selected/running custody, missing rollback, invalid/unowned/unrelated rejection, content-free zero-mutation status/doctor, and two byte-identical executable SEA builds that run without another host runtime;
3. the sole historical E2.2/E2.3 operation custody/failure matrix under IP-network denial, with its own 8-second bound;
4. the explicit current Python behavior manifest under IP-network denial, including strict V2/no-selector and V1 one-way-upgrade tests plus deterministic fake-Docker/network/transport/disposable-root evidence for locking, lazy single creation, exact reuse/start, ordinary binary/range/hash/atomic-file semantics, logical-cwd persistence/fallback, one credential declaration and private custody, create/per-exec rotation, no ambient inheritance, normal/disabled network transcripts, HTTP/download/upload/multipart/remote-Git transcripts, exact binary controller streams, acknowledgement/unknown/no-retry, display-only redaction, opaque background receipts, bounded poll/log/wait/write/kill, rootfs+fresh-`/proc` PID-reuse defence, foreground/background cancellation separation, typed mount custody/RO-RW/spec drift, precise survival truth, stale/duplicate/uncertain failure, exec-only routing, at-most-once receipts, resources, selector-free confirmation, atomic rebuild selection/retained nonselection, conflict disclosure, exact lifecycle reinspection, E4.1 frozen outcome/refinement/multi-source/citation/redirect/truncation/cache/error/persistence/hostile-authority/containment/raw-byte-redaction evidence, E4.2 fake-Telegram atomic save-before-send, exact document/network hashes, fixed target/credential authority, text/caption bounds, acknowledged/failed/unknown/no-auto-resend, persistence/reconciliation and explicit restart-resend evidence, plus E4.3 trusted later resolution, zero-network local summary, expected-revision/hash atomic update/conflict, explicit fresh-receipt refresh, current-byte resend, closed admission, accepted synthetic RW/credential egress, denied unmounted-host/lifecycle/selection authority, raw-byte redaction and clean-next-voice evidence (the historical matrix is not selected again);
5. the bounded local-socket readiness/VAD phase;
6. the installed SDK, capability, and active-composition contract;
7. the complete Vitest surface once;
8. TypeScript typecheck once, then one production and one review-fixture Vite build without repeated typecheck;
9. a short production-entry Firefox smoke through an actual task-owned local LiveKit.

Every phase is a separate process group. The owner sends `TERM`, waits at most 10 seconds, then sends `KILL`; detached children are found by a per-run environment nonce. A leaked process, TCP listener, or private artifact makes the gate fail even if final cleanup succeeds. Python skips also fail. Once its task-owned LiveKit and real Firefox are ready, the functional browser smoke is bounded to 15 seconds and injects regressions for ReviewStand at the production URL, an invalid capability, missing current-generation PCM, and stale request/media correlation. Cold Firefox/GeckoDriver provisioning remains inside the canonical 90-second deadline rather than weakening the behavior budget; browser cleanup has its own 10-second maximum and leak assertion.

`./verify --tracer-only` remains the narrow immutable-release recovery check. It does not run the PR suite and requires only Python 3.11+.

The launcher phase receives no production key, network, Docker daemon, service mutation or live install root. It uses only a committed public fixture key/pre-signed bytes, disposable roots, an injected service probe and Node.js built-ins. GitHub records an isolated Node.js 26 executable for direct SEA construction while retaining the established Node.js 22 application/browser toolchain on `PATH`; the canonical host similarly injects its system Node 26 only into the launcher phase.

### Direct pull-request path

Run `./verify` locally once, push the feature branch, then open or update the pull request. GitHub CI runs the same bounded command at the exact branch head. No second repository-local review/fix pipeline or cumulative slice gate participates in shipping.

## Hosted extended tier

```sh
./verify-extended
```

This command has a 600-second outer deadline and owns the longer mute/unmute, reconnect, diagnostics-download, production/review isolation, detached guardian, and repeated interruption scenarios. It uses deterministic inference over actual Firefox and local LiveKit; it does not claim hardware acceptance.

## Installation/update platform tiers

The launcher deterministic phase is not install/update evidence. Later installation slices require separate authorized tiers:

- **Disposable Linux install root/user service:** exact signed artifact install, private XDG custody, user systemd/linger, exact readiness and no shared/project service mutation.
- **Transaction interruption/VM power loss:** kill or reboot at every durable journal, download, extraction, migration, stop, pointer, readiness, restoration, GC and launcher-replacement phase; next invocation converges to candidate healthy or prior healthy.
- **Legacy migration:** exact current selected/running split fixture and then one separately authorized live migration only after automatic restoration is complete; no manual pointer repair.
- **Upgrade/downgrade:** every supported N-1→N, candidate failure→exact N-1, schema migration/restore, expired/cached/offline metadata and bridge-launcher floor.
- **Physical/reboot:** normal boot, real voice after install and after rollback, full local stack/resources and licensing approval. Deterministic signature/status evidence proves none of these.

## E2.4–E4.3 platform and production-evidence tiers

These are separate authorized evidence activities, not part of the deterministic PR and not interchangeable:

- **Linux Docker Engine:** exact client/server/context, rootless-or-rootful authority, native digest, cgroups/effective limits, storage, mounts/namespaces/security, lock/duplicate behavior, lazy create and same-ID reuse, ordinary binary/archive/local/remote-Git/package workflow, DNS/TLS and network-none behavior, exact synthetic credential rotation/absence of ambient state, binary HTTP/controller streams and acknowledgement truth, opaque process reconciliation and foreground/background cancellation separation, actual RO/RW additional bind authority/custody, controller death, explicit stop→same-ID start with process/tmpfs loss, atomic rebuild/retained lifecycle, daemon restart, stale spec, exec ambiguity, reserve pressure, saved workspace report survival, later revisioned update/current-byte stream after gateway/controller restart, the controlled hostile RW/synthetic-credential authority plus unmounted-host/socket/process/device/control denial matrix, and explicit fixture/remote cleanup.
- **Docker Desktop/macOS:** native Desktop/VMM/server/digest/VM resources, shared-root and container/VM-network semantics, the same ordinary/network/credential/stream/remote-Git/background/additional-mount/exact-lifecycle matrix, lazy reuse and controller-event process survival, Desktop quit/start with same files/ID and lost processes/tmpfs, unavailable/no-fallback behavior, upgrade/VMM revalidation, disk-image reserve, and explicit fixture cleanup. Linux results prove none of these Mac facts.
- **Exact model/live network and exact-Pasha delivery:** natural RU/EN multi-step ordinary-file/package/archive/local/remote-Git, bounded HTTP/stream, start/poll/write/log/wait/kill and iterative cited-research/save-deliver tasks against the exact pinned model use only separately authorized synthetic endpoints/credentials. Research evidence records actual URLs/receipts, refinement, independently fetched sources, citation quality, cache/error truth and costs. Exact-Pasha Telegram evidence is separately authorized and records the pinned tuple, acknowledgement, readable bounded text/document, current artifact revision/document hashes, induced unknown with zero automatic resend, and explicit current-byte resend after gateway restart without implicit research. A separate controlled endpoint may demonstrate accepted synthetic-credential egress; no live personal credential is used in that boundary fixture. It never records the bot token and is neither an E2.1 rerun nor a provider-spend/confidentiality claim.
- **Physical/reboot:** host reboot, real microphone/audible Kseniya, rapid barge-in, full-stack soak and physical resource truth. Synthetic Docker/model/browser evidence cannot close it.

Runtime code never installs or configures Docker, daemon access, rootless/cgroup prerequisites, Desktop resources/file sharing, images, or backups to make a tier pass.

## Canonical-host extended tier

Each command has its own 600-second monotonic outer timeout and process cleanup owner:

```sh
./verify-local-lfm
./verify-silero-kseniya
./verify-real-stt
./verify-canonical-host --config <mode-0600-sanitized-copy-outside-git>
```

These commands require their exact cache, GPU/runtime, corpus, or user-systemd boundary. Missing hardware/artifacts are an unavailable/failing tier, never synthetic green coverage. `verify-canonical-host` starts only its disposable user service; it does not install the product unit or reboot.

E2.1 adds the one-shot proposal-measurement mode:

```sh
./verify-local-lfm --tool-proposals-only
```

It owns the exact preregistered 240-fixture × five-order × three-mechanism matrix, at most two requests, no fallback, a 570-second inner evidence reserve, and the existing 600-second outer cleanup deadline. Evidence is counts and stable failure classes only. Its single authorized run did not enter the matrix because the committed nested CLI rejected the already-consumed selector; the recorded tier is unavailable/incomplete with `model_operation_proposals_unavailable`, not green evidence. The command must not be retried or replaced by another provider/model run for this preregistration.

## Historical evidence-integrity tier

```sh
./verify-evidence
```

This 120-second offline command validates the frozen Slice 2–5 benchmark/evidence schemas, preregistration ancestry, fixtures, and privacy guards. LiteLLM/Qwen executable runtime verification is retired: the dated evidence stays factual, but it is not supported current runtime tooling and cannot block the active PR gate.

Run this command explicitly or when `benchmarks/**`, its schemas/validators, or evidence-integrity wiring changes.

## Physical Acceptance tier

Automation does not close any of these product gates:

- normal Russian microphone → Whisper → local LFM → audible Kseniya;
- natural joins for numbers/date/time/PDF/SSD/`ё` and multi-segment gaps;
- two rapid physical barge-ins with old audio stopped and never resumed;
- resident LFM + Whisper + two Silero workers + LiveKit + browser resource/latency reserve;
- 20 real turns and avatar frame health;
- installed production unit through a normal reboot;
- a real voice turn after rollback;
- Raspberry Pi visual/frame acceptance;
- separate Silero production/commercial licensing and legal approval.

The operator procedure remains in [`evidence/silero-kseniya-48k-private-evaluation.md`](evidence/silero-kseniya-48k-private-evaluation.md). Synthetic RTP, PCM, WebAudio, callbacks, and process counters must not be relabelled as physical evidence.

## Historical defect probes

| Defect class | Current owner |
| --- | --- |
| Production URL serves `ReviewStand` | short actual Firefox/LiveKit smoke in `./verify`; production surface is tested against an injected review build |
| Wrong LFM/TTS identity or fallback | local-LFM, Silero, capability, reducer, configuration, and operations owners in `./verify`; exact identities in canonical-host commands |
| One Silero worker incorrectly reports ready | `tests.test_slice6_livekit_runtime.TTSHealthSnapshotTests` and `tests.test_silero_tts.SileroPoolTests` |
| Stale request/media generation publishes PCM or terminal | realtime/LiveKit owners plus injected browser stale generation |
| Cancellation publishes two terminals, skips cleanup, or leaks operation data into a replacement turn | the sole bounded E2 operation custody/failure matrix plus realtime cancellation and cooperative-cleanup owners |
| Transcript/audio/secret reaches diagnostics | observability, diagnostics, run-voice-turn, and browser diagnostics owners |
| Config/release symlink or pointer race is accepted | operations release/configuration owners |
| Agent profile parser ambiguity, unsafe custody, content-bearing CLI output, or non-empty production capability registry | `tests.test_agent_config` in the hermetic behavior manifest |
| Historical V1 startup reloads live, repairs input, leaks content, or degrades the bounded voice path | `tests.test_agent_profile_runtime` retained as historical compatibility coverage |
| V2 admits a selectable identity/backend/raw Docker argument or carries V1 identity forward | `tests.test_agent_environment.ConfigV2Tests` in the hermetic behavior manifest |
| Concurrent first use duplicates an environment; stale/uncertain Docker truth creates; tools reach host execution; ambiguous exec repeats; normal events tear down state | deterministic `FakeDocker` cases in `tests.test_agent_environment` |
| Background receipt leaks a PID, stale/tampered/PID-reused identity signals, foreground cancellation kills unrelated work, mount custody drifts silently, or rebuild selects before validation | `tests.test_agent_environment` plus disposable real-process `tests.test_agent_environment_processes` in the hermetic manifest |
| Proposal corpus/order/schema drift, unsafe envelope parsing, mutable thresholds, test capability leakage, or evidence relabelling | `tests.test_tool_proposals_benchmark` in the hermetic behavior manifest; exact-model outcome remains a separate canonical-host fact |
| Corrupt/incompatible prior release moves pointers | operations rollback owners |
| Forged/expired/sequence-rollback channel or artifact size/hash/platform/protocol mismatch is accepted | Node.js launcher contract phase in `./verify` |
| Archive traversal, absolute/duplicate/device/escaping-link/undeclared output is admitted | platform-manifest/archive index cases in the launcher phase |
| Status/doctor writes, infers health from a pointer, hides selected-new/running-old, adopts an unowned release, or exposes content/path/argv | disposable legacy/status/doctor zero-mutation and privacy cases in the launcher phase |
| Gateway dies while adapter descendant survives | real adapter parent-death and startup descendant cleanup owners |
| Alive nonresponsive LiveKit/LFM remains ready | bounded local-socket Slice 9 and backend-readiness owners |

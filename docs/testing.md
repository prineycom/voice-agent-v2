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
2. the Node.js 26 signed-release/launcher phase: canonical Ed25519 channel freshness and sequence, artifact/manifest/platform/protocol binding, unsafe archive denial, exact legacy selected/running custody, content-free zero-mutation status/doctor, two byte-identical executable SEA builds, the deterministic fresh-install/canonical-update/I4-adoption matrices, plus I5's strict config-v2 completeness, exact owner-only explicit rootless authority, production inspect parser, content-free image/config/rootfs/workspace/cache/mount/network/resource inventory, disabled/ready/endpoint/missing/stopped/unhealthy/stale states, identical before/after custody through candidate success/failed-safe restoration/recovery/adoption, replacement rejection, same/cross-filesystem durable migration, pre-I5 journal compatibility, non-GC truth and privacy-safe receipts; I6 verified asset/offline/space/self-update cases; and I7 recorded-prior rollback, closed-category uninstall and deterministic privacy-scanned local support-bundle matrices;
3. the sole historical E2.2/E2.3 operation custody/failure matrix under IP-network denial, with its own 8-second bound;
4. the explicit current Python behavior manifest under IP-network denial, including strict V2/no-selector and V1 one-way-upgrade tests plus deterministic fake-Docker/network/transport/disposable-root evidence for locking, lazy single creation, exact reuse/start, ordinary binary/range/hash/atomic-file semantics, logical-cwd persistence/fallback, one credential declaration and private custody, create/per-exec rotation, no ambient inheritance, normal/disabled network transcripts, HTTP/download/upload/multipart/remote-Git transcripts, exact binary controller streams, acknowledgement/unknown/no-retry, display-only redaction, opaque background receipts, bounded poll/log/wait/write/kill, rootfs+fresh-`/proc` PID-reuse defence, foreground/background cancellation separation, typed mount custody/RO-RW/spec drift, precise survival truth, stale/duplicate/uncertain failure, exec-only routing, at-most-once receipts, resources, selector-free confirmation, atomic rebuild selection/retained nonselection, conflict disclosure, exact lifecycle reinspection, E4.1 frozen outcome/refinement/multi-source/citation/redirect/truncation/cache/error/persistence/hostile-authority/containment/raw-byte-redaction evidence, E4.2 fake-Telegram atomic save-before-send, exact document/network hashes, fixed target/credential authority, text/caption bounds, acknowledged/failed/unknown/no-auto-resend, persistence/reconciliation and explicit restart-resend evidence, plus E4.3 trusted later resolution, zero-network local summary, expected-revision/hash atomic update/conflict, explicit fresh-receipt refresh, current-byte resend, closed admission, accepted synthetic RW/credential egress, denied unmounted-host/lifecycle/selection authority, raw-byte redaction and clean-next-voice evidence (the historical matrix is not selected again);
5. the bounded local-socket readiness/VAD phase;
6. the installed SDK, capability, and active-composition contract;
7. the complete Vitest surface once;
8. TypeScript typecheck once, then one production and one review-fixture Vite build without repeated typecheck;
9. a short production-entry Firefox smoke through an actual task-owned local LiveKit.

Every phase is a separate process group. The owner sends `TERM`, waits at most 10 seconds, then sends `KILL`; detached children are found by a per-run environment nonce. A leaked process, TCP listener, or private artifact makes the gate fail even if final cleanup succeeds. Python skips also fail. Once its task-owned LiveKit and real Firefox are ready, the functional browser smoke is bounded to 15 seconds and injects regressions for ReviewStand at the production URL, an invalid capability, missing current-generation PCM, and stale request/media correlation. Cold Firefox/GeckoDriver provisioning remains inside the canonical 90-second deadline rather than weakening the behavior budget; browser cleanup has its own 10-second maximum and leak assertion.

`./verify --tracer-only` remains the narrow immutable-release recovery check. It does not run the PR suite and requires only Python 3.11+.

The launcher phase receives no production key, network, Docker daemon, live service mutation or live install root. Foundation cases use the committed public fixture key/pre-signed bytes; install cases generate an ephemeral test key and use bounded artifact entry bytes, disposable XDG roots, deterministic host/space/assets, fake `sudo -n loginctl`/user-systemd and fake readiness/listener ownership. No fixture accepts a production root. GitHub records an isolated Node.js 26 executable for direct SEA construction while retaining the established Node.js 22 application/browser toolchain on `PATH`; the canonical host similarly injects its system Node 26 only into the launcher phase.

### Direct pull-request path

Run `./verify` locally once, push the feature branch, then open or update the pull request. GitHub CI runs the same bounded command at the exact branch head. No second repository-local review/fix pipeline or cumulative slice gate participates in shipping.

## Hosted extended tier

```sh
./verify-extended
```

This command has a 600-second outer deadline and owns the longer mute/unmute, reconnect, diagnostics-download, production/review isolation, detached guardian, and repeated interruption scenarios. It uses deterministic inference over actual Firefox and local LiveKit; it does not claim hardware acceptance.

## Installation/update platform tiers

The launcher deterministic phase is executable install-contract evidence but not physical platform/reboot evidence. Separate authorized tiers remain required:

- **Disposable real Linux user service:** published exact signed artifact install, real private XDG custody, user systemd/linger, NVIDIA/model/runtime startup, exact readiness and no shared/project service mutation.
- **Transaction interruption/VM power loss:** kill or reboot at every durable journal, download, extraction, migration, stop, pointer, readiness, restoration, GC and launcher-replacement phase; next invocation converges to candidate healthy or prior healthy.
- **Legacy migration:** exact current selected/running split fixture and then one separately authorized live migration only after automatic restoration is complete; no manual pointer repair. The live gate records canonical AgentEnvironment registry/rootfs/workspace/cache identities before/after and proves the legacy cache-class tree migrated without deletion or release placement.
- **Rootless Docker preservation:** actual explicit owner-only socket and generated-service endpoint, daemon/root-directory identity, rootless user namespace, cgroup-v2/systemd, same container/rootfs/workspace/cache/mount identity through candidate success and failed-safe rollback, optional endpoint/container degradation, and stale-spec non-mutation. Rootful/foreign/group-authorized/ambient authority is a failed tier, never fallback permission.
- **Upgrade/downgrade:** every supported N-1→N, candidate failure→exact N-1, selector-free recorded rollback, schema migration/restore, expired/cached/offline metadata and bridge-launcher floor.
- **Uninstall/support:** disposable real user-service/filesystem removal with default preservation and every explicitly confirmed purge root, exact rootless Docker owned-container removal, interruption/race handling, and an independently inspected local support archive. No shared service/data, external mount, ambient Docker object or upload is authorized.
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
| Fresh install mutates before host/signature/archive/space proof, exposes roots/secrets, weakens service policy, claims optional tools as voice failure, starts twice, or commits before exact 5/5 loopback readiness | deterministic injected install cases in the launcher phase |
| Update loses exact running healthy custody, stops before staging/migration proof, repeats a blind start, accepts wrong release/readiness/listener, fails to restore prior, deadlocks on release count, deletes an unreachable unsafe/user/AgentEnvironment target, or lies about offline latest | every-write/action interruption, migration, candidate/prior failure, identity, GC/space, concurrent-lock and offline cases in `launcher/test/update.test.cjs` under the same launcher phase |
| Install/update/adoption accepts rootful/foreign/group-authorized/ambient Docker, changes AgentEnvironment ID/rootfs/workspace/mount custody, rebuilds stale spec, GC's durable state, leaks content, or rejects a pre-I5 interrupted journal | `launcher/test/agent-environment-preservation.test.cjs` plus install/update/adoption success, rollback and interruption owners in the Node.js 26 launcher phase |
| Asset acquisition accepts changed authority/redirect/range/validator/oversize/hash/signature, selects a partial, duplicates a complete asset, GC deletes referenced/durable bytes, disk blocks on counts, offline weakens freshness, or launcher replacement precedes application health/loses exact backup custody | `launcher/test/assets-self-update.test.cjs` plus the channel/offline/update cases, including every launcher receipt/rename/fsync/exec/validation interruption and privacy-safe recovery |
| Explicit rollback accepts a selector/incompatible prior, loses active↔rollback custody, starts twice, fails to restore exact entry health, or loses interruption evidence | recorded-prior success/failure/double-failure/compatibility/retention/interruption cases in `launcher/test/update.test.cjs` |
| Uninstall broadens deletion, removes preserved data/linger/external mounts, leaves an enabled unit pointing at removed bytes, accepts linked/foreign/rootful/ambient Docker state, or support includes forbidden content/uploads/unsafe output | closed inventory/confirmation/purge/race/idempotence/rootless and deterministic archive/scanner cases in `launcher/test/lifecycle.test.cjs` |
| Gateway dies while adapter descendant survives | real adapter parent-death and startup descendant cleanup owners |
| Alive nonresponsive LiveKit/LFM remains ready | bounded local-socket Slice 9 and backend-readiness owners |

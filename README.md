# Voice Agent v2

Voice Agent v2 is a private, local-first voice companion centered on one Arch Linux PC. Local LiveKit, speech-to-text (STT), and text-to-speech (TTS) form the realtime voice path. A local large-language model (LLM) is the initial preference; an explicitly selected cloud LLM is allowed only if measured local candidates cannot meet the real VRAM, latency, and quality budget.

The MVP visual is an original deterministic animated AI eye, not a Live2D-first avatar. Its pupil can follow a bounded external target or move in a bounded idle pattern, blink, change palette and state behavior, and replace the pupil with a thinking indicator. An outer ring or pattern pulses from actual speech playout. A renderer-agnostic avatar boundary allows later custom, Live2D, or 3D modules without letting an LLM generate frames.

## Status

**The accepted simple lifecycle now includes deterministic fresh install, transactional update, legacy adoption, and durable AgentEnvironment preservation.** The separately versioned Node.js 26 SEA `voice-agent` launcher owns signed read-only `status`/`doctor`, `install`, and canonical `update` for Linux `x86_64` systemd/NVIDIA. Its config-v2 defaults are explicitly disabled and name the installation/XDG durable registry, rootfs-storage custody, workspace and cache without inventing Docker, credentials, Telegram or mounts. If an existing environment is configured, every install/update/failed-safe rollback/recovery/adoption records the same exact container/rootfs/workspace/cache/mount identity before and after; unavailable/missing/stopped/unhealthy/stale states degrade tools only, while replacement aborts the transaction. Legacy cache-class state migrates by exact rename or staged no-follow hash/CAS/fsync copy and is outside release/asset GC. The launcher performs no container lifecycle or automatic rebuild. Production signing/channel publication and real-host/Docker/reboot/physical acceptance remain pending; all mutation evidence uses isolated deterministic fixtures and never touches the live stand. Explicit rollback, self-update, uninstall and broader cache hardening remain later slices. ADR-0014, ADR-0015 and [`docs/architecture.md` §6.7](docs/architecture.md#67-launcher-owned-installation-and-update-boundary) are authoritative.

**E4.3 completes later report retrieval, revisioned update, optional bounded refresh, and current-byte exact-Pasha resend in the same single Docker AgentEnvironment.** `report.artifact` resolves only a trusted opaque artifact receipt or an exact bounded `reports/…` index match. `local_summary` returns existing container bytes with zero external calls; `local_update` performs expected-revision/SHA-256 atomic replacement; `bounded_refresh` accepts only actual new successful fetch receipts. Every commit records current bytes/hash/revision and a prior-receipt link. A concurrent writer mismatch is visible and unexpected current bytes are not silently replaced. `report.deliver` rereads the current committed revision through Docker-exec; resend does not research unless the chosen plan separately did so.

The direct-private Pasha chat/user/no-thread tuple remains restart-pinned, the Bot API endpoint and `TELEGRAM_BOT_TOKEN` name remain release-fixed, and acknowledgement/unknown/no-auto-retry custody is unchanged. Model/content/filename metadata cannot select a recipient, credential, endpoint, environment, lifecycle action, mount, port, runtime or host path. Display redaction changes only UI/history/log/support copies—not model-needed results, files/patches, injected credentials, Web/Git/API requests, report/Telegram bytes or deliberate exfiltration.

This is an open sandbox, not prompt-injection prevention: hostile content stays inert until a later valid admitted decision, but that decision may alter/delete RW state and transmit an exposed credential to a reachable endpoint. RO data is readable/exfiltratable. The only containment claim is that a correctly configured unescaped container cannot directly reach unmounted host paths/processes/sockets/devices; no Docker/kernel escape resistance is claimed. Synthetic PR evidence uses fake Docker/research/Telegram/credentials only and remains separate from Linux Engine, Docker Desktop/macOS, exact-model/live-network/exact-Pasha Telegram, reboot, physical voice/full-stack and Raspberry Pi acceptance. There is no injection detection, taint/DLP/domain filter, rollback, automatic retry/refresh, alternate runtime or host fallback.

Silero is licensed CC BY-NC-SA 4.0. This branch authorizes only private local noncommercial evaluation; it is not a production/commercial recommendation or authorization. Separate licensing and legal review are mandatory before any merge, production, or commercial use. Historical DeepSeek/Qwen evidence and contracts remain factual and inactive.

## Verification tiers

Provision pinned PR dependencies without running tests or builds, then use the one canonical PR command:

```sh
./setup-test-runtime
./verify
```

`./verify` has a hard 90-second monotonic deadline, explicit Python/local-socket/web/browser phases, zero tolerated skips, process-group plus detached-child cleanup, the full Vitest surface once, one typecheck, one build per entry mode, and a short production Firefox smoke over an actual task-owned local LiveKit. The smoke rejects ReviewStand at the production URL and invalid capability data, requires current-generation PCM/correlation, and fails on owned leaks. GitHub CI runs this same bounded command at the exact pull-request head.

Long browser lifecycle scenarios are separate:

```sh
./verify-extended
```

Canonical-host exact-cache commands are `./verify-local-lfm`, `./verify-silero-kseniya`, `./verify-real-stt`, and `./verify-canonical-host`. Frozen benchmark/evidence integrity is `./verify-evidence`; it is not an active runtime gate. Every extended command has a hard outer timeout. See [`docs/testing.md`](docs/testing.md) for phase ownership, timeouts, transition traceability, and the unchanged physical Acceptance tier.

The direct pull-request path is intentionally simple: run `./verify` locally once, push the feature branch, open or update its pull request, and let GitHub CI run the same command at the exact head. Do not add a parallel repository-owned review/fix pipeline or rerun cumulative slice gates.

The narrow immutable-release check remains available without the test runtime:

```sh
./verify --tracer-only
./verify --output-directory ./slice-1-artifacts
```

The destination must not already exist. The preserved deterministic tracer PCM is signed 16-bit little-endian mono at 16 kHz; it is reproducibility evidence, not physical microphone or speaker acceptance.

## Signed launcher foundation

The source entry is `launcher/voice-agent.cjs`; Node.js 26 can package it deterministically as one self-contained executable with no extra compiler or language toolchain:

```sh
./launcher/build ./dist/voice-agent
./dist/voice-agent install
./dist/voice-agent update
./dist/voice-agent update --offline
./dist/voice-agent status
./dist/voice-agent status --json
./dist/voice-agent doctor
./dist/voice-agent doctor --json
```

`status` and `doctor` remain strictly read-only. Their `Agent tools` field is one of `disabled`, `ready`, `degraded_endpoint_unavailable`, `degraded_identity_mismatch`, or `stale_spec`, with an explicit action and no workspace filenames/content, commands, output, environment or credential values. `install` and `update` accept no root/user/platform/channel selector: production layout derives only from the invoking user's XDG identity. `update --offline` may reconcile only signed cached metadata and explicitly says latest is unknown. Online metadata unavailability leaves and reports the current healthy release without claiming freshness. Pre-quiesce failures leave the service unchanged; `update_failed_safe` means the candidate failed but the exact prior release/config/unit was restored and proved ready, while `update_failed_needs_repair` retains journal, snapshot and both releases without claiming health. `migration_requires_decision`, `insufficient_space`, and `update_in_progress` are pre-mutation/actionable failures. Deterministic tests inject isolated roots plus fake host/archive/service/readiness/network owners and never mutate live systemd, network, Docker or the stand. The repository carries only public fixture authority; production signing-key provisioning/publication is intentionally not claimed.

## Slice 6 Silero/Kseniya development application

One-time setup acquires pinned LiveKit/browser tooling and verifies local model prerequisites. Silero setup is strictly verify-only: it never downloads, replaces, modifies, copies, or relocates the exact existing artifact.

```sh
./setup-slice6
./setup-silero-kseniya
(cd web && npm run build)
cp .env.slice6.example .env.slice6
```

Generate a dedicated LiveKit key pair and fill every blank in ignored `.env.slice6`:

```sh
"${XDG_CACHE_HOME:-$HOME/.cache}/voice-agent-v2/slice-6/tooling/livekit-server-v1.13.5" generate-keys
```

The active app accepts no `LITELLM_*` or TTS-selection configuration. `./verify` owns the model-free browser/runtime boundary. `./verify-local-lfm` and `./verify-silero-kseniya` are separate exact-cache canonical-host checks. E2.1's one-shot `./verify-local-lfm --tool-proposals-only` attempt is already consumed for its frozen preregistration and recorded unavailable; do not rerun it. Its machine decision remains `model_operation_proposals_unavailable` and is never rerun or relabelled. E2.4 is a new versioned production AgentRun path rather than promotion of that consumed attempt.

`./verify-silero-kseniya` loads exactly two resident CPU workers from the pinned read-only model cache, proves native 48-kHz totals, obsolete/current overlap with stale discard, full two-worker RSS/CPU, VAD/Whisper coexistence, and explicit controlled recovery. It writes content-free task evidence only under `~/.cache/voice-agent-v2/experiments/silero-kseniya-48k-ship/`. It does not claim audibility, voice quality, physical barge-in, or the complete browser stack. The historical Qwen/LiteLLM executable harnesses are retired; their dated evidence remains under the explicit `./verify-evidence` tier.

Start the foreground development stack with:

```sh
./run-slice6
```

Startup clears unrelated inherited `LITELLM_BASE_URL`/`LITELLM_TOKEN_FILE` before loading operator-owned `.env.slice6`; either forbidden name configured in that file still fails closed. It verifies the exact LFM and Silero identities before starting local llama.cpp, loopback-only LiveKit, and the loopback-only gateway/controller. It creates, mutates, monitors, and removes no reverse-proxy or private-network route. Optional external exposure is entirely operator-owned and must preserve appropriate private-network/access controls; the application behaves the same without it. The agent publishes one persistent 48-kHz LiveKit source; request/media generations determine when the browser may attach it. Microphone input remains explicitly 16 kHz. Shared services, firewall, and external exposure settings are not modified by setup or verification.

### Slice 7 avatar/UI verification and review stand

The full avatar/UI Vitest surface and production/review entry isolation run once in `./verify`; mute/reconnect browser lifecycle runs in `./verify-extended`. The browser evidence and explicit physical/full-stack gaps are recorded in [`docs/evidence/slice-7-ui-avatar.md`](docs/evidence/slice-7-ui-avatar.md).

A clean committed head can be deployed for physical acceptance through the existing safe local runtime. The stable stand builds the ordinary production entry and starts local LFM, loopback LiveKit, gateway/controller, and STT/TTS. It configures no external exposure, proxy, firewall, or private-network route:

```sh
./run-review-stand start
./run-review-stand status
./run-review-stand restart
./run-review-stand stop
```

The launcher prints the exact compiled commit and stable loopback URL. The first page is deliberately disconnected: select **CONNECT** to mint a same-origin room capability, join its validated LiveKit endpoint, request microphone permission, and publish the microphone. Conversation history remains empty until genuine server events arrive, and the compact control visibly reports the real microphone lifecycle throughout admission, listening, mute, and failure. The synthetic `ReviewStand` is built only by `npm run build:review` under `web/review/dist`; it is never served by this launcher.

### Slice 8 failure/observability verification

`./verify` executes every current failure/readiness/privacy owner, including public capture deletion and real disposable process-loss recovery. Detached guardian longevity belongs to `./verify-extended`. Neither tier stops shared services, manipulates external exposure, exhausts RAM/VRAM, or claims physical coverage.

Default metadata diagnostics contain no conversation/media content, secrets, exception messages, or content-bearing identifiers. Explicit content capture is off by default. To enable it, add the exact settings documented in [`.env.slice6.example`](.env.slice6.example) to ignored server configuration. Development capture remains outside Git beneath the exact non-lingering `/run/user/<uid>` runtime tmpfs; the system service instead requires its systemd-owned `/run/voice-agent-v2` runtime directory so boot never depends on a user login. Read selected manifest status fields or delete a capture without enumerating its content. Deletion succeeds only while the capture still passes the lifetime-runtime, ownership, privacy-mode, manifest, and path guards:

```sh
./manage-diagnostics status <capture-directory>
./manage-diagnostics delete <capture-directory>
```

[`docs/architecture.md` §8.2](docs/architecture.md#82-data-handling) owns the capture limits, expiry, and privacy contract. The complete exercised matrix, example redacted report, privacy evidence, and exact nonclaims are in [`docs/evidence/slice-8-failure-observability.md`](docs/evidence/slice-8-failure-observability.md).

### Agent configuration and persistent Docker environment

Active configuration is strict [`voice-agent.config.v2`](contracts/agent-config.v2.schema.json), illustrated by [`config/agent-config-v2.example.yaml`](config/agent-config-v2.example.yaml). It couples explicit AgentRun/AgentEnvironment enablement and pins the verified `unix:///run/user/<uid>/docker.sock`, allowed OCI image/spec, stable installation ID, canonical durable registry/rootfs-storage/workspace/cache paths, decision budget, fixed tools, network policy, transfer/resource bounds, exactly one private credential-store reference containing exposed names and modes only, and one typed `additional_mounts` list. Enabled input requires every identity/authority field; first install/adoption and V1 upgrade remain disabled. Every extra mount has an absolute precreated user-owned non-symlink source, fixed container destination, and explicit `read_only`/`read_write` mode; custody and effective Docker facts enter spec compatibility. It contains no selectable identity, execution backend, environment alias/profile, raw credential value/source, persistence choice, task override, model path, or raw Docker arguments. Historical E1 V1 input can be inspected and upgraded once; the upgrade carries no former identity:

```sh
./voice-agent-ops agent-config upgrade-v2 --json
./voice-agent-ops agent-environment status
```

The application runtime's first admitted tool call lazily creates the installation's sole owner-labelled Docker container only after separately valid enabled configuration; the installation launcher never creates or adopts it. Later calls and AgentRuns reuse the exact selected ID, writable rootfs, managed `/workspace`, managed `/cache`, and logical cwd. Application release transactions only inspect the recorded exact ID through the explicit endpoint, persist content-free before/after custody, and never start, stop, remove, retire, prune or rebuild it. Every terminal/file/search/write/edit/patch/code/process/receipt/Web call enters a fixed helper through `docker container exec`; report save/read uses the same fixed byte-stream helper. Missing/unreachable Docker fails only the agent plane and never reaches host execution or another runtime.

`web.search` accepts a caller-chosen HTTP(S) search endpoint, query parameter and query; no provider is built in. `web.fetch` follows at most five visible redirects and can use fresh, cache-only, or stale-on-failure persisted bytes. `web.extract` derives bounded text from a persisted artifact. Search/fetch persist exact bounded response bytes plus a metadata sidecar and expose a model preview; citations use only normalized display URLs and controller-bound successful receipt IDs. Failed/inaccessible pages remain failed receipts, while cached/stale inputs, redirect history, retrieval time, content hash/size, truncation and extraction errors stay explicit. Model-needed page/tool/network/file bytes are never display-redacted; only UI/history/log/support/final accidental display copies are redacted. See [`docs/architecture.md` §9.11](docs/architecture.md#911-e41-bounded-cited-web-research).

Normal configuration uses Docker bridge egress and publishes no host port. A declared disabled fixture uses Docker network `none`. The controller Docker CLI has a private empty home/config and the helper constructs a minimal environment, so host `.env`, `.netrc`, Git/Docker helpers, SSH/GPG agents, sockets, keyrings, browser data, and user home are not inherited. Public credential names select one of four fixed modes: create environment/file or preferred per-exec environment/file. The sole private mode-`0600` installation document is `private/credentials.json` with schema `voice-agent.credentials.v1` and base64 values under exact `environment` and `files` maps; it is never enumerated to the model or copied to status. Per-exec values are resolved for each call; create-time values and keyed private fingerprints are restart-pinned and require explicit rebuild after `stale_spec`. `/run` credential files are `0600`, call-scoped, and tmpfs-backed.

Gateway/controller byte entrypoints accept only bounded bytes plus controller-chosen `workspace`/`cache` destinations. Fixed helpers validate length/SHA-256, fsync a `0600` temporary, atomically rename inbound data, and return outbound data only through Docker-exec stdout. Container paths are never host paths. A delivery becomes `sent` only after exact target/operation/length/hash acknowledgement; uncertainty is `delivery_outcome_unknown` and invokes no automatic repeat. Redaction creates display/log/support copies only: raw shell/model/file/header/body/multipart/Git/stream bytes are unchanged. See [`docs/architecture.md` §9.9](docs/architecture.md#99-e32-network-one-credential-configuration-streams-and-remote-git).

E4.2/E4.3 target state is `private/telegram-delivery.json`, owned by the service user with mode `0600`, containing only `{"schema_version":"voice-agent.telegram-delivery-config.v1","target":{"chat_id":<positive-private-id>,"user_id":<same-id>,"message_thread_id":null}}`. Add the fixed `TELEGRAM_BOT_TOKEN` name only to `agent_environment.credentials.exec_environment_names`; place its base64 value only in the existing `private/credentials.json` environment map. Neither file is tracked, enumerated to the model, or shown in status. `report.deliver` supports new save+send, explicit `{"action":"resend","artifact_id":"…"}`, and non-sending `{"action":"reconcile","delivery_id":"…"}` shapes. `report.artifact` separately admits `local_summary`, expected-revision/hash `local_update`, and new-receipt `bounded_refresh`; it accepts no selector, host path, lifecycle, mount, port or runtime authority. See [`docs/architecture.md` §§9.12–9.13](docs/architecture.md#912-e42-atomic-saved-reports-and-exact-pasha-telegram-delivery).

A shell/code operation with `background=true` returns an opaque receipt promptly. The fixed `process`/`receipt` route can later `poll`, read bounded `logs`, bounded `wait`, `write` bounded base64 stdin, or bounded TERM→KILL. Every observation/signal reconciles the private controller claim, persistent rootfs claim, and fresh `/proc` PID/start/executable/process-group plus PID 1 start identity; a missing/tampered/stale/PID-reused claim never authorizes a signal. Receipts contain no PID or container selector. Foreground cancellation validates its own separate call group and leaves unrelated background receipts alive. Internal listeners can conflict, but no host port is published.

Turn/session completion, cancellation, `/quit`, idle expiry, gateway restart, shutdown, and controller death do not stop or remove the environment. An explicitly stopped compatible container is started with the same ID: files/rootfs persist, old processes and sockets do not. Operator lifecycle accepts no selector; `status` is inspect-only, every destructive action requires confirmation/fresh exact custody, rebuild validates before atomic selection and retains the old stopped generation, and all host binds remain by default:

```sh
./voice-agent-ops agent-environment reset --confirm
./voice-agent-ops agent-environment rebuild --confirm
./voice-agent-ops agent-environment remove --confirm
./voice-agent-ops agent-environment retire --confirm
```

Data deletion is a separate authority not implemented by these commands. There is no prune, wildcard/prefix/age cleanup, automatic conflict repair, runtime image pull, Docker installation/configuration, or normal-event destruction. See [`docs/architecture.md` §9.10](docs/architecture.md#910-e33-background-processes-explicit-mounts-and-lifecycle-ux) for process, mount, lifecycle, persistence, conflict, and evidence boundaries.

### Slice 9 single-host operations

The CI-safe deterministic operational owners run in `./verify`. On the canonical host, run the read-only exact-cache plus disposable transient-systemd preflight through its bounded wrapper:

```sh
./verify-canonical-host --config <mode-0600-sanitized-copy-outside-git>
```

The second command starts no product service, uses no `sudo`, prints no secret, and touches only a uniquely named disposable user service. It creates a new private mode-`0700` deployment-state root under `/var/tmp`—not the smaller `XDG_RUNTIME_DIR` tmpfs to which the persistent 8-GiB bound does not apply—and deletes it on exit. It verifies exact selected artifacts/runtimes, ignored local configuration structure/mode, free-disk and active-cache bounds, then proves one completed restart and an actionable failed state. It does not inspect external exposure, reboot, or claim physical behavior. `--config <path>` may select a different mode-`0600` private file outside Git for a transitional read-only check.

A private config retained from the retired Tailscale-coupled runtime needs a one-time explicit migration before normal validation or deployment: remove only `SLICE6_LIVEKIT_NODE_IP`, `SLICE6_APP_HTTPS_PORT`, `SLICE6_SIGNAL_HTTPS_PORT`, and `SLICE6_ENABLE_TAILSCALE_SERVE`; preserve all other names/values and mode `0600`, without logging secret values. If the product service is installed, run `./voice-agent-ops install-service --restart` afterward and confirm exact readiness with `status`. For transition-only host evidence, use a temporary mode-`0600` sanitized copy outside Git via `--config`, delete it afterward, and leave the original untouched.

Operational activation is explicit. Keep the server configuration untracked and mode `0600`; no operation copies or prints its secret values:

```sh
./voice-agent-ops validate --config .env.slice6
./voice-agent-ops deploy --config .env.slice6
./voice-agent-ops install-service
./voice-agent-ops status
```

`deploy` requires a clean committed source, rejects a selected private configuration tracked by that commit, requires canonical-service configuration beneath the service user's readable home, installs the committed `package-lock.json` dependency tree from the local npm cache in the isolated release stage with lifecycle scripts disabled, builds the ordinary client with the exact commit, and atomically activates an immutable release under `~/.local/share/voice-agent-v2`. The complete payload inventory includes empty directories and modes as well as file/symlink hashes and targets. Runtime bytecode/temp/output goes only to explicit private mutable runtime/cache roots; the systemd unit does not make the release store writable. Its mode-`0700` `/run/voice-agent-v2` directory is created at boot without a user manager and removed on every stop, including automatic restart, so diagnostic captures may expire early and cannot outlive their TTL or reboot. Reapplying the same source/config locator/public configuration reports `changed: false`. Reaching a cache, free-disk, release-count, or release-byte bound fails without deletion. `install-service` is the only privileged step and uses only `sudo -n`; it semantically verifies the exact restart/sandbox policy, reloads systemd, reconciles/restarts an already installed unit after a changed deploy, and returns success only after systemd `Type=notify`, exact-release five-component readiness, and supervised ownership of every local runtime listener. Reapplying the already ready release is a no-op. The unit depends on no external exposure daemon and changes no proxy, private-network route, firewall, or unrelated unit.

LiveKit credentials remain only in the current-user mode-`0600` server configuration. Operational-release v2 stores no credential and no verifier derived from one; startup validates that file once and passes the exact parsed snapshot through the execution boundary. Secret changes are intentionally not detected by release identity or by an already-running process. For credential-only rotation, edit the mode-`0600` private configuration, run `./voice-agent-ops install-service --restart`, and confirm `./voice-agent-ops status`; that explicit command revalidates the active release and private configuration, restarts even at an unchanged release ID, and waits for exact-release readiness. Public configuration or release-payload changes still require `deploy`; its `release_service_apply_required` result refers only to release/unit payload changes, never credentials. This explicit restart limitation is part of the single-host contract, not automatic secret rotation.

The unit has a 300-second hard startup boundary for exact release/artifact validation and sequential component readiness, then starts local LFM → loopback LiveKit → loopback gateway/controller-owned STT/TTS/provider adapter. Shutdown removes application admission first, drains the controller/workers within the bounded 54-second budget, then closes LiveKit and LFM; the complete ordered cleanup budget remains below the 75-second hard stop. A runtime loss gets at most one completed systemd restart before an explicit operator reset; configuration/artifact incompatibility never restarts. `status` reports the exact build/release, local provider, loopback boundary, browser avatar contract/MVP eye, and external-cloud-not-supervised facts without secrets.

Rollback accepts only the recorded previous release and revalidates its complete inventory, referenced local configuration, exact external artifacts/runtimes, and disk/cache preflight before restarting. It does not inspect or alter optional external exposure. When the canonical service is installed, the prior release unit must also be byte-for-byte identical to the installed root-owned unit before either release pointer moves; a different lifecycle or sandbox policy requires a separate explicit service operation and rollback leaves the running service unchanged:

```sh
./voice-agent-ops rollback
```

It is not an arbitrary Git reset and never deletes a cache/release to force success. The narrow post-rollback deterministic check uses the same public Slice 1 tracer without recursively running the full test suite:

```sh
"$HOME/.local/share/voice-agent-v2/current/verify" --tracer-only
```

This focused recovery check is not a substitute for the ordinary full `./verify` gate. [`docs/evidence/slice-9-single-host-reliability.md`](docs/evidence/slice-9-single-host-reliability.md) records the exercised cases and exact remaining physical reboot/voice gaps.

### Pasha physical acceptance and rollback

Follow the authoritative acceptance and rollback checklist in [`docs/evidence/silero-kseniya-48k-private-evaluation.md`](docs/evidence/silero-kseniya-48k-private-evaluation.md). It owns the required Kseniya audibility/quality/join, repeated barge-in, loopback full-stack resource, evidence-handling, and rollback procedure. Optional external exposure is not an application acceptance requirement. Do not merge this branch until physical acceptance, separate licensing, and legal review are all complete.

## Final Slice 5 human acceptance

Pasha ran this canonical-host command against PR 14 head `f1a3de997296e6dac87203cf5c2e157945a865b8` on 2026-08-11:

```sh
./run-voice-turn --microphone --duration 8 --play
```

Pasha attested that the complete physical microphone capture, Whisper → `deepseek-v4-flash` → Qwen3 `ryan` turn, audible playback, and overall manual Slice 5 experience succeeded. No transcript, response, latency, pronunciation detail, quality adjective, or granular score was provided or inferred.

Microphone capture is bounded to 1–30 seconds. The command removes temporary microphone PCM before admitting inference, streams Qwen output without an output file, and writes only content-free evidence under the authorized cache. On this host PipeWire 1.6.8 may return status 1 after producing the exact requested bounded capture; the CLI accepts that case only when byte count is exact. Recorder availability/setup/startup, timeout, nonzero-without-complete-output, missing/unreadable/wrong-size output, and cleanup failures produce content-free `Failure: microphone_capture/<code>` and `Terminal: turn.failed` evidence without a traceback or model admission. Cleanup failure also reports whether microphone bytes may remain after deletion and scrubbing attempts. A live transcript crosses the exact operator-approved HTTP LiteLLM boundary; runtime DNS/route/TSMP proof is not enforced, redirects and fallback aliases remain forbidden, and HTTPS is deferred.

## Current scope

- One Arch Linux host runs every active media, orchestration, STT, TTS, application, and local-LLM process. Historical ADR-0004 evidence used an external relay for a superseded cloud measurement; it is not an active application dependency.
- A private browser experience uses local LiveKit and local STT/TTS. LLM access uses one explicitly configured provider mode, with local preferred initially.
- A cloud LLM may be selected only after local candidates fail preregistered resource, latency, or quality gates. It is never an automatic or silent fallback.
- The application defaults to loopback and relies on local-host access. Any optional external exposure and its private-network/access controls are entirely operator-owned.
- The MVP uses a deterministic custom AI-eye module behind a renderer-agnostic avatar boundary.
- The avatar runtime, never the LLM, owns pupil motion, blinking, speech-synchronous pulsing, palette/state behavior, interpolation, scheduling, and frames.
- Interruption, explicit failure behavior, privacy-safe observability, and measured resource budgets are part of the supported product.

## Deferred

- Physical Raspberry Pi/full-stack acceptance of the implemented avatar-module/UI contract remains required; the contract itself is fixed by Design Gate V and `voice-agent.avatar-host.v1`.
- Live2D and 3D remain possible later avatar modules; neither is an MVP renderer decision.
- Optional wake-word activation, including any custom Russian wake model, begins only after the core MVP is reliable.
- Production cloud identity/provenance, privacy/cost facts, and approval remain deferred. ADR-0006's private cloud testing permission is historical and superseded by the active local-only ADR-0008 path; every earlier provider failure and unknown remains explicit.
- Selective migration from the legacy repository waits for a concrete vertical slice and fresh validation.

## Non-goals

- A Raspberry Pi/Desktop inference or control split; ADR-0004's single LiteLLM relay is the only narrow topology exception.
- Cloud STT/TTS or silent/automatic failover between local and cloud LLM providers.
- Audio2Face (A2F) or Audio2Emotion (A2E) in the active V2 architecture.
- Kiosk mode.
- Automatic exposure, proxy management, wildcard/public listeners, or weakened access controls.
- Proprietary avatar assets, copied character design, or LLM-generated renderer parameters, keyframes, or per-frame animation data.

## Pinned legacy reference

The legacy repository is read-only evidence at [`prineycom/voice-agent@93c5c397`](https://github.com/prineycom/voice-agent/tree/93c5c39786ff790d7ae436772d2cf37a2eeb32c6). Future work must inspect that pinned tree rather than assume its default branch is unchanged.

When a vertical slice finds a useful legacy contract, component, or test:

1. record the pinned source commit and exact path;
2. bring over only the smallest unit required by that slice;
3. adapt it to V2-owned contracts and revalidate its behavior in V2;
4. do not import legacy architecture docs, backlog state, deployment assumptions, secrets, recordings, or model artifacts wholesale.

See the authoritative inspection and migration boundary in [`docs/architecture.md`](docs/architecture.md#34-pinned-legacy-reference).

## Authoritative documentation

| Document | Purpose |
| --- | --- |
| [`CONTEXT.md`](CONTEXT.md) | Stable project vocabulary. |
| [`docs/architecture.md`](docs/architecture.md) | Authoritative system boundaries, contracts, lifecycle, operations, provider/privacy rules, and resource policy. |
| [`docs/testing.md`](docs/testing.md) | Canonical PR, extended/canonical-host, evidence-integrity, and physical verification tiers. |
| [`docs/roadmap.md`](docs/roadmap.md) | Dependency-ordered implementation slices and their evidence gates. |
| [`docs/adr/`](docs/adr/) | Accepted decisions that are costly or confusing to reverse. |

When documents disagree, accepted ADRs govern the decision they record, `docs/architecture.md` governs the current system design, and `docs/roadmap.md` governs implementation order. `CONTEXT.md` defines terminology only.

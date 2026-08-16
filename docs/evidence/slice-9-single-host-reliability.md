# Slice 9 single-host operational reliability evidence

- **Issue:** [#11](https://github.com/prineycom/voice-agent-v2/issues/11)
- **Checkpoint date:** 2026-08-16
- **Status:** deterministic operations and canonical-host config/artifact/process/recovery/rollback preflight pass; physical reboot and physical voice/avatar acceptance remain explicitly unclaimed
- **Legacy source:** none inspected or used

## Operational design and ownership

[ADR-0013](../adr/0013-systemd-bounded-single-host-operations.md) fixes one deliberately small boundary:

- tracked [`config/operations-v1.json`](../../config/operations-v1.json) owns component coverage, start/stop order, restart/drain limits, exact selected artifacts/runtimes, non-destructive disk/cache/release limits, contract/client identities, inactive-cloud reporting, and sustained thresholds;
- tracked [`voice-agent-v2.service`](../../ops/systemd/voice-agent-v2.service) is one system-level systemd service running as non-root `priney`; systemd owns boot and the outer cgroup, not inference requests or provider choice;
- the existing foreground runner owns only local LFM, loopback LiveKit, and the loopback gateway/controller with its STT/two-worker TTS/provider children; external exposure is entirely operator-owned;
- [`voice_agent_v2.operations`](../../src/voice_agent_v2/operations.py) validates/stages immutable clean-commit releases and allows rollback only to the recorded freshly compatible prior release;
- `/api/status` reports one fresh five-component server health document plus build/release, fixed local provider/no-fallback, avatar-host contract, and MVP-eye identity without a secret;
- avatar host/MVP eye remain versioned browser-build/readiness responsibilities. Kiosk/browser autostart is not introduced;
- `cloud-llm` is declared inactive and `external-readiness-only`. No code or unit pretends to start, kill, restart, or fail over to an external provider.

The one-unit choice is intentional: STT/TTS are already controller-owned resident workers, the provider adapter is not a daemon, and splitting them would duplicate turn drain/readiness/cancellation. Any owned direct-child exit fails the complete service. A gateway-owned STT/TTS/provider capability that remains unready for two seconds does the same. This releases the complete old room/inference generation before an outer restart rather than preserving a partially stale stack.

## Compatibility, build, cache, and secret preflight

The canonical-host command below completed successfully against ignored mode-`0600` `.env.slice6`:

```sh
./voice-agent-ops validate --config .env.slice6 \
  --state-root "$PWD/tmp/slice9-validation-state"
```

Observed content-free result:

| Check | Result |
| --- | --- |
| Operations schema/provider/security | `voice-agent.operations.v1`, local, external provider not supervised, automatic fallback false |
| Selected artifacts | Exact LiveKit, VAD, complete selected Whisper file set, llama.cpp/LFM, Silero model/Python/Torch files passed size/hash/executable checks |
| Python runtimes | Exact Slice 6 FastAPI/Uvicorn/LiveKit/ONNX/numpy and selected STT faster-whisper/CTranslate2/numpy versions passed |
| Server configuration | Required local names/loopback defaults passed; signing values remained redacted and were never copied/printed |
| Free disk | About 838 GB observed, above the fixed 8-GiB refusal bound |
| Active caches | Slice 6 ~310 MB/2 GiB; selected STT model ~1.62 GB/2 GiB; STT runtime ~2.87 GB/4 GiB; STT state ~8.3 MB/1 GiB; local LFM ~3.39 GB/5 GiB; Silero runtime ~1.87 GB/2 GiB; Silero state ~30 KB/1 GiB |
| Release store | At most 3 releases and 1 GiB total; reaching either bound refuses without deleting anything |

A safe temporary cache with a one-byte maximum rejected an existing bounded fixture and proved the fixture remained byte-identical. No disk exhaustion, model/cache deletion, or cleanup selection was attempted. Historical Slice 2 caches outside the active selected STT model/runtime/state roots are neither counted as active runtime nor silently deleted.

The release embeds the exact Git commit in the ordinary Vite build and records a complete non-secret payload inventory: every file, symlink, empty directory, type, mode, symlink target, size, and file hash. Identity-bearing metadata is exact-key validated and recomputed into the release-directory identity; the stored payload digest is freshly recomputed. The gateway rejects forged build/release identities. `run-review-stand` now supplies the same exact commit to both its ordinary client and backend public status. Release configuration compatibility hashes only public settings; changing a signing secret does not expose it or alter that public fingerprint.

A post-review security correction replaced the internal operational-release v1 shape with v2 and removed the earlier secret-derived configuration revision rather than replacing it with another private key. A release now persists neither LiveKit credentials nor a verifier derived from them. The execution boundary returns one parsed, validated private snapshot and passes those exact values to the foreground runtime without rereading the file. Credential-only changes are deliberately outside release identity and out-of-band detection: after editing the mode-`0600` private configuration, the operator must run `voice-agent-ops install-service --restart` to revalidate the active release/configuration, force a restart even at unchanged release ID, wait for exact-release readiness, and then check status. `release_service_apply_required` covers only release/unit payload changes. The disposable release IDs recorded below predate this no-key correction and remain historical evidence, not current identity fixtures; no production unit or release required migration.

The same correction makes final systemd readiness require the expected build/release report and a second live poll of every owned child, rejects pre-existing local runtime-port owners before spawning, and feeds fresh PID/start-time custody for parent-owned LiveKit and local LFM into the five-component public report. Focused regressions cover the exact configuration snapshot at `execve`, absent persisted secret verifier, stale PID generations, occupied ports, build/release mismatch, immediate parent-process loss, and invalid sustained measurements. Subsequent review closes boot, listener-custody, and admission races: systemd owns a private `/run/voice-agent-v2` directory without a login-manager dependency, overflow measurements fail controlled validation, final readiness maps every local listener inode to its expected supervised process tree, diagnostic captures are deleted on restart rather than losing their TTL guardian, full fresh readiness is rechecked immediately before capability issue, and credential rotation has an explicit unchanged-release restart path without a secret verifier. Pasha's final exposure decision removes all proxy/private-network coupling: the application, systemd, operations, readiness, rollback, verification, and acceptance are loopback-local and manage no external route. No new physical reboot, microphone, audibility, avatar-soak, or Raspberry Pi evidence is claimed by these follow-ups.

### Immutable-release writer diagnosis and correction

An initial disposable recovered-release root verification correctly failed compatibility after the tracer. A complete before/after inventory captured paths, modes, nanosecond mtimes, sizes, symlink targets, and hashes before attempting any exclusion or validation change. The exact delta was:

| Relative entry | Before mtime ns | After mtime ns | Mode | After SHA-256 |
| --- | ---: | ---: | --- | --- |
| `.` | 1786863985150414887 | 1786863992945524995 | `0700` | directory |
| `src/voice_agent_v2` | 1786863976000000000 | 1786863993857537876 | `0755` | directory |
| `src/voice_agent_v2/__pycache__` | absent | 1786863993892862579 | `0755` | directory |
| `src/voice_agent_v2/__pycache__/__init__.cpython-314.pyc` | absent | 1786863993858710430 | `0644` | `1932e38bb3e32791b8793c2bd78ea5981613bf6df44e1dd596dca2acdde86429` |
| `src/voice_agent_v2/__pycache__/audio.cpython-314.pyc` | absent | 1786863993870250800 | `0644` | `c593e0f4749e43e3a58fa8b9ee6fcaf188f091e19db46aabba9f3fb4a33ab21f` |
| `src/voice_agent_v2/__pycache__/contracts.cpython-314.pyc` | absent | 1786863993871156433 | `0644` | `06c933606c04ec24fca5c012dd8664918ee6bd65c92285e0418c6d8ce746da2e` |
| `src/voice_agent_v2/__pycache__/diagnostics.cpython-314.pyc` | absent | 1786863993877951568 | `0644` | `013668ba7307049cefa186a0cba08f6a06f3e6f825c780d782bb0d2ea9f72f93` |
| `src/voice_agent_v2/__pycache__/observability.cpython-314.pyc` | absent | 1786863993888836142 | `0644` | `782277136839e88e2c938f5376288bc57589cb630d19f7da2d1ad666cb7cc1cb` |
| `src/voice_agent_v2/__pycache__/runtime_directory.cpython-314.pyc` | absent | 1786863993892862579 | `0644` | `9905360a188323e6e7d82eade0f6fb303d0c4c62ea46c793fbe8ba472435321e` |
| `src/voice_agent_v2/__pycache__/tracer.cpython-314.pyc` | absent | 1786863993861098640 | `0644` | `bab63e0f02adb472f2ff25f358b2ce46ada49171e058aabba52f8d9bd15a6846` |
| `tmp` | absent | 1786863998998610491 | `0755` | directory |

`strace`/`inotifywait` binaries were not installed, so a disposable recursive inotify watcher was implemented directly against the kernel API. File events and contemporaneous `/proc` custody identified both writers: `/usr/bin/python3 .../diagnostic_expiry.py` and the short-lived helper importing `_spawn_expiry_guardian`. Each was launched with a deliberately minimal environment that discarded the parent's bytecode controls. A one-factor matrix changing cwd, HOME, XDG cache, TMPDIR, and the parent's `PYTHONPYCACHEPREFIX` still produced the same seven release-local `.pyc` files in every case. The old tracer wrapper separately created `ROOT/tmp`.

The correction did not exclude any path or relax compatibility. Tracer HOME/cache/temp/bytecode moved below an explicit external mutable root. The original deployed rehearsal routed service bytecode beneath `/run/user/1000`; its post-fix inotify observation saw zero release-tree events, and a full real-stack ready/start/stop run left the complete path/hash/mode/mtime inventory byte-identical with a UID-1000 mode-`0700` pycache. The reviewed boot contract now routes that same mutable boundary to systemd-owned `/run/voice-agent-v2/pycache`, recreates it after every stop/restart, and removes the login prerequisite without treating the earlier run as reboot evidence. Release digesting still includes empty directories and modes, and every minimal Python child still uses either `-B` or an explicit mutable prefix.

## Idempotence, incompatibility, and rollback rehearsal

A disposable clean two-commit Git source under `/var/tmp` used the real public `voice-agent-ops` commands, real canonical artifact/local-config preflight, and ordinary client build. The original rehearsal also observed then-configured external exposure, but that observation is historical and no longer part of the application contract:

1. release A activated;
2. exact release A reapplication returned `status=no-op`, `changed=false`, with the same release ID;
3. clean release B activated and recorded A as `previous`;
4. `rollback` freshly revalidated A and restored it through the recoverable fsynced link transaction;
5. the source tracer output and two recovered-release `verify --tracer-only` outputs were byte-identical;
6. after rollback, the recovered tracer ran twice again and a complete inventory snapshot including hashes, modes, and mtimes remained byte-identical after every run;
7. `validate-deployment` passed against restored A;
8. adding forbidden `LITELLM_BASE_URL` to the ignored configuration made validation fail with `configuration_invalid` before the current link moved;
9. the entire disposable source/state/config was removed after evidence extraction.

The final reviewed rehearsal used payload-bound release A `d5be2533e13da98992cc3373`, release B `4bb3aebd03ec318792ff6118`, and restored A. Those IDs are disposable evidence identities, not production deployment claims. The restored release then ran beneath a unique real disposable user-systemd `Type=notify` unit: its start job returned only after exact-release readiness at 23.102 seconds, and graceful stop left zero project processes while the complete inventory remained unchanged.

Focused executable tests additionally corrupt a prior release and prove rollback leaves both current/previous links unchanged, then interrupt a link swap after its first durable replacement and prove the next locked operation completes the recorded pair. For an installed canonical service, a target release unit that differs from the installed root-owned unit is rejected before either link moves, without replacing or restarting that unit; disposable rollback with no installed unit remains unprivileged. No arbitrary commit can be supplied to rollback. A changed/missing prior config path, public config fingerprint, file inventory, operations manifest, client build, external artifact/runtime, disk/cache gate, or secret-file ownership/mode prevents the swap. External exposure is neither checked nor changed.

The full deterministic root gate is run again from the source in cumulative verification. The focused recovered-release command uses the same public tracer with `--tracer-only`; it deliberately avoids recursively executing the repository test suite and is not a substitute for `./verify`. A **real voice turn after rollback was not performed**; it remains part of the physical acceptance gap rather than being inferred from the deterministic tracer or startup smoke.

## Declared drain, no-orphan, and recovery bounds

Start order is:

```text
configuration → artifacts → local LLM → loopback LiveKit
→ loopback gateway/controller/STT/TTS/provider
```

Stop order is:

```text
gateway admission + turn/resident-worker drain → LiveKit → local LLM
```

The hard startup boundary is 300 seconds for exact release/artifact hashing, two bounded runtime probes, and the runner's sequential 80-second readiness allowance. The gateway drain is bounded at 54 seconds, followed by eight seconds each for LiveKit and local LLM; that 70-second graceful total plus one reserved forced-reap second per role gives a 73-second ordered maximum below systemd's 75-second hard `KillMode=mixed` boundary. Service-owned diagnostic guardians remove their captures and exit when the authoritative main PID generation ends, while development guardians retain independent TTL. The unit uses `Type=notify`; the main process emits readiness only after the five-component report passes and every local listener belongs to its expected process tree, while install/reconciliation and rollback also match the public release ID. Fresh bounded operational-health probes cover both the LiveKit listener and local-LFM `/health`, including a living process that no longer serves correctly. Runtime exit `1` may complete one restart after five seconds; `StartLimitBurst=2` across an infinite interval stops the next attempt. Supported install/reconciliation and rollback activations reset the failed/start-rate state immediately before their one manual start, leaving exactly one subsequent automatic recovery rather than consuming a lifetime counter. Compatibility exit `2` is `RestartPreventExitStatus` and never retries. This service recovery is not an inference request retry.

The current `./verify-slice9` starts and gracefully stops three disposable application-owned processes in the exact declared order, with zero orphans and zero hard kills. On the real canonical stack:

- local LFM loss produced runtime exit `1`, full reverse cleanup, and no surviving project process;
- LiveKit loss produced the same bounded failed state and cleanup;
- gateway/controller loss produced the same bounded failed state and cleanup;
- selected STT child loss changed fresh gateway health to unready, crossed only the fixed two-second grace, produced runtime exit `1`, and left no project process;
- one of the exact two selected TTS workers did the same;
- provider-adapter state is owned by the gateway and selected local LLM readiness; it has no invented process to kill. Local-LLM loss covers the active provider process, and no alternate provider/model/module started.

A separate historical normal real-stack run reached fresh overall ready, returned the public local/no-fallback/avatar/build facts, accepted no session, and returned the same five-component document through loopback. That run also observed the then-configured external HTTPS setup; this is preserved only as historical environment evidence and makes no current exposure/readiness claim. Uvicorn completed application shutdown before LiveKit and local LFM stopped, and final checks found no `run_slice6.py`, llama-server, LiveKit, Whisper, or Silero worker.

The canonical host's real user systemd manager also ran one unique disposable transient service with the then-current finite restart interval. It executed twice (initial plus one completed recovery), scheduled the next restart job, blocked it at the start limit, and remained `failed/start-limit-hit`. The tracked contract now uses an infinite interval so elapsed startup or uptime cannot reopen the automatic-recovery allowance; the host verifier uses that exact property, but the earlier finite-interval run is not relabeled as evidence for it. The transient unit was stopped/reset and removed; no production unit/shared service was touched.

## Controlled sustained/resource result

`config/operations-v1.json` preregisters 20 turns, 100% success, total-turn p95 ≤20,000 ms, cancellation p95 ≤250 ms, RSS/VRAM growth ≤512 MiB, avatar healthy-frame ratio ≥0.98, and avatar rate ≥55 fps. The CI-safe metadata-only controlled run produced:

| Metric | Controlled result |
| --- | --- |
| Turns / completed | 20 / 20 |
| Total-turn p95 | 1,180 ms |
| Cancellation p95 | 100 ms (4 samples) |
| Process RSS range | 38 MiB |
| GPU VRAM range | 19 MiB |
| Avatar healthy-frame ratio | 0.999 |
| Avatar frames/s | 60 |

This proves threshold evaluation, sample-count enforcement, and bounded scalar/resource handling. It is synthetic content-free evidence, **not** a 20-turn physical/full-stack voice/avatar soak or a new capacity claim. The real full stack was started repeatedly for readiness/failure/graceful-cleanup evidence, but no transcript, microphone audio, synthesized playback, or physical render timing was created or claimed.

## Validation commands

```sh
./verify-slice9
./scripts/verify_slice9_host.py
./verify
./verify --tracer-only
./verify-slice8
./verify-slice7
./verify-slice6
./verify-local-lfm
./verify-silero-kseniya
```

The host script performs read-only selected-artifact/local-config/cache checks plus one disposable user-systemd service; it does not inspect external exposure. It does not install the product unit. Exact cumulative results are also recorded in the Slice 9 do report.

## Reboot safety decision and exact physical gaps

No physical reboot was performed. At the reboot decision point:

- this disposable task worktree was intentionally still uncommitted/dirty while implementation evidence was being authored;
- other project worktrees existed and no authoritative mechanism proved that no other worker was active;
- the production unit/release was intentionally not installed from a disposable unmerged worktree;
- therefore the brief's required clean committed durable continuation and no-other-worker gate could not be proven.

Risking the host or relying on this interactive agent to survive reboot would have violated the explicit safety contract. The closest safe path was exercised instead: real systemd restart limiting, real full-stack startup/graceful stop, real owned/controller-child loss, repeated clean recovery starts, exact post-stop orphan cleanup, and verified release rollback in disposable state.

Still not performed or claimed:

- product system unit installation followed by a physical normal reboot and post-boot durable evidence cleanup;
- one real microphone → Whisper → selected local LFM → Kseniya voice turn after rollback;
- 20 physical/full-stack turns with actual latency/resource/interrupt/avatar-frame observations;
- physical Kseniya audibility/joins, rapid barge-in timing, or speaker playback timing;
- Raspberry Pi frame-rate/visual acceptance;
- separate licensing/legal approval required by ADR-0009.

Consequently this change does **not** assert the roadmap's physical core-MVP sign-off. Wake remains absent, disabled, and non-blocking; Slice 10 must not be treated as physically unblocked until the listed gates are actually closed.

## Final reversible host state

- `/etc/systemd/system/voice-agent-v2.service`: absent (verified with `sudo -n test`);
- production release state under `~/.local/share/voice-agent-v2`: not created by this lane;
- disposable transient systemd unit: reset/removed;
- Voice Agent runner/model/LiveKit/Whisper/Silero processes: none;
- optional external exposure: not inspected or changed by the application lane;
- firewalld, public exposure, bootloader, disks, unrelated services, user data, wake, kiosk/autostart: unchanged.

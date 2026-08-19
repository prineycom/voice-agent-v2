# E3.1 deterministic evidence — persistent ordinary files

**Issue:** [#37](https://github.com/prineycom/voice-agent-v2/issues/37)  
**Tier:** deterministic PR evidence only  
**Command:** `./verify`

## Implemented contract

- The existing one-container E2.4 route remains the only route: every ordinary shell and convenience-file call is a fixed `docker container exec` invocation of the image helper. No path, archive, Git repository, package name, or model output can select a container, mount, runtime, or host fallback.
- The image supplies ordinary local-development tools (`git`, archive utilities, `patch`, `ripgrep`) and a noninteractive container-local privilege path for a package manager. Rootfs package changes are ordinary writable-rootfs state; this evidence does not perform a network install.
- File reads are binary-safe and range-explicit. Their receipts distinguish returned bytes from complete source bytes, include SHA-256 and mode facts, and label incomplete ranges/truncated output. Search uses byte offsets and bounded scans rather than assuming text decoding.
- Convenience writes, text edits, and patches validate supplied expectations where applicable and materialize a mode-`0600`, same-directory temporary file, fsync it, atomically rename it, then fsync the parent directory. A failed validation or patch leaves the prior target intact. Shell authority remains general inside the selected container.
- Every shell is fresh and receives a deliberately minimal environment: no inherited host Git configuration, credential helper, `.netrc`, SSH/GPG agent, Docker auth/socket, browser/keyring data, or host home is passed through. A repository-local Git configuration remains ordinary container data. Shell exports/aliases remain shell-local; only the controller's observed logical cwd is persisted. Missing observed cwd visibly falls back to `/workspace`.
- Status v2 separately reports rootfs, managed workspace, cache, tmpfs, process, shell-local, and logical-cwd persistence. It does not emit file, command, package, archive, or repository content. The established lifecycle remains non-destructive: normal AgentRun/session/cancellation/quit events never stop/remove the environment; explicit reset/rebuild/remove retain workspace/cache by default; a reserve response can stop but never prune/remove data.

## Deterministic observations

`tests.test_agent_environment_files` exercises binary write/read hash/range receipts, failed precondition preservation, atomic text edit/patch, byte search, transient shell exports, and image-local Git/archive/package prerequisites against disposable roots. `tests.test_agent_environment` extends the fake-Docker transcript with logical-cwd persistence/fallback and the v2 status matrix while retaining E2.4's exact-ID, single-environment, no-fallback, receipt, reserve, and lifecycle cases.

The full canonical output for this branch is saved as `artifacts/issue-37-verify.log` when the PR gate is run.

## Separate, still-open evidence

This deterministic evidence makes no claim that a particular Docker Engine or Docker Desktop host has built/pulled the revised locked image, that package installation can reach a package repository, or that any network/remote-Git/credential operation is enabled. Linux Engine and Docker Desktop/macOS still require separately authorized native-platform workflows, including same-ID rootfs/package persistence, stop/start tmpfs/process loss, bind-root retention, host-boundary isolation, and disk-pressure behavior. Exact-model natural RU/EN tasks, controller/daemon/Desktop restart, reboot, voice, and physical acceptance remain separate tiers.

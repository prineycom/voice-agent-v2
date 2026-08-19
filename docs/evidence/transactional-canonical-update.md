# I3 deterministic evidence: transactional canonical update

## Outcome

`voice-agent update` is implemented for canonical installations created by the fresh-install foundation. The launcher owns one exclusive OS `flock`, one durable content-free update journal, signed candidate verification/staging, safe migration preparation, exact running-healthy rollback custody, bounded service transition, exact candidate readiness, automatic exact-prior restoration and reachability-based release collection.

The causal defect addressed by this boundary was a split selection/application path that could retain a healthy old process while losing its rollback pointer when new application code rejected old application metadata. I3 instead establishes the running release through stable launcher release records, signed inventory and exact service/runtime/readiness evidence before any stop. It does not use a new application's internal manifest parser to decide whether healthy prior bytes exist.

## Deterministic acceptance

The canonical `./verify` launcher phase owns `launcher/test/update.test.cjs`. Its isolated fixtures cover:

- exact candidate success, one quiesce/start, terminal journal removal, rollback retention and canonical private modes;
- candidate failure with exact prior config/unit/pointer/readiness restoration and nonzero failed-safe truth;
- candidate plus prior failure with `failed_needs_repair` and retained journal/snapshot/releases;
- digest-bound ordered safe configuration migration with explicit-value preservation and pre-quiesce refusal of destructive/product-choice work;
- injection after every durable phase/action observed by the successful transaction, followed by retry convergence without a blind duplicate start;
- already-current reconciliation, cached/offline latest-unknown truth, and online metadata-unavailable/no-change truth;
- concurrent lock refusal, channel expiry/downgrade, genuine post-GC disk shortage, prior service identity mismatch and exact candidate readiness mismatch;
- exact owned release collection and preservation sentinels for application data, models and AgentEnvironment workspace, plus unsafe/unowned release refusal.

All filesystem, service, network, channel, artifact, clock, disk and failure facts are deterministic/injected under disposable roots. No live stand, user systemd service, Docker object, model, Telegram target, network release server or production key is touched.

## Security and privacy

`voice-agent.update-transaction.v1` contains opaque IDs, stable release IDs, phase, prior service booleans/unit digest, boolean receipts and a stable failure code only. Config and unit bytes remain mode-`0600` in the opaque migration snapshot and are removed only after healthy commit or proved restoration. Exact no-follow/owner/mode validation gates release custody and deletion. GC cannot construct paths into configuration, secrets, application data, models, AgentEnvironment state, additional mounts, arbitrary cache roots or Docker.

## Nonclaims

This evidence does not claim production signing/channel publication, real downloads, real systemd/network/NVIDIA/model readiness, legacy selected-new/running-old adoption, live-stand repair, AgentEnvironment migration, launcher self-replacement, broad cache/offline hardening, explicit rollback/uninstall/support bundle, macOS support, power-cut VM/reboot survival or physical voice acceptance. Those remain later roadmap gates.

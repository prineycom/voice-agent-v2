# Final v1 lifecycle and local-support evidence

## Scope

This evidence records the deterministic I7 boundary for selector-free recorded-prior rollback, preservation-first uninstall, and a privacy-safe local support archive. ADR-0016 and [`architecture.md` §6.7](../architecture.md#67-launcher-owned-installation-and-update-boundary) own the decision; schemas and executable validators own machine fields.

## Deterministic evidence

Repository gate:

```text
./verify
canonical_pr_gate: PASS deadline_seconds=90 unexpected_skips=0
RESULT: PASS
```

The Node launcher phase uses only disposable XDG roots and injected host/service/Docker/filesystem/journal facts. It proves:

- recorded signed prior-only rollback, selector denial, immutable release and exact model/runtime custody, current host/config/data compatibility, one quiesce/start, exact release/build plus 5/5/admission/owned-loopback readiness, active↔rollback retention, unchanged AgentEnvironment identity, failed-safe entry restoration, double-failure evidence retention, and interruption retry;
- default uninstall preservation of config, private secrets, models, application media/data/logs and the complete AgentEnvironment, with service stop/disable before program bytes and launcher removal last so interrupted retry remains available;
- explicit program-cache/model/all-data categories, typed confirmations, dry-run inventory, no linger mutation, exact-root/no-follow owner/type/race checks, idempotent already-removed behavior, and no arbitrary/prefix/wildcard/prune/external-mount path;
- AgentEnvironment deletion only after exact recorded container plus owner and rootless endpoint reinspection, including interruption after container removal without duplicate removal;
- byte-deterministic owner-only support archives with the five closed categories, checksum/size/category-only summary, unsafe-output denial, fixed service-policy and bounded transaction/journal metadata;
- fail-closed secret field/value, private-key/token, credential-bearing URL and high-entropy scanning, while allowing schema identifiers, stable codes and exact hashes; no upload owner exists.

The full canonical output is stored in `artifacts/update-s8-rollback-uninstall-support-verify.log`.

## Privacy and safety facts

All public lifecycle records contain only operation/state/error, release identity and recovery booleans. The support archive is constructed from new normalized documents rather than copying source logs, configuration, unit bytes or Docker inspection. Its entry names are fixed before archive creation, each value is scanned before the output file is opened, the output is mode `0600`, and an existing destination is never overwritten.

Uninstall never accepts a caller path. Default removal targets only exact canonical service/launcher/program ownership. Destructive roots are selected by closed booleans. The persistent AgentEnvironment is neither release-GC nor default-uninstall input; its optional deletion uses exact recorded-ID authority and never constructs Docker prune or another container/volume target.

## Nonclaims

This work did not provision production signing/channel/release authority; perform a real download; touch the live stand; start, stop or delete a real service, filesystem tree, Docker object, model, configuration, secret, conversation, media file or external mount; upload diagnostics; rotate credentials; rebuild an AgentEnvironment; run a VM power-loss/reboot test; prove real voice after rollback; or establish physical/full-stack/Raspberry Pi/macOS acceptance. Those remain separate authorized tiers.

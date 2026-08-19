# Immutable-release rollback custody across one contract transition

## Scope

This document owns deterministic repository evidence for the narrow immutable-release transition where a newly selected release adds the inert agent-profile parser packages to the Slice 6 Python declaration while the prior service process is still running. It is not live-host, reboot, physical voice, or deployment-readiness evidence.

## Diagnostic separation

The fixtures distinguish three facts that must not be collapsed:

1. a clean merged-source deploy selects a new immutable `current` release;
2. current-contract validation masks the old release only because its otherwise exact operations manifest predates the two added Python package declarations;
3. before apply, systemd can still be running that exact old release while `current` selects the new release.

The proven compatible control already retained `previous` and restored it after an induced activation failure. The divergent case differed first at the original deploy pointer decision: `previous` was retained only when the old `current` passed the new validator. A later hardening correctly made a null transaction remove stale `previous`; it exposed the gap but did not create the selection error. The smallest counterfactual is therefore to admit exactly the superseded runtime declaration for rollback custody, not to preserve a stale pointer or accept arbitrary historical policy.

Disconfirming evidence is retained: service apply already refused before restart when restoration custody was absent; the installed unit could still be identical; the old runtime could still be healthy; and the compatible recorded-`previous` failure path already restored exact readiness. Those facts rule out unit drift, readiness failure, and the rollback executor as the original cause.

## Deterministic behavioral evidence

The canonical `./verify` operations surface covers:

- current validation rejects the superseded Python declaration while rollback validation admits that exact declaration only;
- any other runtime version, disk policy, lifecycle, artifact, or manifest drift remains rejected;
- deploy retains an old current release that passes the narrow rollback validator;
- the selected-new/running-old/no-`previous` apply state records only the runtime-named exact release after immutable release/build/host validation, installed-unit equality, systemd MainPID, owner, cwd, executable, argv, and a stable second observation;
- the already-recorded compatible path remains valid;
- repeated apply after successful exact-new readiness is a no-op and performs no extra restart;
- induced new activation failure swaps back, restarts, and waits for the exact old release;
- mismatched candidates refuse without pointer or restart mutation, and unrelated release directories remain untouched;
- exact process-fixture mismatch is rejected.

## Nonclaims

This change introduces no release scanning, automatic garbage collection, release-count change, wildcard cleanup, generalized migration framework, extra restart, manual pointer repair, arbitrary Git rollback, or live-host action. It does not claim a real systemd apply, reboot, physical voice path, full-stack soak, or Raspberry Pi acceptance.

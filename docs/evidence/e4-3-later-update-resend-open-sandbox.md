# E4.3 deterministic evidence — later update, current-byte resend, and open-sandbox boundary

## Scope

This record covers only deterministic PR evidence for issue #42. It uses frozen report/page/hostile/token/target data, a fake Docker transcript, and a fake Telegram transport under the canonical network-denied `./verify` tier. It uses no real Docker daemon, Internet, model, Telegram target, personal data, production credential, host lifecycle, hardware, or physical voice.

## Implemented boundary

- Active AgentRun/decision v4 adds one fixed `report.artifact` operation. A locator is either one trusted opaque artifact ID or one exact `reports/…` relative-path match in a bounded controller index. Container/environment/runtime selectors, host paths, mounts, ports, credentials and lifecycle arguments are rejected.
- `local_summary` freshly reads exact bytes from the selected environment through the existing Docker-exec outbound stream and records zero external calls. Raw model-needed bytes remain separate from the content-minimal artifact-operation result.
- `local_update` requires the expected current revision and SHA-256. The ordinary file helper checks expected-current hash, fsyncs a mode-`0600` temporary and atomically renames it. The controller rereads the result before advancing the atomic private ledger.
- Every committed revision has an immutable opaque receipt linked to the prior receipt and records current path, byte count/SHA-256, media type, citations and sidecar. A concurrent writer mismatch is a visible conflict and does not advance ledger custody.
- `bounded_refresh` does no network itself. It accepts only explicit successful `web.fetch` receipt IDs already produced in that AgentRun. Local update preserves existing citation custody.
- Resend creates a new E4.2 delivery ID, rereads the artifact's current committed revision and uses the unchanged restart-pinned synthetic exact-Pasha tuple. It performs no Web call. Acknowledgement, rejection, unknown, reconciliation and no-auto-retry semantics are unchanged.
- Status explicitly says prompt injection is not prevented, redaction is display-only, open-sandbox authority is accepted, and the only containment claim is the correctly configured unescaped-container boundary against direct unmounted-host access.

## Deterministic acceptance mapping

| Acceptance property | Synthetic evidence owner |
| --- | --- |
| Later RU retrieval by trusted bounded path; current revision/hash; no host path or network | `ReportDeliverySliceTests.test_later_ru_summary_resolves_trusted_path_with_zero_network_or_host_read` reconstructs the controller, resolves the existing workspace artifact, observes exact raw bytes/revision/hash, zero new research requests and no fixture host path in the result |
| Expected-state atomic local update, prior receipt, current-byte resend, unchanged raw bytes | `test_atomic_local_update_links_revision_and_resends_current_unredacted_bytes` checks revision 2/prior receipt, exact current file and Telegram document length/SHA-256, no research, and synthetic token bytes unchanged despite a different display-redacted copy |
| Concurrent/background writer conflict without silent overwrite | `test_concurrent_writer_conflict_is_visible_and_unexpected_bytes_are_not_overwritten` injects a writer immediately before expected-hash replacement, observes a conflict/no ledger advance and retains the writer's bytes |
| Optional bounded refresh with new actual evidence | `test_explicit_refresh_uses_new_fetch_receipt_then_updates_only_once` performs one new synthetic fetch, binds its actual call ID, updates once and does not resend |
| Closed admission and denied selection/lifecycle/host authority | `test_artifact_admission_rejects_selection_lifecycle_and_host_authority` rejects selector, container, host-path and runtime fields; the hostile matrix observes only selected-container exec calls, one environment and no Docker socket/CLI, host home/process/service/device, mount/port or lifecycle construction |
| Hostile data inert until admission, then accepted RW deletion/change and synthetic-credential egress | `ResearchSliceTests.test_hostile_tool_bytes_are_inert_until_next_admitted_decision_and_accepted_authority_is_explicit` first treats role/system/fake-call/fake-citation bytes as data, then records an admitted RW change/deletion and exact synthetic credential hash/length sent to an allowed synthetic endpoint, explicitly labelled accepted risk |
| Display-only redaction and content-minimal status | E4.1 raw model-byte test plus E4.3 current-byte resend test preserve artifact/request bytes; `test_status_is_content_minimal_and_contract_valid` validates no token/report content and the honest boundary fields |
| Failure isolation and healthy ordinary voice continuation | `test_artifact_failure_emits_one_terminal_failure_and_next_voice_run_is_clean` observes one stable failed AgentRun and a following clean normal RU answer with no stale artifact/delivery result |
| E4.2 acknowledgement/unknown/no-auto-retry custody preserved | Existing E4.2 acknowledgement, mismatch, cancellation, restart reconciliation and unknown-explicit-resend tests now validate delivery v2 current-revision output |

The authority-denial matrix is synthetic evidence of controller construction and fixture-visible container boundaries. It is not Docker Engine/Desktop containment evidence.

## Verification

The repository command authority is [`../testing.md`](../testing.md). The only PR gate is:

```sh
./verify
```

The complete captured output for this issue is stored at `artifacts/issue-42-verify.log`. A passing log establishes deterministic fake-boundary behavior only.

## Explicit nonclaims

This evidence does **not** claim:

- prevention, detection or classification of prompt injection; semantic taint, DLP, domain/payload filtering, safe browsing, or credential confidentiality from admitted agent work;
- prevention of mounted-data destruction, readable RO-data exfiltration, malicious package persistence, remote effects, or deliberate use of an exposed credential;
- transactional rollback/compensation for arbitrary shell, background, Git, API or Telegram effects; automatic refresh, retry of an unknown send, message retraction, or exactly-once Telegram delivery beyond acknowledgement/reconciliation;
- resistance to Docker/kernel/container escape, or real Linux Engine/Docker Desktop host/socket/process/device containment;
- live Web truth, exact-model natural quality, exact-Pasha tuple/readability, possession/use of a real bot token, personal Telegram data, daemon/Desktop/host reboot persistence, physical voice/audibility, full-stack soak, Raspberry Pi or licensing acceptance;
- another recipient, credential configuration, environment/runtime, dynamic mount/port, general knowledge base, scheduled refresh, host/Podman fallback, or model lifecycle authority.

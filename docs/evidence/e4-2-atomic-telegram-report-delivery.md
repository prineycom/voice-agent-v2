# E4.2 deterministic evidence — atomic saved report and Telegram delivery

## Scope

This record covers only deterministic PR evidence for issue #41. It uses synthetic report/page/token/target data, fake Docker transcripts, and a fake Telegram transport under the canonical `./verify` network-denied tier. It does not use a real bot token, personal Telegram data, Internet, Docker Engine/Desktop, physical voice, or the exact Pasha target.

## Implemented boundary

- Active AgentRun/decision is v3. Its fixed `report.deliver` operation can save+send, explicitly resend a known artifact ID, or reconcile a known delivery ID without sending.
- A new report is streamed into `/workspace/reports/…` through the existing fixed Docker-exec helper before target, credential, or Telegram admission. The atomic stream receipt owns path, byte count, and SHA-256.
- A workspace sidecar preserves bounded UTF-8 chunks, caption, media/filename, and controller-bound displayed source receipts for restart recovery. Private controller state contains only opaque IDs and content-minimal custody metadata.
- Trusted mode-`0600` installation state pins one direct-private `chat_id == user_id`, no-thread tuple at controller startup. The release pins `https://api.telegram.org` and `TELEGRAM_BOT_TOKEN`; the token resolves only from the single E3.2 exec-environment declaration/private credential store.
- Text is whitespace/UTF-8-boundary chunked to at most four 4096-byte requests. Caption is at most 1024 bytes. Text/caption source lines come from actual successful fetch receipt metadata, not model-supplied URLs.
- The document multipart contains the exact streamed report bytes. Content-minimal operation receipts record payload, document, and complete request-body byte counts/SHA-256 without retaining request bodies in status.
- Each opaque delivery ID is durably `dispatching` before one transport call per operation. Only an exact target/kind/count/hash acknowledgement becomes `sent`; definite rejection is `failed`; ambiguous acceptance is `delivery_outcome_unknown`. There is no automatic resend.
- Restart reconciliation never dispatches a known ID and converts a leftover dispatch claim to unknown. Explicit artifact resend creates a new delivery ID, rereads the same report/sidecar hashes through Docker exec, and performs no Web research.

## Deterministic acceptance mapping

| Acceptance property | Synthetic evidence owner |
| --- | --- |
| Natural research → atomic saved report → useful final status | `ReportDeliverySliceTests.test_natural_research_saves_before_exact_text_and_document_ack` |
| Exact saved/document length and SHA-256; bounded cited text/caption | The natural test validates saved bytes, actual receipt ID, multipart containment and document receipt; `test_utf8_text_chunking_is_fixed_bounded_and_receipt_cited` proves multi-chunk UTF-8 byte limits and custody hashes |
| Model/content/metadata cannot alter recipient, credential, endpoint, or environment | `test_target_is_restart_pinned_and_model_content_cannot_select_authority` plus strict request parsing/config tests |
| Raw report/document/request bytes are not display-redacted | Natural test compares stored/multipart bytes with the binary fixture and separately observes only the derived display copy redacted |
| Acknowledged/failed/unknown and zero automatic resend | `test_unknown_is_never_automatic_retried_and_explicit_restart_resend_is_same_bytes` and `test_outage_rejection_cancellation_and_missing_readiness_preserve_report` |
| Outage/rejection/cancellation preserves artifact and ordinary environment health | Outage/rejection/cancellation test checks the exact workspace bytes, no retraction claim, no send after pre-dispatch cancellation, and running environment state |
| Restart reconcile and explicit same-byte resend without research | Unknown/restart test reconciles with zero calls, then runs a new natural AgentRun resend with the same document hash and no research request |
| Known acknowledgement is not duplicated; changed bytes fail closed | `test_known_ack_reconciles_without_duplicate_and_changed_artifact_fails_closed` |
| Missing target/credential stays delivery-local | Outage/readiness test proves report persistence and zero transport calls for stable readiness reasons |
| Safe status and machine contracts | `test_status_is_content_minimal_and_contract_valid` plus `agent-run.v3`, `telegram-delivery.v1`, and `telegram-delivery-status.v1` schema validation |

## Verification

The repository command authority is [`docs/testing.md`](../testing.md). The only PR gate is:

```sh
./verify
```

The complete captured output for this issue is stored at `artifacts/issue-41-verify.log`. A passing log establishes deterministic fake-boundary behavior only.

## Explicit nonclaims

This evidence does **not** claim:

- exact-Pasha recipient correctness, real Telegram acknowledgement/readability, or live-network behavior;
- possession, validity, confidentiality, rotation, or use of a personal bot credential;
- Linux Docker Engine, Docker Desktop/macOS, gateway restart against a physical container, daemon/Desktop restart, or host reboot behavior;
- exact-model natural save/delivery quality, physical microphone/audible Kseniya, full-stack soak, or Raspberry Pi acceptance;
- exactly-once semantics beyond known local/Telegram acknowledgement, automatic reconciliation of an unknown remote side effect, or remote retraction;
- prompt-injection prevention, semantic taint/DLP/content approval, Telegram confidentiality, edit/refresh UX, arbitrary recipients, a messaging router, another environment/runtime, or host fallback.

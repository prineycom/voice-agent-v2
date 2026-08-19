# E3.2 deterministic evidence — network, credentials, streams, and remote Git

**Issue:** [#38](https://github.com/prineycom/voice-agent-v2/issues/38)
**Tier:** deterministic PR evidence only
**Command:** `./verify`

## Implemented contract

- The E2.4/E3.1 owner/spec/generation, exact-container, Docker-exec-only, persistent-state, at-most-once, resource, explicit lifecycle, and no-host-fallback boundaries remain the only execution path.
- Normal configuration selects Docker bridge egress and no published port; a typed disabled fixture selects network `none`.
- One strict credential object contains fixed exposed names/modes only. The exact private mode-`0600` installation document supplies bytes. It is never replaced by ambient host environment, home, `.env`, `.netrc`, Git/Docker helpers, SSH/GPG agents, keyrings, browser state, or sockets.
- Create-time bytes are pinned at controller composition and keyed into private spec compatibility. A changed restart snapshot leaves the old exact container `stale_spec` until confirmed rebuild. Per-exec values are resolved immediately before each call; environment bytes use Docker exec `-e`, file bytes use fixed `0600` helpers beneath tmpfs `/run`, and cleanup is bounded.
- Fixed inbound/outbound helper routes use only Docker-exec stdin/stdout. They validate configured byte limits and SHA-256; inbound writes fsynced `0600` temporaries followed by atomic rename. Root plus relative container paths cannot become gateway host paths.
- A transport is invoked once. Only exact target/length/hash acknowledgement produces `sent`; ambiguity is `unknown`, with no automatic repeat. Docker-exec ambiguity retains the existing call-level unknown/no-replay rule for HTTP/Git mutations.
- Raw model/tool/file/header/body/multipart/Git/stream bytes remain unchanged. Only derived display/log/support/final-human copies are redacted.
- Status exposes fixed names/modes and the accepted broad-authority warning, never values, fingerprints, source paths, direct secret digests, or a confidentiality/domain-binding claim.

## Deterministic observations

`tests.test_agent_environment_network` uses only synthetic values and fake Docker/network/transport transcripts. It covers strict single-object schema and selector rejection; private mode custody and rotation; create-time stale-spec/rebuild; per-exec next-call rotation and inherited old-process bytes; `0600` create and call-scoped files; no ambient helper forwarding; bridge and network-none commands; exact token delivery to two controlled endpoints; display-only redaction with raw model-visible bytes; binary download/upload/multipart and persistent download hashes; clone/fetch/push transcripts; exact inbound/outbound byte hashes; host-looking path rejection; exact acknowledgement, unknown outcome, and single transport invocation; nonsecret status and explicit no-rollback/no-auto-retry facts.

The full canonical output is saved as `artifacts/issue-38-verify.log`.

## Accepted authority and nonclaims

Hostile content or a mistaken model may read/exfiltrate all readable container/mounted data, modify all writable state, install persistent software, use every exposed credential for its full authority, consume quota, reach arbitrary accessible services, push Git, and transfer data. This slice claims only that a correctly configured unescaped container cannot directly reach unmounted host paths/processes/sockets/devices. It does not claim credential confidentiality from the agent, semantic DLP, domain/payload binding, URL allowlisting, proxy enforcement, remote rollback, or that cancellation reverses a remote effect.

## Separate, still-open evidence

This report makes no claim about real Docker Engine DNS/TLS/network namespaces, Docker Desktop/macOS/VMM behavior, real tmpfs across stop, a built/pulled revised image, live Internet/API/provider credentials or spend, actual remote Git hosting, exact-model natural network work, daemon/Desktop/reboot behavior, Telegram delivery, physical voice, full-stack resource acceptance, or Raspberry Pi behavior. Those require separately authorized bounded tiers and cannot borrow this deterministic result or one another's platform evidence.

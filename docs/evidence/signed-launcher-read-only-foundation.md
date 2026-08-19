# Signed launcher and read-only legacy discovery foundation

## Outcome

A separately versioned `voice-agent` launcher now owns protocol-1 release verification and read-only diagnostics. Node.js 26 SEA packages the single CommonJS source into one deterministic self-contained Linux executable without another compiler/toolchain. The launcher carries no application runtime, credentials, mutable checkout, persistent container, updater daemon, or mutation command.

## Deterministic evidence

The canonical `./verify` launcher phase proves:

- canonical stable-channel metadata with detached Ed25519 verification, monotonic sequence and expiry;
- forged bytes, wrong key, expiry, sequence rollback and noncanonical JSON rejection;
- exact artifact byte count/SHA-256, manifest SHA-256, Linux x86_64 NVIDIA platform, launcher protocol and schema-range binding;
- complete declared archive inventory with traversal, absolute path, duplicate, device/unknown type and escaping-link denial;
- stable release-record validation;
- exact historical release identity and complete immutable path/type/mode/size/hash inventory without requiring its incompatible application manifest to match current code;
- selected-new/running-old/no-previous truth with installed-unit, service MainPID, process UID/cwd/executable/argv, runtime release/build, five-component readiness and explicit rootless Docker evidence;
- invalid, unowned and unrelated candidate refusal;
- content-free human/JSON output and byte-for-byte zero mutation for `status` and `doctor`;
- two byte-identical SEA builds and successful execution against an empty disposable home.

Only a public fixture key and pre-signed bounded fixture bytes are tracked. The deterministic signing tool requires an explicitly supplied external test key.

## Nonclaims

No production key was provisioned, generated or committed. No release was published or downloaded. No installation root, service, pointer, configuration, cache, AgentEnvironment, Docker object or live stand was mutated. Install, update, rollback, recovery, migration, reachability GC, launcher replacement and macOS runtime support remain later slices. Deterministic evidence is not Linux service/install, real Docker/model/network/Telegram, power-loss, reboot, physical voice/full-stack, licensing or Raspberry Pi evidence.

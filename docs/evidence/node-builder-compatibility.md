# Node 26.7.0 builder compatibility

## Deterministic cause

A focused isolated copy of the exact production tool boundary reproduced the blocker without the live stand, production credentials, npm acquisition or application output. The official accepted Node archive remained unchanged. Its extracted executable is SHA-256 `ad19784f7e90ba789a099eccba77ede8dc90a778c424f1c10a70fed3ff903fdc`, ELF64 little-endian x86-64 `EXEC`, with interpreter `/lib64/ld-linux-x86-64.so.2`.

Its sorted `DT_NEEDED` closure is:

- `ld-linux-x86-64.so.2`
- `libatomic.so.1`
- `libc.so.6`
- `libdl.so.2`
- `libgcc_s.so.1`
- `libm.so.6`
- `libpthread.so.0`
- `libstdc++.so.6`

The executable's highest required versions are GLIBC `2.28`, GLIBCXX `3.4.21`, and CXXABI `1.3.11`. Those are compatible with the digest-pinned CUDA-12.9.1 Rocky-8 builder. The interpreter resolves to the admitted glibc-2.28 loader and the other required builder libraries are present, but `libatomic.so.1` is absent. With no library injection, the exact executable returns 127; bounded stderr is classified `missing-libatomic` and hashes to `1f8d90ae432ea9b5bff473c68cfe44196b2320aed67980959217a76f4d011ea0`. No stderr content is retained in receipts.

## Smallest exact correction

The build-tool closure adds only Rocky Linux `libatomic-8.5.0-26.el8_10.x86_64.rpm`, matching the builder's GCC/libgcc/libstdc++ release:

- exact HTTPS locator: `release/inputs/runtime-sources.v1.json`;
- 25,724 bytes;
- SHA-256 `6fa29bd69543e3b817cf50824d8a34a20f59ace733fd373379798674f2f6f28a`;
- Rocky 8 signing fingerprint and `gcc-8.5.0-26.el8_10.src.rpm` provenance;
- `GPL-3.0-or-later WITH GCC-exception-3.1` receipt.

Parent-bound RPM tooling extracts under `--network=none`. Only `usr/lib64/libatomic.so.1.2.0`, SHA-256 `5807cd82fe685155d2831a25111f14c366f681b47a304b997c6d32adeec59a1b`, becomes a regular private build-tool file named `libatomic.so.1`. It needs only `libc.so.6` and `libpthread.so.0` and has a highest GLIBC requirement of `2.14`.

Node is invoked by the exact admitted builder loader with `--inhibit-cache` and the fixed path `/build/tools/node-runtime/lib:/lib64:/usr/lib64`. `LD_LIBRARY_PATH` and `LD_PRELOAD` are removed by both preflight and the generated wrapper. The focused corrected command returned 0 with exact `v26.7.0`; output including its newline hashes to `fe9da612097c5b1f2808feef32b5685e7a19a607fd96c7d6abe1454f0edc7e1c`.

## Executable refusal evidence

`preflight-runtime` now performs the compatibility receipt before npm acquisition or output. It reports all differing architecture, node-byte, interpreter, `DT_NEEDED`, GLIBC/GLIBCXX/CXXABI, loader, library, raw-exit/stderr, corrected-exit/output and environment facts together without returning observed untrusted content. Parent/child provenance binds the Node archive, libatomic RPM and builder manifest. Deterministic fixtures cover pass, missing/tampered loader or library, symbol/architecture/interpreter/cause mismatch, host/ambient-library denial, network-none orchestration, repeat receipt identity and Node exclusion from the application runtime.

## Nonclaims

This focused proof did not run the complete cached production preflight, acquire npm inputs, assemble the multi-gigabyte runtime, compare two full outputs, create/sign/publish a candidate, use production secrets, or mutate/restart the live stand. Those remain their separately authorized tiers.

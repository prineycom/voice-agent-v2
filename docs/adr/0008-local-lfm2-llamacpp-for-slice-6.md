# ADR-0008: Replace the Slice 6 cloud route with pinned local LFM2.5 on llama.cpp

- **Status:** Accepted for the combined Slice 6 / Issue #15 delivery
- **Date:** 2026-08-12
- **Decision owner:** Pasha

## Context

ADR-0006 temporarily authorized private Slice 6 transcripts through the failed, operator-opaque LiteLLM `deepseek-v4-flash` route. It did not approve production use. Pasha subsequently required Issue #15 to land in the same Slice 6 branch and PR and to replace that active route completely before physical acceptance.

An already-measured cache-local official GGUF and GPU-enabled llama.cpp runtime exist on the canonical host. Historical measurements used other slot/context shapes, so the final two-slot 32K-per-slot shape still required direct server evidence. Historical DeepSeek and prior local-LFM failure evidence must remain factual rather than being rewritten.

## Decision

The active Slice 6 provider is exactly:

- `LiquidAI/LFM2.5-2.6B-GGUF` revision `b421ad1d549afeda6a0fb2ad3a697cb5a7879adc`;
- `LFM2.5-2.6B-Q4_K_M.gguf`, 1,674,454,848 bytes, SHA-256 `79fdf00351b46cf26f020aead28d01889886be87c55fa0eb907e6f9b00bfee14`;
- upstream llama.cpp tag `b10357`, commit `689e227db485c6b33d061555e74034c93a867649`, server binary SHA-256 `08625d7c6f380ce14a1fd6085e6468b13a7d169083928ab46706edb62979ac11`;
- cache root `/home/priney/.cache/voice-agent-v2/llama-cpp-gguf-q4/`, outside Git, with no alternate artifact download or runtime fallback.

The foreground Slice 6 runner verifies both hashes and the model size, then launches one loopback-only OpenAI-compatible server at `127.0.0.1:18080` with full GPU offload, flash attention, official Jinja and DeepSeek-format reasoning parsing, two slots, and 65,536 total context tokens. Runtime `/slots` evidence must show exactly two `n_ctx=32768` slots. CPU inference threads are six based on the prior measured host configuration; `--parallel 2` alone controls concurrency.

The controller uses one provider-neutral `LocalLFMProvider`. It has a compiled loopback endpoint and exact model alias, requires no token or provider environment, performs no cloud request, and has no fallback. Any `LITELLM_*` server configuration is rejected. Its bounded memory-only context is isolated per session and reset on reconnect/close. Hidden `reasoning_content` is counted only for safe diagnostics and never reaches the UI, returned response, TTS handoff, or durable storage.

The frozen voice generation contract is tracked in `config/local-lfm-v1.json`: Russian 1–3 sentence plain-text response, 500 visible characters / 2,000 UTF-8 bytes, temperature 0.3, top-p 0.9, top-k 40, repetition penalty 1.05, 768 total generated tokens, 384-token reasoning budget, no prompt cache, a 20-second whole-request deadline, exact model identity, and only normal `stop`/`eos_token` terminal results. Empty, hidden-only, over-limit, wrong-model, truncated, transport, cancellation, and readiness outcomes fail explicitly.

Historical ADR-0005/0006 evidence remains unchanged as the record of prior decisions and failures, but ADR-0008 supersedes their active Slice 6 runtime authorization. The Slice 3–5 standalone historical commands may remain as historical evidence tools; the LiveKit application itself has no LiteLLM dependency.

## Consequences

### Positive

- Live transcript and context remain on the canonical host.
- Browser assets/capabilities contain no LLM endpoint, credential, or management surface.
- The selected model/runtime/artifact and two 32K slots are reproducible and fail closed.
- Provider cancellation, context rollback, reconnect reset, and no-fallback semantics remain behind the existing controller contract.

### Costs and limits

- The local model consumes approximately 2.9 GiB process VRAM in the focused two-slot server run; full coexistence with Whisper, Qwen3, LiveKit and a real browser remains a required measured gate.
- The prior isolated benchmark is provenance, not proof that the final full stack fits or that human response quality is accepted.
- Physical microphone/listening, remote tailnet, actual playout interruption, subjective usefulness, and sustained full-stack resources remain Pasha-owned evidence and must not be fabricated.
- The foreground test deployment is not Slice 9 autostart/reboot/supervision.

## Alternatives considered

- **Keep LiteLLM as fallback:** rejected; Issue #15 requires no active cloud route or automatic switching.
- **Download another quantization/runtime:** rejected; only the fixed cache-local artifact/runtime is authorized.
- **Use two CPU threads for “two streams”:** rejected; concurrency is two llama.cpp slots, while CPU threads remain evidence-based.
- **Treat 65,536 total context as one 32K claim:** rejected; `/slots` must prove 32,768 for each of two slots.

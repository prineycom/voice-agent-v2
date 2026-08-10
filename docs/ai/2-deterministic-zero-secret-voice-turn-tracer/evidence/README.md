# Slice 1 deterministic evidence

**Source issue:** https://github.com/prineycom/voice-agent-v2/issues/2

The documented root command was executed twice from this worktree:

```sh
./verify > docs/ai/2-deterministic-zero-secret-voice-turn-tracer/evidence/run-1.txt
./verify > docs/ai/2-deterministic-zero-secret-voice-turn-tracer/evidence/run-2.txt
cmp docs/ai/2-deterministic-zero-secret-voice-turn-tracer/evidence/run-1.txt \
    docs/ai/2-deterministic-zero-secret-voice-turn-tracer/evidence/run-2.txt
```

Each invocation creates a new empty temporary home/cache root. The verifier installs a Python audit policy before importing or exercising the tracer and rejects every socket creation attempt. The implementation uses only the Python standard library and performs no subprocess or provider/model access.

`cmp` exited `0`. The two complete outputs share SHA-256 `2d3beb8f7cf9570934af0b5597512ad07b005d3f06dfca971c1c06ce88fb6159`. Both report:

- normalized trace SHA-256 `9b607f8f17878c9d1caf3caf85b106c896140a2f9ecd44e33dd39ef47d2f305f`;
- input PCM SHA-256 `9ee9638a619b160078dc8d86ca32a01bc5c32536eb9698c59b01b16c789ac844` (`6400` bytes);
- output PCM SHA-256 `69935a91444eaca69d96477a0de66aea7640fd476ea5858fcdd1f2143a46131e` (`6400` bytes, raw `pcm_s16le`, 16 kHz, mono);
- passing success, hard STT/selected-provider-LLM/TTS failure, and cancellation cases.

The canonical normalized traces and PCM declarations live under [`contracts/fixtures/`](../../../../contracts/fixtures/). No legacy material was inspected or used for Slice 1.

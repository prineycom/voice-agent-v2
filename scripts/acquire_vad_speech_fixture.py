#!/usr/bin/env python3
"""Acquire one checksum-pinned public Russian speech fixture for Silero tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request

DATASET = "istupakov/russian_librispeech"
REVISION = "a519c986bb3342cc8136d3d14e5ad8a4f1e1a2bd"
REFERENCE = "для вас души моей царицы красавицы для вас одних времен минувших небылицы в часы досугов золотых под шепот старины болтливой рукою верной я писал"
SOURCE_PATH = "audio/2671/2145/poemi_01_pushkin_0000.wav"
SHA256 = "8f3c840638f5ec0ea3f63fe157d49484c301846371f14bdb903427c366a16058"
SIZE = 363_244


def identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size, digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: acquire_vad_speech_fixture.py DESTINATION")
    destination = Path(sys.argv[1])
    if destination.is_file() and identity(destination) == (SIZE, SHA256):
        return 0
    query = urllib.parse.urlencode({
        "dataset": DATASET,
        "config": "default",
        "split": "test",
        "offset": 0,
        "length": 1,
    })
    request = urllib.request.Request(
        f"https://datasets-server.huggingface.co/rows?{query}",
        headers={"User-Agent": "voice-agent-v2-slice6-tests/1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        record = json.load(response)["rows"][0]
    row = record["row"]
    asset_url = row["audio"][0]["src"]
    if (
        record["row_idx"] != 0
        or row["text"] != REFERENCE
        or row["audio_filepath"] != SOURCE_PATH
        or REVISION not in asset_url
    ):
        raise RuntimeError("pinned public speech fixture metadata drifted")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    temporary.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(asset_url, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if identity(temporary) != (SIZE, SHA256):
            raise RuntimeError("pinned public speech fixture checksum mismatch")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

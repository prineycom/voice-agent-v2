#!/usr/bin/env python3
"""Acquire the hash-pinned public Russian evaluation corpus into the task cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "parakeet-stt-evaluation-v1.json"
REFERENCE_MANIFEST = ROOT / "benchmarks" / "fixtures" / "stt-russian-ruls.v1.json"
DESTINATION = Path("/home/priney/.cache/voice-agent-v2/experiments/parakeet-stt/fixtures/ruls-v1")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request(url: str, *, method: str = "GET"):
    return urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "voice-agent-v2-parakeet-evaluation/1"},
    )


def fixture_matches(path: Path, sample: dict[str, object]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == sample["bytes"]
        and sha256(path) == sample["sha256"]
    )


def main() -> int:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if document.get("schema_version") != "voice-agent.parakeet-stt-evaluation.v1":
        raise ValueError("unexpected Parakeet evaluation manifest")
    corpus = document["corpus"]
    references = {
        sample["id"]: sample
        for sample in json.loads(REFERENCE_MANIFEST.read_text(encoding="utf-8"))["samples"]
    }
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for sample in document["samples"]:
        reference = references[sample["id"]]
        output = DESTINATION / sample["filename"]
        if output.exists():
            if not fixture_matches(output, sample):
                raise ValueError(f"cached public fixture identity mismatch: {output.name}")
            continue
        partial = output.with_suffix(".wav.partial")
        if partial.exists():
            if fixture_matches(partial, sample):
                partial.replace(output)
                continue
            partial.unlink()
        query = urllib.parse.urlencode({
            "dataset": corpus["dataset"],
            "config": "default",
            "split": corpus["split"],
            "offset": sample["row_index"],
            "length": 1,
        })
        with urllib.request.urlopen(
            request(f"https://datasets-server.huggingface.co/rows?{query}"), timeout=60
        ) as response:
            row = json.load(response)["rows"][0]
        values = row["row"]
        if (
            row["row_idx"] != sample["row_index"]
            or values["text"] != reference["reference"]
            or values["audio_filepath"] != reference["source_audio_path"]
        ):
            raise ValueError(f"pinned public corpus metadata drift: {sample['id']}")
        asset_url = values["audio"][0]["src"]
        if corpus["revision"] not in asset_url:
            raise ValueError("public fixture URL is not tied to the pinned corpus revision")
        with urllib.request.urlopen(request(asset_url), timeout=120) as response, partial.open("xb") as sink:
            shutil.copyfileobj(response, sink, length=1024 * 1024)
        if not fixture_matches(partial, sample):
            partial.unlink(missing_ok=True)
            raise ValueError(f"downloaded public fixture identity mismatch: {sample['id']}")
        partial.replace(output)
    (DESTINATION / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Pinned licensed Russian fixtures ready: {DESTINATION}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
        print(f"Parakeet fixture acquisition failed: {error}", file=sys.stderr)
        raise SystemExit(2)

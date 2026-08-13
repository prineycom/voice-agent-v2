"""Hash-checked VoxCPM2 model and public-reference acquisition into the task cache."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "voxcpm2-fast-tts-v1.json"
USER_AGENT = "voice-agent-v2-voxcpm2-fast-tts/1"


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verified(path: Path, item: dict) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == int(item["size_bytes"])
        and digest(path) == item["sha256"]
    )


def acquire(url: str, path: Path, item: dict, *, verify_only: bool) -> None:
    if verified(path, item):
        return
    if path.exists():
        raise ValueError(f"cached artifact does not match the manifest: {path}")
    if verify_only:
        raise ValueError(f"required cached artifact is absent: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise ValueError(f"partial acquisition requires inspection before retry: {partial}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response, partial.open("xb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    if not verified(partial, item):
        raise ValueError(f"downloaded artifact failed hash/size verification: {path.name}")
    partial.rename(path)


def reference_url(reference: dict) -> str:
    dataset = urllib.parse.quote(reference["dataset"], safe="")
    url = (
        "https://datasets-server.huggingface.co/rows"
        f"?dataset={dataset}&config=default&split={reference['split']}"
        f"&offset={reference['row_index']}&length=1&revision={reference['revision']}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if len(payload.get("rows", [])) != 1:
        raise ValueError("public reference dataset returned an unexpected row count")
    returned = payload["rows"][0]
    row = returned["row"]
    if (
        returned.get("row_idx") != reference["row_index"]
        or row.get("text") != reference["text"]
        or row.get("audio_filepath") != reference["source_audio_path"]
        or float(row.get("duration", -1)) != float(reference["duration_seconds"])
    ):
        raise ValueError("public reference metadata drifted from the pinned manifest")
    assets = row.get("audio")
    if not isinstance(assets, list) or len(assets) != 1:
        raise ValueError("public reference row has an invalid audio asset")
    asset_url = assets[0].get("src", "")
    if reference["revision"] not in asset_url or not asset_url.startswith("https://"):
        raise ValueError("public reference asset is not tied to the pinned dataset revision")
    return asset_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "voice-agent.voxcpm2-fast-tts.v1":
        raise ValueError("unsupported VoxCPM2 artifact manifest")
    cache = Path(manifest["cache_root"]).resolve()
    expected = Path("/home/priney/.cache/voice-agent-v2/experiments/voxcpm2-fast-tts")
    if cache != expected:
        raise ValueError("manifest cache root is outside the authorized task cache")

    model = manifest["model"]
    model_root = cache / model["directory"]
    base = f"https://huggingface.co/{model['identity']}/resolve/{model['revision']}"
    for item in model["files"]:
        acquire(
            f"{base}/{item['path']}?download=true",
            model_root / item["path"], item, verify_only=args.verify_only,
        )

    reference = manifest["reference"]
    reference_path = cache / reference["cache_path"]
    if not verified(reference_path, reference):
        if reference_path.exists() or args.verify_only:
            acquire("", reference_path, reference, verify_only=args.verify_only)
        acquire(reference_url(reference), reference_path, reference, verify_only=False)
    print(f"Pinned VoxCPM2 model and public reference verified: {cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Explicit, hash-checked acquisition into the authorized Slice 2 cache only."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
from typing import Any

from .safety import cache_path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "config" / "candidates.v1.json"
STT_CORPUS = ROOT / "fixtures" / "stt-russian-ruls.v1.json"
RUNTIME_ARTIFACTS = ROOT / "config" / "runtime-artifacts.v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_approved_preregistration() -> str:
    preregistration = ROOT / "config" / "preregistration.v1.json"
    payload = _load(preregistration)
    if payload.get("state") != "approved" or payload.get("approval", {}).get("status") != "approved":
        raise ValueError("downloads are disabled until the Slice 2 preregistration is approved")
    clean = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", str(preregistration.relative_to(ROOT.parent))],
        cwd=ROOT.parent,
        check=False,
    )
    if clean.returncode != 0:
        raise ValueError("approved preregistration must be committed before any download")
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", str(preregistration.relative_to(ROOT.parent))],
        cwd=ROOT.parent,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if len(commit) != 40:
        raise ValueError("could not identify the committed preregistration revision")
    return commit


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, destination: Path, size_bytes: int, expected_sha256: str | None) -> dict[str, Any]:
    destination = cache_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        observed_sha256 = _sha256(destination)
        if destination.stat().st_size == size_bytes and (
            expected_sha256 is None or observed_sha256 == expected_sha256
        ):
            return {"path": destination.name, "bytes": size_bytes, "sha256": observed_sha256, "reused": True}
        raise ValueError(f"existing cache artifact does not match manifest; refusing replacement: {destination}")

    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        raise ValueError(f"partial download already exists; inspect it before retrying: {partial}")
    request = urllib.request.Request(url, headers={"User-Agent": "voice-agent-v2-slice2-evidence/1"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("xb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    if partial.stat().st_size != size_bytes:
        raise ValueError(f"download size mismatch for {destination.name}: {partial.stat().st_size} != {size_bytes}")
    observed_sha256 = _sha256(partial)
    if expected_sha256 is not None and observed_sha256 != expected_sha256:
        raise ValueError(f"download SHA-256 mismatch for {destination.name}")
    partial.rename(destination)
    return {"path": destination.name, "bytes": size_bytes, "sha256": observed_sha256, "reused": False}


def candidate_plan(candidate_ids: list[str]) -> dict[str, Any]:
    manifest = _load(CANDIDATES)
    by_id = {candidate["id"]: candidate for candidate in manifest["candidates"]}
    unknown = sorted(set(candidate_ids) - set(by_id))
    if unknown:
        raise ValueError(f"unknown candidate IDs: {unknown}")
    entries = []
    total = 0
    for candidate_id in candidate_ids:
        candidate = by_id[candidate_id]
        if not candidate["screen"]["testable"]:
            raise ValueError(f"candidate is not testable: {candidate_id}")
        artifact_bytes = sum(item["size_bytes"] for item in candidate["artifact"]["files"])
        total += artifact_bytes
        entries.append({"id": candidate_id, "artifact_bytes": artifact_bytes})
    return {
        "candidate_artifacts": entries,
        "artifact_download_bytes": total,
        "shared_runtime_estimate_bytes": manifest["shared_runtime"]["expected_download_bytes"],
        "note": "shared runtime is installed separately and counted once",
    }


def acquire_candidates(candidate_ids: list[str], confirmed_bytes: int) -> dict[str, Any]:
    preregistration_commit = _require_approved_preregistration()
    plan = candidate_plan(candidate_ids)
    required = plan["artifact_download_bytes"]
    if confirmed_bytes != required:
        raise ValueError(f"confirmation must equal exact planned artifact bytes: {required}")
    manifest = _load(CANDIDATES)
    by_id = {candidate["id"]: candidate for candidate in manifest["candidates"]}
    acquired: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        candidate = by_id[candidate_id]
        directory = cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/artifacts") / candidate_id)
        files = []
        for item in candidate["artifact"]["files"]:
            destination = directory / item["path"]
            files.append(_download(item["url"], destination, item["size_bytes"], item.get("sha256")))
        acquired.append({"id": candidate_id, "files": files})
    result = {
        "schema_version": "voice-agent.slice2-acquisition.v1",
        "preregistration_commit": preregistration_commit,
        "artifacts": acquired,
    }
    evidence = cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/acquisition/candidates.json"))
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def prepare_runtime(confirmed_bytes: int) -> dict[str, Any]:
    preregistration_commit = _require_approved_preregistration()
    manifest = _load(RUNTIME_ARTIFACTS)
    expected = manifest["expected_download_bytes"]
    if confirmed_bytes != expected:
        raise ValueError(f"confirmation must equal declared shared-runtime estimate: {expected}")
    runtime_root = cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/runtime"))
    runtime_root.mkdir(parents=True, exist_ok=True)
    safe_environment = {
        "HOME": str(cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/home"))),
        "XDG_CACHE_HOME": str(cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/xdg"))),
        "PIP_CACHE_DIR": str(cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/pip-cache"))),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for directory_key in ("HOME", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
        Path(safe_environment[directory_key]).mkdir(parents=True, exist_ok=True)

    locks: dict[str, dict[str, Any]] = {}
    for name, requirement_file in (
        ("vllm", "requirements-vllm.lock"),
        ("stt-tts", "requirements-stt-tts.lock"),
    ):
        venv = runtime_root / f"{name}-venv"
        if venv.exists() and not (venv / "bin" / "python").is_file():
            raise ValueError(f"incomplete runtime venv exists; inspect before retrying: {venv}")
        if not venv.exists():
            subprocess.run(["/usr/bin/python3", "-m", "venv", str(venv)], check=True, env=safe_environment)
        subprocess.run(
            [
                str(venv / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--only-binary=:all:",
                "--index-url=https://pypi.org/simple",
                "--requirement",
                str(ROOT / "config" / requirement_file),
            ],
            check=True,
            env=safe_environment,
        )
        freeze = subprocess.run(
            [str(venv / "bin" / "python"), "-m", "pip", "freeze", "--all"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            env=safe_environment,
        ).stdout
        freeze_path = runtime_root / f"{name}-freeze.txt"
        if freeze_path.exists():
            raise ValueError(f"refusing to replace runtime lock: {freeze_path}")
        freeze_path.write_text(freeze, encoding="utf-8")
        locks[name] = {
            "python_version": subprocess.run(
                [str(venv / "bin" / "python"), "--version"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                env=safe_environment,
            ).stdout.strip(),
            "freeze_sha256": _sha256(freeze_path),
        }

    result = {
        "schema_version": "voice-agent.slice2-runtime-lock.v1",
        "preregistration_commit": preregistration_commit,
        "runtimes": locks,
    }
    lock_path = runtime_root / "runtime-lock.json"
    if lock_path.exists():
        raise ValueError(f"refusing to replace runtime evidence: {lock_path}")
    lock_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _fetch_corpus_row(dataset: str, split: str, row_index: int) -> dict[str, Any]:
    encoded = dataset.replace("/", "%2F")
    url = (
        "https://datasets-server.huggingface.co/rows"
        f"?dataset={encoded}&config=default&split={split}&offset={row_index}&length=1"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "voice-agent-v2-slice2-evidence/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    row = payload["rows"][0]
    if row["row_idx"] != row_index:
        raise ValueError(f"dataset server returned wrong row for {row_index}")
    return row["row"]


def acquire_stt_corpus(confirmed_max_bytes: int) -> dict[str, Any]:
    preregistration_commit = _require_approved_preregistration()
    fixture = _load(STT_CORPUS)
    declared_max = fixture["acquisition"]["maximum_download_bytes"]
    if confirmed_max_bytes != declared_max:
        raise ValueError(f"confirmation must equal declared corpus maximum bytes: {declared_max}")
    corpus_root = cache_path(Path("/home/priney/.cache/voice-agent-v2/slice-2/corpora/stt-ruls-v1"))
    corpus_root.mkdir(parents=True, exist_ok=True)
    acquired: list[dict[str, Any]] = []
    total = 0
    for sample in fixture["samples"]:
        row = _fetch_corpus_row(fixture["source"]["dataset"], fixture["source"]["split"], sample["row_index"])
        if row["text"] != sample["reference"] or row["audio_filepath"] != sample["source_audio_path"]:
            raise ValueError(f"pinned corpus metadata drift for row {sample['row_index']}")
        asset_url = row["audio"][0]["src"]
        if fixture["source"]["revision"] not in asset_url:
            raise ValueError("dataset server asset is not tied to the pinned corpus revision")
        destination = corpus_root / f"ruls-test-{sample['row_index']:04d}.wav"
        request = urllib.request.Request(asset_url, method="HEAD")
        with urllib.request.urlopen(request, timeout=60) as response:
            size = int(response.headers["Content-Length"])
        if total + size > declared_max:
            raise ValueError("selected corpus exceeds preregistered acquisition bound")
        record = _download(asset_url, destination, size, None)
        total += size
        acquired.append({"sample_id": sample["id"], **record})
    result = {
        "schema_version": "voice-agent.slice2-corpus-acquisition.v1",
        "preregistration_commit": preregistration_commit,
        "source_revision": fixture["source"]["revision"],
        "total_bytes": total,
        "samples": acquired,
    }
    (corpus_root / "acquisition.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result

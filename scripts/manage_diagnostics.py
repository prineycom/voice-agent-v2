#!/usr/bin/env python3
"""Bounded operator deletion/status utility for explicit content captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent_v2.diagnostics import DiagnosticContentCapture  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or delete one explicitly enabled outside-Git diagnostic capture."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    status = subcommands.add_parser("status")
    status.add_argument("capture_directory", type=Path)
    delete = subcommands.add_parser("delete")
    delete.add_argument("capture_directory", type=Path)
    purge = subcommands.add_parser("purge-expired")
    purge.add_argument("capture_root", type=Path)
    return parser.parse_args()


def safe_manifest(path: Path) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    document = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != "voice-agent.diagnostic-content-capture.v1"
        or document.get("explicit_opt_in") is not True
        or resolved.name != f"capture-{document.get('session_id')}"
        or not isinstance(document.get("owner_nonce"), str)
        or len(document["owner_nonce"]) != 32
    ):
        raise ValueError("capture manifest is invalid")
    # Never enumerate or print captured content names.
    return {
        "schema_version": document["schema_version"],
        "session_id": document["session_id"],
        "created_unix_seconds": document["created_unix_seconds"],
        "expires_unix_seconds": document["expires_unix_seconds"],
        "max_files": document["max_files"],
        "max_bytes": document["max_bytes"],
        "explicit_opt_in": True,
        "path": str(resolved),
    }


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.command == "status":
            print(json.dumps(safe_manifest(arguments.capture_directory), separators=(",", ":")))
        elif arguments.command == "delete":
            capture = arguments.capture_directory.expanduser().resolve()
            DiagnosticContentCapture.delete_path(capture)
            print(json.dumps({"deleted": True, "path": str(capture)}, separators=(",", ":")))
        else:
            count = DiagnosticContentCapture.purge_expired(arguments.capture_root)
            print(json.dumps({"expired_captures_deleted": count}, separators=(",", ":")))
    except (OSError, ValueError) as error:
        print(f"diagnostic management refused: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

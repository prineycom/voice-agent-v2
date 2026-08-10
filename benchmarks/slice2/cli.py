"""Command-line entrypoint for Slice 2 evidence tooling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .acquire import acquire_candidates, acquire_stt_corpus, candidate_plan, prepare_runtime
from .cloud_measure import measure_cloud
from .cloud_scoring import score_cloud_automated
from .host import write_capture
from .measure import measure_llm, measure_stt, measure_tts
from .overlap_measure import measure_fixed_stack_overlap
from .results import write_failed_selection
from .safety import assert_privacy_safe_result
from .schema import validate
from .scoring import aggregate_human_score_card, score_stt
from .stack_measure import measure_qwen3_tts

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def validate_committed() -> dict[str, Any]:
    pairs = {
        ROOT / "config" / "candidates.v1.json": SCHEMA_DIR / "candidates.v1.schema.json",
        ROOT / "config" / "preregistration.v1.json": SCHEMA_DIR / "preregistration.v1.schema.json",
        ROOT / "config" / "preregistration.cloud.v2.json": SCHEMA_DIR / "cloud-preregistration.v2.schema.json",
        ROOT / "config" / "preregistration.stack.v3.json": SCHEMA_DIR / "stack-preregistration.v3.schema.json",
        ROOT / "config" / "runtime-artifacts.v1.json": SCHEMA_DIR / "runtime-artifacts.v1.schema.json",
        ROOT / "fixtures" / "stt-russian-ruls.v1.json": SCHEMA_DIR / "stt-corpus.v1.schema.json",
        ROOT / "fixtures" / "llm-russian.v1.json": SCHEMA_DIR / "llm-rubric.v1.schema.json",
        ROOT / "fixtures" / "llm-parallel-russian.v1.json": SCHEMA_DIR / "llm-parallel.v1.schema.json",
        ROOT / "fixtures" / "tts-russian.v1.json": SCHEMA_DIR / "tts-rubric.v1.schema.json",
        ROOT / "evidence" / "host-live.v1.json": SCHEMA_DIR / "host.v1.schema.json",
        ROOT / "evidence" / "litellm-discovery.v1.json": SCHEMA_DIR / "cloud-discovery.v1.schema.json",
    }
    validated = []
    for instance_path, schema_path in pairs.items():
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validate(instance, schema)
        validated.append(str(instance_path.relative_to(ROOT.parent)))

    result_schema = json.loads((SCHEMA_DIR / "result.v1.schema.json").read_text(encoding="utf-8"))
    fixed_stack_schema = json.loads((SCHEMA_DIR / "fixed-stack-evidence.v1.schema.json").read_text(encoding="utf-8"))
    result_files = sorted((ROOT / "results").glob("*.json")) if (ROOT / "results").exists() else []
    for result_path in result_files:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        schema = fixed_stack_schema if result.get("schema_version") == "voice-agent.slice2-fixed-stack-evidence.v1" else result_schema
        validate(result, schema)
        assert_privacy_safe_result(result)
        validated.append(str(result_path.relative_to(ROOT.parent)))

    selection_path = ROOT / "selection" / "selection.v1.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selection_schema = json.loads((SCHEMA_DIR / "selection.v1.schema.json").read_text(encoding="utf-8"))
        validate(selection, selection_schema)
        assert_privacy_safe_result(selection)
        validated.append(str(selection_path.relative_to(ROOT.parent)))

    return {"validated": validated, "result_files": len(result_files), "status": "pass"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice Agent v2 Slice 2 benchmark evidence harness")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate all committed schemas, fixtures, and redacted evidence")

    host = commands.add_parser("capture-host", help="capture a whitelisted live host inventory under the cache root")
    host.add_argument("--output", type=Path, required=True)

    plan = commands.add_parser("plan-download", help="print exact candidate artifact bytes before acquisition")
    plan.add_argument("candidate", nargs="+")

    acquire = commands.add_parser("acquire", help="download pinned candidate artifacts after exact byte acknowledgement")
    acquire.add_argument("candidate", nargs="+")
    acquire.add_argument("--confirm-bytes", type=int, required=True)

    runtime = commands.add_parser("prepare-runtime", help="install pinned user-space tooling only under the authorized cache")
    runtime.add_argument("--confirm-bytes", type=int, required=True)

    corpus = commands.add_parser("acquire-stt-corpus", help="download the fixed public corpus subset into cache")
    corpus.add_argument("--confirm-max-bytes", type=int, required=True)

    stt = commands.add_parser("score-stt", help="score cached public-corpus hypotheses without emitting content")
    stt.add_argument("--raw-output", type=Path, required=True)

    cloud_score = commands.add_parser("score-cloud", help="evaluate preregistered automated cloud gates without emitting content")
    cloud_score.add_argument("--raw-output", type=Path, required=True)

    human = commands.add_parser("score-human", help="aggregate a cached numeric blind-review card")
    human.add_argument("--role", choices=("llm", "tts"), required=True)
    human.add_argument("--score-card", type=Path, required=True)

    measure = commands.add_parser("measure", help="run one approved candidate into cache-local raw evidence")
    measure.add_argument("role", choices=("stt", "llm", "tts", "cloud"))
    measure.add_argument("candidate_id")
    measure.add_argument("--run-label", choices=("primary", "repeat"), default="primary")
    qwen_tts = commands.add_parser("measure-qwen-tts", help="measure the committed fixed Qwen3 TTS configuration")
    qwen_tts.add_argument("--run-label", choices=("primary", "repeat"), default="primary")
    commands.add_parser("measure-fixed-stack-overlap", help="measure delegated cloud/TTS and barge-in/STT overlap")
    commands.add_parser("finalize-failure", help="write fail-closed results after measured hard-gate failure")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "validate":
            _print(validate_committed())
        elif args.command == "capture-host":
            _print(write_capture(args.output))
        elif args.command == "plan-download":
            _print(candidate_plan(args.candidate))
        elif args.command == "acquire":
            _print(acquire_candidates(args.candidate, args.confirm_bytes))
        elif args.command == "prepare-runtime":
            _print(prepare_runtime(args.confirm_bytes))
        elif args.command == "acquire-stt-corpus":
            _print(acquire_stt_corpus(args.confirm_max_bytes))
        elif args.command == "score-stt":
            _print(score_stt(args.raw_output))
        elif args.command == "score-cloud":
            _print(score_cloud_automated(args.raw_output))
        elif args.command == "score-human":
            _print(aggregate_human_score_card(args.score_card, args.role))
        elif args.command == "measure":
            if args.role != "cloud" and args.run_label != "primary":
                raise ValueError("run labels apply only to cloud measurement")
            if args.role == "stt":
                _print(measure_stt(args.candidate_id))
            elif args.role == "llm":
                _print(measure_llm(args.candidate_id))
            elif args.role == "tts":
                _print(measure_tts(args.candidate_id))
            else:
                if args.candidate_id != "deepseek-v4-flash":
                    raise ValueError("cloud measurement is approved only for deepseek-v4-flash")
                _print(measure_cloud(args.run_label))
        elif args.command == "measure-qwen-tts":
            _print(measure_qwen3_tts(args.run_label))
        elif args.command == "measure-fixed-stack-overlap":
            _print(measure_fixed_stack_overlap())
        elif args.command == "finalize-failure":
            _print(write_failed_selection())
        else:  # pragma: no cover - argparse closes the command set
            raise AssertionError(args.command)
    except (AssertionError, KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from chat_session import ChatSession

from .workflows import (
    ROOT,
    SCHEMA_VERSION,
    audit_solar_data,
    apply_solar_cleaning,
    engineer_solar_features,
    ingest_align_solar_data,
    plan_solar_feature_workflow,
    prepare_experiment_handoff,
    propose_solar_cleaning,
    rebuild_solar_data_pipeline,
    run_solar_feature_workflow,
)


READ_ONLY_SKILLS = {"audit-solar-data-quality"}


def _parser(skill_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Independent runner for {skill_name}")
    parser.add_argument("--input", action="append", default=[], help="Input CSV path; repeat for multiple sources.")
    parser.add_argument("--mode", choices=["plan", "execute"], default="plan")
    parser.add_argument("--approved", action="store_true", help="Confirm the planned business-data writes.")
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--session-file")
    parser.add_argument("--use-llm-semantics", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-strategy", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--run-tests", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--action", choices=["propose", "apply"], default="propose")
    parser.add_argument("--split-proposal", help="JSON split proposal previously confirmed by the user.")
    return parser


def _planned_writes(skill_name: str, run_id: str) -> list[str]:
    values = {
        "ingest-align-solar-data": ["data/uploads/<dataset-id>/", "data/processed/uploads/<dataset-id>/"],
        "clean-solar-data": ["data/processed/uploads/<dataset-id>/cleaned_v1.csv", "data/processed/uploads/<dataset-id>/quality_report.json"],
        "engineer-solar-features": ["data/processed/uploads/<dataset-id>/engineered_features.csv", "data/processed/uploads/<dataset-id>/feature_registry.json"],
        "prepare-experiment-handoff": ["data/processed/uploads/<dataset-id>/experiment_handoff.json", "data/processed/uploads/<dataset-id>/strategy_recommendation.json"],
        "rebuild-solar-data-pipeline": ["data/processed/"],
        "run-solar-feature-workflow": [f"data/processed/skill_runs/{run_id}/run_manifest.json", "data/uploads/<dataset-id>/", "data/processed/uploads/<dataset-id>/"],
    }
    return values.get(skill_name, [])


def _envelope(skill: str, status: str, run_id: str, **values: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": skill,
        "status": status,
        "run_id": run_id,
        "stage": values.pop("stage", None),
        "summary": values.pop("summary", {}),
        "planned_writes": values.pop("planned_writes", []),
        "artifacts": values.pop("artifacts", []),
        "warnings": values.pop("warnings", []),
        "error": values.pop("error", None),
        **values,
    }


def run_skill_cli(skill_name: str, argv: list[str] | None = None) -> int:
    args = _parser(skill_name).parse_args(argv)
    run_id = args.run_id or f"run_{uuid.uuid4().hex[:16]}"
    planned = _planned_writes(skill_name, run_id)
    mutates = skill_name not in READ_ONLY_SKILLS and not (skill_name == "clean-solar-data" and args.action == "propose")
    if args.mode == "plan":
        if skill_name == "run-solar-feature-workflow" and args.input:
            split_proposal = json.loads(args.split_proposal) if args.split_proposal else None
            result = plan_solar_feature_workflow(
                args.input,
                run_id=run_id,
                use_llm_semantics=args.use_llm_semantics,
                include_strategy=args.include_strategy,
                split_proposal=split_proposal,
            )
            payload = _envelope(
                skill_name,
                result["status"],
                run_id,
                stage="preflight",
                summary=result,
                planned_writes=result["planned_writes"],
                warnings=result["warnings"],
            )
        else:
            payload = _envelope(skill_name, "planned", run_id, stage="plan", planned_writes=planned if mutates else [])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if mutates and not args.approved:
        payload = _envelope(skill_name, "approval_required", run_id, stage="approval", planned_writes=planned)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    session_path = Path(args.session_file) if args.session_file else ROOT / "data" / "processed" / "skill_runs" / run_id / "session.json"
    session = ChatSession(session_path)
    split_proposal = json.loads(args.split_proposal) if args.split_proposal else None
    try:
        if skill_name == "audit-solar-data-quality":
            if len(args.input) != 1:
                raise ValueError("audit-solar-data-quality requires exactly one --input")
            result = audit_solar_data(args.input[0])
        elif skill_name == "ingest-align-solar-data":
            result = ingest_align_solar_data(args.input, session=session, use_llm_semantics=args.use_llm_semantics, split_proposal=split_proposal)
        elif skill_name == "clean-solar-data":
            if args.input and not session.get_current_dataset_path():
                ingest_align_solar_data(args.input, session=session, use_llm_semantics=args.use_llm_semantics, split_proposal=split_proposal)
            current = session.get_current_dataset_path()
            if not current:
                raise ValueError("Provide --input or --session-file with a current dataset")
            result = propose_solar_cleaning(current, session=session) if args.action == "propose" else apply_solar_cleaning(session)
        elif skill_name == "engineer-solar-features":
            if args.input and not session.get_current_dataset_path():
                ingest_align_solar_data(args.input, session=session, use_llm_semantics=args.use_llm_semantics, split_proposal=split_proposal)
            current = session.get_current_dataset_path()
            if not current:
                raise ValueError("Provide --input or --session-file with a current dataset")
            audit = audit_solar_data(current, session=session)
            result = audit if audit["status"] != "ok" else engineer_solar_features(session)
        elif skill_name == "prepare-experiment-handoff":
            if args.input and not session.get_current_dataset_path():
                ingest_align_solar_data(args.input, session=session, use_llm_semantics=args.use_llm_semantics, split_proposal=split_proposal)
                current = session.get_current_dataset_path()
                audit = audit_solar_data(current, session=session) if current else {"status": "failed"}
                if audit.get("status") != "ok":
                    result = audit
                else:
                    features = engineer_solar_features(session)
                    result = features if features.get("status") != "ok" else prepare_experiment_handoff(session, include_strategy=args.include_strategy)
            else:
                result = prepare_experiment_handoff(session, include_strategy=args.include_strategy)
        elif skill_name == "rebuild-solar-data-pipeline":
            result = rebuild_solar_data_pipeline(run_tests=args.run_tests)
        elif skill_name == "run-solar-feature-workflow":
            preflight = plan_solar_feature_workflow(
                args.input,
                run_id=run_id,
                use_llm_semantics=args.use_llm_semantics,
                include_strategy=args.include_strategy,
                split_proposal=split_proposal,
            )
            if preflight["status"] != "planned":
                result = preflight
            else:
                result = run_solar_feature_workflow(args.input, session=session, run_id=run_id, resume=args.resume, use_llm_semantics=args.use_llm_semantics, include_strategy=args.include_strategy, split_proposal=split_proposal, preflight_token=preflight["preflight_token"])
        else:
            raise ValueError(f"Unsupported skill runner: {skill_name}")
        status = result.get("status", "completed")
        exit_code = 0 if status in {"ok", "completed"} else 1
        payload = _envelope(
            skill_name,
            status,
            run_id,
            stage=result.get("last_stage") or result.get("stage"),
            summary=result,
            artifacts=result.get("artifacts", []),
            warnings=result.get("warnings", []),
            error=result.get("error"),
        )
    except Exception as exc:
        exit_code = 1
        payload = _envelope(skill_name, "failed", run_id, error={"type": type(exc).__name__, "message": str(exc)})
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_skill_cli("run-solar-feature-workflow", sys.argv[1:]))

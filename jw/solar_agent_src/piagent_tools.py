from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chat_session import ChatSession
from piagent_schemas import RECOMMENDED_EXPERIMENT_SPLITS, REQUIRED_OUTPUTS, PiAgentRequest

import dataset_stats_engine
import safe_expression_eval
import upload_quality_analyzer
import upload_column_splitter


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
DECISION_LOG_PATH = PROCESSED_DIR / "data_feature_agent_decision_log.json"
EXPERIMENT_HANDOFF_PATH = PROCESSED_DIR / "experiment_handoff.json"
BAILIAN_SUMMARY_PATH = PROCESSED_DIR / "bailian_experiment_summary.md"
BAILIAN_ANSWER_PATH = PROCESSED_DIR / "bailian_agent_answer.md"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_required_outputs() -> list[dict[str, Any]]:
    checks = []
    for item in REQUIRED_OUTPUTS:
        path = ROOT / item
        checks.append(
            {
                "path": item,
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else None,
            }
        )
    return checks


def run_full_workflow() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    import data_feature_agent

    data_feature_agent.main()
    finished = datetime.now(timezone.utc)
    return {
        "tool": "run_full_data_pipeline",
        "status": "ok",
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
    }


def run_contract_tests() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    finished = datetime.now(timezone.utc)
    return {
        "tool": "validate_data_contracts",
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def forbidden_inputs_from_registry(registry: dict[str, Any]) -> list[str]:
    forbidden = []
    for item in registry.get("fields", []):
        if item.get("leakage_risk") == "forbidden_as_input" or item.get("allowed_as_model_input") is False and item.get("role") == "label":
            forbidden.append(item["field"])
    return sorted(set(forbidden))


def build_risk_flags(report: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    flags = []
    incomplete = report.get("cycle_table_quality", {}).get("incomplete_cycles", [])
    if incomplete:
        flags.append("cycle_25_incomplete" if 25 in incomplete else "incomplete_cycles_present")
    source_profiles = report.get("source_profiles", {})
    if source_profiles.get("cycle_hale_wso_features", {}).get("cycles_with_wso_hale_evidence", 0) <= 5:
        flags.append("wso_small_sample")
    if "goes_xrs_monthly_features" in source_profiles:
        flags.append("goes_legacy_auxiliary_only")
    if forbidden_inputs_from_registry(registry):
        flags.append("next_cycle_labels_not_inputs")
    flags.append("hemisphere_1940_1991_external_calibrated_observation")
    return sorted(set(flags))


def summarize_quality(report: dict[str, Any]) -> dict[str, Any]:
    master_quality = report.get("master_table_quality", {})
    cycle_quality = report.get("cycle_table_quality", {})
    evidence = report.get("evidence_tiers", {})
    return {
        "all_source_overlap": master_quality.get("all_sources_overlap"),
        "incomplete_cycles": cycle_quality.get("incomplete_cycles", []),
        "primary_evidence": [
            item.get("signal")
            for item in evidence.get("primary_evidence", [])
            if item.get("signal")
        ],
        "auxiliary_evidence": [
            item.get("signal")
            for item in evidence.get("mechanism_or_auxiliary_evidence", [])
            if item.get("signal")
        ],
        "claims_policy": report.get("claims_policy_for_downstream_agents", {}),
    }


def summarize_feature_registry_for_llm(registry: dict[str, Any]) -> dict[str, Any]:
    fields = registry.get("fields", [])
    allowed_inputs = []
    labels = []
    forbidden_inputs = []
    for item in fields:
        field_summary = {
            "field": item.get("field"),
            "role": item.get("role"),
            "source": item.get("source"),
            "allowed_as_model_input": item.get("allowed_as_model_input"),
            "leakage_risk": item.get("leakage_risk"),
            "description": item.get("description"),
        }
        if item.get("leakage_risk") == "forbidden_as_input" or item.get("allowed_as_model_input") is False:
            forbidden_inputs.append(field_summary)
        elif item.get("role") == "label":
            labels.append(field_summary)
        else:
            allowed_inputs.append(field_summary)
    return {
        "field_count": len(fields),
        "allowed_or_candidate_inputs": allowed_inputs[:80],
        "labels": labels,
        "forbidden_inputs": forbidden_inputs,
    }


def build_bailian_payload(
    request: PiAgentRequest,
    agent_output: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request": request.to_dict(),
        "handoff": handoff,
        "quality_summary": summarize_quality(report),
        "quality_constraints": report.get("claims_policy_for_downstream_agents", {}),
        "source_profiles": report.get("source_profiles", {}),
        "feature_registry_summary": summarize_feature_registry_for_llm(registry),
        "workflow_outputs": agent_output.get("outputs", {}),
    }


def run_bailian_experiment_summary(
    request: PiAgentRequest,
    agent_output: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        from bailian_llm import generate_experiment_summary

        payload = build_bailian_payload(request, agent_output, report, registry, handoff)
        summary = generate_experiment_summary(payload)
        BAILIAN_SUMMARY_PATH.write_text(summary, encoding="utf-8")
        status = "ok"
        error = None
    except Exception as exc:
        status = "failed"
        error = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finished = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "tool": "bailian_experiment_summary",
        "status": status,
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "summary_path": rel(BAILIAN_SUMMARY_PATH),
    }
    if error:
        result.update(error)
    return result


def run_bailian_agent_answer(
    request: PiAgentRequest,
    agent_output: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        from bailian_llm import generate_agent_answer

        payload = build_bailian_payload(request, agent_output, report, registry, handoff)
        if BAILIAN_SUMMARY_PATH.exists():
            payload["latest_bailian_experiment_summary"] = BAILIAN_SUMMARY_PATH.read_text(encoding="utf-8")
        answer = generate_agent_answer(payload, request.question or "")
        BAILIAN_ANSWER_PATH.write_text(answer, encoding="utf-8")
        status = "ok"
        error = None
    except Exception as exc:
        answer = None
        status = "failed"
        error = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finished = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "tool": "bailian_agent_answer",
        "status": status,
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "answer_path": rel(BAILIAN_ANSWER_PATH),
        "question": request.question,
        "answer": answer,
    }
    if error:
        result.update(error)
    return result


def build_experiment_handoff(
    request: PiAgentRequest,
    agent_output: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    forbidden_inputs = forbidden_inputs_from_registry(registry)
    risk_flags = build_risk_flags(report, registry)
    handoff = {
        "agent": "data_feature_agent",
        "platform_target": "bailian_function_calling",
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "request": request.to_dict(),
        "primary_outputs": {
            "monthly_table": "data/processed/clean_monthly_timeseries.csv",
            "cycle_features": "data/processed/cycle_features.csv",
            "quality_report": "data/processed/data_quality_report.json",
            "feature_registry": "data/processed/feature_registry.json",
            "lineage_manifest": "data/processed/data_lineage_manifest.json",
            "agent_output": "data/processed/agent_output.json",
        },
        "specialized_outputs": {
            "goes_monthly_features": "data/processed/goes_xrs_monthly_features.csv",
            "cycle_flare_features": "data/processed/cycle_flare_features.csv",
            "wso_hale_monthly_features": "data/processed/wso_polar_monthly_features.csv",
            "cycle_hale_wso_features": "data/processed/cycle_hale_wso_features.csv",
            "cycle_hale_wso_sensitivity": "data/processed/cycle_hale_wso_sensitivity.csv",
        },
        "handoff_to_experiment_agent": {
            "recommended_tables": [
                "data/processed/cycle_features.csv",
                "data/processed/clean_monthly_timeseries.csv",
                "data/processed/feature_registry.json",
                "data/processed/data_quality_report.json",
            ],
            "recommended_splits": RECOMMENDED_EXPERIMENT_SPLITS,
            "forbidden_inputs": forbidden_inputs,
            "required_quality_files": [
                "data/processed/data_quality_report.json",
                "data/processed/feature_registry.json",
                "data/processed/data_lineage_manifest.json",
            ],
        },
        "risk_flags": risk_flags,
        "quality_summary": summarize_quality(report),
        "workflow_agent_output_status": agent_output.get("status"),
    }
    EXPERIMENT_HANDOFF_PATH.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    return handoff


def build_decision_log(
    request: PiAgentRequest,
    selected_tools: list[dict[str, Any]],
    required_output_checks: list[dict[str, Any]],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    if request.rebuild:
        decision = "full_rebuild_completed"
        reason = "Rebuild was requested by PiAgent task input."
    else:
        missing = [item["path"] for item in required_output_checks if not item["exists"]]
        decision = "validated_existing_outputs" if not missing else "existing_outputs_incomplete"
        reason = "Existing outputs were used because rebuild=false." if not missing else f"Missing outputs: {missing}"
    decision_log = {
        "agent": "data_feature_agent",
        "platform_target": "bailian_function_calling",
        "status": "ok" if all(tool["status"] == "ok" for tool in selected_tools) else "failed",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "request": request.to_dict(),
        "decision": decision,
        "reasoning_summary": reason,
        "selected_tools": selected_tools,
        "required_output_checks": required_output_checks,
        "quality_constraints_checked": [
            "date_month month-start format",
            "GOES coverage zero-event policy",
            "GOES outside coverage is not zero-filled",
            "WSO-Hale pre-WSO cycles remain missing",
            "next_cycle_* leakage risk",
            "1940-1991 hemispheric source type is external calibrated observation",
        ],
        "handoff_path": rel(EXPERIMENT_HANDOFF_PATH),
        "llm_summary_path": rel(BAILIAN_SUMMARY_PATH) if BAILIAN_SUMMARY_PATH.exists() else None,
        "llm_answer_path": rel(BAILIAN_ANSWER_PATH) if BAILIAN_ANSWER_PATH.exists() else None,
        "handoff_summary": {
            "forbidden_inputs": handoff["handoff_to_experiment_agent"]["forbidden_inputs"],
            "risk_flags": handoff["risk_flags"],
            "recommended_split_ids": [
                item["id"] for item in handoff["handoff_to_experiment_agent"]["recommended_splits"]
            ],
        },
    }
    DECISION_LOG_PATH.write_text(json.dumps(decision_log, ensure_ascii=False, indent=2), encoding="utf-8")
    return decision_log


def load_dataset_for_chat(request: PiAgentRequest, session: ChatSession) -> dict[str, Any]:
    """Load a CSV (internal or external) and set it as the current dataset."""
    import pandas as pd
    from upload_inspector import inspect_csv, inspect_uploaded_file
    import llm_upload_semantic_recognizer

    path_arg = request.upload_path or ""
    path = Path(path_arg).expanduser().resolve()

    # Support project-internal paths (e.g. data/processed/cycle_features.csv).
    try:
        rel_to_root = path.relative_to(ROOT)
        is_internal = True
    except ValueError:
        rel_to_root = path
        is_internal = False

    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")

    if path.suffix.lower() != ".csv":
        raise ValueError(f"Only CSV files are supported; got {path.suffix!r}")

    if is_internal:
        inspection = inspect_csv(path)
        stored_path = str(rel_to_root).replace("\\", "/")
        # Wrap inspection in the same shape as inspect_uploaded_file for session extraction.
        inspection = {
            "source_file": {
                "name": path.name,
                "absolute_path": str(path),
                "bytes": path.stat().st_size,
                "stored_path": stored_path,
            },
            "inspection": inspection,
        }
    else:
        inspection = inspect_uploaded_file(path)
        stored_path = inspection["source_file"]["stored_path"]

    session.set_current_dataset(stored_path, inspection)

    # Read the file as pandas would by default; this is what downstream tools see.
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]

    # Detect single-column multi-field CSVs.
    split_proposal: dict[str, Any] | None = None
    split_detection = upload_column_splitter.detect_multifield_single_column(df)
    if split_detection is not None:
        # If the single-column header looks like data when split by the detected
        # delimiter, the original file has no real header row. Re-read as
        # header=None so the first data row is not lost.
        header = str(df.columns[0])
        pattern = upload_column_splitter.DELIMITER_PATTERNS[split_detection["delimiter"]]
        header_parts = [p for p in re.split(pattern, header) if p]
        if len(header_parts) > 1:
            try:
                df = pd.read_csv(path, header=None, names=["raw_column"], encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(path, header=None, names=["raw_column"], encoding="gb18030")
            df.columns = [str(c).strip() for c in df.columns]
            first_row_is_header = False
        else:
            first_row_is_header = None  # Let the LLM/heuristic decide.

        use_llm = getattr(request, "use_llm_semantics", True)
        split_proposal = upload_column_splitter.llm_recognize_split(df, split_detection, use_llm=use_llm)
        if first_row_is_header is not None:
            split_proposal["first_row_is_header"] = first_row_is_header

    llm_result: dict[str, Any] | None = None
    llm_warning: str | None = None
    if not is_internal and getattr(request, "use_llm_semantics", True):
        if split_proposal is not None:
            llm_warning = "检测到单列多字段 CSV；语义识别将在拆分后执行。"
        else:
            llm_result = llm_upload_semantic_recognizer.run(df, use_llm=True)
            if llm_result.get("status") == "llm_unavailable":
                llm_warning = llm_result.get("error", "LLM unavailable; rule-based recognition was used.")
            session.set_llm_recognition(stored_path, llm_result)

    return {
        "status": "ok",
        "task": "load_dataset",
        "dataset": stored_path,
        "is_internal": is_internal,
        "inspection": inspection.get("inspection", inspection),
        "llm_recognition": llm_result,
        "warning": llm_warning,
        "requires_split_confirmation": split_proposal is not None,
        "split_proposal": split_proposal,
    }


def run_chat_llm_answer(request: PiAgentRequest, session: ChatSession) -> dict[str, Any]:
    """Answer an open-ended question with LLM, injecting current dataset context."""
    from bailian_llm import generate_agent_answer

    required_checks = check_required_outputs()
    missing = [item for item in required_checks if not item["exists"]]
    if missing:
        raise FileNotFoundError(f"Required outputs missing for chat LLM answer: {missing}")

    agent_output = read_json(PROCESSED_DIR / "agent_output.json")
    report = read_json(PROCESSED_DIR / "data_quality_report.json")
    registry = read_json(PROCESSED_DIR / "feature_registry.json")
    handoff = build_experiment_handoff(request, agent_output, report, registry)

    dataset_context = session.get_inspection_summary() or {}
    payload = build_bailian_payload(request, agent_output, report, registry, handoff)
    payload["current_dataset"] = dataset_context
    payload["chat_history"] = session.get_history()
    payload["tool_trace"] = session.get_tool_trace()[-20:]

    if BAILIAN_SUMMARY_PATH.exists():
        payload["latest_bailian_experiment_summary"] = BAILIAN_SUMMARY_PATH.read_text(encoding="utf-8")

    started = datetime.now(timezone.utc)
    try:
        answer = generate_agent_answer(payload, request.question or "")
        status = "ok"
        error = None
    except Exception as exc:
        answer = None
        status = "failed"
        error = {"error_type": type(exc).__name__, "error": str(exc)}
    finished = datetime.now(timezone.utc)

    result: dict[str, Any] = {
        "tool": "chat_agent_answer",
        "status": status,
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "question": request.question,
        "answer": answer,
    }
    if error:
        result.update(error)
    return result


def run_chat_request(request: PiAgentRequest, session: ChatSession) -> dict[str, Any]:
    """Dispatch chat-related PiAgent requests."""
    if request.task == "load_dataset":
        return load_dataset_for_chat(request, session)

    if request.task == "apply_multifield_split":
        proposal = request.split_proposal or session.get_agent_state("pending_split_proposal")
        if not proposal:
            raise ValueError("apply_multifield_split requires a split_proposal")
        return upload_column_splitter.apply_split(
            session,
            proposal,
            run_quality=True,
            run_features=True,
        )

    if request.task == "align_uploads":
        import upload_alignment_engine

        return upload_alignment_engine.run(session)

    if request.task == "prepare_features_for_upload":
        import upload_data_feature_pipeline

        if request.upload_path:
            load_dataset_for_chat(request, session)
        return upload_data_feature_pipeline.run(session)

    if request.task == "dataset_stats":
        return dataset_stats_engine.run(request, session)

    if request.task == "dataset_query":
        return safe_expression_eval.run(request.query or "", session)

    if request.task == "analyze_quality":
        if request.upload_path:
            load_dataset_for_chat(request, session)
        return upload_quality_analyzer.run(session)

    if request.task in {"propose_cleaning", "apply_cleaning"}:
        import data_cleaning_engine

        if request.upload_path:
            load_dataset_for_chat(request, session)
        report = data_cleaning_engine.run(session, apply=(request.task == "apply_cleaning"))
        report["task"] = request.task
        return report

    if request.task == "generate_features":
        import feature_engineering_engine

        if request.upload_path:
            load_dataset_for_chat(request, session)
        return feature_engineering_engine.run(session)

    if request.task == "experiment_handoff":
        import experiment_handoff_engine

        if request.upload_path:
            load_dataset_for_chat(request, session)
        return experiment_handoff_engine.run(session)

    if request.task == "strategy_recommendation":
        import llm_strategy_recommender

        if request.upload_path:
            load_dataset_for_chat(request, session)
        return llm_strategy_recommender.run(session)

    if request.task == "ask_agent":
        from bailian_data_feature_agent import BailianDataFeatureAgent

        response = BailianDataFeatureAgent(session=session).run(
            request.question or "",
            session_id=session.session_id,
            approval_id=request.approval_id,
        )
        return {"agent": "data_feature_agent", "task": "ask_agent", **response.to_dict()}

    if request.task == "chat":
        action = getattr(request, "action", None)
        if action == "clear":
            session.clear_all()
            return {"status": "ok", "task": "chat", "action": "clear"}
        if action == "exit":
            return {"status": "ok", "task": "chat", "action": "exit"}
        raise ValueError(f"Unknown chat action: {action}")

    return run_piagent_request(request)


def run_piagent_request(request: PiAgentRequest) -> dict[str, Any]:
    if request.task == "inspect_upload":
        from upload_inspector import inspect_uploaded_file

        inspection = inspect_uploaded_file(request.upload_path or "")
        return {
            "agent": "data_feature_agent",
            "platform_target": "bailian_function_calling",
            "status": "ok",
            "task": "inspect_upload",
            "upload": inspection,
        }

    selected_tools = []
    required_checks_before = check_required_outputs()
    missing_before = [item for item in required_checks_before if not item["exists"]]
    if request.rebuild or missing_before:
        selected_tools.append(run_full_workflow())
    if request.run_tests:
        selected_tools.append(run_contract_tests())

    required_checks_after = check_required_outputs()
    missing_after = [item for item in required_checks_after if not item["exists"]]
    if missing_after:
        raise FileNotFoundError(f"Required outputs are missing after PiAgent run: {missing_after}")
    failed_tools = [tool for tool in selected_tools if tool["status"] != "ok"]
    if failed_tools:
        raise RuntimeError(f"PiAgent tool failures: {failed_tools}")

    agent_output = read_json(PROCESSED_DIR / "agent_output.json")
    report = read_json(PROCESSED_DIR / "data_quality_report.json")
    registry = read_json(PROCESSED_DIR / "feature_registry.json")

    handoff = build_experiment_handoff(request, agent_output, report, registry)
    llm_summary_tool = None
    llm_answer_tool = None
    if request.task == "summarize_for_experiment":
        llm_summary_tool = run_bailian_experiment_summary(request, agent_output, report, registry, handoff)
        selected_tools.append(llm_summary_tool)
    if request.task == "ask_agent":
        llm_answer_tool = run_bailian_agent_answer(request, agent_output, report, registry, handoff)
        selected_tools.append(llm_answer_tool)
    decision_log = build_decision_log(request, selected_tools, required_checks_after, handoff)
    if llm_summary_tool and llm_summary_tool["status"] != "ok":
        raise RuntimeError(f"Bailian summary failed: {llm_summary_tool}")
    if llm_answer_tool and llm_answer_tool["status"] != "ok":
        raise RuntimeError(f"Bailian answer failed: {llm_answer_tool}")
    return {
        "agent": "data_feature_agent",
        "platform_target": "bailian_function_calling",
        "status": "ok",
        "decision_log": rel(DECISION_LOG_PATH),
        "experiment_handoff": rel(EXPERIMENT_HANDOFF_PATH),
        "llm_summary": rel(BAILIAN_SUMMARY_PATH) if llm_summary_tool else None,
        "llm_answer": rel(BAILIAN_ANSWER_PATH) if llm_answer_tool else None,
        "answer": llm_answer_tool["answer"] if llm_answer_tool else None,
        "decision": decision_log["decision"],
        "risk_flags": handoff["risk_flags"],
        "forbidden_inputs": handoff["handoff_to_experiment_agent"]["forbidden_inputs"],
        "recommended_split_ids": [
            item["id"] for item in handoff["handoff_to_experiment_agent"]["recommended_splits"]
        ],
    }

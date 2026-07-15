from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import data_cleaning_engine
import dataset_stats_engine
import experiment_handoff_engine
import feature_engineering_engine
import llm_strategy_recommender
import upload_alignment_engine
import upload_column_splitter
import upload_quality_analyzer
from chat_session import ChatSession
from piagent_schemas import PiAgentRequest, REQUIRED_OUTPUTS
from piagent_tools import load_dataset_for_chat, run_contract_tests, run_full_workflow
from upload_inspector import inspect_csv


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "data" / "processed" / "skill_runs"
SCHEMA_VERSION = "1.0"


class EphemeralSession(ChatSession):
    """A ChatSession that keeps read-only audit state in memory."""

    def __init__(self) -> None:
        self.session_path = Path("<ephemeral>")
        self._data = self._default_session()

    def save(self) -> None:
        return


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _resolve_csv(value: str | Path, resolver: Callable[[str | Path], Path] | None = None) -> Path:
    if resolver is not None:
        return resolver(value)
    from agent_tools import PathPolicy

    return PathPolicy().resolve_csv(value)


def _read_csv(path: Path, inspection: dict[str, Any] | None = None) -> pd.DataFrame:
    inspection = inspection or inspect_csv(path)
    delimiter = str(inspection.get("delimiter") or ",").replace("\\t", "\t")
    frame = pd.read_csv(
        path,
        encoding=inspection.get("encoding") or "utf-8",
        sep=delimiter,
        low_memory=False,
    )
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _inspection_wrapper(path: Path, inspection: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file": {
            "name": path.name,
            "absolute_path": str(path),
            "stored_path": str(path),
            "bytes": path.stat().st_size,
        },
        "inspection": inspection,
    }


def _restore_lineage_dataset(session: ChatSession, current_path: str | None, original_summary: dict[str, Any] | None) -> None:
    """Keep run artifacts under the original dataset id after engines auto-load derived files."""
    if not current_path or not original_summary:
        return
    full_path = Path(current_path) if Path(current_path).is_absolute() else ROOT / current_path
    inspection = inspect_csv(full_path)
    stored_path = original_summary.get("stored_path") or current_path
    wrapper = {
        "source_file": {
            "name": full_path.name,
            "absolute_path": str(full_path.resolve()),
            "stored_path": stored_path,
            "bytes": full_path.stat().st_size,
            "sha256": original_summary.get("sha256"),
        },
        "inspection": inspection,
        "report_path": original_summary.get("report_path"),
    }
    session.set_current_dataset(current_path, wrapper)


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _preflight_token(input_fingerprints: dict[str, str], config_hash: str) -> str:
    return _config_fingerprint({"inputs": input_fingerprints, "configuration": config_hash})


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _critical_quality_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [issue for issue in report.get("issues", []) if issue.get("severity") == "critical"]


def audit_solar_data(
    path: str | Path,
    *,
    session: ChatSession | None = None,
    resolver: Callable[[str | Path], Path] | None = None,
) -> dict[str, Any]:
    source = _resolve_csv(path, resolver)
    inspection = inspect_csv(source)
    frame = _read_csv(source, inspection)
    audit_session = EphemeralSession()
    audit_session.set_current_dataset(str(source), _inspection_wrapper(source, inspection))
    statistics = dataset_stats_engine.describe(audit_session)
    report = upload_quality_analyzer.analyze(frame, inspection)
    time_detection = inspection.get("time_detection") or {}
    critical = _critical_quality_errors(report)
    if not time_detection.get("primary_time_column") and not time_detection.get("primary_time_columns"):
        critical = [
            *critical,
            {
                "type": "missing_time_field",
                "severity": "critical",
                "message": "No reliable time field was detected.",
            },
        ]
    if session is not None:
        session.set_agent_state("latest_quality_report", report)
        session.set_agent_state("latest_quality_dataset", str(source))
    return {
        "status": "failed" if critical else "ok",
        "path": str(source),
        "input_fingerprint": _fingerprint(source),
        "inspection": inspection,
        "statistics": statistics,
        "quality_report": report,
        "critical_issues": critical,
        "warnings": inspection.get("warnings", []),
    }


def ingest_align_solar_data(
    paths: list[str | Path],
    *,
    session: ChatSession,
    use_llm_semantics: bool = True,
    split_proposal: dict[str, Any] | None = None,
    resolver: Callable[[str | Path], Path] | None = None,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("At least one CSV path is required")
    loaded: list[dict[str, Any]] = []
    for raw_path in paths:
        source = _resolve_csv(raw_path, resolver)
        request = PiAgentRequest(task="load_dataset", upload_path=str(source))
        request.use_llm_semantics = use_llm_semantics
        result = load_dataset_for_chat(request, session)
        loaded.append(result)
        if result.get("requires_split_confirmation"):
            proposal = split_proposal or result.get("split_proposal")
            if split_proposal is None:
                return {
                    "status": "confirmation_required",
                    "stage": "split",
                    "loaded": loaded,
                    "split_proposal": proposal,
                    "warnings": ["Confirm the proposed single-column split before continuing."],
                }
            split_result = upload_column_splitter.apply_split(
                session,
                proposal,
                run_quality=False,
                run_features=False,
            )
            loaded[-1]["split_result"] = split_result

    alignment = None
    if len(paths) > 1:
        alignment = upload_alignment_engine.run(session)
    return {
        "status": "ok",
        "stage": "ingest",
        "loaded": loaded,
        "alignment": alignment,
        "current_dataset": session.get_current_dataset_path(),
        "artifacts": [
            item
            for item in [
                *(result.get("dataset") for result in loaded),
                alignment.get("aligned_path") if alignment else None,
                alignment.get("report_path") if alignment else None,
            ]
            if item
        ],
    }


def propose_solar_cleaning(
    path: str | Path,
    *,
    session: ChatSession | None = None,
    resolver: Callable[[str | Path], Path] | None = None,
) -> dict[str, Any]:
    source = _resolve_csv(path, resolver)
    inspection = inspect_csv(source)
    frame = _read_csv(source, inspection)
    overrides = session.get_cleaning_column_overrides() if session else {}
    coverage = session.get_cleaning_coverage_overrides() if session else {}
    semantics = data_cleaning_engine.infer_column_semantics(frame, overrides)
    report = data_cleaning_engine.generate_report(frame, semantics, coverage)
    return {"status": "ok", "path": str(source), "cleaning_report": report, "artifacts": []}


def apply_solar_cleaning(session: ChatSession) -> dict[str, Any]:
    original_summary = session.get_inspection_summary()
    result = data_cleaning_engine.run(session, apply=True)
    _restore_lineage_dataset(session, result.get("cleaned_file_path"), original_summary)
    artifacts = [
        item
        for item in [
            result.get("quality_report_path"),
            result.get("text_path"),
            result.get("cleaned_file_path"),
        ]
        if item
    ]
    return {"status": "ok", "cleaning_report": result, "artifacts": artifacts}


def engineer_solar_features(session: ChatSession) -> dict[str, Any]:
    if not session.get_current_dataset_path():
        raise ValueError("Ingest a dataset before feature engineering")
    if not session.get_agent_state("latest_quality_report"):
        current = session.get_current_dataset_path()
        audit = audit_solar_data(current, session=session)
        if audit["status"] != "ok":
            return {"status": "failed", "stage": "audit", "audit": audit, "artifacts": []}
    original_summary = session.get_inspection_summary()
    result = feature_engineering_engine.run(session)
    _restore_lineage_dataset(session, result.get("engineered_file_path"), original_summary)
    artifacts = [
        item for item in [result.get("engineered_file_path"), result.get("registry_path")] if item
    ]
    status = "failed" if any(
        issue.get("severity") == "critical" for issue in result.get("validation_issues", [])
    ) else "ok"
    return {"status": status, "feature_result": result, "artifacts": artifacts}


def prepare_experiment_handoff(session: ChatSession, *, include_strategy: bool = True) -> dict[str, Any]:
    result = experiment_handoff_engine.run(session)
    strategy = llm_strategy_recommender.run(session) if include_strategy else None
    artifacts = [item for item in [result.get("handoff_path")] if item]
    if strategy:
        artifacts.extend(item for item in strategy.get("paths", {}).values() if item)
    return {
        "status": "ok",
        "handoff": result,
        "strategy": strategy,
        "artifacts": artifacts,
    }


def rebuild_solar_data_pipeline(*, run_tests: bool = True) -> dict[str, Any]:
    workflow = run_full_workflow()
    tests = run_contract_tests() if run_tests else None
    status = "ok" if workflow.get("status") == "ok" and (not tests or tests.get("status") == "ok") else "failed"
    return {
        "status": status,
        "workflow": workflow,
        "tests": tests,
        "artifacts": [item for item in REQUIRED_OUTPUTS if (ROOT / item).exists()],
    }


def plan_solar_feature_workflow(
    paths: list[str | Path],
    *,
    run_id: str | None = None,
    use_llm_semantics: bool = True,
    include_strategy: bool = True,
    split_proposal: dict[str, Any] | None = None,
    resolver: Callable[[str | Path], Path] | None = None,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("At least one CSV path is required")
    resolved = [_resolve_csv(path, resolver) for path in paths]
    input_fingerprints = {str(path): _fingerprint(path) for path in resolved}
    configuration = {
        "use_llm_semantics": use_llm_semantics,
        "include_strategy": include_strategy,
        "split_proposal": split_proposal,
    }
    config_hash = _config_fingerprint(configuration)
    audits: list[dict[str, Any]] = []
    cleaning_proposals: list[dict[str, Any]] = []
    pending_splits: list[dict[str, Any]] = []
    for source in resolved:
        inspection = inspect_csv(source)
        frame = _read_csv(source, inspection)
        candidate = upload_column_splitter.detect_multifield_single_column(frame)
        if candidate and split_proposal is None:
            pending_splits.append({"path": str(source), "proposal": candidate})
        audit = audit_solar_data(source, resolver=resolver)
        audits.append(audit)
        cleaning_proposals.append(propose_solar_cleaning(source, resolver=resolver))
    critical = [issue for audit in audits for issue in audit.get("critical_issues", [])]
    status = "failed" if critical else "confirmation_required" if pending_splits else "planned"
    actual_run_id = run_id or f"run_{uuid.uuid4().hex[:16]}"
    planned_writes = [
        f"data/processed/skill_runs/{actual_run_id}/run_manifest.json",
        "data/uploads/<dataset-id>/",
        "data/processed/uploads/<dataset-id>/",
    ]
    return {
        "status": status,
        "stage": "preflight",
        "run_id": actual_run_id,
        "input_fingerprints": input_fingerprints,
        "config_fingerprint": config_hash,
        "preflight_token": _preflight_token(input_fingerprints, config_hash),
        "audits": audits,
        "cleaning_proposals": cleaning_proposals,
        "pending_splits": pending_splits,
        "critical_issues": critical,
        "planned_writes": planned_writes,
        "artifacts": [],
        "warnings": ["Confirm every pending split before requesting write approval."] if pending_splits else [],
    }


def run_solar_feature_workflow(
    paths: list[str | Path],
    *,
    session: ChatSession,
    run_id: str | None = None,
    resume: bool = False,
    use_llm_semantics: bool = True,
    include_strategy: bool = True,
    split_proposal: dict[str, Any] | None = None,
    preflight_token: str | None = None,
    resolver: Callable[[str | Path], Path] | None = None,
) -> dict[str, Any]:
    resolved = [_resolve_csv(path, resolver) for path in paths]
    run_id = run_id or f"run_{uuid.uuid4().hex[:16]}"
    run_dir = RUNS_DIR / run_id
    manifest_path = run_dir / "run_manifest.json"
    input_fingerprints = {str(path): _fingerprint(path) for path in resolved}
    configuration = {
        "use_llm_semantics": use_llm_semantics,
        "include_strategy": include_strategy,
        "split_proposal": split_proposal,
    }
    config_hash = _config_fingerprint(configuration)
    expected_preflight_token = _preflight_token(input_fingerprints, config_hash)
    if preflight_token is not None and preflight_token != expected_preflight_token:
        raise ValueError("Preflight token does not match the current inputs and configuration")
    manifest: dict[str, Any]
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not resume:
            if manifest.get("status") == "completed" and manifest.get("input_fingerprints") == input_fingerprints and manifest.get("config_fingerprint") == config_hash:
                return {**manifest, "manifest_path": _rel(manifest_path), "idempotent": True}
            raise ValueError(f"Run {run_id} already exists; pass resume=true to continue it")
        if manifest.get("input_fingerprints") != input_fingerprints:
            raise ValueError("Cannot resume because an input fingerprint changed")
        if manifest.get("config_fingerprint") != config_hash:
            raise ValueError("Cannot resume because the workflow configuration changed")
    else:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "skill": "run-solar-feature-workflow",
            "run_id": run_id,
            "status": "running",
            "started_utc": utc_now(),
            "input_fingerprints": input_fingerprints,
            "config_fingerprint": config_hash,
            "configuration": configuration,
            "stages_completed": [],
            "artifacts": [],
            "warnings": [],
            "error": None,
        }
        _atomic_json(manifest_path, manifest)

    def complete_stage(name: str, result: dict[str, Any]) -> None:
        if name not in manifest["stages_completed"]:
            manifest["stages_completed"].append(name)
        manifest["last_stage"] = name
        manifest.setdefault("stage_results", {})[name] = result
        manifest["artifacts"] = list(dict.fromkeys([*manifest.get("artifacts", []), *result.get("artifacts", [])]))
        _atomic_json(manifest_path, manifest)

    try:
        if "ingest" not in manifest["stages_completed"]:
            ingestion = ingest_align_solar_data(
                resolved,
                session=session,
                use_llm_semantics=use_llm_semantics,
                split_proposal=split_proposal,
                resolver=resolver,
            )
            if ingestion.get("status") == "confirmation_required":
                manifest["status"] = "confirmation_required"
                manifest["pending"] = ingestion
                _atomic_json(manifest_path, manifest)
                return {**manifest, "manifest_path": _rel(manifest_path)}
            complete_stage("ingest", ingestion)

        current = session.get_current_dataset_path()
        if not current:
            raise ValueError("Ingestion did not establish a current dataset")
        audit = audit_solar_data(current, session=session)
        if audit["status"] != "ok":
            manifest["status"] = "failed"
            manifest["last_stage"] = "audit"
            manifest["error"] = {"type": "CriticalQualityError", "message": "Critical quality issues block downstream stages"}
            manifest["stage_results"] = {**manifest.get("stage_results", {}), "audit": audit}
            _atomic_json(manifest_path, manifest)
            return {**manifest, "manifest_path": _rel(manifest_path)}
        complete_stage("audit", {**audit, "artifacts": []})

        if "clean" not in manifest["stages_completed"]:
            proposal = propose_solar_cleaning(current, session=session)
            applied = apply_solar_cleaning(session)
            complete_stage("clean", {"status": "ok", "proposal": proposal, **applied})
            current = session.get_current_dataset_path() or current
            cleaned_audit = audit_solar_data(current, session=session)
            if cleaned_audit["status"] != "ok":
                raise ValueError("Cleaned dataset failed the quality gate")

        if "features" not in manifest["stages_completed"]:
            features = engineer_solar_features(session)
            if features.get("status") != "ok":
                raise ValueError("Feature validation failed")
            complete_stage("features", features)

        if "handoff" not in manifest["stages_completed"]:
            handoff = prepare_experiment_handoff(session, include_strategy=include_strategy)
            complete_stage("handoff", handoff)

        missing = [artifact for artifact in manifest.get("artifacts", []) if not (ROOT / artifact).exists() and not Path(artifact).is_absolute()]
        if missing:
            raise FileNotFoundError(f"Workflow artifacts are missing: {missing}")
        manifest["status"] = "completed"
        manifest["last_stage"] = "verify"
        manifest["finished_utc"] = utc_now()
        manifest["error"] = None
        _atomic_json(manifest_path, manifest)
        return {**manifest, "manifest_path": _rel(manifest_path)}
    except Exception as exc:
        manifest["status"] = "partial" if manifest.get("stages_completed") else "failed"
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
        manifest["finished_utc"] = utc_now()
        _atomic_json(manifest_path, manifest)
        return {**manifest, "manifest_path": _rel(manifest_path)}

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from chat_session import ChatSession
from data_cleaning_engine import run as run_cleaning
from llm_upload_semantic_recognizer import run as run_llm_recognition
from upload_cycle_features import run as run_cycle_features
from upload_data_quality_report import run as run_quality_report
from upload_drift_report import run as run_drift_report
from upload_feature_registry import run as run_feature_registry
from upload_inspector import normalize_time_to_month


ROOT = Path(__file__).resolve().parents[1]


def _load_current_df(session: ChatSession) -> tuple[pd.DataFrame, Path]:
    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("没有当前数据集，请先使用 /load <csv_path> 加载")
    full_path = Path(path) if Path(path).is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"当前数据集不存在: {full_path}")
    df = pd.read_csv(full_path)
    df.columns = [str(c).strip() for c in df.columns]
    return df, full_path


def _save_clean_monthly_timeseries(df: pd.DataFrame, out_dir: Path) -> Path:
    path = out_dir / "upload_clean_monthly_timeseries.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def _wrap_as_inspection(path: Path, df: pd.DataFrame, warnings: list[str]) -> dict[str, Any]:
    return {
        "source_file": {
            "name": path.name,
            "stored_path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "absolute_path": str(path),
        },
        "inspection": {
            "format": "csv",
            "rows_read": int(len(df)),
            "column_count": int(len(df.columns)),
            "columns": [{"name": c} for c in df.columns],
            "time_detection": {
                "primary_time_column": "date_month",
                "primary_time_columns": ["date_month"],
                "warnings": warnings,
            },
        },
    }


def run(session: ChatSession, use_llm: bool = True) -> dict[str, Any]:
    """Run the full standard feature pipeline on the current uploaded dataset."""
    df, original_path = _load_current_df(session)
    inspection = session.get_inspection_summary()

    # 1. LLM semantic recognition (use cached if available)
    stored_path = str(original_path.relative_to(ROOT)).replace("\\", "/")
    llm_recognition = session.get_llm_recognition(stored_path)
    if llm_recognition is None:
        llm_recognition = run_llm_recognition(df, use_llm=use_llm)
        session.set_llm_recognition(stored_path, llm_recognition)
    semantic_map = llm_recognition.get("semantic_map", {})

    # 2. Time normalization
    time_columns = (
        llm_recognition.get("time_columns")
        or inspection.get("primary_time_columns")
        or []
    )
    df = normalize_time_to_month(df, time_columns=time_columns)

    # 3. Conservative cleaning (apply safe actions)
    cleaning_report = run_cleaning(session, apply=True)
    cleaned_file_path = cleaning_report.get("cleaned_file_path")
    if cleaned_file_path:
        cleaned_full = ROOT / cleaned_file_path
        df = pd.read_csv(cleaned_full)
        df.columns = [str(c).strip() for c in df.columns]
        # Ensure date_month is normalized in the cleaned file too.
        if "date_month" not in df.columns:
            df = normalize_time_to_month(df, time_columns=time_columns)
        df["date_month"] = pd.to_datetime(df["date_month"], errors="coerce")
        # Remove intermediate cleaning artifacts; the final monthly table is the canonical output.
        try:
            cleaned_full.unlink()
        except OSError:
            pass
        for p in (cleaned_full.parent / "quality_report.json", cleaned_full.parent / "quality_report.txt"):
            try:
                p.unlink()
            except OSError:
                pass

    # 4. Output directory
    dataset_id = session.get_dataset_id() or session.session_id or "chat_unknown"
    out_dir = ROOT / "data" / "processed" / "uploads" / dataset_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 5. Clean monthly timeseries
    clean_path = _save_clean_monthly_timeseries(df, out_dir)

    # 6. Cycle features
    cycle_features_path = out_dir / "upload_cycle_features.csv"
    cycle_report = run_cycle_features(df, semantic_map, output_path=cycle_features_path)
    cycle_features = cycle_report.get("cycle_features")
    if cycle_features is None:
        cycle_features = pd.read_csv(cycle_features_path)

    # 7. Drift report
    drift_report_path = out_dir / "upload_drift_report.json"
    drift_report = run_drift_report(df, cycle_features, semantic_map, output_path=drift_report_path)

    # 8. Unified quality report
    quality_report_path = out_dir / "upload_data_quality_report.json"
    quality_report = run_quality_report(df, inspection, llm_recognition, output_path=quality_report_path)

    # 9. Feature registry (built from cycle features, the experiment-level table)
    registry_path = out_dir / "upload_feature_registry.json"
    verification = quality_report.get("physical_meaning_verification")
    cycle_semantic_map: dict[str, str] = {}
    for col in cycle_features.columns:
        if col.startswith("next_cycle"):
            cycle_semantic_map[col] = "cycle_label"
        elif col in semantic_map:
            cycle_semantic_map[col] = semantic_map[col]
        else:
            cycle_semantic_map[col] = "unknown"
    registry = run_feature_registry(
        cycle_features,
        cycle_semantic_map,
        llm_recognition,
        output_path=registry_path,
        verification=verification,
    )

    # 10. Update current dataset to the standard clean monthly timeseries
    warnings = drift_report.get("confidence_recommendations", [])
    wrapped = _wrap_as_inspection(clean_path, df, warnings)
    session.set_aligned_dataset(str(clean_path.relative_to(ROOT)).replace("\\", "/"), wrapped)

    paths = {
        "upload_clean_monthly_timeseries": str(clean_path.relative_to(ROOT)).replace("\\", "/"),
        "upload_cycle_features": str(cycle_features_path.relative_to(ROOT)).replace("\\", "/"),
        "upload_drift_report": str(drift_report_path.relative_to(ROOT)).replace("\\", "/"),
        "upload_data_quality_report": str(quality_report_path.relative_to(ROOT)).replace("\\", "/"),
        "upload_data_quality_report_text": str(quality_report_path.with_suffix(".txt").relative_to(ROOT)).replace("\\", "/"),
        "upload_feature_registry": str(registry_path.relative_to(ROOT)).replace("\\", "/"),
    }

    return {
        "status": "ok",
        "task": "prepare_features_for_upload",
        "llm_status": llm_recognition.get("status"),
        "llm_used": llm_recognition.get("llm_used", False),
        "llm_error": llm_recognition.get("llm_error"),
        "paths": paths,
        "warnings": warnings,
    }

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from feature_physical_meaning import lookup_physical_meaning
from llm_upload_semantic_recognizer import (
    build_narrative,
    check_wording_risk,
    explain_evidence_tiers,
    verify_physical_meaning,
)
from upload_quality_analyzer import analyze as analyze_quality
import cycle_context_summary
import data_quality_report_text


ROOT = Path(__file__).resolve().parents[1]


def _field_records(df: pd.DataFrame, semantic_map: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for col in df.columns:
        meaning = lookup_physical_meaning(col)
        records.append(
            {
                "field": col,
                "semantic": semantic_map.get(col, "unknown"),
                "physical_meaning": meaning.get("physical_meaning"),
                "mechanism_link": meaning.get("mechanism_link", []),
            }
        )
    return records


def build_upload_data_quality_report(
    df: pd.DataFrame,
    inspection: dict[str, Any] | None,
    llm_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a unified quality report for uploaded data, combining rule-based checks and LLM insights."""
    semantic_map = llm_result.get("semantic_map", {})
    feature_recommendations = llm_result.get("feature_recommendations", [])
    proxy_suggestions = llm_result.get("missing_data_proxy_suggestions", [])
    plausibility = llm_result.get("physical_plausibility", {})

    general_report = analyze_quality(df, inspection)
    evidence_tiers = explain_evidence_tiers(semantic_map)
    quality_issues = general_report.get("issues", [])

    narrative = build_narrative(df, semantic_map, quality_issues, plausibility, proxy_suggestions)

    field_records = _field_records(df, semantic_map)
    verification = verify_physical_meaning(field_records)
    if llm_result.get("status") == "llm_unavailable":
        wording_check = {
            "has_risk": False,
            "risks": [],
            "safer_text": narrative,
            "note": "LLM unavailable; wording risk check skipped.",
        }
    else:
        wording_check = check_wording_risk(narrative)

    date_range = general_report.get("coverage", {}).get("time_range")
    if df.empty:
        date_range = None
    elif "date_month" in df.columns and not df["date_month"].empty:
        dates = pd.to_datetime(df["date_month"], errors="coerce")
        if dates.notna().any():
            date_range = {
                "start": dates.min().isoformat(),
                "end": dates.max().isoformat(),
            }

    # Cycle-context physical feature summary for ML-oriented downstream use.
    try:
        cycle_summary = cycle_context_summary.build_cycle_context_summary(
            df, inspection, semantic_map=semantic_map
        )
    except Exception as exc:
        cycle_summary = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "status": general_report.get("status"),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "llm_status": llm_result.get("status"),
        "llm_used": llm_result.get("llm_used", False),
        "llm_error": llm_result.get("llm_error"),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "date_range": date_range,
        "quality_score": general_report.get("quality_score"),
        "severity_counts": general_report.get("severity_counts"),
        "semantic_mapping": semantic_map,
        "evidence_tiers": evidence_tiers,
        "feature_recommendations": feature_recommendations,
        "missing_data_proxy_suggestions": proxy_suggestions,
        "quality_issues": quality_issues,
        "coverage": general_report.get("coverage"),
        "missing_per_column": general_report.get("missing_per_column"),
        "cleaning_report": general_report.get("cleaning"),
        "physical_plausibility": plausibility,
        "physical_meaning_verification": verification,
        "wording_risk_check": wording_check,
        "narrative": narrative,
        "cycle_context_summary": cycle_summary,
    }


def run(
    df: pd.DataFrame,
    inspection: dict[str, Any] | None,
    llm_result: dict[str, Any],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and save the unified upload data quality report."""
    report = build_upload_data_quality_report(df, inspection, llm_result)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["path"] = str(output_path.relative_to(ROOT)).replace("\\", "/")
        text_path = output_path.with_suffix(".txt")
        text_path.write_text(
            data_quality_report_text.render_data_quality_report_text(report), encoding="utf-8"
        )
        report["text_path"] = str(text_path.relative_to(ROOT)).replace("\\", "/")
    return report

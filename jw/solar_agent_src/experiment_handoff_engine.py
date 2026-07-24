from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from data_quality_constants import SOLAR_COVERAGE


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _forbidden_inputs_from_registry(registry: dict[str, Any]) -> list[str]:
    forbidden = []
    for item in registry.get("fields", []):
        if (
            item.get("leakage_risk") == "forbidden_as_input"
            or item.get("allowed_as_model_input") is False
        ):
            forbidden.append(item["field"])
    return sorted(set(forbidden))


def _risk_flags_from_quality_report(quality_report: dict[str, Any]) -> list[str]:
    flags: set[str] = set()
    cleaning = quality_report.get("cleaning", {})
    for finding in cleaning.get("findings", []):
        ftype = finding.get("type", "")
        if ftype == "before_f107_coverage":
            flags.add("before_f107_coverage")
        elif ftype == "before_polar_coverage":
            flags.add("before_polar_coverage")
        elif ftype == "outside_goes_xrs_coverage":
            flags.add("outside_goes_xrs_coverage")
        elif ftype == "label_leakage_risk":
            flags.add("label_leakage_risk")
        elif ftype == "hemisphere_external_calibrated_period":
            flags.add("hemisphere_external_calibrated_observation")
    return sorted(flags)


def _recommended_splits(df: pd.DataFrame, date_col: str | None) -> list[dict[str, Any]]:
    """Recommend experiment splits based on instrument coverage."""
    splits = []
    if not date_col or date_col not in df.columns:
        return splits
    dates = pd.to_datetime(df[date_col], errors="coerce")
    if dates.isna().all():
        return splits

    date_min = dates.min()
    date_max = dates.max()

    def _add_split(
        id: str,
        start: str | None,
        end: str | None,
        description: str,
        sources: list[str],
    ) -> None:
        start_date = pd.to_datetime(start) if start else date_min
        end_date = pd.to_datetime(end) if end else date_max
        mask = dates.notna() & (dates >= start_date) & (dates <= end_date)
        splits.append(
            {
                "id": id,
                "description": description,
                "sources": sources,
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "rows": int(mask.sum()),
            }
        )

    _add_split(
        "sunspot_only",
        None,
        None,
        "Long-historical sunspot-only baseline using primary evidence.",
        ["sunspot"],
    )
    _add_split(
        "f107_era",
        SOLAR_COVERAGE["f107"]["start"],
        None,
        "Modern-era proxy comparison with F10.7.",
        ["sunspot", "f107"],
    )
    _add_split(
        "wso_era",
        SOLAR_COVERAGE["polar"]["start"],
        None,
        "WSO-era polar precursor and Hale-phase analysis.",
        ["sunspot", "f107", "polar", "hale"],
    )
    _add_split(
        "goes_xrs_legacy",
        SOLAR_COVERAGE["goes_xrs"]["start"],
        SOLAR_COVERAGE["goes_xrs"]["end"],
        "Legacy GOES XRS high-activity diagnostics (1975-2017).",
        ["sunspot", "f107", "polar", "goes_xrs"],
    )
    _add_split(
        "all_source_overlap",
        SOLAR_COVERAGE["polar"]["start"],
        SOLAR_COVERAGE["goes_xrs"]["end"],
        "All-source overlap window for homogeneous experiments.",
        ["sunspot", "f107", "polar", "goes_xrs"],
    )
    return splits


def _quality_summary(quality_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_score": quality_report.get("quality_score"),
        "critical_count": quality_report.get("severity_counts", {}).get("critical", 0),
        "warning_count": quality_report.get("severity_counts", {}).get("warning", 0),
        "info_count": quality_report.get("severity_counts", {}).get("info", 0),
        "coverage": quality_report.get("coverage", {}),
        "cleaning_findings": [
            {
                "type": f.get("type"),
                "severity": f.get("severity"),
                "message": f.get("message"),
            }
            for f in quality_report.get("cleaning", {}).get("findings", [])
        ],
    }


def build_handoff(
    df: pd.DataFrame,
    dataset_path: str,
    registry: dict[str, Any],
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    """Construct an experiment handoff for an uploaded/cleaned/engineered dataset."""
    date_col = None
    for candidate in ["date_month", "date", "datetime"]:
        if candidate in df.columns:
            date_col = candidate
            break

    forbidden_inputs = _forbidden_inputs_from_registry(registry)
    risk_flags = _risk_flags_from_quality_report(quality_report)
    recommended_splits = _recommended_splits(df, date_col)
    quality_summary = _quality_summary(quality_report)

    return {
        "agent": "data_feature_agent",
        "platform_target": "bailian_function_calling",
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_path": dataset_path,
        "primary_outputs": {
            "engineered_features": str(Path(dataset_path).as_posix()),
            "feature_registry": "feature_registry.json",
            "quality_report": "quality_report.json",
        },
        "handoff_to_experiment_agent": {
            "recommended_tables": [
                dataset_path,
                "feature_registry.json",
                "quality_report.json",
            ],
            "recommended_splits": recommended_splits,
            "forbidden_inputs": forbidden_inputs,
            "required_quality_files": [
                "quality_report.json",
                "feature_registry.json",
            ],
        },
        "risk_flags": risk_flags,
        "quality_summary": quality_summary,
    }


def run(session: Any) -> dict[str, Any]:
    """Generate experiment_handoff.json for the current dataset."""
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()

    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")
    full_path = Path(path) if Path(path).is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"Current dataset not found: {full_path}")

    df = pd.read_csv(full_path)
    df.columns = [str(c).strip() for c in df.columns]

    upload_dir = session.get_upload_registry_path()
    if not upload_dir:
        raise ValueError("Cannot determine upload directory for handoff.")
    report_dir = upload_dir.parent

    registry = _load_json(report_dir / "feature_registry.json") or {}
    quality_report = _load_json(report_dir / "quality_report.json") or {}

    handoff = build_handoff(df, path, registry, quality_report)

    handoff_path = report_dir / "experiment_handoff.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    handoff["handoff_path"] = str(handoff_path.relative_to(ROOT)).replace("\\", "/")

    return handoff

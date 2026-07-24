from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from feature_physical_meaning import lookup_physical_meaning


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "feature_registry.json"


TABLES = {
    "clean_monthly_timeseries": PROCESSED_DIR / "clean_monthly_timeseries.csv",
    "cycle_features": PROCESSED_DIR / "cycle_features.csv",
    "goes_xrs_monthly_features": PROCESSED_DIR / "goes_xrs_monthly_features.csv",
    "cycle_flare_features": PROCESSED_DIR / "cycle_flare_features.csv",
    "wso_polar_monthly_features": PROCESSED_DIR / "wso_polar_monthly_features.csv",
    "cycle_hale_wso_features": PROCESSED_DIR / "cycle_hale_wso_features.csv",
    "cycle_hale_wso_sensitivity": PROCESSED_DIR / "cycle_hale_wso_sensitivity.csv",
}

ID_FIELDS = {"date_month", "year", "month", "cycle_no", "event_id", "source_file", "source_year", "raw_line_no"}
LABEL_FIELDS = {"next_cycle_peak_sunspot", "next_cycle_strength_class"}
QUALITY_SUFFIXES = ("quality_flag", "coverage_status", "warning", "evidence_tier")
FILTER_FIELDS = {
    "has_flare_data",
    "is_complete",
    "is_peak_window_13m",
    "is_high_activity_phase",
    "flare_coverage_months",
    "position_valid_rate",
    "flare_position_valid_rate",
    "flare_class_valid_rate",
    "flare_parse_ok_rate",
    "flare_time_complete_rate",
}
EXPLANATION_FIELDS = {
    "cycle_phase",
    "cycle_phase_windowed",
    "hemisphere_source_type",
    "polar_north_sign",
    "polar_south_sign",
    "polar_dipole_state",
    "hale_phase_wso_monthly",
    "hale_phase_wso_at_cycle_start",
    "hale_phase_wso_at_cycle_minimum",
    "north_reversal_month",
    "south_reversal_month",
    "reversal_asymmetry_months",
}


def table_summary(path: Path) -> dict[str, Any]:
    df = pd.read_csv(path)
    summary: dict[str, Any] = {"rows": int(len(df)), "columns": list(df.columns)}
    if "date_month" in df.columns:
        dates = pd.to_datetime(df["date_month"], errors="coerce")
        summary["date_start"] = dates.min().strftime("%Y-%m-%d") if dates.notna().any() else None
        summary["date_end"] = dates.max().strftime("%Y-%m-%d") if dates.notna().any() else None
    if "cycle_no" in df.columns:
        cycles = pd.to_numeric(df["cycle_no"], errors="coerce")
        summary["cycle_start"] = int(cycles.min()) if cycles.notna().any() else None
        summary["cycle_end"] = int(cycles.max()) if cycles.notna().any() else None
    return summary


def evidence_tier(field: str) -> str:
    if field.startswith(("sunspot", "cycle_", "months_", "official_cycle", "peak_sunspot", "min_sunspot")):
        return "primary"
    if field.startswith(("f107", "polar", "hale", "north_reversal", "south_reversal", "reversal_")):
        return "auxiliary_mechanism_proxy"
    if field.startswith(("flare", "m_x", "xray", "active_region", "limb", "position", "hemisphere_unknown")):
        return "auxiliary_event_proxy"
    if field.startswith(("north_sunspot", "south_sunspot", "hemispheric", "hemisphere_")):
        return "auxiliary_spatial_observation"
    return "metadata"


def role_for(field: str) -> str:
    if field in LABEL_FIELDS:
        return "label"
    if field in ID_FIELDS:
        return "identifier"
    if field.endswith(QUALITY_SUFFIXES) or field in {"hemisphere_source_type"}:
        return "quality_field"
    if field in FILTER_FIELDS:
        return "filter_field"
    if field in EXPLANATION_FIELDS:
        return "explanation_field"
    if field.startswith("next_cycle_"):
        return "label"
    if field.endswith("_date") or field.endswith("_month") and field != "date_month":
        return "explanation_field"
    return "input_feature"


def leakage_risk(field: str, role: str) -> str:
    if role == "label" or field.startswith("next_cycle_"):
        return "forbidden_as_input"
    if field in {"cycle_no", "date_month", "year", "month"}:
        return "use_only_for_grouping_or_time_split"
    if field in {"months_to_cycle_peak", "cycle_phase", "cycle_phase_windowed", "is_peak_window_13m"}:
        return "high_if_predicting_before_peak"
    if field.endswith("_quality_flag") or field.endswith("_coverage_status"):
        return "low_use_for_filtering_not_signal"
    return "low"


def allowed_as_model_input(role: str, risk: str) -> bool:
    return role == "input_feature" and risk != "forbidden_as_input"


def field_note(field: str) -> str:
    notes = {
        "next_cycle_peak_sunspot": "Supervised target only. Never use as an input feature.",
        "next_cycle_strength_class": "Supervised class label only. Never use as an input feature.",
        "hemisphere_source_type": "Separates 1940-1991 RGO/NOAA external calibrated observation from 1992+ SILSO official hemispheric observation.",
        "sunspot_smoothed_13m": "Centered 13-month smoothed monthly sunspot number used for broad activity windows.",
        "peak_sunspot_number": "Compatibility field equal to monthly raw max; prefer explicit peak_sunspot_number_monthly_raw_max or official_cycle_max_sn.",
        "official_cycle_max_sn": "SILSO min/max table value based on 13-month smoothed monthly sunspot sequence.",
        "official_cycle_min_sn": "SILSO min/max table value based on 13-month smoothed monthly sunspot sequence.",
    }
    return notes.get(field, "")


def build_registry() -> dict[str, Any]:
    tables = {}
    fields = []
    for table_name, path in TABLES.items():
        if not path.exists():
            continue
        summary = table_summary(path)
        tables[table_name] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            **summary,
        }
        for field in summary["columns"]:
            role = role_for(field)
            risk = leakage_risk(field, role)
            meaning = lookup_physical_meaning(field)
            fields.append(
                {
                    "table": table_name,
                    "field": field,
                    "role": role,
                    "allowed_as_model_input": allowed_as_model_input(role, risk),
                    "leakage_risk": risk,
                    "evidence_tier": evidence_tier(field),
                    "note": field_note(field),
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Machine-readable field contract for experiment agents.",
        "rules": {
            "labels": sorted(LABEL_FIELDS),
            "hard_forbidden_inputs": sorted(LABEL_FIELDS),
            "quality_fields_should_filter_or_weight": True,
            "time_and_cycle_fields_require_temporal_split_care": True,
        },
        "tables": tables,
        "fields": fields,
    }


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    registry = build_registry()
    OUTPUT_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUTPUT_PATH}")
    print(f"fields={len(registry['fields'])} tables={len(registry['tables'])}")


if __name__ == "__main__":
    main()

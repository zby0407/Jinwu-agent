from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_cleaning_engine import infer_column_semantics
from feature_physical_meaning import lookup_physical_meaning
from upload_cycle_features import build_upload_cycle_features
from upload_inspector import normalize_time_to_month


ROOT = Path(__file__).resolve().parents[1]
CYCLE_FEATURES_PATH = ROOT / "data" / "processed" / "cycle_features.csv"
FEATURE_REGISTRY_PATH = ROOT / "data" / "processed" / "feature_registry.json"


SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "sunspot": ["sunspot", "sn", "spot_number"],
    "f107": ["f107"],
    "polar": ["polar"],
    "hemisphere": ["hemisphere", "north", "south"],
    "flare": ["flare", "goes", "xrs"],
    "hale": ["hale", "dipole"],
}

# Key fields to prioritize when showing global cycle features per cycle.
KEY_GLOBAL_FIELDS = [
    "cycle_length_months",
    "rise_time_months",
    "decline_time_months",
    "peak_sunspot_number",
    "mean_sunspot_number",
    "f107_mean",
    "f107_max",
    "north_sunspot_mean",
    "south_sunspot_mean",
    "hemispheric_asymmetry_mean",
    "polar_precursor_mean",
    "cycle_flare_count_total",
    "cycle_mx_flare_count",
    "next_cycle_peak_sunspot",
    "next_cycle_strength_class",
]


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _build_semantic_map(semantics: dict[str, list[str]]) -> dict[str, str]:
    semantic_map: dict[str, str] = {}
    for semantic, cols in semantics.items():
        for col in cols:
            if col not in semantic_map:
                semantic_map[col] = semantic
    return semantic_map


def _relevant_keywords_for_signals(signals: set[str]) -> set[str]:
    keywords: set[str] = set()
    for sig in signals:
        keywords.update(SIGNAL_KEYWORDS.get(sig, [sig]))
    return keywords


def _field_matches_signals(field_info: dict[str, Any], keywords: set[str]) -> bool:
    field_name = str(field_info.get("field", "")).lower()
    physical_meaning = str(field_info.get("physical_meaning", "")).lower()
    text = f"{field_name} {physical_meaning}"
    return any(kw in text for kw in keywords)


def _extract_features(row: pd.Series, exclude_cols: set[str]) -> dict[str, str]:
    features: dict[str, str] = {}
    for col in row.index:
        if col in exclude_cols:
            continue
        value = row.get(col)
        if pd.notna(value):
            features[str(col)] = _fmt(value)
    return features


def _limit_features(features: dict[str, str], limit: int = 20) -> dict[str, str]:
    ordered: dict[str, str] = {}
    for key in KEY_GLOBAL_FIELDS:
        if key in features:
            ordered[key] = features[key]
    for key, value in features.items():
        if key not in ordered:
            ordered[key] = value
    return dict(list(ordered.items())[:limit])


def build_cycle_context_summary(
    df: pd.DataFrame,
    inspection: dict[str, Any] | None,
    semantic_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a standard, ML-oriented cycle-context summary for an uploaded dataset.

    Combines:
    - Upload-derived cycle features (from the uploaded time range and columns).
    - Global canonical cycle features (from the fixed-raw-dataset pipeline).
    - ML-ready input features and labels from the global feature registry.
    - Physical meanings for columns that are recognized by the local seed.
    """
    # Normalize the uploaded time column to month-start for cycle assignment.
    try:
        time_columns = inspection.get("primary_time_columns") if inspection else None
        df_norm = normalize_time_to_month(df.copy(), time_columns=time_columns)
    except Exception:
        df_norm = df.copy()

    # Infer solar-physics semantics for each column (use supplied map if available).
    if semantic_map is None:
        semantics = infer_column_semantics(df_norm)
        semantic_map = _build_semantic_map(semantics)

    # Compute cycle-level features from the uploaded data itself.
    upload_cycle_features = build_upload_cycle_features(df_norm, semantic_map)

    # Load canonical global cycle features.
    global_cycles: pd.DataFrame | None = None
    if CYCLE_FEATURES_PATH.exists():
        global_cycles = pd.read_csv(CYCLE_FEATURES_PATH)

    # Build per-cycle summaries for cycles that overlap the uploaded data.
    overlapping_cycles: list[dict[str, Any]] = []
    if not upload_cycle_features.empty:
        exclude_cols = {
            "cycle_no",
            "start_date",
            "peak_date",
            "end_date",
            "is_complete",
        }
        for _, row in upload_cycle_features.iterrows():
            cycle_no = int(row["cycle_no"])
            global_row: pd.Series | None = None
            if global_cycles is not None:
                matches = global_cycles[global_cycles["cycle_no"].eq(cycle_no)]
                if not matches.empty:
                    global_row = matches.iloc[0]

            upload_features = _extract_features(row, exclude_cols)
            global_features: dict[str, str] = {}
            if global_row is not None:
                global_features = _extract_features(global_row, exclude_cols)
            global_features_limited = _limit_features(global_features)

            overlapping_cycles.append(
                {
                    "cycle_no": cycle_no,
                    "start_date": _fmt(row.get("start_date")),
                    "peak_date": _fmt(row.get("peak_date")),
                    "end_date": _fmt(row.get("end_date")),
                    "is_complete": bool(row.get("is_complete")),
                    "upload_features": upload_features,
                    "global_features": global_features_limited,
                    "global_feature_count": len(global_features),
                }
            )

    # Load global feature registry.
    registry_fields: list[dict[str, Any]] = []
    if FEATURE_REGISTRY_PATH.exists():
        registry = json.loads(FEATURE_REGISTRY_PATH.read_text(encoding="utf-8"))
        registry_fields = registry.get("fields", [])

    # Filter ML-ready input features that are relevant to the uploaded signals.
    present_signals = set(semantic_map.values())
    keywords = _relevant_keywords_for_signals(present_signals)
    ml_ready_features: list[dict[str, Any]] = []
    label_fields: list[dict[str, Any]] = []
    for field in registry_fields:
        role = field.get("role")
        if role == "input_feature" and field.get("allowed_as_model_input"):
            if _field_matches_signals(field, keywords):
                ml_ready_features.append(
                    {
                        "field": field["field"],
                        "physical_meaning": field.get("physical_meaning"),
                        "evidence_tier": field.get("evidence_tier"),
                        "mechanism_link": field.get("mechanism_link", []),
                        "source_table": field.get("table"),
                    }
                )
        elif role == "label":
            label_fields.append(
                {
                    "field": field["field"],
                    "physical_meaning": field.get("physical_meaning"),
                    "note": "只能作为预测目标，不能作为模型输入",
                }
            )

    # Physical meanings for upload columns that are recognized by the local seed.
    upload_column_meanings: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        meaning = lookup_physical_meaning(col)
        if meaning and meaning.get("physical_meaning") not in (None, "未验证字段"):
            upload_column_meanings[col] = meaning

    # Upload time range from the normalized DataFrame.
    date_range: dict[str, str] | None = None
    if "date_month" in df_norm.columns:
        dates = pd.to_datetime(df_norm["date_month"], errors="coerce")
        if dates.notna().any():
            date_range = {
                "start": dates.min().strftime("%Y-%m-%d"),
                "end": dates.max().strftime("%Y-%m-%d"),
            }

    return {
        "upload_time_range": date_range,
        "present_signals": sorted(present_signals),
        "overlapping_cycles": overlapping_cycles,
        "ml_ready_features": ml_ready_features,
        "label_fields": label_fields,
        "upload_column_meanings": upload_column_meanings,
    }

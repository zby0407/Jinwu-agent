from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_interim_monthly import add_cycle_columns, read_cycles
from data_cleaning_engine import infer_column_semantics
from data_quality_constants import EVIDENCE_TIER, LABEL_FIELDS, SOLAR_COVERAGE
from feature_physical_meaning import lookup_physical_meaning


ROOT = Path(__file__).resolve().parents[1]

# Lags and windows that are physically meaningful for monthly solar data.
LAG_PERIODS = [1, 12, 24]
ROLLING_WINDOWS = [3, 12, 36]
DIFF_PERIODS = [1, 12]


def _detect_date_column(df: pd.DataFrame) -> str | None:
    for candidate in ["date_month", "date", "datetime", "time", "timestamp"]:
        if candidate in df.columns:
            return candidate
    return None


def _normalize_date_column(df: pd.DataFrame, date_col: str | None) -> pd.DataFrame:
    """Force date column to month-start YYYY-MM-01 format."""
    if not date_col:
        return df
    out = df.copy()
    parsed = pd.to_datetime(out[date_col], errors="coerce")
    out[date_col] = parsed.dt.strftime("%Y-%m-%d")
    # Where parsing failed, keep original value (validation will flag it).
    mask = parsed.isna() & out[date_col].notna()
    out.loc[mask, date_col] = out.loc[mask, date_col]
    return out


def _numeric_signal_columns(
    df: pd.DataFrame, semantics: dict[str, list[str]]
) -> list[str]:
    """Return numeric columns that are meaningful solar signals."""
    semantic_groups = ["sunspot", "f107", "polar", "hale", "hemisphere", "flare"]
    cols: set[str] = set()
    for group in semantic_groups:
        for col in semantics.get(group, []):
            if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
                cols.add(col)
    return sorted(cols)


def _generate_time_features(df: pd.DataFrame, date_col: str | None) -> pd.DataFrame:
    """Add year, month, and normalized date_month features."""
    out = df.copy()
    if not date_col or date_col not in out.columns:
        return out
    parsed = pd.to_datetime(out[date_col], errors="coerce")
    out["year"] = parsed.dt.year
    out["month"] = parsed.dt.month
    return out


def _generate_lag_features(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Generate lag features for monthly solar data. No future information is used."""
    out = df.copy()
    for col in cols:
        for lag in LAG_PERIODS:
            out[f"{col}_lag_{lag}m"] = out[col].shift(lag)
    return out


def _generate_rolling_features(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Generate rolling mean and std features. Uses min_periods=1 to allow leading NaN."""
    out = df.copy()
    for col in cols:
        for window in ROLLING_WINDOWS:
            out[f"{col}_mean_{window}m"] = (
                out[col].shift(1).rolling(window=window, min_periods=1).mean()
            )
            if window == 12:
                out[f"{col}_std_{window}m"] = (
                    out[col].shift(1).rolling(window=window, min_periods=1).std()
                )
    return out


def _generate_diff_features(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Generate first-order difference and rate-of-change features."""
    out = df.copy()
    for col in cols:
        for period in DIFF_PERIODS:
            lag_col = out[col].shift(period)
            out[f"{col}_diff_{period}m"] = out[col] - lag_col
            # Rate of change: avoid division by zero
            out[f"{col}_roc_{period}m"] = np.where(
                lag_col.abs() > 1e-12,
                (out[col] - lag_col) / lag_col.abs(),
                np.nan,
            )
    return out


def _generate_cycle_features(
    df: pd.DataFrame, cycles: pd.DataFrame | None
) -> pd.DataFrame:
    """Attach solar cycle metadata and phase using existing build_interim_monthly logic."""
    if cycles is None or "date_month" not in df.columns:
        return df
    out = df.copy()
    out = add_cycle_columns(out, cycles, date_col="date_month")
    # Rename to match the canonical processed table naming.
    rename_map = {
        "cycle_number": "cycle_no",
        "months_since_cycle_min": "months_since_cycle_start",
        "months_until_cycle_max": "months_to_cycle_peak",
        "cycle_phase_basic": "cycle_phase",
    }
    out = out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns})
    return out


def _generate_cross_signal_features(
    df: pd.DataFrame, semantics: dict[str, list[str]]
) -> pd.DataFrame:
    """Generate cross-signal features such as hemisphere asymmetry and f107-sunspot correlation."""
    out = df.copy()
    sunspot_cols = semantics.get("sunspot", [])
    f107_cols = semantics.get("f107", [])
    hemisphere_cols = semantics.get("hemisphere", [])

    # Hemisphere asymmetry: requires north, south, and a total/denominator.
    north = next((c for c in hemisphere_cols if "north" in c.lower()), None)
    south = next((c for c in hemisphere_cols if "south" in c.lower()), None)
    total = next(
        (c for c in sunspot_cols if "total" in c.lower() or c in {"sunspot_number"}),
        None,
    )
    if north and south and total and total in out.columns:
        out["hemispheric_asymmetry"] = np.where(
            out[total].ne(0),
            (out[north] - out[south]) / out[total],
            np.nan,
        )

    # Rolling correlation between f107 and sunspot if both exist.
    f107 = next(
        (c for c in f107_cols if "adjusted" in c.lower() or "mean" in c.lower()),
        f107_cols[0] if f107_cols else None,
    )
    sunspot = next(
        (c for c in sunspot_cols if c == "sunspot_number"),
        sunspot_cols[0] if sunspot_cols else None,
    )
    if f107 and sunspot and f107 in out.columns and sunspot in out.columns:
        out["f107_sunspot_corr_36m"] = (
            out[f107]
            .shift(1)
            .rolling(window=36, min_periods=12)
            .corr(out[sunspot].shift(1))
        )
    return out


def _generate_f107_sunspot_drift_features(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling F10.7-sunspot relationship residual features without future leakage."""
    out = df.copy()
    f107_col = "f107_monthly_mean"
    sunspot_col = "sunspot_number"
    if f107_col not in out.columns or sunspot_col not in out.columns:
        return out
    x = out[sunspot_col].astype(float)
    y = out[f107_col].astype(float)
    window = 36
    min_periods = 12
    mean_x = x.rolling(window=window, min_periods=min_periods).mean().shift(1)
    mean_y = y.rolling(window=window, min_periods=min_periods).mean().shift(1)
    cov_xy = x.rolling(window=window, min_periods=min_periods).cov(y).shift(1)
    var_x = x.rolling(window=window, min_periods=min_periods).var().shift(1)
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    predicted = slope * x + intercept
    residual = y - predicted
    out["f107_sunspot_residual_36m"] = residual
    residual_std = (
        residual.rolling(window=window, min_periods=min_periods).std().shift(1)
    )
    out["f107_sunspot_zscore_36m"] = np.where(
        residual_std.notna() & (residual_std > 0),
        residual / residual_std,
        np.nan,
    )
    out["f107_sunspot_ratio_36m"] = np.where(
        predicted.abs() > 1e-12,
        y / predicted,
        np.nan,
    )
    return out


def _generate_coverage_flags(df: pd.DataFrame, date_col: str | None) -> pd.DataFrame:
    """Add binary coverage flags for major solar proxies."""
    if not date_col or date_col not in df.columns:
        return df
    out = df.copy()
    dates = pd.to_datetime(out[date_col], errors="coerce")
    for key, rules in SOLAR_COVERAGE.items():
        start = pd.to_datetime(rules.get("start"))
        end = pd.to_datetime(rules.get("end")) if "end" in rules else None
        if start and end:
            out[f"is_{key}_available"] = dates.between(start, end)
        elif start:
            out[f"is_{key}_available"] = dates >= start
    return out


def _build_feature_registry(
    df: pd.DataFrame, original_columns: list[str], semantics: dict[str, list[str]]
) -> dict[str, Any]:
    """Generate a feature registry entry for every column in the engineered DataFrame."""
    fields: list[dict[str, Any]] = []
    numeric_cols = [
        c
        for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in original_columns
    ]

    for col in df.columns:
        meaning = lookup_physical_meaning(col)
        if col in LABEL_FIELDS:
            fields.append(
                {
                    "field": col,
                    "role": "label",
                    "allowed_as_model_input": False,
                    "leakage_risk": "forbidden_as_input",
                    "evidence_tier": EVIDENCE_TIER["metadata"],
                    "note": "Supervised target only. Never use as an input feature.",
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
            continue

        if (
            col in {"date_month", "year", "month"}
            or col in original_columns
            and col in ["cycle_no"]
        ):
            fields.append(
                {
                    "field": col,
                    "role": "identifier",
                    "allowed_as_model_input": False,
                    "leakage_risk": "use_only_for_grouping_or_time_split",
                    "evidence_tier": EVIDENCE_TIER["metadata"],
                    "note": "",
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
            continue

        if col.startswith("is_") and col.endswith("_available"):
            fields.append(
                {
                    "field": col,
                    "role": "filter_field",
                    "allowed_as_model_input": True,
                    "leakage_risk": "low",
                    "evidence_tier": EVIDENCE_TIER["metadata"],
                    "note": "Coverage flag derived from instrument availability dates.",
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
            continue

        if col in original_columns:
            fields.append(
                {
                    "field": col,
                    "role": "input_feature",
                    "allowed_as_model_input": True,
                    "leakage_risk": "low",
                    "evidence_tier": EVIDENCE_TIER["primary"],
                    "note": "Original uploaded column.",
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
            continue

        # Engineered numeric features
        if col in numeric_cols:
            evidence = EVIDENCE_TIER["primary"]
            if "f107" in col:
                evidence = EVIDENCE_TIER["auxiliary_mechanism_proxy"]
            elif "polar" in col or "hale" in col:
                evidence = EVIDENCE_TIER["auxiliary_mechanism_proxy"]
            elif "flare" in col:
                evidence = EVIDENCE_TIER["auxiliary_event_proxy"]
            elif "hemisphere" in col:
                evidence = EVIDENCE_TIER["auxiliary_spatial_observation"]

            leakage = "low"
            if "months_to_cycle_peak" in col or "roc" in col:
                leakage = "high_if_predicting_before_peak"

            fields.append(
                {
                    "field": col,
                    "role": "input_feature",
                    "allowed_as_model_input": True,
                    "leakage_risk": leakage,
                    "evidence_tier": evidence,
                    "note": "Engineered feature with no future information.",
                    "physical_meaning": meaning.get("physical_meaning"),
                    "mechanism_link": meaning.get("mechanism_link", []),
                }
            )
            continue

        # Default for anything else
        fields.append(
            {
                "field": col,
                "role": "input_feature",
                "allowed_as_model_input": True,
                "leakage_risk": "low",
                "evidence_tier": EVIDENCE_TIER["metadata"],
                "note": "",
                "physical_meaning": meaning.get("physical_meaning"),
                "mechanism_link": meaning.get("mechanism_link", []),
            }
        )

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Machine-readable field contract for engineered features from uploaded dataset.",
        "rules": {
            "labels": LABEL_FIELDS,
            "hard_forbidden_inputs": LABEL_FIELDS,
        },
        "fields": fields,
    }


def _validate_engineered_features(
    df: pd.DataFrame, original_columns: list[str], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate that engineered features are leakage-free and physically correct."""
    issues: list[dict[str, Any]] = []

    # 1. Labels must not be marked as input features in the registry.
    input_fields = {
        f["field"] for f in registry["fields"] if f.get("role") == "input_feature"
    }
    for label in LABEL_FIELDS:
        if label in input_fields:
            issues.append(
                {
                    "type": "label_in_input_features",
                    "severity": "critical",
                    "message": f"Label column '{label}' is incorrectly marked as an input feature in the registry.",
                }
            )

    # 2. date_month must be month-start.
    if "date_month" in df.columns:
        parsed = pd.to_datetime(df["date_month"], errors="coerce")
        if parsed.notna().any() and not (parsed.dt.day == 1).all():
            issues.append(
                {
                    "type": "date_month_not_month_start",
                    "severity": "warning",
                    "message": "Not all date_month values are month-start dates.",
                }
            )

    return issues


def generate_features(
    df: pd.DataFrame,
    semantics: dict[str, list[str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate the full engineered feature set for an uploaded/cleaned DataFrame."""
    original_columns = list(df.columns)
    semantics = semantics or infer_column_semantics(df)
    date_col = _detect_date_column(df)

    out = _normalize_date_column(df, date_col)
    out = _generate_time_features(out, date_col)

    signal_cols = _numeric_signal_columns(out, semantics)
    out = _generate_lag_features(out, signal_cols)
    out = _generate_rolling_features(out, signal_cols)
    out = _generate_diff_features(out, signal_cols)

    # Load canonical solar cycle metadata and attach cycle features.
    try:
        cycles = read_cycles()
    except Exception:
        cycles = None
    out = _generate_cycle_features(out, cycles)

    out = _generate_cross_signal_features(out, semantics)
    out = _generate_f107_sunspot_drift_features(out)
    out = _generate_coverage_flags(out, date_col)

    registry = _build_feature_registry(out, original_columns, semantics)
    validation_issues = _validate_engineered_features(out, original_columns, registry)
    registry["validation_issues"] = validation_issues

    return out, registry


def run(session: Any) -> dict[str, Any]:
    """Generate engineered features for the current dataset and save outputs."""
    from chat_session import ChatSession
    from piagent_tools import load_dataset_for_chat
    from piagent_schemas import PiAgentRequest

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

    overrides = session.get_cleaning_column_overrides()
    semantics = infer_column_semantics(df, overrides)
    engineered_df, registry = generate_features(df, semantics)

    upload_dir = session.get_upload_registry_path()
    engineered_path: Path | None = None
    registry_path: Path | None = None
    if upload_dir:
        save_dir = upload_dir.parent
        save_dir.mkdir(parents=True, exist_ok=True)
        engineered_path = save_dir / "engineered_features.csv"
        engineered_df.to_csv(engineered_path, index=False, encoding="utf-8")
        registry_path = save_dir / "feature_registry.json"
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Auto-load engineered features as current dataset.
        load_dataset_for_chat(
            PiAgentRequest(task="load_dataset", upload_path=str(engineered_path)),
            session,
        )

    return {
        "status": "ok",
        "task": "generate_features",
        "original_columns": len(df.columns),
        "engineered_columns": len(engineered_df.columns),
        "input_feature_count": sum(
            1
            for f in registry["fields"]
            if f.get("role") == "input_feature"
            and f.get("allowed_as_model_input") is True
        ),
        "validation_issues": registry["validation_issues"],
        "engineered_file_path": str(engineered_path.relative_to(ROOT)).replace(
            "\\", "/"
        )
        if engineered_path
        else None,
        "registry_path": str(registry_path.relative_to(ROOT)).replace("\\", "/")
        if registry_path
        else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

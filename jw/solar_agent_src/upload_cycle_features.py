from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_interim_monthly import add_cycle_columns, read_cycles


ROOT = Path(__file__).resolve().parents[1]


def _linear_slope(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 2:
        return np.nan
    slope, _ = np.polyfit(x[valid].astype(float), y[valid].astype(float), 1)
    return float(slope)


def _corr(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        return np.nan
    if x[valid].nunique() < 2 or y[valid].nunique() < 2:
        return np.nan
    return float(x[valid].corr(y[valid]))


def _residual_std(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        return np.nan
    xv = x[valid].astype(float)
    yv = y[valid].astype(float)
    if xv.nunique() < 2:
        return np.nan
    slope, intercept = np.polyfit(xv, yv, 1)
    residuals = yv - (slope * xv + intercept)
    return float(residuals.std(ddof=1))


def _detect_semantic_columns(semantic_map: dict[str, str]) -> dict[str, str | None]:
    """Return the first column for each important semantic role."""
    return {
        "sunspot": next((c for c, s in semantic_map.items() if s == "sunspot"), None),
        "f107": next((c for c, s in semantic_map.items() if s == "f107"), None),
    }


def _month_diff(a: pd.Timestamp, b: pd.Timestamp) -> int:
    if pd.isna(a) or pd.isna(b):
        return 0
    return int((b.year - a.year) * 12 + (b.month - a.month))


def build_upload_cycle_features(
    df: pd.DataFrame,
    semantic_map: dict[str, str],
) -> pd.DataFrame:
    """Build a cycle-level feature table from uploaded monthly data.

    The function uses the official solar cycle metadata to assign cycle numbers,
    then computes per-cycle statistics for whatever semantic columns are present.
    """
    out = df.copy()
    out["date_month"] = pd.to_datetime(out["date_month"], errors="coerce")
    out = out.dropna(subset=["date_month"]).copy()
    if out.empty:
        return pd.DataFrame()

    cycles = read_cycles()
    out = add_cycle_columns(out, cycles)

    out = out[out["cycle_number"].notna()].copy()
    out["cycle_number"] = out["cycle_number"].astype(int)
    max_observed_month = out["date_month"].max()

    cols = _detect_semantic_columns(semantic_map)
    sunspot_col = cols["sunspot"]
    f107_col = cols["f107"]
    polar_cols = [c for c, s in semantic_map.items() if s == "polar"]
    hemisphere_cols = [c for c, s in semantic_map.items() if s == "hemisphere"]
    flare_cols = [c for c, s in semantic_map.items() if s == "flare"]

    # Restrict to columns that actually exist and are numeric. Non-numeric columns
    # (e.g., leftover label strings) cannot be used in physical calculations.
    def _usable(col: str | None) -> bool:
        return col is not None and col in out.columns and pd.api.types.is_numeric_dtype(out[col])

    sunspot_col = sunspot_col if _usable(sunspot_col) else None
    f107_col = f107_col if _usable(f107_col) else None
    polar_cols = [c for c in polar_cols if _usable(c)]
    hemisphere_cols = [c for c in hemisphere_cols if _usable(c)]
    flare_cols = [c for c in flare_cols if _usable(c)]

    rows: list[dict[str, Any]] = []
    for cycle_no, group in out.groupby("cycle_number", sort=True):
        group = group.sort_values("date_month").copy()
        start_date = group["date_month"].min()
        peak_date = pd.to_datetime(group["cycle_max_date_month"].iloc[0], errors="coerce")
        if pd.isna(peak_date) and sunspot_col and group[sunspot_col].notna().any():
            peak_date = group.loc[group[sunspot_col].idxmax(), "date_month"]

        next_cycle = out[out["cycle_number"].eq(cycle_no + 1)]
        if not next_cycle.empty:
            end_date = next_cycle["date_month"].min() - pd.DateOffset(months=1)
            is_complete = True
        else:
            end_date = group["date_month"].max()
            is_complete = bool(end_date < max_observed_month)

        cycle_length = _month_diff(start_date, end_date) + 1 if pd.notna(end_date) else np.nan
        rise_time = _month_diff(start_date, peak_date) if pd.notna(peak_date) else np.nan
        decline_time = _month_diff(peak_date, end_date) if pd.notna(peak_date) and pd.notna(end_date) else np.nan

        rising = group[group["date_month"].le(peak_date)] if pd.notna(peak_date) else group.iloc[0:0]
        declining = group[group["date_month"].ge(peak_date)] if pd.notna(peak_date) else group.iloc[0:0]

        row: dict[str, Any] = {
            "cycle_no": cycle_no,
            "start_date": start_date,
            "peak_date": peak_date,
            "end_date": end_date,
            "is_complete": is_complete,
            "cycle_length_months": cycle_length,
            "rise_time_months": rise_time,
            "decline_time_months": decline_time,
            "official_cycle_min_sn": group["cycle_min_sn"].dropna().iloc[0] if group["cycle_min_sn"].notna().any() else np.nan,
            "official_cycle_max_sn": group["cycle_max_sn"].dropna().iloc[0] if group["cycle_max_sn"].notna().any() else np.nan,
        }

        if sunspot_col:
            row["min_sunspot_number"] = group[sunspot_col].min(skipna=True)
            row["peak_sunspot_number"] = group[sunspot_col].max(skipna=True)
            row["mean_sunspot_number"] = group[sunspot_col].mean(skipna=True)
            row["integral_sunspot"] = group[sunspot_col].sum(skipna=True)
            if "months_since_cycle_min" in rising.columns and not rising.empty:
                row["rise_slope"] = _linear_slope(rising["months_since_cycle_min"], rising[sunspot_col])
            else:
                row["rise_slope"] = np.nan
            if "months_since_cycle_min" in declining.columns and not declining.empty:
                row["decline_slope"] = _linear_slope(declining["months_since_cycle_min"], declining[sunspot_col])
            else:
                row["decline_slope"] = np.nan

        if f107_col:
            row["f107_mean"] = group[f107_col].mean(skipna=True)
            row["f107_max"] = group[f107_col].max(skipna=True)
            if sunspot_col:
                row["f107_sunspot_corr"] = _corr(group[sunspot_col], group[f107_col])
                row["f107_sunspot_slope"] = _linear_slope(group[sunspot_col], group[f107_col])
                row["f107_sunspot_residual_std"] = _residual_std(group[sunspot_col], group[f107_col])

        for col in hemisphere_cols:
            row[f"{col}_mean"] = group[col].mean(skipna=True)
            row[f"{col}_max_abs"] = group[col].abs().max(skipna=True)

        for col in polar_cols:
            precursor_start = start_date - pd.DateOffset(months=36)
            precursor_end = start_date - pd.DateOffset(months=1)
            precursor = out[(out["date_month"] >= precursor_start) & (out["date_month"] <= precursor_end)]
            row[f"{col}_precursor_mean"] = precursor[col].mean(skipna=True)
            row[f"{col}_mean"] = group[col].mean(skipna=True)

        for col in flare_cols:
            row[f"{col}_cycle_sum"] = group[col].sum(skipna=True)
            row[f"{col}_cycle_max"] = group[col].max(skipna=True)

        rows.append(row)

    features = pd.DataFrame(rows).sort_values("cycle_no").reset_index(drop=True)

    if sunspot_col and "peak_sunspot_number" in features.columns:
        features["next_cycle_peak_sunspot"] = features["peak_sunspot_number"].shift(-1)

    date_cols = [c for c in ["start_date", "peak_date", "end_date"] if c in features.columns]
    for col in date_cols:
        features[col] = pd.to_datetime(features[col]).dt.strftime("%Y-%m-%d")

    int_cols = [c for c in ["cycle_no", "cycle_length_months", "rise_time_months", "decline_time_months"] if c in features.columns]
    for col in int_cols:
        features[col] = features[col].astype("Int64")

    return features


def run(
    df: pd.DataFrame,
    semantic_map: dict[str, str],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and save cycle features for uploaded data."""
    features = build_upload_cycle_features(df, semantic_map)
    if output_path is None:
        return {"status": "ok", "cycle_features": features, "cycle_rows": int(len(features))}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output_path, index=False, encoding="utf-8")
    report = {
        "status": "ok",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_rows": int(len(features)),
        "columns": list(features.columns),
        "path": str(output_path.relative_to(ROOT)).replace("\\", "/"),
    }
    return report

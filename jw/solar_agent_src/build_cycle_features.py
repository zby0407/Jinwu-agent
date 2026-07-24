from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
INPUT_PATH = PROCESSED_DIR / "clean_monthly_timeseries.csv"
OUTPUT_PATH = PROCESSED_DIR / "cycle_features.csv"
CYCLE_FLARE_PATH = PROCESSED_DIR / "cycle_flare_features.csv"


STRENGTH_BINS = [-np.inf, 100.0, 160.0, np.inf]
STRENGTH_LABELS = ["weak", "moderate", "strong"]


def month_diff(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int((end.year - start.year) * 12 + (end.month - start.month))


def linear_slope(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 2:
        return np.nan
    slope, _ = np.polyfit(x[valid].astype(float), y[valid].astype(float), 1)
    return float(slope)


def corr(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 3:
        return np.nan
    if x[valid].nunique() < 2 or y[valid].nunique() < 2:
        return np.nan
    return float(x[valid].corr(y[valid]))


def residual_std(x: pd.Series, y: pd.Series) -> float:
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


def strength_class(peak: pd.Series) -> pd.Series:
    return pd.cut(peak, bins=STRENGTH_BINS, labels=STRENGTH_LABELS).astype("object")


def main() -> None:
    df = pd.read_csv(INPUT_PATH, parse_dates=["date_month"])
    df = df[df["cycle_no"].notna()].copy()
    df["cycle_no"] = df["cycle_no"].astype(int)
    max_observed_month = df["date_month"].max()

    rows = []
    for cycle_no, group in df.groupby("cycle_no", sort=True):
        group = group.sort_values("date_month").copy()
        start_date = group["date_month"].min()
        peak_rows = group[group["cycle_phase"].eq("maximum")]
        peak_date = peak_rows["date_month"].iloc[0] if not peak_rows.empty else pd.NaT

        next_cycle = df[df["cycle_no"].eq(cycle_no + 1)]
        if not next_cycle.empty:
            end_date = next_cycle["date_month"].min() - pd.DateOffset(months=1)
            is_complete = True
        else:
            end_date = group["date_month"].max()
            is_complete = bool(end_date < max_observed_month)

        cycle_length_months = (
            month_diff(start_date, end_date) + 1 if pd.notna(end_date) else np.nan
        )
        rise_time_months = (
            month_diff(start_date, peak_date) if pd.notna(peak_date) else np.nan
        )
        decline_time_months = (
            month_diff(peak_date, end_date)
            if pd.notna(peak_date) and pd.notna(end_date)
            else np.nan
        )

        rising = (
            group[group["date_month"].le(peak_date)]
            if pd.notna(peak_date)
            else group.iloc[0:0]
        )
        declining = (
            group[group["date_month"].ge(peak_date)]
            if pd.notna(peak_date)
            else group.iloc[0:0]
        )

        precursor_start = start_date - pd.DateOffset(months=36)
        precursor_end = start_date - pd.DateOffset(months=1)
        precursor = df[
            (df["date_month"].ge(precursor_start))
            & (df["date_month"].le(precursor_end))
        ]

        f107_slope = linear_slope(group["sunspot_number"], group["f107_monthly_mean"])

        row = {
            "cycle_no": cycle_no,
            "start_date": start_date,
            "peak_date": peak_date,
            "end_date": end_date,
            "is_complete": is_complete,
            "cycle_length_months": cycle_length_months,
            "rise_time_months": rise_time_months,
            "decline_time_months": decline_time_months,
            "official_cycle_min_sn": group["official_cycle_min_sn"].dropna().iloc[0]
            if group["official_cycle_min_sn"].notna().any()
            else np.nan,
            "official_cycle_max_sn": group["official_cycle_max_sn"].dropna().iloc[0]
            if group["official_cycle_max_sn"].notna().any()
            else np.nan,
            "min_sunspot_number": group["sunspot_number"].min(skipna=True),
            "peak_sunspot_number_monthly_raw_max": group["sunspot_number"].max(
                skipna=True
            ),
            "peak_sunspot_number": group["sunspot_number"].max(skipna=True),
            "mean_sunspot_number": group["sunspot_number"].mean(skipna=True),
            "integral_sunspot": group["sunspot_number"].sum(skipna=True),
            "rise_slope": linear_slope(
                rising["months_from_cycle_start"], rising["sunspot_number"]
            ),
            "decline_slope": linear_slope(
                declining["months_from_cycle_start"], declining["sunspot_number"]
            ),
            "f107_mean": group["f107_monthly_mean"].mean(skipna=True),
            "f107_max": group["f107_monthly_mean"].max(skipna=True),
            "f107_sunspot_corr": corr(
                group["sunspot_number"], group["f107_monthly_mean"]
            ),
            "f107_sunspot_slope": f107_slope,
            "f107_sunspot_residual_std": residual_std(
                group["sunspot_number"], group["f107_monthly_mean"]
            ),
            "north_sunspot_mean": group["north_sunspot_number"].mean(skipna=True),
            "south_sunspot_mean": group["south_sunspot_number"].mean(skipna=True),
            "hemispheric_asymmetry_mean": group["hemispheric_asymmetry"].mean(
                skipna=True
            ),
            "hemispheric_asymmetry_max_abs": group["hemispheric_asymmetry"]
            .abs()
            .max(skipna=True),
            "polar_precursor_mean": precursor["polar_mean_signed"].mean(skipna=True),
            "polar_precursor_abs_mean": precursor["polar_mean_abs"].mean(skipna=True),
            "polar_north_mean": group["polar_north"].mean(skipna=True),
            "polar_south_mean": group["polar_south"].mean(skipna=True),
            "polar_asymmetry_mean": group["polar_asymmetry"].mean(skipna=True),
        }
        rows.append(row)

    features = pd.DataFrame(rows).sort_values("cycle_no").reset_index(drop=True)
    flare_cols = [
        "cycle_flare_count_total",
        "cycle_mx_flare_count",
        "cycle_x_flare_count",
        "cycle_flare_flux_sum_proxy",
        "cycle_flare_flux_max_proxy",
        "rise_phase_mx_flare_count",
        "max_phase_mx_flare_count",
        "decline_phase_mx_flare_count",
        "flare_peak_lag_to_sunspot_peak_months",
        "cycle_flare_asymmetry_mean",
        "flare_coverage_months",
        "flare_cycle_quality_flag",
    ]
    if CYCLE_FLARE_PATH.exists():
        flare_features = pd.read_csv(CYCLE_FLARE_PATH)
        features = features.merge(flare_features, on="cycle_no", how="left")
    else:
        for col in flare_cols:
            features[col] = np.nan
    features["next_cycle_peak_sunspot"] = features["peak_sunspot_number"].shift(-1)
    features["next_cycle_strength_class"] = strength_class(
        features["next_cycle_peak_sunspot"]
    )

    date_cols = ["start_date", "peak_date", "end_date"]
    for col in date_cols:
        features[col] = pd.to_datetime(features[col]).dt.strftime("%Y-%m-%d")

    integer_cols = [
        "cycle_no",
        "cycle_length_months",
        "rise_time_months",
        "decline_time_months",
    ]
    for col in integer_cols:
        features[col] = features[col].astype("Int64")

    ordered_cols = [
        "cycle_no",
        "start_date",
        "peak_date",
        "end_date",
        "is_complete",
        "cycle_length_months",
        "rise_time_months",
        "decline_time_months",
        "official_cycle_min_sn",
        "official_cycle_max_sn",
        "min_sunspot_number",
        "peak_sunspot_number_monthly_raw_max",
        "peak_sunspot_number",
        "mean_sunspot_number",
        "integral_sunspot",
        "rise_slope",
        "decline_slope",
        "f107_mean",
        "f107_max",
        "f107_sunspot_corr",
        "f107_sunspot_slope",
        "f107_sunspot_residual_std",
        "north_sunspot_mean",
        "south_sunspot_mean",
        "hemispheric_asymmetry_mean",
        "hemispheric_asymmetry_max_abs",
        "polar_precursor_mean",
        "polar_precursor_abs_mean",
        "polar_north_mean",
        "polar_south_mean",
        "polar_asymmetry_mean",
        "cycle_flare_count_total",
        "cycle_mx_flare_count",
        "cycle_x_flare_count",
        "cycle_flare_flux_sum_proxy",
        "cycle_flare_flux_max_proxy",
        "rise_phase_mx_flare_count",
        "max_phase_mx_flare_count",
        "decline_phase_mx_flare_count",
        "flare_peak_lag_to_sunspot_peak_months",
        "cycle_flare_asymmetry_mean",
        "flare_coverage_months",
        "flare_cycle_quality_flag",
        "next_cycle_peak_sunspot",
        "next_cycle_strength_class",
    ]
    features = features[ordered_cols]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    features.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"saved {OUTPUT_PATH}")
    print(
        f"rows={len(features)} cycles={features['cycle_no'].min()}..{features['cycle_no'].max()}"
    )
    print(
        features[["cycle_no", "start_date", "peak_date", "end_date", "is_complete"]]
        .tail()
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()

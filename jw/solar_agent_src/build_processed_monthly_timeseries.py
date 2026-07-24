from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "clean_monthly_timeseries.csv"
CYCLE_METADATA_PATH = INTERIM_DIR / "solar_cycle_metadata_clean.csv"
GOES_MONTHLY_PATH = PROCESSED_DIR / "goes_xrs_monthly_features.csv"
GOES_COVERAGE_START = pd.Timestamp("1975-09-01")
GOES_COVERAGE_END = pd.Timestamp("2017-06-01")


def read_interim(name: str) -> pd.DataFrame:
    df = pd.read_csv(INTERIM_DIR / name)
    df["date_month"] = pd.to_datetime(df["date_month"])
    return df


def bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().eq("true")


def first_non_null(base: pd.DataFrame, candidates: list[str]) -> pd.Series:
    result = pd.Series(pd.NA, index=base.index)
    for col in candidates:
        if col in base:
            result = result.combine_first(base[col])
    return result


def quality_flag_from_conditions(
    missing: pd.Series,
    provisional: pd.Series | None = None,
    partial: pd.Series | None = None,
    limited: pd.Series | None = None,
    external_calibrated: pd.Series | None = None,
) -> pd.Series:
    flag = pd.Series("ok", index=missing.index, dtype="object")
    if limited is not None:
        flag.loc[limited.fillna(False)] = "limited_metadata"
    if partial is not None:
        flag.loc[partial.fillna(False)] = "partial"
    if external_calibrated is not None:
        flag.loc[external_calibrated.fillna(False)] = "external_calibrated_observation"
    if provisional is not None:
        flag.loc[provisional.fillna(False)] = "provisional"
    flag.loc[missing.fillna(False)] = "missing"
    return flag


def build_f107_monthly_median() -> pd.DataFrame:
    raw = pd.read_csv(RAW_DIR / "f107_daily_flux.csv", dtype={"date_utc": str}, low_memory=False)
    raw["date"] = pd.to_datetime(raw["date_utc"], errors="coerce")
    raw = raw[raw["date"].notna()].copy()
    raw["f107_adjusted"] = pd.to_numeric(raw["f107_adjusted"], errors="coerce")
    raw["missing_flag"] = raw["missing_flag"].astype(str).str.lower().eq("true") | raw["f107_adjusted"].isna()

    daily = (
        raw[~raw["missing_flag"]]
        .groupby("date", as_index=False)
        .agg(f107_adjusted_daily_mean=("f107_adjusted", "mean"))
    )
    daily["date_month"] = daily["date"].values.astype("datetime64[M]")
    monthly = (
        daily.groupby("date_month", as_index=False)
        .agg(f107_monthly_median=("f107_adjusted_daily_mean", "median"))
    )
    return monthly


def add_sunspot_smoothing_and_activity_flags(master: pd.DataFrame) -> pd.DataFrame:
    master = master.sort_values("date_month").copy()
    master["sunspot_smoothed_13m"] = (
        master["sunspot_number"].rolling(window=13, center=True, min_periods=7).mean()
    )
    master["is_peak_window_13m"] = False
    master["is_high_activity_phase"] = False
    for _, group in master[master["cycle_no"].notna()].groupby("cycle_no", sort=True):
        smoothed_max = group["sunspot_smoothed_13m"].max(skipna=True)
        if pd.isna(smoothed_max) or smoothed_max <= 0:
            continue
        idx = group.index
        master.loc[idx, "is_peak_window_13m"] = master.loc[idx, "sunspot_smoothed_13m"].ge(
            0.85 * smoothed_max
        ).fillna(False)
        master.loc[idx, "is_high_activity_phase"] = master.loc[idx, "sunspot_smoothed_13m"].ge(
            0.50 * smoothed_max
        ).fillna(False)
    return master


def main() -> None:
    total = read_interim("silso_sn_m_tot_v2_interim.csv")
    hem = read_interim("silso_sn_m_hem_v2_interim.csv")
    f107 = read_interim("f107_daily_flux_interim.csv")
    wso = read_interim("wso_polar_field_interim.csv")
    cycles = read_interim("solar_cycle_metadata_clean.csv")
    f107_median = build_f107_monthly_median()
    goes = pd.read_csv(GOES_MONTHLY_PATH) if GOES_MONTHLY_PATH.exists() else None
    if goes is not None:
        goes["date_month"] = pd.to_datetime(goes["date_month"])

    min_month = min(
        total["date_month"].min(),
        hem["date_month"].min(),
        f107["date_month"].min(),
        wso["date_month"].min(),
        cycles["date_month"].min(),
    )
    max_month = max(
        total["date_month"].max(),
        hem["date_month"].max(),
        f107["date_month"].max(),
        wso["date_month"].max(),
        cycles["date_month"].max(),
    )
    master = pd.DataFrame({"date_month": pd.date_range(min_month, max_month, freq="MS")})

    total_small = total[
        [
            "date_month",
            "sunspot_number",
            "std_dev_clean",
            "is_provisional",
            "has_uncertainty",
            "has_observation_count",
        ]
    ].rename(columns={"std_dev_clean": "sunspot_std"})
    hem_small = hem[
        [
            "date_month",
            "north_sn",
            "south_sn",
            "hemispheric_asymmetry",
            "is_provisional",
            "is_external_calibrated_observation",
            "hemisphere_source_type",
            "missing_value_flag",
        ]
    ].rename(
        columns={
            "north_sn": "north_sunspot_number",
            "south_sn": "south_sunspot_number",
            "is_provisional": "hemisphere_is_provisional",
            "is_external_calibrated_observation": "hemisphere_is_external_calibrated_observation",
            "missing_value_flag": "hemisphere_missing_value_flag",
        }
    )
    f107_small = f107[
        [
            "date_month",
            "f107_adjusted_monthly_mean",
            "f107_observed_days_in_month",
            "f107_month_completeness",
            "missing_value_flag",
            "anomaly_flag",
        ]
    ].rename(
        columns={
            "f107_adjusted_monthly_mean": "f107_monthly_mean",
            "f107_observed_days_in_month": "f107_valid_days",
            "missing_value_flag": "f107_missing_value_flag",
            "anomaly_flag": "f107_anomaly_flag",
        }
    )
    wso_small = wso[
        [
            "date_month",
            "north_field_filtered_mean",
            "south_field_filtered_mean",
            "avg_field_filtered_mean",
            "north_field_filtered_abs_mean",
            "south_field_filtered_abs_mean",
            "polar_asymmetry_filtered",
            "wso_month_completeness",
            "missing_value_flag",
            "anomaly_flag",
        ]
    ].rename(
        columns={
            "north_field_filtered_mean": "polar_north",
            "south_field_filtered_mean": "polar_south",
            "avg_field_filtered_mean": "polar_mean_signed",
            "polar_asymmetry_filtered": "polar_asymmetry",
            "missing_value_flag": "polar_missing_value_flag",
            "anomaly_flag": "polar_anomaly_flag",
        }
    )
    wso_small["polar_mean_abs"] = (
        wso_small["north_field_filtered_abs_mean"] + wso_small["south_field_filtered_abs_mean"]
    ) / 2
    wso_small = wso_small.drop(columns=["north_field_filtered_abs_mean", "south_field_filtered_abs_mean"])

    cycle_small = cycles[
        [
            "date_month",
            "cycle_no",
            "cycle_phase",
            "cycle_phase_windowed",
            "months_from_cycle_start",
            "months_to_cycle_peak",
            "official_cycle_min_sn",
            "official_cycle_max_sn",
        ]
    ]

    merge_frames = [total_small, hem_small, f107_small, f107_median, wso_small, cycle_small]
    if goes is not None:
        merge_frames.append(goes)

    for frame in merge_frames:
        master = master.merge(frame, on="date_month", how="left")

    master = add_sunspot_smoothing_and_activity_flags(master)

    master["year"] = master["date_month"].dt.year
    master["month"] = master["date_month"].dt.month

    master["sunspot_quality_flag"] = quality_flag_from_conditions(
        missing=master["sunspot_number"].isna(),
        provisional=bool_series(master["is_provisional"]),
        limited=~bool_series(master["has_uncertainty"]) | ~bool_series(master["has_observation_count"]),
    )
    master["hemisphere_quality_flag"] = quality_flag_from_conditions(
        missing=master["north_sunspot_number"].isna() | master["south_sunspot_number"].isna(),
        provisional=bool_series(master["hemisphere_is_provisional"]),
        partial=bool_series(master["hemisphere_missing_value_flag"]),
        external_calibrated=bool_series(master["hemisphere_is_external_calibrated_observation"]),
    )
    master["f107_quality_flag"] = quality_flag_from_conditions(
        missing=master["f107_monthly_mean"].isna(),
        partial=(master["f107_month_completeness"].fillna(0) < 0.95) | bool_series(master["f107_anomaly_flag"]),
    )
    master["polar_quality_flag"] = quality_flag_from_conditions(
        missing=master["polar_north"].isna() | master["polar_south"].isna(),
        partial=(master["wso_month_completeness"].fillna(0) < 1.0) | bool_series(master["polar_anomaly_flag"]),
    )

    flare_count_cols = [
        "flare_count_total",
        "flare_count_a",
        "flare_count_b",
        "flare_count_c",
        "flare_count_m",
        "flare_count_x",
        "flare_count_unknown",
        "flare_count_ge_c",
        "flare_count_ge_m",
        "m_x_flare_count",
        "flare_days_count",
        "active_region_count",
        "flare_north_count",
        "flare_south_count",
        "position_valid_count",
        "hemisphere_unknown_count",
    ]
    flare_numeric_cols = [
        "xray_peak_flux_sum_proxy",
        "xray_peak_flux_max_proxy",
        "flare_hemispheric_asymmetry",
        "limb_flare_share",
        "position_valid_rate",
        "flare_parse_ok_rate",
        "flare_time_complete_rate",
        "flare_position_valid_rate",
        "flare_class_valid_rate",
    ]
    in_goes_coverage = master["date_month"].between(GOES_COVERAGE_START, GOES_COVERAGE_END)
    if goes is None:
        master["has_flare_data"] = False
        master["flare_coverage_status"] = np.where(
            master["date_month"].lt(GOES_COVERAGE_START),
            "outside_coverage",
            "outside_legacy_goes_xrs_report",
        )
        for col in flare_count_cols + flare_numeric_cols + [
            "flare_data_quality_flag",
            "flare_evidence_tier",
            "flare_legacy_duration_warning",
        ]:
            master[col] = np.nan
    else:
        for col in flare_count_cols:
            if col in master.columns:
                master.loc[in_goes_coverage, col] = master.loc[in_goes_coverage, col].fillna(0)
        for col in ["xray_peak_flux_sum_proxy", "xray_peak_flux_max_proxy", "limb_flare_share"]:
            if col in master.columns:
                master.loc[in_goes_coverage, col] = master.loc[in_goes_coverage, col].fillna(0)
        master["has_flare_data"] = False
        master.loc[in_goes_coverage, "has_flare_data"] = True
        master.loc[master["date_month"].lt(GOES_COVERAGE_START), "flare_coverage_status"] = "outside_coverage"
        master.loc[master["date_month"].gt(GOES_COVERAGE_END), "flare_coverage_status"] = (
            "outside_legacy_goes_xrs_report"
        )
        master.loc[in_goes_coverage & master["flare_coverage_status"].isna(), "flare_coverage_status"] = (
            "observed_zero_event"
        )
        master.loc[in_goes_coverage & master["flare_data_quality_flag"].isna(), "flare_data_quality_flag"] = (
            "observed_zero_event"
        )
        master.loc[in_goes_coverage & master["flare_evidence_tier"].isna(), "flare_evidence_tier"] = "auxiliary"
        master.loc[in_goes_coverage & master["flare_legacy_duration_warning"].isna(), "flare_legacy_duration_warning"] = False

    coverage_sources = {
        "sunspot": master["sunspot_number"].notna(),
        "hemisphere": master["north_sunspot_number"].notna() & master["south_sunspot_number"].notna(),
        "f107": master["f107_monthly_mean"].notna(),
        "polar": master["polar_north"].notna() & master["polar_south"].notna(),
        "cycle": master["cycle_no"].notna(),
    }
    coverage_count = sum(mask.astype(int) for mask in coverage_sources.values())
    master["data_coverage_flag"] = [
        "none" if count == 0 else "all" if count == len(coverage_sources) else "partial:" + "|".join(
            name for name, mask in coverage_sources.items() if mask.iloc[i]
        )
        for i, count in enumerate(coverage_count)
    ]

    final_cols = [
        "date_month",
        "year",
        "month",
        "sunspot_number",
        "sunspot_smoothed_13m",
        "sunspot_std",
        "sunspot_quality_flag",
        "north_sunspot_number",
        "south_sunspot_number",
        "hemispheric_asymmetry",
        "hemisphere_source_type",
        "hemisphere_quality_flag",
        "f107_monthly_mean",
        "f107_monthly_median",
        "f107_valid_days",
        "f107_quality_flag",
        "polar_north",
        "polar_south",
        "polar_mean_signed",
        "polar_mean_abs",
        "polar_asymmetry",
        "polar_quality_flag",
        "cycle_no",
        "cycle_phase",
        "cycle_phase_windowed",
        "months_from_cycle_start",
        "months_to_cycle_peak",
        "official_cycle_min_sn",
        "official_cycle_max_sn",
        "is_peak_window_13m",
        "is_high_activity_phase",
        "data_coverage_flag",
        "flare_count_total",
        "flare_count_a",
        "flare_count_b",
        "flare_count_c",
        "flare_count_m",
        "flare_count_x",
        "flare_count_unknown",
        "flare_count_ge_c",
        "flare_count_ge_m",
        "m_x_flare_count",
        "xray_peak_flux_sum_proxy",
        "xray_peak_flux_max_proxy",
        "flare_days_count",
        "active_region_count",
        "flare_north_count",
        "flare_south_count",
        "flare_hemispheric_asymmetry",
        "position_valid_count",
        "position_valid_rate",
        "hemisphere_unknown_count",
        "limb_flare_share",
        "flare_parse_ok_rate",
        "flare_time_complete_rate",
        "flare_position_valid_rate",
        "flare_class_valid_rate",
        "has_flare_data",
        "flare_coverage_status",
        "flare_legacy_duration_warning",
        "flare_data_quality_flag",
        "flare_evidence_tier",
    ]
    final = master[final_cols].copy()
    final["date_month"] = final["date_month"].dt.strftime("%Y-%m-%d")
    final["cycle_no"] = final["cycle_no"].astype("Int64")
    final["months_from_cycle_start"] = final["months_from_cycle_start"].astype("Int64")
    final["months_to_cycle_peak"] = final["months_to_cycle_peak"].astype("Int64")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    final.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"saved {OUTPUT_PATH}")
    print(f"rows={len(final)} range={final['date_month'].min()}..{final['date_month'].max()}")
    print(final["data_coverage_flag"].value_counts().to_string())


if __name__ == "__main__":
    main()

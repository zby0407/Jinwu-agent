from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"


def month_start(year: pd.Series, month: pd.Series) -> pd.Series:
    return pd.to_datetime(
        {"year": year.astype(int), "month": month.astype(int), "day": 1}
    )


def add_cycle_columns(
    df: pd.DataFrame, cycles: pd.DataFrame, date_col: str = "date_month"
) -> pd.DataFrame:
    out = df.copy()
    dates = pd.to_datetime(out[date_col])
    cycle_number = np.full(len(out), np.nan)
    cycle_min_date = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    cycle_max_date = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    cycle_min_sn = np.full(len(out), np.nan)
    cycle_max_sn = np.full(len(out), np.nan)
    cycle_rise_months = np.full(len(out), np.nan)

    ordered = cycles.sort_values("cycle").reset_index(drop=True)
    for i, row in ordered.iterrows():
        start = row["min_date_month"]
        if i + 1 < len(ordered):
            end = ordered.loc[i + 1, "min_date_month"]
            mask = (dates >= start) & (dates < end)
        else:
            mask = dates >= start

        cycle_number[mask] = row["cycle"]
        cycle_min_date.loc[mask] = row["min_date_month"]
        cycle_max_date.loc[mask] = row["max_date_month"]
        cycle_min_sn[mask] = row["min_sn"]
        cycle_max_sn[mask] = row["max_sn"]
        cycle_rise_months[mask] = row["rise_months"]

    out["cycle_number"] = pd.Series(cycle_number, index=out.index).astype("Int64")
    out["cycle_min_date_month"] = cycle_min_date.dt.strftime("%Y-%m-%d")
    out["cycle_max_date_month"] = cycle_max_date.dt.strftime("%Y-%m-%d")
    out["cycle_min_sn"] = cycle_min_sn
    out["cycle_max_sn"] = cycle_max_sn
    out["cycle_rise_months"] = cycle_rise_months
    min_dates = pd.to_datetime(out["cycle_min_date_month"], errors="coerce")
    max_dates = pd.to_datetime(out["cycle_max_date_month"], errors="coerce")
    out["months_since_cycle_min"] = (
        (dates.dt.year - min_dates.dt.year) * 12 + (dates.dt.month - min_dates.dt.month)
    ).astype("Int64")
    out["months_until_cycle_max"] = (
        (max_dates.dt.year - dates.dt.year) * 12 + (max_dates.dt.month - dates.dt.month)
    ).astype("Int64")
    out["is_cycle_min_month"] = dates.eq(min_dates)
    out["is_cycle_max_month"] = dates.eq(max_dates)
    out["cycle_phase_basic"] = "unknown"
    out.loc[
        out["cycle_number"].notna() & out["months_until_cycle_max"].isna(),
        "cycle_phase_basic",
    ] = "ongoing_or_unknown"
    out.loc[
        out["cycle_number"].notna() & out["months_until_cycle_max"].gt(0).fillna(False),
        "cycle_phase_basic",
    ] = "ascending"
    out.loc[
        out["cycle_number"].notna() & out["months_until_cycle_max"].eq(0).fillna(False),
        "cycle_phase_basic",
    ] = "maximum"
    out.loc[
        out["cycle_number"].notna() & out["months_until_cycle_max"].lt(0).fillna(False),
        "cycle_phase_basic",
    ] = "declining"
    return out


def read_cycles() -> pd.DataFrame:
    path = RAW_DIR / "silso_cycle_minmax.csv"
    cycles = pd.read_csv(
        path,
        sep=";",
        skiprows=2,
        header=None,
        names=[
            "cycle",
            "min_year",
            "min_month",
            "min_sn",
            "max_year",
            "max_month",
            "max_sn",
        ],
        skipinitialspace=True,
    )
    cycles["min_date_month"] = month_start(cycles["min_year"], cycles["min_month"])
    cycles["max_date_month"] = month_start(cycles["max_year"], cycles["max_month"])
    cycles["rise_months"] = (
        cycles["max_date_month"].dt.year - cycles["min_date_month"].dt.year
    ) * 12 + (cycles["max_date_month"].dt.month - cycles["min_date_month"].dt.month)
    cycles["source_file"] = "silso_cycle_minmax.csv"
    return cycles


def clean_total_sunspot(cycles: pd.DataFrame) -> pd.DataFrame:
    path = RAW_DIR / "silso_sn_m_tot_v2.csv"
    cols = [
        "year",
        "month",
        "date_frac",
        "sunspot_number",
        "std_dev",
        "observations",
        "definitive",
    ]
    df = pd.read_csv(path, sep=";", header=None, names=cols, skipinitialspace=True)
    df["date_month"] = month_start(df["year"], df["month"])
    df["std_dev_clean"] = df["std_dev"].where(df["std_dev"] >= 0)
    df["observations_clean"] = (
        df["observations"].where(df["observations"] >= 0).astype("Int64")
    )
    df["is_provisional"] = df["definitive"].eq(0)
    df["has_uncertainty"] = df["std_dev_clean"].notna()
    df["has_observation_count"] = df["observations_clean"].notna()
    df["missing_value_flag"] = (
        df[["sunspot_number", "std_dev_clean", "observations_clean"]].isna().any(axis=1)
    )
    df["source_file"] = path.name
    out = add_cycle_columns(df, cycles)
    ordered = [
        "date_month",
        "year",
        "month",
        "date_frac",
        "sunspot_number",
        "std_dev_clean",
        "observations_clean",
        "is_provisional",
        "has_uncertainty",
        "has_observation_count",
        "missing_value_flag",
        "cycle_number",
        "cycle_phase_basic",
        "months_since_cycle_min",
        "months_until_cycle_max",
        "is_cycle_min_month",
        "is_cycle_max_month",
        "cycle_min_date_month",
        "cycle_max_date_month",
        "cycle_min_sn",
        "cycle_max_sn",
        "cycle_rise_months",
        "source_file",
    ]
    return out[ordered]


def clean_hemispheric_sunspot(cycles: pd.DataFrame) -> pd.DataFrame:
    path = RAW_DIR / "silso_sn_m_hem_v2.csv"
    cols = [
        "year",
        "month",
        "date_frac",
        "total_sn",
        "north_sn",
        "south_sn",
        "total_std",
        "north_std",
        "south_std",
        "total_obs",
        "north_obs",
        "south_obs",
        "definitive",
    ]
    df = pd.read_csv(path, sep=";", header=None, names=cols, skipinitialspace=True)
    df["date_month"] = month_start(df["year"], df["month"])
    for col in [
        "total_std",
        "north_std",
        "south_std",
        "total_obs",
        "north_obs",
        "south_obs",
    ]:
        clean = f"{col}_clean"
        df[clean] = df[col].where(df[col] >= 0)
        if col.endswith("_obs"):
            df[clean] = df[clean].astype("Int64")
    df["north_south_diff"] = df["north_sn"] - df["south_sn"]
    df["north_south_abs_diff"] = df["north_south_diff"].abs()
    df["hemispheric_asymmetry"] = np.where(
        df["total_sn"].ne(0), df["north_south_diff"] / df["total_sn"], np.nan
    )
    df["north_share"] = np.where(
        df["total_sn"].ne(0), df["north_sn"] / df["total_sn"], np.nan
    )
    df["south_share"] = np.where(
        df["total_sn"].ne(0), df["south_sn"] / df["total_sn"], np.nan
    )
    df["north_plus_south_check"] = df["north_sn"] + df["south_sn"]
    df["total_minus_hemispheres"] = df["total_sn"] - df["north_plus_south_check"]
    df["is_provisional"] = df["definitive"].eq(0)
    df["is_external_calibrated_observation"] = df["year"].lt(1992)
    df["hemisphere_source_type"] = np.where(
        df["is_external_calibrated_observation"],
        "rgo_noaa_external_calibrated_observation",
        "silso_official_hemispheric_observation",
    )
    df["missing_value_flag"] = (
        df[
            [
                "total_sn",
                "north_sn",
                "south_sn",
                "total_std_clean",
                "north_std_clean",
                "south_std_clean",
            ]
        ]
        .isna()
        .any(axis=1)
    )
    df["source_file"] = path.name
    out = add_cycle_columns(df, cycles)
    ordered = [
        "date_month",
        "year",
        "month",
        "date_frac",
        "total_sn",
        "north_sn",
        "south_sn",
        "total_std_clean",
        "north_std_clean",
        "south_std_clean",
        "total_obs_clean",
        "north_obs_clean",
        "south_obs_clean",
        "north_south_diff",
        "north_south_abs_diff",
        "hemispheric_asymmetry",
        "north_share",
        "south_share",
        "total_minus_hemispheres",
        "is_provisional",
        "is_external_calibrated_observation",
        "hemisphere_source_type",
        "missing_value_flag",
        "cycle_number",
        "cycle_phase_basic",
        "months_since_cycle_min",
        "months_until_cycle_max",
        "is_cycle_min_month",
        "is_cycle_max_month",
        "cycle_min_date_month",
        "cycle_max_date_month",
        "cycle_min_sn",
        "cycle_max_sn",
        "cycle_rise_months",
        "source_file",
    ]
    return out[ordered]


def build_cycle_monthly(
    cycles: pd.DataFrame, latest_month: pd.Timestamp
) -> pd.DataFrame:
    months = pd.date_range(cycles["min_date_month"].min(), latest_month, freq="MS")
    df = pd.DataFrame({"date_month": months})
    out = add_cycle_columns(df, cycles)
    out["source_file"] = "silso_cycle_minmax.csv"
    return out[
        [
            "date_month",
            "cycle_number",
            "cycle_phase_basic",
            "months_since_cycle_min",
            "months_until_cycle_max",
            "is_cycle_min_month",
            "is_cycle_max_month",
            "cycle_min_date_month",
            "cycle_max_date_month",
            "cycle_min_sn",
            "cycle_max_sn",
            "cycle_rise_months",
            "source_file",
        ]
    ]


def clean_f107(cycles: pd.DataFrame) -> pd.DataFrame:
    path = RAW_DIR / "f107_daily_flux.csv"
    df = pd.read_csv(path, dtype={"date_utc": str, "time_utc": str}, low_memory=False)
    df["date"] = pd.to_datetime(df["date_utc"], errors="coerce")
    df = df[df["date"].notna()].copy()
    for col in [
        "julian_date",
        "carrington_rotation",
        "f107_observed",
        "f107_adjusted",
        "f107_ursi",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["missing_flag"] = (
        df["missing_flag"].astype(str).str.lower().eq("true")
        | df["f107_adjusted"].isna()
    )
    df["duplicate_flag"] = df["duplicate_flag"].astype(str).str.lower().eq("true")
    daily = df.groupby("date", as_index=False).agg(
        f107_records_per_day=("f107_adjusted", "size"),
        f107_valid_records_per_day=("missing_flag", lambda x: int((~x).sum())),
        f107_missing_records_per_day=("missing_flag", "sum"),
        f107_duplicate_records_per_day=("duplicate_flag", "sum"),
        f107_observed_daily_mean=("f107_observed", "mean"),
        f107_adjusted_daily_mean=("f107_adjusted", "mean"),
        f107_ursi_daily_mean=("f107_ursi", "mean"),
        f107_observed_daily_min=("f107_observed", "min"),
        f107_observed_daily_max=("f107_observed", "max"),
        f107_adjusted_daily_min=("f107_adjusted", "min"),
        f107_adjusted_daily_max=("f107_adjusted", "max"),
        f107_source_segments=(
            "source_segment",
            lambda x: "|".join(sorted(set(x.dropna().astype(str)))),
        ),
        f107_record_types=(
            "record_type",
            lambda x: "|".join(sorted(set(x.dropna().astype(str)))),
        ),
    )
    full_days = pd.DataFrame(
        {"date": pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")}
    )
    daily = full_days.merge(daily, on="date", how="left")
    daily["is_missing_day"] = daily["f107_records_per_day"].isna()
    daily["f107_records_per_day"] = daily["f107_records_per_day"].fillna(0).astype(int)
    daily["f107_valid_records_per_day"] = (
        daily["f107_valid_records_per_day"].fillna(0).astype(int)
    )
    daily["f107_missing_records_per_day"] = (
        daily["f107_missing_records_per_day"].fillna(0).astype(int)
    )
    daily["f107_duplicate_records_per_day"] = (
        daily["f107_duplicate_records_per_day"].fillna(0).astype(int)
    )
    daily["date_month"] = daily["date"].values.astype("datetime64[M]")
    monthly = daily.groupby("date_month", as_index=False).agg(
        f107_observed_monthly_mean=("f107_observed_daily_mean", "mean"),
        f107_adjusted_monthly_mean=("f107_adjusted_daily_mean", "mean"),
        f107_ursi_monthly_mean=("f107_ursi_daily_mean", "mean"),
        f107_observed_monthly_min=("f107_observed_daily_min", "min"),
        f107_observed_monthly_max=("f107_observed_daily_max", "max"),
        f107_adjusted_monthly_min=("f107_adjusted_daily_min", "min"),
        f107_adjusted_monthly_max=("f107_adjusted_daily_max", "max"),
        f107_days_in_month=("date", "size"),
        f107_observed_days_in_month=("is_missing_day", lambda x: int((~x).sum())),
        f107_missing_days_in_month=("is_missing_day", "sum"),
        f107_total_records_in_month=("f107_records_per_day", "sum"),
        f107_valid_records_in_month=("f107_valid_records_per_day", "sum"),
        f107_missing_records_in_month=("f107_missing_records_per_day", "sum"),
        f107_duplicate_records_in_month=("f107_duplicate_records_per_day", "sum"),
        f107_days_with_less_than_3_records=(
            "f107_records_per_day",
            lambda x: int(((x > 0) & (x < 3)).sum()),
        ),
        f107_source_segments=(
            "f107_source_segments",
            lambda x: "|".join(sorted(set(x.dropna().astype(str)))),
        ),
        f107_record_types=(
            "f107_record_types",
            lambda x: "|".join(sorted(set(x.dropna().astype(str)))),
        ),
    )
    monthly["f107_month_completeness"] = (
        monthly["f107_observed_days_in_month"] / monthly["f107_days_in_month"]
    )
    monthly["missing_value_flag"] = monthly["f107_missing_days_in_month"].gt(
        0
    ) | monthly["f107_missing_records_in_month"].gt(0)
    monthly["anomaly_flag"] = monthly["missing_value_flag"] | monthly[
        "f107_duplicate_records_in_month"
    ].gt(0)
    monthly["source_file"] = path.name
    out = add_cycle_columns(monthly, cycles)
    return out[
        [
            "date_month",
            "f107_observed_monthly_mean",
            "f107_adjusted_monthly_mean",
            "f107_ursi_monthly_mean",
            "f107_observed_monthly_min",
            "f107_observed_monthly_max",
            "f107_adjusted_monthly_min",
            "f107_adjusted_monthly_max",
            "f107_days_in_month",
            "f107_observed_days_in_month",
            "f107_missing_days_in_month",
            "f107_total_records_in_month",
            "f107_valid_records_in_month",
            "f107_missing_records_in_month",
            "f107_duplicate_records_in_month",
            "f107_days_with_less_than_3_records",
            "f107_source_segments",
            "f107_record_types",
            "f107_month_completeness",
            "missing_value_flag",
            "anomaly_flag",
            "cycle_number",
            "cycle_phase_basic",
            "months_since_cycle_min",
            "months_until_cycle_max",
            "is_cycle_min_month",
            "is_cycle_max_month",
            "cycle_min_date_month",
            "cycle_max_date_month",
            "cycle_min_sn",
            "cycle_max_sn",
            "cycle_rise_months",
            "source_file",
        ]
    ]


def clean_wso(cycles: pd.DataFrame) -> pd.DataFrame:
    path = RAW_DIR / "wso_polar_field.csv"
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    date_pat = re.compile(r"(?P<date>\d{4}:\d{2}:\d{2})_(?P<time>[^\s]+)")
    numeric_pat = re.compile(
        r"(?P<north>[-+]?\d+(?:\.\d+)?)N\s+"
        r"(?P<south>[-+]?\d+(?:\.\d+)?)S\s+"
        r"(?P<avg>[-+]?\d+(?:\.\d+)?)Avg\s+"
        r"(?P<nhz>[-+]?\d+(?:\.\d+)?)nhz.*?"
        r"(?P<north_f>[-+]?\d+(?:\.\d+)?)Nf\s+"
        r"(?P<south_f>[-+]?\d+(?:\.\d+)?)Sf\s+"
        r"(?P<avg_f>[-+]?\d+(?:\.\d+)?)Avgf"
    )
    rows = []
    for line_number, line in enumerate(lines, start=1):
        date_match = date_pat.search(line)
        if not date_match:
            continue
        date = pd.to_datetime(
            date_match.group("date").replace(":", "-"), errors="coerce"
        )
        numeric_match = numeric_pat.search(line)
        is_missing_row = "XXX" in line
        row = {
            "date": date,
            "raw_line_number": line_number,
            "is_missing_row": is_missing_row,
            "parse_success": numeric_match is not None,
        }
        for field in ["north", "south", "avg", "nhz", "north_f", "south_f", "avg_f"]:
            row[field] = np.nan
        if numeric_match:
            for field, value in numeric_match.groupdict().items():
                row[field] = float(value)
        rows.append(row)

    daily = pd.DataFrame(rows).sort_values("date")
    daily["gap_from_previous_days"] = daily["date"].diff().dt.days
    daily["date_month"] = daily["date"].values.astype("datetime64[M]")
    monthly = daily.groupby("date_month", as_index=False).agg(
        wso_rows_in_month=("date", "size"),
        wso_numeric_rows_in_month=("parse_success", "sum"),
        wso_missing_rows_in_month=("is_missing_row", "sum"),
        wso_max_gap_days_month=("gap_from_previous_days", "max"),
        north_field_mean=("north", "mean"),
        south_field_mean=("south", "mean"),
        avg_field_mean=("avg", "mean"),
        north_field_filtered_mean=("north_f", "mean"),
        south_field_filtered_mean=("south_f", "mean"),
        avg_field_filtered_mean=("avg_f", "mean"),
        north_field_filtered_abs_mean=("north_f", lambda x: x.abs().mean()),
        south_field_filtered_abs_mean=("south_f", lambda x: x.abs().mean()),
    )
    monthly["polar_asymmetry_filtered"] = (
        monthly["north_field_filtered_mean"] - monthly["south_field_filtered_mean"]
    )
    monthly["wso_month_completeness"] = (
        monthly["wso_numeric_rows_in_month"] / monthly["wso_rows_in_month"]
    )
    monthly["missing_value_flag"] = monthly["wso_missing_rows_in_month"].gt(0)
    monthly["anomaly_flag"] = monthly["missing_value_flag"] | monthly[
        "wso_max_gap_days_month"
    ].gt(40)
    monthly["source_file"] = path.name
    out = add_cycle_columns(monthly, cycles)
    return out[
        [
            "date_month",
            "north_field_mean",
            "south_field_mean",
            "avg_field_mean",
            "north_field_filtered_mean",
            "south_field_filtered_mean",
            "avg_field_filtered_mean",
            "north_field_filtered_abs_mean",
            "south_field_filtered_abs_mean",
            "polar_asymmetry_filtered",
            "wso_rows_in_month",
            "wso_numeric_rows_in_month",
            "wso_missing_rows_in_month",
            "wso_max_gap_days_month",
            "wso_month_completeness",
            "missing_value_flag",
            "anomaly_flag",
            "cycle_number",
            "cycle_phase_basic",
            "months_since_cycle_min",
            "months_until_cycle_max",
            "is_cycle_min_month",
            "is_cycle_max_month",
            "cycle_min_date_month",
            "cycle_max_date_month",
            "cycle_min_sn",
            "cycle_max_sn",
            "cycle_rise_months",
            "source_file",
        ]
    ]


def save_csv(df: pd.DataFrame, filename: str) -> None:
    output = INTERIM_DIR / filename
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    df.to_csv(output, index=False, encoding="utf-8")
    print(
        f"{filename}: rows={len(df)}, range={df['date_month'].min()}..{df['date_month'].max()}"
    )


def main() -> None:
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    cycles = read_cycles()
    total = clean_total_sunspot(cycles)
    hem = clean_hemispheric_sunspot(cycles)
    f107 = clean_f107(cycles)
    wso = clean_wso(cycles)
    latest_month = max(
        pd.to_datetime(total["date_month"]).max(),
        pd.to_datetime(hem["date_month"]).max(),
        pd.to_datetime(f107["date_month"]).max(),
        pd.to_datetime(wso["date_month"]).max(),
    )
    cycle_monthly = build_cycle_monthly(cycles, latest_month)

    save_csv(total, "silso_sn_m_tot_v2_interim.csv")
    save_csv(hem, "silso_sn_m_hem_v2_interim.csv")
    save_csv(cycle_monthly, "silso_cycle_minmax_interim.csv")
    save_csv(f107, "f107_daily_flux_interim.csv")
    save_csv(wso, "wso_polar_field_interim.csv")


if __name__ == "__main__":
    main()

"""Canonical, column-name based F10.7 adapter for audited research inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from jw.research_integrity import DatasetSemanticManifest, sha256_file

ADAPTER_ID = "solar.f107.daily-to-monthly"
ADAPTER_VERSION = "1.0.0"
REQUIRED_COLUMNS = {
    "date_utc",
    "time_utc",
    "f107_observed",
    "f107_adjusted",
    "f107_ursi",
    "source_segment",
    "record_type",
    "missing_flag",
    "duplicate_flag",
}


def _flag(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def _source_union(series: pd.Series) -> str:
    return "|".join(sorted({str(value) for value in series.dropna() if str(value)}))


def canonicalize_f107(
    input_path: Path,
) -> tuple[pd.DataFrame, DatasetSemanticManifest]:
    """Return equal-weight monthly means and their semantic manifest."""

    input_path = input_path.resolve()
    frame = pd.read_csv(
        input_path,
        dtype={"date_utc": str, "time_utc": str},
        low_memory=False,
    )
    missing_columns = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing_columns:
        raise ValueError(f"F10.7 input missing required columns: {missing_columns}")

    original_records = len(frame)
    frame["date"] = pd.to_datetime(frame["date_utc"], errors="coerce")
    invalid_dates = int(frame["date"].isna().sum())
    frame = frame.loc[frame["date"].notna()].copy()
    for column in ("f107_observed", "f107_adjusted", "f107_ursi"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["missing_flag"] = _flag(frame["missing_flag"]) | frame["f107_adjusted"].isna()
    frame["duplicate_flag"] = _flag(frame["duplicate_flag"])

    valid = frame.loc[~frame["missing_flag"]].copy()
    duplicate_key = [
        "date_utc",
        "time_utc",
        "record_type",
    ]
    has_unflagged = valid.groupby(duplicate_key, dropna=False)[
        "duplicate_flag"
    ].transform(lambda values: bool((~values).any()))
    drop_shadow = valid["duplicate_flag"] & has_unflagged
    shadow_duplicates_dropped = int(drop_shadow.sum())
    valid = valid.loc[~drop_shadow].copy()

    # If a group consists only of flagged duplicates, retain one deterministic
    # representative so old segments do not vanish silently.
    before_deduplicate = len(valid)
    valid = valid.sort_values(
        ["date_utc", "time_utc", "source_segment", "record_type"],
        kind="stable",
    ).drop_duplicates(duplicate_key, keep="first")
    equivalent_duplicates_dropped = before_deduplicate - len(valid)

    daily = valid.groupby("date", as_index=False).agg(
        f107_records_per_day=("f107_adjusted", "size"),
        f107_observed_daily_mean=("f107_observed", "mean"),
        f107_adjusted_daily_mean=("f107_adjusted", "mean"),
        f107_ursi_daily_mean=("f107_ursi", "mean"),
        f107_observed_daily_min=("f107_observed", "min"),
        f107_observed_daily_max=("f107_observed", "max"),
        f107_adjusted_daily_min=("f107_adjusted", "min"),
        f107_adjusted_daily_max=("f107_adjusted", "max"),
        f107_source_segments=("source_segment", _source_union),
        f107_record_types=("record_type", _source_union),
    )
    if daily.empty:
        raise ValueError("F10.7 input has no valid adjusted-flux observations")

    daily["date_month"] = daily["date"].dt.to_period("M").dt.to_timestamp()
    monthly = daily.groupby("date_month", as_index=False).agg(
        f107_observed_monthly_mean=("f107_observed_daily_mean", "mean"),
        f107_adjusted_monthly_mean=("f107_adjusted_daily_mean", "mean"),
        f107_ursi_monthly_mean=("f107_ursi_daily_mean", "mean"),
        f107_observed_monthly_min=("f107_observed_daily_min", "min"),
        f107_observed_monthly_max=("f107_observed_daily_max", "max"),
        f107_adjusted_monthly_min=("f107_adjusted_daily_min", "min"),
        f107_adjusted_monthly_max=("f107_adjusted_daily_max", "max"),
        f107_observed_days_in_month=("date", "size"),
        f107_total_records_in_month=("f107_records_per_day", "sum"),
        f107_days_with_less_than_3_records=(
            "f107_records_per_day",
            lambda values: int(((values > 0) & (values < 3)).sum()),
        ),
        f107_source_segments=("f107_source_segments", _source_union),
        f107_record_types=("f107_record_types", _source_union),
    )
    monthly["f107_days_in_month"] = monthly["date_month"].dt.days_in_month
    monthly["f107_missing_days_in_month"] = (
        monthly["f107_days_in_month"] - monthly["f107_observed_days_in_month"]
    )
    monthly["f107_month_completeness"] = (
        monthly["f107_observed_days_in_month"] / monthly["f107_days_in_month"]
    )
    frame["date_month"] = frame["date"].dt.to_period("M").dt.to_timestamp()
    quality = frame.groupby("date_month", as_index=False).agg(
        f107_missing_records_in_month=("missing_flag", "sum"),
        f107_duplicate_records_in_month=("duplicate_flag", "sum"),
    )
    valid_counts = valid.copy()
    valid_counts["date_month"] = (
        valid_counts["date"].dt.to_period("M").dt.to_timestamp()
    )
    valid_counts = valid_counts.groupby("date_month", as_index=False).agg(
        f107_valid_records_in_month=("f107_adjusted", "size")
    )
    monthly = monthly.merge(quality, on="date_month", how="left").merge(
        valid_counts, on="date_month", how="left"
    )
    for column in (
        "f107_missing_records_in_month",
        "f107_duplicate_records_in_month",
        "f107_valid_records_in_month",
    ):
        monthly[column] = monthly[column].fillna(0).astype(int)
    monthly["missing_value_flag"] = monthly["f107_missing_days_in_month"].gt(
        0
    ) | monthly["f107_missing_records_in_month"].gt(0)
    monthly["anomaly_flag"] = monthly["missing_value_flag"] | monthly[
        "f107_duplicate_records_in_month"
    ].gt(0)

    input_sha = sha256_file(input_path)
    manifest_key = f"{ADAPTER_ID}:{ADAPTER_VERSION}:{input_sha}"
    manifest_id = (
        "dataset-" + hashlib.sha256(manifest_key.encode("utf-8")).hexdigest()[:20]
    )
    manifest = DatasetSemanticManifest(
        manifest_id=manifest_id,
        input_path=input_path.name,
        input_sha256=input_sha,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        product_id="f107_adjusted",
        product_version="Canadian 10.7 cm solar flux adjusted series",
        column_bindings={
            "time": "date_utc",
            "observed_flux": "f107_observed",
            "primary_flux": "f107_adjusted",
            "ursi_series_d": "f107_ursi",
            "missing": "missing_flag",
            "duplicate": "duplicate_flag",
            "source_segment": "source_segment",
            "record_type": "record_type",
        },
        unit="sfu (10^-22 W m^-2 Hz^-1)",
        observation_grain="zero or more flux determinations per UTC day",
        time_column="date_utc",
        primary_key=("date_utc", "time_utc", "f107_adjusted"),
        duplicate_policy=(
            "Drop flagged copies when an equivalent unflagged record exists; "
            "otherwise retain one deterministic representative."
        ),
        missing_policy="Exclude flagged or non-numeric adjusted-flux records.",
        quality_policy=(
            "Record missing, duplicate, source-segment, record-type, and "
            "monthly day-completeness diagnostics."
        ),
        aggregation_plan=(
            "raw determinations -> equal-weight UTC daily means",
            "daily means -> equal-weight calendar monthly means",
        ),
        coverage_start=str(daily["date"].min().date()),
        coverage_end=str(daily["date"].max().date()),
        diagnostics={
            "input_records": original_records,
            "invalid_date_records": invalid_dates,
            "missing_records_excluded": int(frame["missing_flag"].sum()),
            "shadow_duplicates_dropped": shadow_duplicates_dropped,
            "equivalent_duplicates_dropped": equivalent_duplicates_dropped,
            "canonical_daily_rows": len(daily),
            "canonical_monthly_rows": len(monthly),
        },
        limitations=(
            "Monthly averaging can introduce low-activity non-linearity.",
            "Observed and URSI products are retained only as sensitivity series.",
        ),
        analysis_requirements=(
            "Do not use F10.7/SN as a primary statistic near SN=0.",
            "Compute full-period, 1947-1980, and 1981-2015 relations.",
            "Use segmented slope/intercept terms and cross-period residual checks.",
            "Use autocorrelation-aware uncertainty and a time-block sensitivity.",
            "Treat the published 10.5% discontinuity as an external comparison, "
            "not a forced local result.",
            "Do not attribute the 1980-1981 break to the 1991 Ottawa-Penticton move.",
            "Derive fixed-SN contrasts from stored model coefficients.",
        ),
    )
    return monthly, manifest


def canonicalize_f107_sn(
    f107_path: Path,
    silso_total_path: Path,
    *,
    silso_hemispheric_path: Path | None = None,
) -> tuple[pd.DataFrame, DatasetSemanticManifest]:
    """Align canonical monthly adjusted F10.7 with SILSO total monthly SN v2."""

    f107_monthly, f107_manifest = canonicalize_f107(f107_path)
    names = [
        "year",
        "month",
        "decimal_year",
        "sunspot_number",
        "sunspot_std",
        "observation_count",
        "definitive",
    ]
    silso = pd.read_csv(
        silso_total_path,
        sep=";",
        header=None,
        names=names,
        usecols=range(len(names)),
    )
    for column in names:
        silso[column] = pd.to_numeric(silso[column], errors="coerce")
    invalid_silso = int(
        (
            silso["year"].isna()
            | silso["month"].isna()
            | silso["sunspot_number"].isna()
            | silso["sunspot_number"].lt(0)
        ).sum()
    )
    silso = silso.loc[
        silso["year"].notna() & silso["month"].notna() & silso["sunspot_number"].ge(0)
    ].copy()
    silso["date_month"] = pd.to_datetime(
        {
            "year": silso["year"].astype(int),
            "month": silso["month"].astype(int),
            "day": 1,
        },
        errors="coerce",
    )
    silso = silso.loc[silso["date_month"].notna()].copy()
    aligned = f107_monthly.merge(
        silso[
            [
                "date_month",
                "sunspot_number",
                "sunspot_std",
                "observation_count",
                "definitive",
            ]
        ],
        on="date_month",
        how="inner",
        validate="one_to_one",
    )
    aligned = aligned.loc[
        aligned["date_month"].between("1947-01-01", "2015-12-01")
    ].reset_index(drop=True)
    if aligned.empty:
        raise ValueError("F10.7 and SILSO monthly inputs have no 1947-2015 overlap")

    silso_sha = sha256_file(silso_total_path)
    combined_sha = hashlib.sha256(
        f"{f107_manifest.input_sha256}:{silso_sha}".encode()
    ).hexdigest()
    excluded: tuple[dict[str, str], ...] = ()
    if silso_hemispheric_path is not None:
        excluded = (
            {
                "path": silso_hemispheric_path.name,
                "reason": (
                    "The primary estimand uses SILSO Version 2 total monthly "
                    "sunspot number; hemispheric series is out of scope."
                ),
            },
        )
    manifest = replace(
        f107_manifest,
        manifest_id="dataset-"
        + hashlib.sha256(f"{ADAPTER_ID}:silso-v2:{combined_sha}".encode()).hexdigest()[
            :20
        ],
        input_path=f"{f107_path.name}+{silso_total_path.name}",
        input_sha256=combined_sha,
        product_id="f107_adjusted+silso_sn_total_v2",
        product_version=(
            "Canadian adjusted 10.7 cm flux aligned to SILSO Version 2 "
            "total monthly mean sunspot number"
        ),
        column_bindings={
            **dict(f107_manifest.column_bindings),
            "sunspot_number": "SILSO total monthly column 4",
        },
        coverage_start=str(aligned["date_month"].min().date()),
        coverage_end=str(aligned["date_month"].max().date()),
        diagnostics={
            **dict(f107_manifest.diagnostics),
            "silso_input_sha256": silso_sha,
            "silso_invalid_or_missing_rows_excluded": invalid_silso,
            "aligned_monthly_rows_1947_2015": len(aligned),
        },
        excluded_inputs=excluded,
    )
    return aligned, manifest


def write_f107_contract(
    input_path: Path,
    *,
    canonical_path: Path,
    receipt_path: Path,
    silso_total_path: Path | None = None,
    silso_hemispheric_path: Path | None = None,
) -> dict[str, Any]:
    if silso_total_path is None:
        monthly, manifest = canonicalize_f107(input_path)
    else:
        monthly, manifest = canonicalize_f107_sn(
            input_path,
            silso_total_path,
            silso_hemispheric_path=silso_hemispheric_path,
        )
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(canonical_path, index=False)
    payload = manifest.to_dict()
    payload.update(
        {
            "status": "verified",
            "canonical_artifact": canonical_path.name,
            "canonical_sha256": sha256_file(canonical_path),
        }
    )
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload

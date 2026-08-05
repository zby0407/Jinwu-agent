#!/usr/bin/env python3
"""Audit Huairou polar products and derive diagnostic time-series features.

The input archive spans incompatible instrument epochs and signal units.  Raw
values are therefore summarized within their epoch, while cross-epoch plots and
features use robust within-epoch/hemisphere standardization.  The output remains
diagnostic until the image geometry and physical calibration are validated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

DAILY_REQUIRED = {
    "date",
    "hemisphere",
    "instrument_epoch",
    "camera",
    "source_format",
    "signal_definition",
    "signal_unit",
    "calibration_status",
    "byte_order_normalization",
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "valid_pixel_ratio",
    "n_obs",
}
MONTHLY_REQUIRED = {
    "year",
    "month",
    "hemisphere",
    "instrument_epoch",
    "camera",
    "source_format",
    "signal_definition",
    "signal_unit",
    "calibration_status",
    "byte_order_normalization",
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "n_days",
    "polarity_strength",
}
METADATA_COLUMNS = [
    "instrument_epoch",
    "camera",
    "source_format",
    "signal_definition",
    "signal_unit",
    "calibration_status",
    "byte_order_normalization",
]
DAILY_NUMERIC = [
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "valid_pixel_ratio",
    "n_obs",
]
MONTHLY_NUMERIC = [
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "n_days",
    "polarity_strength",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_checksum_manifest(root: Path, manifest: Path) -> int:
    entries = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        path = root / Path(relative.replace("\\", "/"))
        if not path.is_file():
            raise ValueError(f"checksum target is missing: {relative}")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"checksum mismatch for {relative}: expected {expected}, got {actual}"
            )
        entries += 1
    return entries


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _assert_finite(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    values = frame[columns].apply(pd.to_numeric, errors="coerce").to_numpy()
    if not np.isfinite(values).all():
        raise ValueError(f"{label} contains non-finite required numeric values")


def load_and_validate(
    daily_path: Path, monthly_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load and independently validate the daily/monthly product pair."""
    daily = pd.read_csv(daily_path)
    monthly = pd.read_csv(monthly_path)
    if daily.empty or monthly.empty:
        raise ValueError("daily and monthly inputs must both contain data rows")
    _require_columns(daily, DAILY_REQUIRED, "daily input")
    _require_columns(monthly, MONTHLY_REQUIRED, "monthly input")

    daily["date"] = pd.to_datetime(daily["date"], errors="raise")
    monthly["date"] = pd.to_datetime(
        monthly[["year", "month"]].assign(day=1), errors="raise"
    )
    if not daily["hemisphere"].isin(["N", "S"]).all():
        raise ValueError("daily input contains an invalid hemisphere")
    if not monthly["hemisphere"].isin(["N", "S"]).all():
        raise ValueError("monthly input contains an invalid hemisphere")
    if daily.duplicated(["date", "hemisphere"]).any():
        raise ValueError("daily input contains duplicate date/hemisphere keys")
    if monthly.duplicated(["year", "month", "hemisphere"]).any():
        raise ValueError("monthly input contains duplicate year/month/hemisphere keys")

    _assert_finite(daily, DAILY_NUMERIC, "daily input")
    _assert_finite(monthly, MONTHLY_NUMERIC, "monthly input")
    if not daily["valid_pixel_ratio"].between(0, 1, inclusive="both").all():
        raise ValueError("daily valid_pixel_ratio must be in [0, 1]")
    if not (daily["n_obs"] > 0).all() or not (monthly["n_days"] > 0).all():
        raise ValueError("daily n_obs and monthly n_days must be positive")
    if not (daily["field_mean_abs"] > 0).all():
        raise ValueError("daily field_mean_abs must be positive")

    daily_identity_error = (
        daily["field_mean_raw"]
        - daily["field_mean_center"]
        - daily["field_mean_corrected"]
    ).abs()
    monthly_identity_error = (
        monthly["field_mean_raw"]
        - monthly["field_mean_center"]
        - monthly["field_mean_corrected"]
    ).abs()
    polarity_error = (
        monthly["polarity_strength"] - monthly["field_mean_corrected"].abs()
    ).abs()
    tolerance = 1e-9
    if daily_identity_error.max() > tolerance:
        raise ValueError("daily corrected-field identity check failed")
    if monthly_identity_error.max() > tolerance:
        raise ValueError("monthly corrected-field identity check failed")
    if polarity_error.max() > tolerance:
        raise ValueError("monthly polarity_strength identity check failed")

    group_columns = ["year", "month", "hemisphere", *METADATA_COLUMNS]
    daily_for_month = daily.copy()
    daily_for_month["year"] = daily_for_month["date"].dt.year
    daily_for_month["month"] = daily_for_month["date"].dt.month
    recomputed = (
        daily_for_month.groupby(group_columns, dropna=False)
        .agg(
            field_mean_raw=("field_mean_raw", "mean"),
            field_mean_center=("field_mean_center", "mean"),
            field_mean_corrected=("field_mean_corrected", "mean"),
            field_mean_abs=("field_mean_abs", "mean"),
            n_days=("date", "nunique"),
        )
        .reset_index()
    )
    recomputed["polarity_strength"] = recomputed["field_mean_corrected"].abs()
    compare_columns = [*group_columns, *MONTHLY_NUMERIC]
    expected = (
        recomputed[compare_columns].sort_values(group_columns).reset_index(drop=True)
    )
    actual = monthly[compare_columns].sort_values(group_columns).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        actual,
        expected,
        check_dtype=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )

    validation = {
        "daily_rows": len(daily),
        "monthly_rows": len(monthly),
        "daily_duplicate_keys": 0,
        "monthly_duplicate_keys": 0,
        "monthly_reaggregation_matches_daily": True,
        "daily_identity_max_abs_error": float(daily_identity_error.max()),
        "monthly_identity_max_abs_error": float(monthly_identity_error.max()),
        "polarity_identity_max_abs_error": float(polarity_error.max()),
    }
    return daily, monthly, validation


def _robust_z(values: pd.Series, min_group_size: int) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    finite = values[np.isfinite(values)]
    if len(finite) < min_group_size:
        return result
    median = float(finite.median())
    mad = float((finite - median).abs().median())
    if mad > 0:
        result.loc[finite.index] = 0.67448975 * (finite - median) / mad
        return result
    q25, q75 = finite.quantile([0.25, 0.75])
    iqr = float(q75 - q25)
    scale = iqr / 1.3489795 if iqr > 0 else float((finite - median).abs().mean())
    if scale > 0:
        result.loc[finite.index] = (finite - median) / scale
    else:
        result.loc[finite.index] = 0.0
    return result


def add_within_epoch_features(
    frame: pd.DataFrame, *, min_group_size: int, outlier_threshold: float
) -> pd.DataFrame:
    """Add scale-safe standardized features without mixing instrument epochs."""
    result = frame.copy()
    groups = result.groupby(
        ["instrument_epoch", "signal_unit", "hemisphere"], dropna=False
    )
    result["field_abs_robust_z_epoch"] = groups["field_mean_abs"].transform(
        lambda values: _robust_z(values, min_group_size)
    )
    result["field_corrected_robust_z_epoch"] = groups["field_mean_corrected"].transform(
        lambda values: _robust_z(values, min_group_size)
    )
    result["field_abs_outlier_epoch"] = (
        result["field_abs_robust_z_epoch"].abs() >= outlier_threshold
    )
    result["field_corrected_outlier_epoch"] = (
        result["field_corrected_robust_z_epoch"].abs() >= outlier_threshold
    )
    result["signed_to_abs_ratio"] = (
        result["field_mean_corrected"].abs() / result["field_mean_abs"]
    )
    if "n_days" in result.columns and "date" in result.columns:
        result["calendar_days_in_month"] = result["date"].dt.days_in_month
        result["observed_day_fraction"] = (
            result["n_days"] / result["calendar_days_in_month"]
        )
    return result


def build_monthly_pair_features(monthly: pd.DataFrame) -> pd.DataFrame:
    """Build north/south pair features only when epoch and unit are comparable."""
    columns = [
        "date",
        "year",
        "month",
        "instrument_epoch",
        "signal_unit",
        "field_mean_abs",
        "field_mean_corrected",
        "n_days",
        "field_abs_robust_z_epoch",
        "field_corrected_robust_z_epoch",
    ]
    north = monthly.loc[monthly["hemisphere"] == "N", columns].add_suffix("_n")
    south = monthly.loc[monthly["hemisphere"] == "S", columns].add_suffix("_s")
    pairs = north.merge(
        south,
        left_on=["year_n", "month_n"],
        right_on=["year_s", "month_s"],
        how="outer",
        validate="one_to_one",
    )
    pairs["year"] = pairs["year_n"].fillna(pairs["year_s"]).astype(int)
    pairs["month"] = pairs["month_n"].fillna(pairs["month_s"]).astype(int)
    pairs["date"] = pd.to_datetime(pairs[["year", "month"]].assign(day=1))
    pairs["has_north"] = pairs["year_n"].notna()
    pairs["has_south"] = pairs["year_s"].notna()
    pairs["pair_same_epoch_unit"] = (
        pairs["has_north"]
        & pairs["has_south"]
        & (pairs["instrument_epoch_n"] == pairs["instrument_epoch_s"])
        & (pairs["signal_unit_n"] == pairs["signal_unit_s"])
    )
    comparable = pairs["pair_same_epoch_unit"]
    denominator = pairs["field_mean_abs_n"] + pairs["field_mean_abs_s"]
    pairs["field_abs_pair_mean"] = np.where(
        comparable,
        (pairs["field_mean_abs_n"] + pairs["field_mean_abs_s"]) / 2,
        np.nan,
    )
    pairs["field_abs_asymmetry_ns"] = np.where(
        comparable & (denominator > 0),
        (pairs["field_mean_abs_n"] - pairs["field_mean_abs_s"]) / denominator,
        np.nan,
    )
    pairs["field_abs_asymmetry_magnitude"] = pairs["field_abs_asymmetry_ns"].abs()
    pairs["field_abs_robust_z_pair_mean"] = np.where(
        comparable,
        (pairs["field_abs_robust_z_epoch_n"] + pairs["field_abs_robust_z_epoch_s"]) / 2,
        np.nan,
    )
    pairs["signed_opposite_sign_diagnostic"] = np.where(
        comparable,
        np.sign(pairs["field_mean_corrected_n"])
        != np.sign(pairs["field_mean_corrected_s"]),
        pd.NA,
    )
    return pairs.sort_values("date").reset_index(drop=True)


def build_annual_features(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    frame["year"] = frame["date"].dt.year
    return (
        frame.groupby(
            ["year", "hemisphere", "instrument_epoch", "signal_unit"],
            dropna=False,
        )
        .agg(
            first_date=("date", "min"),
            last_date=("date", "max"),
            observed_days=("date", "nunique"),
            observed_months=("date", lambda values: values.dt.month.nunique()),
            total_observations=("n_obs", "sum"),
            valid_pixel_ratio_median=("valid_pixel_ratio", "median"),
            field_abs_mean=("field_mean_abs", "mean"),
            field_abs_median=("field_mean_abs", "median"),
            field_abs_std=("field_mean_abs", "std"),
            field_abs_q05=("field_mean_abs", lambda values: values.quantile(0.05)),
            field_abs_q95=("field_mean_abs", lambda values: values.quantile(0.95)),
            field_corrected_mean=("field_mean_corrected", "mean"),
            field_corrected_std=("field_mean_corrected", "std"),
        )
        .reset_index()
        .sort_values(["year", "hemisphere", "instrument_epoch"])
    )


def build_epoch_features(daily: pd.DataFrame, monthly: pd.DataFrame) -> pd.DataFrame:
    keys = ["instrument_epoch", "signal_unit", "hemisphere"]
    daily_summary = (
        daily.groupby(keys, dropna=False)
        .agg(
            first_date=("date", "min"),
            last_date=("date", "max"),
            daily_rows=("date", "size"),
            observed_days=("date", "nunique"),
            total_observations=("n_obs", "sum"),
            valid_pixel_ratio_median=("valid_pixel_ratio", "median"),
            field_abs_daily_median=("field_mean_abs", "median"),
            field_abs_daily_mean=("field_mean_abs", "mean"),
            field_abs_daily_std=("field_mean_abs", "std"),
        )
        .reset_index()
    )
    monthly_summary = (
        monthly.groupby(keys, dropna=False)
        .agg(
            monthly_rows=("date", "size"),
            observed_months=("date", "nunique"),
            field_abs_monthly_median=("field_mean_abs", "median"),
            field_abs_monthly_mean=("field_mean_abs", "mean"),
            field_abs_monthly_std=("field_mean_abs", "std"),
            abs_outlier_months=("field_abs_outlier_epoch", "sum"),
            corrected_outlier_months=("field_corrected_outlier_epoch", "sum"),
        )
        .reset_index()
    )
    return daily_summary.merge(monthly_summary, on=keys, how="outer").sort_values(keys)


def build_seasonal_features(monthly: pd.DataFrame) -> pd.DataFrame:
    return (
        monthly.groupby(
            ["instrument_epoch", "signal_unit", "hemisphere", "month"],
            dropna=False,
        )
        .agg(
            samples=("date", "size"),
            field_abs_median=("field_mean_abs", "median"),
            field_abs_mean=("field_mean_abs", "mean"),
            field_abs_robust_z_median=("field_abs_robust_z_epoch", "median"),
            field_corrected_median=("field_mean_corrected", "median"),
        )
        .reset_index()
        .sort_values(["instrument_epoch", "hemisphere", "month"])
    )


def build_pair_summary(pairs: pd.DataFrame) -> pd.DataFrame:
    usable = pairs[pairs["pair_same_epoch_unit"]].copy()
    rows = []
    for (epoch, unit), group in usable.groupby(
        ["instrument_epoch_n", "signal_unit_n"], dropna=False
    ):
        enough_pairs = len(group) >= 2
        north_varies = group["field_mean_abs_n"].nunique(dropna=True) >= 2
        south_varies = group["field_mean_abs_s"].nunique(dropna=True) >= 2
        can_correlate = enough_pairs and north_varies and south_varies
        rows.append(
            {
                "instrument_epoch": epoch,
                "signal_unit": unit,
                "paired_months": len(group),
                "field_abs_pearson_ns": (
                    group["field_mean_abs_n"].corr(
                        group["field_mean_abs_s"], method="pearson"
                    )
                    if can_correlate
                    else np.nan
                ),
                "field_abs_spearman_ns": (
                    group["field_mean_abs_n"].corr(
                        group["field_mean_abs_s"], method="spearman"
                    )
                    if can_correlate
                    else np.nan
                ),
                "asymmetry_median": group["field_abs_asymmetry_ns"].median(),
                "asymmetry_abs_median": group["field_abs_asymmetry_magnitude"].median(),
                "opposite_signed_fraction_diagnostic": pd.to_numeric(
                    group["signed_opposite_sign_diagnostic"], errors="coerce"
                ).mean(),
            }
        )
    columns = [
        "instrument_epoch",
        "signal_unit",
        "paired_months",
        "field_abs_pearson_ns",
        "field_abs_spearman_ns",
        "asymmetry_median",
        "asymmetry_abs_median",
        "opposite_signed_fraction_diagnostic",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values("instrument_epoch")


def _missing_month_runs(monthly: pd.DataFrame) -> list[dict]:
    start = monthly["date"].min().to_period("M")
    end = monthly["date"].max().to_period("M")
    full = pd.period_range(start, end, freq="M")
    runs = []
    for hemisphere in ("N", "S"):
        observed = set(
            monthly.loc[monthly["hemisphere"] == hemisphere, "date"].dt.to_period("M")
        )
        missing = [period for period in full if period not in observed]
        if not missing:
            continue
        run_start = run_end = missing[0]
        for period in missing[1:]:
            if period.ordinal == run_end.ordinal + 1:
                run_end = period
                continue
            runs.append(
                {
                    "hemisphere": hemisphere,
                    "start": str(run_start),
                    "end": str(run_end),
                    "months": run_end.ordinal - run_start.ordinal + 1,
                }
            )
            run_start = run_end = period
        runs.append(
            {
                "hemisphere": hemisphere,
                "start": str(run_start),
                "end": str(run_end),
                "months": run_end.ordinal - run_start.ordinal + 1,
            }
        )
    return sorted(runs, key=lambda row: (-row["months"], row["hemisphere"]))


def _write_plot(monthly: pd.DataFrame, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    colors = {"N": "#1f77b4", "S": "#d62728"}
    for hemisphere in ("N", "S"):
        group = monthly[monthly["hemisphere"] == hemisphere]
        axes[0].scatter(
            group["date"],
            group["field_abs_robust_z_epoch"],
            s=14,
            alpha=0.8,
            color=colors[hemisphere],
            label=hemisphere,
        )
        axes[1].scatter(
            group["date"],
            group["n_days"],
            s=14,
            alpha=0.8,
            color=colors[hemisphere],
            label=hemisphere,
        )
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_ylabel("within-epoch robust z\n(field_mean_abs)")
    axes[1].set_ylabel("observed days / month")
    axes[1].set_xlabel("date")
    for axis in axes:
        axis.grid(alpha=0.2)
        axis.legend(title="hemisphere", ncol=2)
    figure.suptitle("Huairou SMFT polar diagnostic features (not cross-calibrated)")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def analyze(
    daily_path: Path,
    monthly_path: Path,
    output_dir: Path,
    *,
    run_summary_path: Path | None = None,
    outlier_threshold: float = 5.0,
    min_outlier_group_size: int = 6,
    write_plot: bool = True,
) -> dict:
    if outlier_threshold <= 0:
        raise ValueError("outlier_threshold must be positive")
    if min_outlier_group_size < 3:
        raise ValueError("min_outlier_group_size must be at least 3")
    daily, monthly, validation = load_and_validate(daily_path, monthly_path)
    daily_features = add_within_epoch_features(
        daily,
        min_group_size=min_outlier_group_size,
        outlier_threshold=outlier_threshold,
    )
    monthly_features = add_within_epoch_features(
        monthly,
        min_group_size=min_outlier_group_size,
        outlier_threshold=outlier_threshold,
    )
    pairs = build_monthly_pair_features(monthly_features)
    annual = build_annual_features(daily_features)
    epochs = build_epoch_features(daily_features, monthly_features)
    seasonal = build_seasonal_features(monthly_features)
    pair_summary = build_pair_summary(pairs)
    daily_outliers = daily_features[
        daily_features["field_abs_outlier_epoch"]
        | daily_features["field_corrected_outlier_epoch"]
    ].copy()
    monthly_outliers = monthly_features[
        monthly_features["field_abs_outlier_epoch"]
        | monthly_features["field_corrected_outlier_epoch"]
    ].copy()

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "daily_features": output_dir / "huairou_daily_features.csv",
        "monthly_features": output_dir / "huairou_monthly_features.csv",
        "monthly_pair_features": output_dir / "huairou_monthly_pair_features.csv",
        "annual_features": output_dir / "huairou_annual_features.csv",
        "epoch_features": output_dir / "huairou_epoch_features.csv",
        "seasonal_features": output_dir / "huairou_seasonal_features.csv",
        "hemisphere_pair_summary": output_dir / "huairou_hemisphere_pair_summary.csv",
        "daily_outliers": output_dir / "huairou_daily_outliers.csv",
        "monthly_outliers": output_dir / "huairou_monthly_outliers.csv",
    }
    frames = {
        "daily_features": daily_features,
        "monthly_features": monthly_features,
        "monthly_pair_features": pairs,
        "annual_features": annual,
        "epoch_features": epochs,
        "seasonal_features": seasonal,
        "hemisphere_pair_summary": pair_summary,
        "daily_outliers": daily_outliers,
        "monthly_outliers": monthly_outliers,
    }
    for name, frame in frames.items():
        frame.to_csv(outputs[name], index=False, date_format="%Y-%m-%d")

    missing_runs = _missing_month_runs(monthly_features)
    run_summary = None
    server_artifact_validation = None
    if run_summary_path is not None:
        run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
        run_root = run_summary_path.parent
        checksum_manifest = run_root / "checksums.sha256"
        checksum_entries = (
            _verify_checksum_manifest(run_root, checksum_manifest)
            if checksum_manifest.is_file()
            else None
        )
        error_records = sum(
            1
            for path in (run_root / "artifacts").glob("huairou_*_errors.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        expected_errors = sum(
            row["processing_errors"] for row in run_summary["year_results"]
        )
        if error_records != expected_errors:
            raise ValueError(
                "server error logs do not match run_summary: "
                f"records={error_records}, summary={expected_errors}"
            )
        server_artifact_validation = {
            "checksum_manifest": str(checksum_manifest),
            "checksum_entries_verified": checksum_entries,
            "processing_error_records_verified": error_records,
        }

    summary = {
        "product_status": (
            run_summary.get("product_status", "diagnostic_unvalidated")
            if run_summary
            else "diagnostic_unvalidated"
        ),
        "interpretation_boundary": (
            "Raw values are comparable only within a shared instrument_epoch and "
            "signal_unit. Robust z scores are diagnostic normalizations, not a "
            "physical cross-calibration."
        ),
        "inputs": {
            "daily": str(daily_path),
            "daily_sha256": _sha256(daily_path),
            "monthly": str(monthly_path),
            "monthly_sha256": _sha256(monthly_path),
            "run_summary": str(run_summary_path) if run_summary_path else None,
            "run_summary_sha256": (
                _sha256(run_summary_path) if run_summary_path else None
            ),
        },
        "validation": validation,
        "coverage": {
            "first_daily_date": daily["date"].min().strftime("%Y-%m-%d"),
            "last_daily_date": daily["date"].max().strftime("%Y-%m-%d"),
            "years_with_data": sorted(daily["date"].dt.year.unique().tolist()),
            "years_without_polar_output_inside_range": [
                year
                for year in range(
                    daily["date"].dt.year.min(), daily["date"].dt.year.max() + 1
                )
                if year not in set(daily["date"].dt.year)
            ],
            "monthly_rows_north": int((monthly["hemisphere"] == "N").sum()),
            "monthly_rows_south": int((monthly["hemisphere"] == "S").sum()),
            "paired_months": int((pairs["has_north"] & pairs["has_south"]).sum()),
            "comparable_paired_months": int(pairs["pair_same_epoch_unit"].sum()),
            "longest_missing_month_runs": missing_runs[:10],
        },
        "epochs": sorted(monthly["instrument_epoch"].unique().tolist()),
        "features": {
            "outlier_threshold_abs_robust_z": outlier_threshold,
            "minimum_group_size_for_outliers": min_outlier_group_size,
            "daily_outlier_rows": len(daily_outliers),
            "monthly_outlier_rows": len(monthly_outliers),
            "monthly_observed_day_fraction_median": float(
                monthly_features["observed_day_fraction"].median()
            ),
            "single_observation_day_months": int((monthly["n_days"] == 1).sum()),
            "median_comparable_pair_abs_asymmetry": float(
                pairs.loc[
                    pairs["pair_same_epoch_unit"],
                    "field_abs_asymmetry_magnitude",
                ].median()
            ),
        },
        "server_processing": (
            {
                "supported_files": run_summary["inventory"]["supported_files"],
                "unsupported_files": run_summary["inventory"]["unsupported_files"],
                "read_error_files": run_summary["inventory"]["read_error_files"],
                "processing_errors": sum(
                    row["processing_errors"] for row in run_summary["year_results"]
                ),
                "empty_polar_years": run_summary["inventory"].get(
                    "empty_polar_years", run_summary["inventory"]["empty_years"]
                ),
                "artifact_validation": server_artifact_validation,
            }
            if run_summary
            else None
        ),
        "outputs": {name: str(path) for name, path in outputs.items()},
        "wiki_grounding": [
            "kb_data_source_huairou_smft_polar_1987_2001",
            "kb_concept_polar_field_observable_001",
            "kb_mechanism_hemispheric_coupling_001",
        ],
    }
    summary_path = output_dir / "huairou_feature_analysis_summary.json"
    plot_path = output_dir / "huairou_feature_diagnostics.png"
    checksum_path = output_dir / "analysis_checksums.sha256"
    if write_plot:
        _write_plot(monthly_features, plot_path)
        summary["outputs"]["diagnostic_plot"] = str(plot_path)
    summary["outputs"]["summary"] = str(summary_path)
    summary["outputs"]["checksums"] = str(checksum_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksum_targets = [*outputs.values(), summary_path]
    if write_plot:
        checksum_targets.append(plot_path)
    checksum_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(output_dir).as_posix()}\n"
            for path in sorted(checksum_targets)
        ),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Huairou daily/monthly products and derive features"
    )
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--monthly", type=Path, required=True)
    parser.add_argument("--run-summary", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--outlier-threshold", type=float, default=5.0)
    parser.add_argument("--min-outlier-group-size", type=int, default=6)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    if args.outlier_threshold <= 0:
        parser.error("--outlier-threshold must be positive")
    if args.min_outlier_group_size < 3:
        parser.error("--min-outlier-group-size must be at least 3")

    summary = analyze(
        args.daily,
        args.monthly,
        args.output_dir,
        run_summary_path=args.run_summary,
        outlier_threshold=args.outlier_threshold,
        min_outlier_group_size=args.min_outlier_group_size,
        write_plot=not args.no_plot,
    )
    print(
        json.dumps(
            {
                "product_status": summary["product_status"],
                "validation": summary["validation"],
                "coverage": summary["coverage"],
                "features": summary["features"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = (
    ROOT
    / "jw/subagents/solar/skills/solar-cycle/scripts/analyze_polar_huairou_features.py"
)
SPEC = importlib.util.spec_from_file_location("analyze_polar_huairou_features", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for month in range(1, 9):
        for hemisphere, offset in (("N", 0.0), ("S", 0.5)):
            raw = float(month + offset + 10)
            center = 10.0
            corrected = raw - center
            rows.append(
                {
                    "date": f"2020-{month:02d}-01",
                    "hemisphere": hemisphere,
                    "instrument_epoch": "epoch-a",
                    "camera": "camera-a",
                    "source_format": "fits",
                    "signal_definition": "diagnostic",
                    "signal_unit": "proxy",
                    "calibration_status": "unvalidated",
                    "byte_order_normalization": "fits-standard-byte-order",
                    "field_mean_raw": raw,
                    "field_mean_center": center,
                    "field_mean_corrected": corrected,
                    "field_mean_abs": abs(corrected) + 1,
                    "valid_pixel_ratio": 0.99,
                    "n_obs": 2,
                }
            )
    daily = pd.DataFrame(rows)
    monthly = daily.copy()
    monthly["date"] = pd.to_datetime(monthly["date"])
    monthly["year"] = monthly["date"].dt.year
    monthly["month"] = monthly["date"].dt.month
    monthly["n_days"] = 1
    monthly["polarity_strength"] = monthly["field_mean_corrected"].abs()
    monthly = monthly[
        [
            "year",
            "month",
            "hemisphere",
            *analysis.METADATA_COLUMNS,
            "field_mean_raw",
            "field_mean_center",
            "field_mean_corrected",
            "field_mean_abs",
            "n_days",
            "polarity_strength",
        ]
    ]
    return daily, monthly


def test_load_and_validate_reaggregates_monthly(tmp_path: Path):
    daily, monthly = _frames()
    daily_path = tmp_path / "daily.csv"
    monthly_path = tmp_path / "monthly.csv"
    daily.to_csv(daily_path, index=False)
    monthly.to_csv(monthly_path, index=False)

    _, _, validation = analysis.load_and_validate(daily_path, monthly_path)

    assert validation["monthly_reaggregation_matches_daily"] is True
    assert validation["daily_rows"] == 16
    assert validation["monthly_rows"] == 16


def test_load_and_validate_rejects_duplicate_key(tmp_path: Path):
    daily, monthly = _frames()
    daily = pd.concat([daily, daily.iloc[[0]]], ignore_index=True)
    daily_path = tmp_path / "daily.csv"
    monthly_path = tmp_path / "monthly.csv"
    daily.to_csv(daily_path, index=False)
    monthly.to_csv(monthly_path, index=False)

    with pytest.raises(ValueError, match="duplicate date/hemisphere"):
        analysis.load_and_validate(daily_path, monthly_path)


def test_pair_features_require_matching_epoch_and_unit():
    _, monthly = _frames()
    monthly["date"] = pd.to_datetime(monthly[["year", "month"]].assign(day=1))
    monthly = analysis.add_within_epoch_features(
        monthly, min_group_size=6, outlier_threshold=5
    )
    monthly.loc[
        (monthly["month"] == 8) & (monthly["hemisphere"] == "S"),
        "instrument_epoch",
    ] = "epoch-b"

    pairs = analysis.build_monthly_pair_features(monthly)

    comparable = pairs.loc[pairs["month"] == 1].iloc[0]
    mixed = pairs.loc[pairs["month"] == 8].iloc[0]
    assert bool(comparable["pair_same_epoch_unit"]) is True
    assert pd.notna(comparable["field_abs_asymmetry_ns"])
    assert bool(mixed["pair_same_epoch_unit"]) is False
    assert pd.isna(mixed["field_abs_asymmetry_ns"])


def test_robust_z_fallback_stays_finite_when_mad_is_zero():
    values = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 10.0])

    result = analysis._robust_z(values, min_group_size=6)

    assert result.notna().all()
    assert result.map(
        lambda value: bool(pd.notna(value) and abs(value) < float("inf"))
    ).all()
    assert result.iloc[-1] > 5


def test_checksum_manifest_detects_tampering(tmp_path: Path):
    data = tmp_path / "data.csv"
    data.write_text("value\n1\n", encoding="utf-8")
    manifest = tmp_path / "checksums.sha256"
    manifest.write_text(f"{analysis._sha256(data)}  data.csv\n", encoding="utf-8")
    assert analysis._verify_checksum_manifest(tmp_path, manifest) == 1

    data.write_text("value\n2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        analysis._verify_checksum_manifest(tmp_path, manifest)


def test_analyze_writes_reproducible_feature_artifacts(tmp_path: Path):
    daily, monthly = _frames()
    daily_path = tmp_path / "daily.csv"
    monthly_path = tmp_path / "monthly.csv"
    output_dir = tmp_path / "analysis"
    daily.to_csv(daily_path, index=False)
    monthly.to_csv(monthly_path, index=False)

    summary = analysis.analyze(
        daily_path,
        monthly_path,
        output_dir,
        write_plot=False,
    )

    assert summary["product_status"] == "diagnostic_unvalidated"
    assert summary["coverage"]["paired_months"] == 8
    assert summary["coverage"]["comparable_paired_months"] == 8
    assert summary["features"]["single_observation_day_months"] == 16
    assert (output_dir / "huairou_monthly_pair_features.csv").is_file()
    assert (output_dir / "analysis_checksums.sha256").is_file()
    saved = json.loads(
        (output_dir / "huairou_feature_analysis_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["inputs"]["daily_sha256"] == analysis._sha256(daily_path)

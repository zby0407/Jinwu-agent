from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from astropy.io import fits

ROOT = Path(__file__).parents[1]
SCRIPT_DIR = ROOT / "jw/subagents/solar/skills/solar-cycle/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
runner = importlib.import_module("run_polar_huairou_server")
loader = runner.loader


def _write_polar(path: Path, hemisphere: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plane0 = np.full((992, 992), 1000, dtype=np.int32)
    plane1 = np.full((992, 992), 900, dtype=np.int32)
    y, x = np.ogrid[:992, :992]
    circle = (y - 496) ** 2 + (x - 496) ** 2 <= 150**2
    plane0[circle] = 1200
    hdu = fits.PrimaryHDU(np.stack([plane0, plane1]))
    hdu.header["CAMERA"] = "IMPERX 1M48"
    hdu.header["HSOS_NO"] = f"26{hemisphere.lower()}pl"
    hdu.header["T_START"] = "2026-1-8 5:0:23"
    hdu.header["CALIBRAT"] = 10000
    hdu.writeto(path)


def _historical_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = pd.DataFrame(
        [
            {
                "date": "2013-01-01",
                "hemisphere": "N",
                "instrument_epoch": "legacy",
                "camera": "legacy",
                "source_format": "dat-int16-le",
                "signal_definition": "stored-longitudinal-image",
                "signal_unit": "detector_count_proxy",
                "calibration_status": "uncalibrated",
                "byte_order_normalization": "legacy-little-endian",
                "field_mean_raw": 1.0,
                "field_mean_center": 0.0,
                "field_mean_corrected": 1.0,
                "field_mean_abs": 1.0,
                "valid_pixel_ratio": 1.0,
                "n_obs": 1,
            },
            {
                "date": "2026-01-01",
                "hemisphere": "N",
                "instrument_epoch": "old-overlap",
                "camera": "old",
                "source_format": "fits-bitpix32",
                "signal_definition": "old",
                "signal_unit": "old",
                "calibration_status": "old",
                "byte_order_normalization": "old",
                "field_mean_raw": 99.0,
                "field_mean_center": 0.0,
                "field_mean_corrected": 99.0,
                "field_mean_abs": 99.0,
                "valid_pixel_ratio": 1.0,
                "n_obs": 1,
            },
        ],
        columns=loader.DAILY_COLUMNS,
    )
    monthly = pd.DataFrame(
        [
            {
                "year": 2013,
                "month": 1,
                "hemisphere": "N",
                "instrument_epoch": "legacy",
                "camera": "legacy",
                "source_format": "dat-int16-le",
                "signal_definition": "stored-longitudinal-image",
                "signal_unit": "detector_count_proxy",
                "calibration_status": "uncalibrated",
                "byte_order_normalization": "legacy-little-endian",
                "field_mean_raw": 1.0,
                "field_mean_center": 0.0,
                "field_mean_corrected": 1.0,
                "field_mean_abs": 1.0,
                "n_days": 1,
                "polarity_strength": 1.0,
            }
        ],
        columns=loader.MONTHLY_COLUMNS,
    )
    return daily, monthly


def test_server_batch_routes_imperx_and_replaces_overlap(tmp_path: Path):
    archive = tmp_path / "archive"
    _write_polar(archive / "2026" / "20260108" / "L526npl260108050054.fit", "N")
    _write_polar(archive / "2026" / "20260108" / "L526spl260108052013.fit", "S")
    historical_daily, historical_monthly = _historical_frames()
    daily_path = tmp_path / "historical_daily.csv"
    monthly_path = tmp_path / "historical_monthly.csv"
    historical_daily.to_csv(daily_path, index=False)
    historical_monthly.to_csv(monthly_path, index=False)
    output = tmp_path / "run"
    args = argparse.Namespace(
        polar_dir=archive,
        start_year=2026,
        end_year=2026,
        workers=1,
        output_root=output,
        fit_signal="calibrated_vi",
        fit_aperture_mode="center-circle",
        fit_center_radius=150,
        allow_unvalidated_geometry=True,
        historical_daily=daily_path,
        historical_monthly=monthly_path,
    )

    summary = runner.run_batch(args)

    assert summary["product_status"] == "diagnostic_unvalidated"
    assert summary["inventory"]["unsupported_files"] == 0
    assert summary["new_rows"] == {"daily": 2, "monthly": 2}
    combined = pd.read_csv(
        output / "data" / "huairou_polar_precursor_1987_2026_daily.csv"
    )
    assert set(combined["date"]) == {"2013-01-01", "2026-01-08"}
    assert "old-overlap" not in set(combined["instrument_epoch"])
    assert set(combined.loc[combined["date"] == "2026-01-08", "instrument_epoch"]) == {
        "imperx_fit32_2018_2026"
    }
    assert (
        json.loads((output / "run_summary.json").read_text(encoding="utf-8"))[
            "product_status"
        ]
        == "diagnostic_unvalidated"
    )
    assert (output / "checksums.sha256").is_file()

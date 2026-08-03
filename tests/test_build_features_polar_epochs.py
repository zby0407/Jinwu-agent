from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "jw/subagents/solar/skills/solar-cycle/scripts/build_features.py"
SPEC = importlib.util.spec_from_file_location("build_features", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
build_features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_features)


def _row(date: str, hemisphere: str, epoch: str, unit: str, value: float) -> dict:
    return {
        "date": pd.Timestamp(date),
        "hemisphere": hemisphere,
        "instrument_epoch": epoch,
        "signal_unit": unit,
        "signal_definition": f"signal-{epoch}",
        "field_mean_abs": value,
        "field_mean_corrected": value / 10,
    }


def test_mixed_window_selects_epoch_with_most_month_coverage():
    rows = []
    for month in range(1, 5):
        for hemisphere in ("N", "S"):
            rows.append(
                _row(f"2008-{month:02d}-01", hemisphere, "dominant", "counts", 10)
            )
    for month in range(5, 7):
        for hemisphere in ("N", "S"):
            rows.append(
                _row(f"2008-{month:02d}-01", hemisphere, "other", "gauss", 1000)
            )

    result = build_features._polar_proxy_in_window(
        pd.DataFrame(rows), pd.Timestamp("2008-04-01"), 12, 3
    )
    assert result["polar_proxy_abs_n"] == 10
    assert result["polar_proxy_abs_s"] == 10
    assert result["polar_instrument_epoch"] == "dominant"
    assert result["polar_signal_unit"] == "counts"
    assert result["polar_epoch_selection"] == "dominant_month_coverage"
    assert result["polar_n_epochs_window"] == 2
    assert result["polar_excluded_mixed_rows"] == 4
    assert result["polar_epoch_mixed_window"] is True


def test_legacy_table_without_provenance_remains_supported():
    frame = pd.DataFrame(
        [
            {
                "date": pd.Timestamp(f"1996-{month:02d}-01"),
                "hemisphere": hemisphere,
                "field_mean_abs": 5.0,
                "field_mean_corrected": -1.0,
            }
            for month in range(1, 4)
            for hemisphere in ("N", "S")
        ]
    )
    result = build_features._polar_proxy_in_window(
        frame, pd.Timestamp("1996-02-01"), 12, 3
    )
    assert result["polar_proxy_abs_combined"] == 5
    assert result["polar_instrument_epoch"] == "unknown"
    assert result["polar_epoch_selection"] == "single_epoch"

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build_solar_cycle_asof_features.py"
SPEC = importlib.util.spec_from_file_location("build_solar_cycle_asof_features", SCRIPT)
assert SPEC
assert SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _monthly(start: str, periods: int) -> pd.DataFrame:
    dates = pd.date_range(start, periods=periods, freq="MS")
    return pd.DataFrame({"date": dates, "sn": np.arange(periods, dtype=float)})


def test_cycle_selector_supports_ranges_and_rejects_descending_ranges() -> None:
    assert MODULE.parse_cycle_selector("1-3,5") == [1, 2, 3, 5]
    with pytest.raises(ValueError, match="descending"):
        MODULE.parse_cycle_selector("3-1")


def test_asof_feature_uses_only_declared_early_months() -> None:
    monthly = _monthly("1990-01-01", 180)
    smoothed = monthly.copy()
    cycles = {
        1: MODULE.OfficialCycle(
            1,
            MODULE.Extremum(pd.Timestamp("1995-01-01"), 60.0),
            MODULE.Extremum(pd.Timestamp("1998-01-01"), 120.0),
        )
    }

    result = MODULE.build_asof_features(monthly, smoothed, cycles, [1])
    row = result.iloc[0]

    assert row["feature_start_date"] == "1995-08-01"
    assert row["feature_end_date"] == "1997-01-01"
    assert row["issue_date"] == "1997-01-01"
    assert row["feature_month_count"] == 18
    assert row["future_input_count"] == 0
    assert bool(row["target_after_issue"]) is True
    assert row["rise_rate_monthly_7_24"] == pytest.approx(1.0)


def test_official_minimum_can_be_one_of_multiple_asof_candidates() -> None:
    monthly = _monthly("1990-01-01", 180)
    smoothed = monthly.copy()
    smoothed.loc[
        smoothed["date"].isin([pd.Timestamp("1995-01-01"), pd.Timestamp("1995-03-01")]),
        "sn",
    ] = -5.0
    cycles = {
        1: MODULE.OfficialCycle(
            1,
            MODULE.Extremum(pd.Timestamp("1995-03-01"), -5.0),
            MODULE.Extremum(pd.Timestamp("1998-01-01"), 120.0),
        )
    }

    result = MODULE.build_asof_features(monthly, smoothed, cycles, [1])

    assert bool(result.loc[0, "official_minimum_is_asof_candidate"]) is True
    assert result.loc[0, "asof_minimum_candidate_dates"] == ("1995-01-01|1995-03-01")


def test_missing_feature_month_stops_the_build() -> None:
    monthly = _monthly("1990-01-01", 180)
    monthly = monthly[monthly["date"] != pd.Timestamp("1996-01-01")]
    smoothed = _monthly("1990-01-01", 180)
    cycles = {
        1: MODULE.OfficialCycle(
            1,
            MODULE.Extremum(pd.Timestamp("1995-01-01"), 60.0),
            MODULE.Extremum(pd.Timestamp("1998-01-01"), 120.0),
        )
    }

    with pytest.raises(ValueError, match="missing feature months"):
        MODULE.build_asof_features(monthly, smoothed, cycles, [1])


def test_monthly_loader_accepts_current_silso_six_column_text_with_marker(tmp_path: Path) -> None:
    path = tmp_path / "SN_m_tot_V2.0.txt"
    path.write_text(
        "1749 01 1749.042   96.7  -1.0    -1\n"
        "2026 07 2026.538   78.1  11.2  1371 *\n",
        encoding="utf-8",
    )

    result = MODULE.load_monthly_total(path)

    assert result["date"].dt.strftime("%Y-%m").tolist() == ["1749-01", "2026-07"]
    assert result["sn"].tolist() == pytest.approx([96.7, 78.1])

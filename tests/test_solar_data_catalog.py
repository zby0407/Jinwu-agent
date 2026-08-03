from __future__ import annotations

import math

import pytest

from jw.solar_data_catalog import _validate_polar_field, _validate_silso_monthly
from jw.tools.solar_feature import _build_solar_precursor_cycle_rows


def test_silso_validator_requires_long_monotonic_monthly_record() -> None:
    rows = []
    year, month = 1749, 1
    for _ in range(3_312):
        rows.append(f"{year} {month:02d} {year + (month - 0.5) / 12:.3f} 1.0 0.1 1")
        month += 1
        if month == 13:
            year += 1
            month = 1
    result = _validate_silso_monthly(("\n".join(rows) + "\n").encode())

    assert result["row_count"] == 3_312
    assert result["coverage_start"] == "1749-01"
    assert result["coverage_end"] == "2024-12"


def test_silso_validator_rejects_short_or_duplicate_record() -> None:
    with pytest.raises(ValueError, match="coverage is too short"):
        _validate_silso_monthly(b"1749 01 1749.042 1.0 0.1 1\n")


def test_polar_validator_binds_columns_and_century_coverage() -> None:
    rows = []
    for year in range(1906, 2024):
        rows.extend(
            [
                f"{year}.7,1,0.1,NaN,NaN,NaN,{year}.2,-1,0.1,NaN,NaN,NaN",
                f"{year}.8,1,0.1,NaN,NaN,NaN,{year}.3,-1,0.1,NaN,NaN,NaN",
            ]
        )
    header = ",".join(
        [
            "N MWO Date",
            "N MWO PField",
            "N MWO SEM",
            "N WSO Date",
            "N WSO PField",
            "N WSO SEM",
            "S MWO Date",
            "S MWO PField",
            "S MWO SEM",
            "S WSO Date",
            "S WSO PField",
            "S WSO SEM",
        ]
    )
    result = _validate_polar_field(
        ("# header\n" + header + "\n" + "\n".join(rows)).encode()
    )

    assert result["columns"] == 12
    assert result["north_coverage"] == [1906.7, 2023.8]


def test_precursor_cycle_builder_uses_complete_cycles_and_preminimum_fields(
    tmp_path
) -> None:
    monthly = []
    for year in range(1749, 2026):
        for month in range(1, 13):
            decimal = year + (month - 0.5) / 12
            value = 5 + 100 * (1 - math.cos(2 * math.pi * (decimal - 1902) / 11))
            monthly.append(
                f"{year} {month:02d} {decimal:.3f} {value:.4f} 1.0 10"
            )
    sunspot = tmp_path / "silso.txt"
    sunspot.write_text("\n".join(monthly) + "\n", encoding="ascii")

    header = (
        "N MWO Date,N MWO PField,N MWO SEM,N WSO Date,N WSO PField,N WSO SEM,"
        "S MWO Date,S MWO PField,S MWO SEM,S WSO Date,S WSO PField,S WSO SEM"
    )
    polar_rows = [header]
    for year in range(1900, 2025):
        polar_rows.append(
            f"{year}.7,1.0,0.1,NaN,NaN,NaN,"
            f"{year}.2,-1.2,0.1,NaN,NaN,NaN"
        )
    polar = tmp_path / "polar.csv"
    polar.write_text("\n".join(polar_rows) + "\n", encoding="utf-8")

    rows = _build_solar_precursor_cycle_rows(sunspot, polar)

    assert [row["cycle_number"] for row in rows] == list(range(15, 25))
    assert all(
        float(row["north_measurement_date"])
        <= float(row["predictor_cutoff_decimal_year"])
        and float(row["south_measurement_date"])
        <= float(row["predictor_cutoff_decimal_year"])
        for row in rows
    )

from __future__ import annotations

import hashlib
import json
import math

import pytest

from jw.solar_data_catalog import (
    _acquire_polar_field,
    _acquire_silso,
    _validate_polar_field,
    _validate_silso_extrema,
    _validate_silso_monthly,
    _validate_silso_smoothed,
)
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


def test_silso_acquisition_does_not_downgrade_to_plain_http_mirror(
    monkeypatch,
) -> None:
    attempted_urls: list[str] = []

    def unavailable(url: str, *, timeout: float = 20.0):
        del timeout
        attempted_urls.append(url)
        raise OSError("authority unavailable")

    monkeypatch.setattr("jw.solar_data_catalog._fetch", unavailable)

    with pytest.raises(OSError, match="authority unavailable"):
        _acquire_silso()

    assert attempted_urls == ["https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt"]


def test_silso_smoothed_validator_binds_official_series_semantics() -> None:
    rows = []
    year, month = 1749, 1
    for _ in range(3_312):
        rows.append(f"{year};{month:02d};0;1.0;0;0")
        month += 1
        if month == 13:
            year += 1
            month = 1

    result = _validate_silso_smoothed(("\n".join(rows) + "\n").encode())

    assert result["row_count"] == 3_312
    assert result["coverage_start"] == "1749-01"
    assert result["coverage_end"] == "2024-12"


def test_silso_extrema_validator_requires_completed_cycle_24() -> None:
    rows = [f"{cycle} 1900 01 1.0 1904 01 100.0" for cycle in range(1, 25)]

    result = _validate_silso_extrema(("\n".join(rows) + "\n").encode())

    assert result["cycle_count"] == 24
    assert result["latest_completed_cycle"] == 24


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


def test_polar_acquisition_persists_stable_dataverse_locator(monkeypatch) -> None:
    rows = [
        f"{year}.7,1,0.1,NaN,NaN,NaN,{year}.2,-1,0.1,NaN,NaN,NaN"
        for year in range(1906, 2024)
    ]
    payload = ("\n".join(rows) + "\n").encode()
    file_id = 42
    stable_url = f"https://dataverse.harvard.edu/api/access/datafile/{file_id}"
    metadata = {
        "data": {
            "latestVersion": {
                "versionNumber": 5,
                "versionMinorNumber": 0,
                "releaseTime": "2023-10-17T11:58:06Z",
                "license": {"name": "CC0 1.0"},
                "files": [
                    {
                        "restricted": False,
                        "dataFile": {
                            "id": file_id,
                            "filename": "e_PField_MWO_WSO.csv",
                            "checksum": {
                                "type": "MD5",
                                "value": hashlib.md5(
                                    payload, usedforsecurity=False
                                ).hexdigest(),
                            },
                        },
                    }
                ],
            }
        }
    }

    def fake_fetch(url: str, *, timeout: float = 20.0):
        del timeout
        if "/api/datasets/:persistentId/" in url:
            return json.dumps(metadata).encode(), url
        assert url == stable_url
        return payload, "https://download.example.test/object?X-Amz-Signature=temporary"

    monkeypatch.setattr("jw.solar_data_catalog._fetch", fake_fetch)

    acquired, provenance = _acquire_polar_field()

    assert acquired == payload
    assert provenance["retrieval_url"] == stable_url
    assert provenance["data_file_id"] == file_id
    assert "?" not in provenance["retrieval_url"]


def test_precursor_cycle_builder_uses_complete_cycles_and_uncertainty_fields(
    tmp_path,
) -> None:
    monthly = []
    for year in range(1749, 2026):
        for month in range(1, 13):
            decimal = year + (month - 0.5) / 12
            value = 5 + 100 * (1 - math.cos(2 * math.pi * (decimal - 1902) / 11))
            monthly.append(f"{year} {month:02d} {decimal:.3f} {value:.4f} 1.0 10")
    sunspot = tmp_path / "silso.txt"
    sunspot.write_text("\n".join(monthly) + "\n", encoding="ascii")

    header = (
        "N MWO Date,N MWO PField,N MWO SEM,N WSO Date,N WSO PField,N WSO SEM,"
        "S MWO Date,S MWO PField,S MWO SEM,S WSO Date,S WSO PField,S WSO SEM"
    )
    polar_rows = [header]
    for year in range(1900, 2025):
        polar_rows.append(f"{year}.7,1.0,0.1,NaN,NaN,NaN,{year}.2,-1.2,0.1,NaN,NaN,NaN")
    polar = tmp_path / "polar.csv"
    polar.write_text("\n".join(polar_rows) + "\n", encoding="utf-8")

    rows = _build_solar_precursor_cycle_rows(sunspot, polar)

    assert [row["cycle_number"] for row in rows] == list(range(14, 25))
    boundary = rows[0]
    assert boundary["row_role"] == "boundary"
    assert boundary["minimum_date"]
    assert boundary["polar_field_proxy_gauss"] is None
    assert boundary["maximum_date"]
    assert boundary["peak_smoothed_sunspot_number"] > 0
    assert boundary["peak_smoothed_sunspot_number_sigma"] == pytest.approx(1.0)
    assert all(row["row_role"] == "analysis" for row in rows[1:])
    for row in rows[1:]:
        year, month = (int(value) for value in str(row["minimum_date"]).split("-"))
        minimum_decimal = year + (month - 0.5) / 12
        assert row["predictor_window_start_decimal_year"] == pytest.approx(
            minimum_decimal - 0.5
        )
        assert row["predictor_window_end_decimal_year"] == pytest.approx(
            minimum_decimal + 0.5
        )
        assert row["predictor_cutoff_decimal_year"] == pytest.approx(
            minimum_decimal + 0.5
        )
        assert row["north_window_observation_count"] >= 1
        assert row["south_window_observation_count"] >= 1
        assert row["polar_field_proxy_gauss"] == pytest.approx(1.1)
        assert row["peak_smoothed_sunspot_number_sigma"] == pytest.approx(1.0)
        assert row["minimum_date_sensitivity_start"] <= row["minimum_date"]
        assert row["minimum_date_sensitivity_end"] >= row["minimum_date"]
        assert row["minimum_date_sensitivity_span_months"] >= 0


def test_precursor_cycle_builder_marks_sparse_window_fallback_without_future_data(
    tmp_path,
) -> None:
    monthly = []
    for year in range(1749, 2026):
        for month in range(1, 13):
            decimal = year + (month - 0.5) / 12
            value = 5 + 100 * (1 - math.cos(2 * math.pi * (decimal - 1902.5) / 11))
            monthly.append(f"{year} {month:02d} {decimal:.3f} {value:.4f} 1.0 10")
    sunspot = tmp_path / "silso.txt"
    sunspot.write_text("\n".join(monthly) + "\n", encoding="ascii")
    header = (
        "N MWO Date,N MWO PField,N MWO SEM,N WSO Date,N WSO PField,N WSO SEM,"
        "S MWO Date,S MWO PField,S MWO SEM,S WSO Date,S WSO PField,S WSO SEM"
    )
    polar = tmp_path / "polar.csv"
    polar_rows = [header]
    for year in range(1900, 2025):
        south = "NaN,NaN" if year == 1913 else "-1.2,0.1"
        polar_rows.append(f"{year}.7,1.0,0.1,NaN,NaN,NaN,{year}.2,{south},NaN,NaN,NaN")
    polar.write_text(
        "\n".join(polar_rows) + "\n",
        encoding="utf-8",
    )

    rows = _build_solar_precursor_cycle_rows(sunspot, polar)

    sparse = [row for row in rows[1:] if row["predictor_window_complete"] is False]
    assert sparse
    for row in sparse:
        assert row["predictor_fallback"] == "latest_preminimum_within_1.5_years"
        assert row["north_measurement_date"] <= row["predictor_cutoff_decimal_year"]
        assert row["south_measurement_date"] <= row["predictor_cutoff_decimal_year"]


def test_precursor_receipt_is_self_describing_and_hash_binds_boundary_table(
    tmp_path, monkeypatch
) -> None:
    import hashlib
    import json

    import jw.tools.solar_feature as solar_feature

    monthly = []
    for year in range(1749, 2026):
        for month in range(1, 13):
            decimal = year + (month - 0.5) / 12
            value = 5 + 100 * (1 - math.cos(2 * math.pi * (decimal - 1902) / 11))
            monthly.append(f"{year} {month:02d} {decimal:.3f} {value:.4f} 1.0 10")
    sunspot = tmp_path / "silso.txt"
    sunspot.write_text("\n".join(monthly) + "\n", encoding="ascii")
    header = (
        "N MWO Date,N MWO PField,N MWO SEM,N WSO Date,N WSO PField,N WSO SEM,"
        "S MWO Date,S MWO PField,S MWO SEM,S WSO Date,S WSO PField,S WSO SEM"
    )
    polar = tmp_path / "polar.csv"
    polar.write_text(
        "\n".join(
            [header]
            + [
                f"{year}.7,1.0,0.1,NaN,NaN,NaN,{year}.2,-1.2,0.1,NaN,NaN,NaN"
                for year in range(1900, 2025)
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    records = [
        {
            "path": "inputs/silso.txt",
            "dataset_id": "silso-monthly-total-v2",
            "sha256": hashlib.sha256(sunspot.read_bytes()).hexdigest(),
        },
        {
            "path": "inputs/polar.csv",
            "dataset_id": "mwo-wso-polar-field-v2",
            "sha256": hashlib.sha256(polar.read_bytes()).hexdigest(),
        },
    ]
    monkeypatch.setattr(
        solar_feature, "_eligible_input_records", lambda _config: records
    )
    monkeypatch.setattr(
        solar_feature, "workspace_root_from_config", lambda _config: tmp_path
    )
    monkeypatch.setattr(
        solar_feature,
        "_resolve_eligible_data_path",
        lambda value, _config: sunspot if value == "inputs/silso.txt" else polar,
    )
    monkeypatch.setattr(
        solar_feature,
        "_validated_task_metadata",
        lambda _config: (tmp_path, "precursor-task", {}),
    )

    result = json.loads(
        solar_feature.prepare_solar_precursor_cycle_table.func(
            sunspot_path="inputs/silso.txt",
            polar_field_path="inputs/polar.csv",
            config={},
        )
    )

    assert result["status"] == "verified"
    receipt = json.loads(
        (tmp_path / "receipts/datasets/solar_precursor_cycle_table.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["schema_version"] == "solar-precursor-cycle-table-v2"
    assert receipt["producer"] == "solar-data"
    assert receipt["task_id"] == "precursor-task"
    assert receipt["dataset_ids"] == [
        "silso-monthly-total-v2",
        "mwo-wso-polar-field-v2",
    ]
    assert receipt["row_count"] == 11
    assert receipt["pair_coverage"] == {
        "requested_pairs": [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)],
        "available_pairs": [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)],
        "unavailable_pairs": [],
    }
    assert {column["name"] for column in receipt["column_schema"]} >= {
        "row_role",
        "minimum_date",
        "polar_field_proxy_gauss",
    }
    assert receipt["units"]["polar_field_proxy_gauss"] == "gauss"
    assert "absolute" in receipt["sign_convention"]["polar_field_proxy_gauss"]
    assert receipt["temporal_ordering_rule"]
    assert receipt["sample_size"]["independent_sample_count"] == 10
    assert receipt["sample_size"]["n_eff_upper_bound"] == 10
    assert receipt["sample_size"]["n_eff_status"] == "bounded_not_estimated"
    assert set(receipt["uncertainty_fields"]["reported"]) >= {
        "peak_smoothed_sunspot_number_sigma",
        "minimum_date_sensitivity_start",
        "minimum_date_sensitivity_end",
    }
    gap_codes = {gap["code"] for gap in receipt["gaps"]}
    assert "PLANNED_PREDICTOR_WINDOW_NOT_IMPLEMENTED" not in gap_codes
    assert "TARGET_AMPLITUDE_UNCERTAINTY_NOT_COMPUTED" not in gap_codes
    assert "MINIMUM_DATE_UNCERTAINTY_NOT_COMPUTED" not in gap_codes
    assert "plus/minus 6 months" in receipt["method"]["predictor"]
    assert "arithmetic mean" in receipt["method"]["predictor"]
    assert (
        "sqrt(north_sem^2 + south_sem^2) / 2"
        in receipt["method"]["predictor_uncertainty"]
    )
    assert "not a confidence interval" in receipt["method"]["minimum_date_uncertainty"]
    assert result["sample_size"] == receipt["sample_size"]
    assert result["uncertainty_fields"] == receipt["uncertainty_fields"]
    assert result["gaps"] == receipt["gaps"]
    output = receipt["outputs"][0]
    output_path = tmp_path / output["path"]
    assert output["bytes"] == output_path.stat().st_size
    assert output["sha256"] == hashlib.sha256(output_path.read_bytes()).hexdigest()

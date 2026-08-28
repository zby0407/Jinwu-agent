from __future__ import annotations

import hashlib
import json
import math

import pytest

from jw.solar_data_catalog import (
    _fetch,
    _acquire_noaa_monthly_f107,
    _acquire_polar_field,
    _acquire_silso,
    _acquire_wso_current_polar_field,
    _validate_noaa_monthly_f107,
    _validate_polar_field,
    _validate_silso_extrema,
    _validate_silso_monthly,
    _validate_silso_smoothed,
    _validate_wso_current_polar_field,
    acquire_authoritative_solar_data,
)
from jw.tools.solar_feature import (
    _build_solar_cycle_26_readiness_inventory,
    _build_solar_precursor_cycle_rows,
    prepare_solar_cycle_26_readiness,
)


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


def test_authority_fetch_retries_one_transient_connection_failure(
    monkeypatch,
) -> None:
    attempts = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"validated payload"

        def geturl(self):
            return "https://authority.example.test/data"

    def urlopen(_request, *, timeout):
        nonlocal attempts
        assert timeout == 20.0
        attempts += 1
        if attempts == 1:
            raise OSError("temporary proxy tunnel failure")
        return Response()

    monkeypatch.setattr("jw.solar_data_catalog.urllib.request.urlopen", urlopen)

    payload, resolved = _fetch("https://authority.example.test/data")

    assert payload == b"validated payload"
    assert resolved == "https://authority.example.test/data"
    assert attempts == 2


def test_acquisition_persists_validated_source_and_names_failed_dataset(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "jw.solar_data_catalog._acquire_silso",
        lambda: (b"monthly", {"validation": {"row_count": 1}}),
    )

    def fail_smoothed():
        raise OSError("temporary upstream failure")

    monkeypatch.setattr("jw.solar_data_catalog._acquire_silso_smoothed", fail_smoothed)

    with pytest.raises(
        RuntimeError,
        match=r"silso-monthly-smoothed-v2.*OSError",
    ):
        acquire_authoritative_solar_data(
            tmp_path,
            dataset_ids=(
                "silso-monthly-total-v2",
                "silso-monthly-smoothed-v2",
            ),
        )

    registered = (
        tmp_path / "projects/default/shared/data/solar_cycle/silso/monthly_total_v2/"
        "SN_m_tot_V2.0.txt"
    )
    assert registered.read_bytes() == b"monthly"


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


def test_noaa_monthly_f107_validator_preserves_cutoff_coverage() -> None:
    payload = json.dumps(
        [
            {"time-tag": "2025-12", "f10.7": 160.13},
            {"time-tag": "2026-01", "f10.7": 151.16},
            {"time-tag": "2026-06", "f10.7": 138.21},
            {"time-tag": "2026-07", "f10.7": 136.01},
        ]
    ).encode()

    result = _validate_noaa_monthly_f107(payload)

    assert result["coverage_start"] == "2025-12"
    assert result["coverage_end"] == "2026-07"
    assert result["cutoff_2026_06_available"] is True


def test_noaa_monthly_f107_acquisition_uses_official_swpc_endpoint(monkeypatch) -> None:
    payload = json.dumps(
        [
            {"time-tag": "2026-05", "f10.7": 125.69},
            {"time-tag": "2026-06", "f10.7": 138.21},
        ]
    ).encode()
    requested: list[str] = []

    def fake_fetch(url: str, *, timeout: float = 20.0):
        del timeout
        requested.append(url)
        return payload, url

    monkeypatch.setattr("jw.solar_data_catalog._fetch", fake_fetch)

    acquired, provenance = _acquire_noaa_monthly_f107()

    assert acquired == payload
    assert requested == [
        "https://services.swpc.noaa.gov/json/solar-cycle/f10-7cm-flux.json"
    ]
    assert provenance["retrieval_source_kind"] == "authority"


def test_wso_current_polar_validator_records_observed_gap_at_cutoff() -> None:
    payload = b"""<pre>
Last updated Mon Aug 10 17:27:49 UTC 2026
2026:01:09_21h:07m:13s     1N   29S  -14Avg   20nhz filt:  -32Nf   16Sf  -24Avgf
2026:01:19_21h:07m:13s   XXXN  XXXS  XXXAvg   20nhz filt:  XXXNf  XXXSf  XXXAvgf
2026:06:28_21h:07m:13s   XXXN  XXXS  XXXAvg   20nhz filt:  XXXNf  XXXSf  XXXAvgf
</pre>"""

    result = _validate_wso_current_polar_field(payload)

    assert result["latest_valid_observation"] == "2026-01-09"
    assert result["cutoff_date"] == "2026-06-30"
    assert result["cutoff_window_status"] == "observations_missing"
    assert result["cutoff_window_start"] == "2026-01-01"
    assert result["missing_rows_in_cutoff_window"] == 2


def test_wso_current_polar_acquisition_uses_observatory_page(monkeypatch) -> None:
    payload = b"""<pre>
Last updated Mon Aug 10 17:27:49 UTC 2026
2026:01:09_21h:07m:13s     1N   29S  -14Avg   20nhz filt:  -32Nf   16Sf  -24Avgf
2026:06:28_21h:07m:13s   XXXN  XXXS  XXXAvg   20nhz filt:  XXXNf  XXXSf  XXXAvgf
</pre>"""

    monkeypatch.setattr(
        "jw.solar_data_catalog._fetch", lambda url, timeout=20.0: (payload, url)
    )

    acquired, provenance = _acquire_wso_current_polar_field()

    assert acquired == payload
    assert provenance["authority_url"] == "http://wso.stanford.edu/Polar.html"
    assert provenance["validation"]["cutoff_window_status"] == "observations_missing"


def test_cycle_26_readiness_inventory_preserves_observed_gap(tmp_path) -> None:
    monthly_total = tmp_path / "monthly.txt"
    monthly_total.write_text(
        "\n".join(
            [
                "2019 12 2019.958 1.8 0.1 10",
                "2024 10 2024.790 216.0 5.0 20",
                "2026 01 2026.042 112.0 4.0 20",
                "2026 06 2026.453 114.6 4.1 20",
                "2026 07 2026.538 125.0 4.2 20",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    smoothed = tmp_path / "smoothed.csv"
    smoothed.write_text(
        "\n".join(
            [
                "2019;12;2019.958;1.8;0.1;10;1",
                "2024;10;2024.790;160.9;6.0;20;1",
                "2026;01;2026.042;104.2;5.0;20;0",
                "2026;02;2026.122;-1.0;-1.0;20;0",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    extrema = tmp_path / "extrema.txt"
    extrema.write_text(
        "24 2008 12 2.2 2014 04 116.4\n25 2019 12 1.8\n",
        encoding="ascii",
    )
    f107 = tmp_path / "f107.json"
    f107.write_text(
        json.dumps(
            [
                {"time-tag": "2019-12", "f10.7": 70.93},
                {"time-tag": "2024-08", "f10.7": 245.6},
                {"time-tag": "2026-06", "f10.7": 138.21},
                {"time-tag": "2026-07", "f10.7": 136.01},
            ]
        ),
        encoding="utf-8",
    )
    historical_polar = tmp_path / "historical.csv"
    historical_polar.write_text(
        "N MWO Date,N MWO PField,N MWO SEM,N WSO Date,N WSO PField,N WSO SEM,"
        "S MWO Date,S MWO PField,S MWO SEM,S WSO Date,S WSO PField,S WSO SEM\n"
        "1906.7,1,0.1,2023.7,-1,0.1,1906.2,-1,0.1,2023.2,1,0.1\n",
        encoding="utf-8",
    )
    current_polar = tmp_path / "current.html"
    current_polar.write_text(
        """<pre>
2026:01:09_21h:07m:13s     1N   29S  -14Avg   20nhz filt:  -32Nf   16Sf  -24Avgf
2026:01:19_21h:07m:13s   XXXN  XXXS  XXXAvg   20nhz filt:  XXXNf  XXXSf  XXXAvgf
2026:06:28_21h:07m:13s   XXXN  XXXS  XXXAvg   20nhz filt:  XXXNf  XXXSf  XXXAvgf
</pre>""",
        encoding="ascii",
    )

    inventory = _build_solar_cycle_26_readiness_inventory(
        monthly_total,
        smoothed,
        extrema,
        f107,
        historical_polar,
        current_polar,
        cutoff_date="2026-06-30",
    )

    assert inventory["launch_readiness"] == "insufficient_evidence"
    assert inventory["formal_classification_ready"] is False
    assert inventory["testable_peak_interval_ready"] is False
    assert inventory["observations"]["silso_monthly"]["latest_month"] == "2026-06"
    assert inventory["observations"]["silso_smoothed"]["latest_month"] == "2026-01"
    assert inventory["observations"]["f107_monthly"]["latest_month"] == "2026-06"
    assert (
        inventory["observations"]["wso_current_polar"]["latest_valid_observation"]
        == "2026-01-09"
    )
    assert inventory["observations"]["wso_current_polar"]["cutoff_window_status"] == (
        "observations_missing"
    )
    assert inventory["cycle_25_state_assessment"] == {
        "peak_status": "provisional_observed_not_official",
        "activity_below_observed_peaks": True,
        "decline_interpretation": (
            "below_observed_peaks_but_cycle_decline_not_officially_confirmed"
        ),
        "next_minimum_status": "not_established",
    }
    assert inventory["cycle_26_precursor_assessment"]["status"] == "unavailable"
    assert inventory["cycle_26_precursor_assessment"]["same_definition_ready"] is False
    assert {gap["code"] for gap in inventory["evidence_gaps"]} >= {
        "SC25_OFFICIAL_MAXIMUM_UNCONFIRMED",
        "NEXT_MINIMUM_NOT_ESTABLISHED",
        "WSO_CUTOFF_WINDOW_MISSING",
        "MINIMUM_NEAR_POLAR_PRECURSOR_UNAVAILABLE",
    }


def test_cycle_26_readiness_tool_persists_verified_insufficient_evidence(
    monkeypatch, tmp_path
) -> None:
    import jw.tools.solar_feature as solar_feature

    dataset_ids = [
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
        "noaa-swpc-monthly-f107-v1",
        "mwo-wso-polar-field-v2",
        "wso-current-polar-field-v1",
    ]
    paths = {
        dataset_id: tmp_path / f"{index}.dat"
        for index, dataset_id in enumerate(dataset_ids)
    }
    records = {
        dataset_id: {
            "dataset_id": dataset_id,
            "path": f"/project/{index}.dat",
            "sha256": str(index + 1) * 64,
            "provenance_ref": f"provenance/{index}.json",
        }
        for index, dataset_id in enumerate(dataset_ids)
    }
    for path in paths.values():
        path.write_text("fixture", encoding="utf-8")

    def fake_resolve(value: str, dataset_id: str, _config):
        assert value == records[dataset_id]["path"]
        return paths[dataset_id], records[dataset_id]

    inventory = {
        "schema_version": "solar-cycle-26-readiness-inventory-v1",
        "analysis_protocol": "solar_cycle_26_readiness_v1",
        "cutoff_date": "2026-06-30",
        "launch_readiness": "insufficient_evidence",
        "formal_classification_ready": False,
        "testable_peak_interval_ready": False,
        "observations": {},
        "evidence_gaps": [{"code": "WSO_CUTOFF_WINDOW_MISSING"}],
    }
    monkeypatch.setattr(solar_feature, "_resolve_eligible_dataset_path", fake_resolve)
    monkeypatch.setattr(
        solar_feature,
        "_build_solar_cycle_26_readiness_inventory",
        lambda *_args, cutoff_date: {**inventory, "cutoff_date": cutoff_date},
    )
    monkeypatch.setattr(
        solar_feature,
        "workspace_root_from_config",
        lambda _config: tmp_path,
    )
    monkeypatch.setattr(
        solar_feature,
        "_validated_task_metadata",
        lambda _config: (tmp_path, "task-1", {}),
    )

    result = json.loads(
        prepare_solar_cycle_26_readiness.func(
            **{
                "monthly_total_path": records[dataset_ids[0]]["path"],
                "smoothed_path": records[dataset_ids[1]]["path"],
                "official_extrema_path": records[dataset_ids[2]]["path"],
                "f107_path": records[dataset_ids[3]]["path"],
                "historical_polar_path": records[dataset_ids[4]]["path"],
                "current_polar_path": records[dataset_ids[5]]["path"],
                "cutoff_date": "2026-06-30",
                "config": {"configurable": {"thread_id": "task-1"}},
            }
        )
    )

    assert result["status"] == "verified"
    assert result["launch_readiness"] == "insufficient_evidence"
    artifact = tmp_path / result["artifact_refs"][0]
    receipt = tmp_path / result["receipt_refs"][0]
    assert json.loads(artifact.read_text(encoding="utf-8")) == inventory
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["receipt_type"] == "solar_cycle_26_readiness_inventory"
    assert receipt_payload["status"] == "verified"
    assert receipt_payload["outputs"][0]["path"] == result["artifact_refs"][0]


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
        assert row["north_polar_field_abs_gauss"] == pytest.approx(1.0)
        assert row["south_polar_field_abs_gauss"] == pytest.approx(1.2)
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
    feature_records = receipt["feature_records"]
    assert len(feature_records) == 10
    assert {row["observable_kind"] for row in feature_records} == {
        "polar_aperture_field"
    }
    assert all(
        row["source_dataset_ids"] == ["mwo-wso-polar-field-v2"]
        for row in feature_records
    )
    assert all(
        float(row["available_at"]) <= float(row["forecast_origin"])
        for row in feature_records
    )
    assert [row["target_cycle_id"] for row in feature_records] == list(range(15, 25))
    blocked = receipt["unavailable_feature_records"]
    assert len(blocked) == 1
    assert blocked[0]["hypothesis_id"] == "h3_axial_dipole_discriminator"
    assert blocked[0]["observable_kind"] == "axial_dipole_moment"
    assert blocked[0]["status"] == "blocked_by_data"
    assert blocked[0]["value"] is None
    assert (
        blocked[0]["data_gap"]
        == "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT"
    )
    assert receipt["pair_coverage"] == {
        "requested_pairs": [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)],
        "available_pairs": [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)],
        "unavailable_pairs": [],
    }
    assert {column["name"] for column in receipt["column_schema"]} >= {
        "row_role",
        "minimum_date",
        "polar_field_proxy_gauss",
        "north_polar_field_abs_gauss",
        "south_polar_field_abs_gauss",
    }
    assert receipt["units"]["polar_field_proxy_gauss"] == "gauss"
    assert receipt["units"]["north_polar_field_abs_gauss"] == "gauss"
    assert receipt["units"]["south_polar_field_abs_gauss"] == "gauss"
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
    assert result["feature_record_count"] == 10
    assert result["unavailable_feature_record_count"] == 1
    assert result["hypothesis_data_status"] == {
        "h2_polar_precursor": "available",
        "h3_axial_dipole_discriminator": "blocked_by_data",
    }
    output = receipt["outputs"][0]
    output_path = tmp_path / output["path"]
    assert output["bytes"] == output_path.stat().st_size
    assert output["sha256"] == hashlib.sha256(output_path.read_bytes()).hexdigest()

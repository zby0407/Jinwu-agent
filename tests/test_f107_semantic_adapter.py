from __future__ import annotations

import json

import pandas as pd
import pytest

from jw.research_protocols import F107_DISCONTINUITY_REQUIRED_MEASUREMENTS
from jw.solar_agent_src.f107_semantic_adapter import (
    canonicalize_f107,
    canonicalize_f107_sn,
    write_f107_contract,
)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "date_utc": "1980-01-01",
            "time_utc": "1700",
            "f107_observed": 100.0,
            "f107_adjusted": 110.0,
            "f107_ursi": 99.0,
            "source_segment": "a",
            "record_type": "intraday",
            "missing_flag": False,
            "duplicate_flag": False,
        },
        {
            "date_utc": "1980-01-01",
            "time_utc": "2000",
            "f107_observed": 120.0,
            "f107_adjusted": 130.0,
            "f107_ursi": 117.0,
            "source_segment": "a",
            "record_type": "intraday",
            "missing_flag": False,
            "duplicate_flag": False,
        },
        {
            "date_utc": "1980-01-02",
            "time_utc": "",
            "f107_observed": 190.0,
            "f107_adjusted": 200.0,
            "f107_ursi": 180.0,
            "source_segment": "legacy",
            "record_type": "legacy_daily",
            "missing_flag": False,
            "duplicate_flag": False,
        },
        {
            "date_utc": "1980-01-02",
            "time_utc": "",
            "f107_observed": 190.0,
            "f107_adjusted": 200.0,
            "f107_ursi": 180.0,
            "source_segment": "overlap",
            "record_type": "legacy_daily",
            "missing_flag": False,
            "duplicate_flag": True,
        },
        {
            "date_utc": "1980-01-03",
            "time_utc": "",
            "f107_observed": 999.0,
            "f107_adjusted": 999.0,
            "f107_ursi": 899.1,
            "source_segment": "a",
            "record_type": "legacy_daily",
            "missing_flag": True,
            "duplicate_flag": False,
        },
    ]


def _write_csv(
    path,
    *,
    shuffled: bool = False,
    duplicate_intraday: bool = False,
) -> None:
    rows = _rows()
    if duplicate_intraday:
        rows.append(dict(rows[0]))
    frame = pd.DataFrame(rows)
    if shuffled:
        frame = frame[list(reversed(frame.columns))]
    frame.to_csv(path, index=False)


def test_f107_adapter_is_column_order_invariant(tmp_path) -> None:
    normal = tmp_path / "normal.csv"
    shuffled = tmp_path / "shuffled.csv"
    _write_csv(normal)
    _write_csv(shuffled, shuffled=True)

    first, first_manifest = canonicalize_f107(normal)
    second, second_manifest = canonicalize_f107(shuffled)

    assert (
        first["f107_adjusted_monthly_mean"].tolist()
        == second["f107_adjusted_monthly_mean"].tolist()
    )
    assert first_manifest.product_id == "f107_adjusted"
    assert second_manifest.column_bindings["duplicate"] == "duplicate_flag"


def test_f107_adapter_uses_equal_weight_days_and_deduplicates(tmp_path) -> None:
    source = tmp_path / "f107.csv"
    duplicated = tmp_path / "f107-duplicated.csv"
    _write_csv(source)
    _write_csv(duplicated, duplicate_intraday=True)

    monthly, manifest = canonicalize_f107(source)
    duplicated_monthly, duplicated_manifest = canonicalize_f107(duplicated)

    # Day one mean is 120 and day two is 200, so the equal-day month mean is 160.
    assert monthly.loc[0, "f107_adjusted_monthly_mean"] == pytest.approx(160.0)
    assert duplicated_monthly.loc[0, "f107_adjusted_monthly_mean"] == pytest.approx(
        160.0
    )
    assert manifest.diagnostics["missing_records_excluded"] == 1
    assert manifest.diagnostics["shadow_duplicates_dropped"] == 1
    assert duplicated_manifest.diagnostics["equivalent_duplicates_dropped"] >= 1
    assert monthly.loc[0, "f107_observed_days_in_month"] == 2


def test_f107_adapter_aligns_silso_total_and_declares_protocol(tmp_path) -> None:
    source = tmp_path / "f107.csv"
    total = tmp_path / "silso_total.csv"
    hemispheric = tmp_path / "silso_hemispheric.csv"
    _write_csv(source)
    total.write_text("1980;01;1980.042; 155.0; 3.0; 31;1\n", encoding="utf-8")
    hemispheric.write_text("not used\n", encoding="utf-8")

    aligned, manifest = canonicalize_f107_sn(
        source,
        total,
        silso_hemispheric_path=hemispheric,
    )

    assert aligned.loc[0, "sunspot_number"] == pytest.approx(155.0)
    assert manifest.product_id == "f107_adjusted+silso_sn_total_v2"
    assert manifest.excluded_inputs[0]["path"] == "silso_hemispheric.csv"
    assert manifest.adapter_version == "1.1.0"
    assert manifest.unit.startswith("sfu")
    assert set(manifest.diagnostics["product_definitions"]) == {
        "observed",
        "adjusted",
        "absolute",
    }
    assert manifest.diagnostics["required_measurement_ids"] == list(
        F107_DISCONTINUITY_REQUIRED_MEASUREMENTS
    )
    requirements = " ".join(manifest.analysis_requirements)
    assert "model F10.7 as the response" in requirements
    assert "survival function" in requirements
    assert "minimum 20 and 25 observed days" in requirements
    assert "verified measurement id" in requirements
    assert "SN=100" in requirements
    assert "published ~10.5%" in requirements


def test_f107_contract_hash_binds_written_artifact(tmp_path) -> None:
    source = tmp_path / "f107.csv"
    canonical = tmp_path / "work" / "canonical.csv"
    receipt = tmp_path / "receipts" / "datasets" / "f107_semantics.json"
    _write_csv(source)

    payload = write_f107_contract(
        source,
        canonical_path=canonical,
        receipt_path=receipt,
    )

    assert canonical.is_file()
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8")) == payload
    assert payload["status"] == "verified"
    assert len(payload["canonical_sha256"]) == 64

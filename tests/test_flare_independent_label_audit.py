"""Synthetic fixtures only; never run the actual source audit or preparation."""

import csv
from datetime import datetime, timedelta, timezone
import json

import pytest

from jw.flare_forecast.engine import load

m = load("independent_label_audit")
UTC = timezone.utc


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event(start, flux, *, peak=None, end=None, identity="invented"):
    return {
        "start": stamp(start),
        "peak_time": stamp(peak or start),
        "end": stamp(end) if end else None,
        "peak": flux,
        "id": identity,
        "source_file": "synthetic.csv",
        "saturated": False,
    }


def label(rows, bad=(), *, qualified=True):
    first = stamp("2023-01-01T00:00:01Z")
    return m.audit_window(
        first,
        first + timedelta(days=1),
        sorted(rows, key=lambda item: item["start"]),
        bad,
        stamp("2023-01-05T00:00:00Z"),
        stamp("2010-01-01T00:00:00Z"),
        stamp("2024-01-01T00:00:00Z"),
        {"qualified": qualified},
    )


@pytest.mark.parametrize(
    "flux,expected",
    [(1e-7, (0, 0, 0)), (1e-6, (1, 0, 1)), (1e-5, (1, 1, 2)), (1e-4, (1, 1, 2))],
)
def test_independent_nested_thresholds_and_future_end_optional(flux, expected):
    result = label([event("2023-01-01T12:00:00Z", flux)])
    assert result["label_qualified"]
    assert (
        tuple(result[name] for name in ("label_c1", "label_m1", "y_class")) == expected
    )


def test_independent_start_boundaries_and_outside_peak():
    result = label(
        [
            event("2023-01-01T00:00:00Z", 1e-4, identity="old"),
            event("2023-01-01T00:00:01Z", 1e-6, identity="included"),
            event(
                "2023-01-02T00:00:00Z",
                1e-5,
                peak="2023-01-02T01:00:00Z",
                identity="late",
            ),
            event("2023-01-02T00:00:01Z", 1e-4, identity="excluded"),
        ]
    )
    assert result["event_count"] == 2
    assert result["y_class"] == 2
    assert {entry["flare_id"] for entry in result["supporters"][0]} == {
        "included",
        "late",
    }


def test_no_negative_when_bad_event_unlocatable_or_coverage_unknown():
    assert label([], bad=[{"start": None}])["label_m1"] is None
    result = label([], qualified=False)
    assert result["label_c1"] is None and not result["label_qualified"]


def test_saturated_lower_bound_does_not_assert_negative_above_known_peak():
    row = event("2023-01-01T12:00:00Z", 1e-6)
    row["saturated"] = True
    result = label([row])
    assert result["label_c1"] == 1 and result["label_m1"] is None
    assert result["y_class"] is None


def test_independent_second_coverage_keeps_partial_boundary_minutes():
    first = stamp("2023-01-01T00:00:00Z")
    coverage = m.IndependentCoverage([True, False, True], int(first.timestamp()) // 60)
    result = coverage.stats(
        first + timedelta(seconds=31), first + timedelta(seconds=151)
    )
    assert result["valid_seconds"] == 60
    assert result["coverage_fraction"] == 0.5
    assert result["max_gap_seconds"] == 60


@pytest.mark.parametrize("gap,qualified", [(5, True), (6, False)])
def test_independent_gap_gate_includes_exact_five_minutes(gap, qualified):
    first = stamp("2023-01-01T00:00:00Z")
    flags = [True] * 1442
    flags[100 : 100 + gap] = [False] * gap
    coverage = m.IndependentCoverage(flags, int(first.timestamp()) // 60)
    result = coverage.stats(
        first + timedelta(seconds=17), first + timedelta(days=1, seconds=17)
    )
    assert result["qualified"] is qualified
    assert result["max_gap_seconds"] == gap * 60


def test_independent_outside_domain_and_boundary_gaps_merge():
    first = stamp("2023-01-01T00:00:00Z")
    coverage = m.IndependentCoverage([False, True, False], int(first.timestamp()) // 60)
    result = coverage.stats(
        first - timedelta(seconds=10), first + timedelta(seconds=200)
    )
    assert result["valid_seconds"] == 60 and result["max_gap_seconds"] == 80


def test_official_event_reader_does_not_import_prepared_label_function(tmp_path):
    path = tmp_path / "invented.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "flare_id",
                "start_time",
                "time",
                "end_time",
                "xrsb_irrad",
                "peak_saturated",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "flare_id": "A",
                "start_time": "2023-01-01 12:00:00",
                "time": "2023-01-01 12:05:00",
                "end_time": "",
                "xrsb_irrad": "1e-6",
                "peak_saturated": "0",
            }
        )
        writer.writerow(
            {
                "flare_id": "B",
                "start_time": "2023-01-01 13:00:00",
                "time": "2023-01-01 13:05:00",
                "end_time": "",
                "xrsb_irrad": "nan",
                "peak_saturated": "0",
            }
        )
    valid, bad = m.read_events([("invented.csv", path)])
    assert len(valid) == 1 and valid[0]["end"] is None
    assert len(bad) == 1


def test_audit_cannot_overwrite_or_create_output_in_input(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="fresh separate"):
        m.audit(raw, prepared, tmp_path / "absent", output, tmp_path / "gate")
    with pytest.raises(ValueError, match="fresh separate"):
        m.audit(raw, prepared, tmp_path / "absent", raw / "out", tmp_path / "gate")


@pytest.mark.parametrize("wrong_m_label", [False, True])
def test_independent_gate_checks_every_primary_and_negative_source_proof(
    tmp_path, monkeypatch, wrong_m_label
):
    raw = tmp_path / "invented_raw"
    raw.mkdir()
    prepared = tmp_path / "invented_prepared"
    prepared.mkdir()
    entries = []
    for year in range(2010, 2027):
        name = f"events{year}.csv"
        with (raw / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "flare_id",
                    "start_time",
                    "time",
                    "end_time",
                    "xrsb_irrad",
                    "peak_saturated",
                ],
            )
            writer.writeheader()
            if year == 2023:
                writer.writerow(
                    {
                        "flare_id": "synthetic",
                        "start_time": "2023-01-01 12:00:00",
                        "time": "2023-01-01 12:05:00",
                        "end_time": "",
                        "xrsb_irrad": "1e-6",
                        "peak_saturated": "0",
                    }
                )
        entries.append(
            {
                "role": "science_event_annual",
                "parameters": {"year": year},
                "path": name,
                "sha256": m.sha(raw / name),
                "bytes": (raw / name).stat().st_size,
            }
        )
    metadata = raw / "metadata.json"
    m.write(
        metadata,
        {
            "global_attributes": {
                "time_coverage_start": "20100101",
                "time_coverage_end": "20261231",
            }
        },
    )
    entries.append(
        {
            "role": "science_event_metadata",
            "path": metadata.name,
            "sha256": m.sha(metadata),
            "bytes": metadata.stat().st_size,
        }
    )
    m.write(raw / "source_manifest.json", {"files": entries})
    contract = tmp_path / "synthetic_contract.json"
    m.write(
        contract,
        {
            "source_manifest_sha256": m.sha(raw / "source_manifest.json"),
            "historical_catalog_cutoff_utc": "2026-10-11T00:00:00Z",
            "test_target_end_max_utc": "2026-01-01T00:00:00Z",
        },
    )
    first = stamp("2023-01-01T00:00:00Z")
    monkeypatch.setattr(
        m,
        "load_coverage",
        lambda *args: (
            m.IndependentCoverage([True] * 1442, int(first.timestamp()) // 60),
            [{"path": "synthetic coverage", "sha256": "0" * 64}],
        ),
    )
    label_path = prepared / "label_audit.csv"
    row = {
        "sample_id": "sample_one",
        "observation_date": "2023-01-01",
        "label_qualified": True,
        "target_start_utc": "2023-01-01T00:00:01Z",
        "target_end_utc": "2023-01-02T00:00:01Z",
        "label_c1": 1,
        "label_m1": 1 if wrong_m_label else 0,
        "y_class": 2 if wrong_m_label else 1,
        "valid_seconds": 86400,
        "window_seconds": 86400,
        "coverage_fraction": 1.0,
        "max_gap_seconds": 0.0,
    }
    with label_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (prepared / "daily_physical.csv").write_text(
        "sample_id\nsample_one\n", encoding="utf-8"
    )
    files = [
        {"path": path.name, "sha256": m.sha(path), "bytes": path.stat().st_size}
        for path in sorted(prepared.iterdir())
    ]
    m.write(
        prepared / "prepared_manifest.json",
        {"files": files, "contract_sha256": m.sha(contract)},
    )
    gate = tmp_path / "independent_gate.json"
    result = m.audit(raw, prepared, contract, tmp_path / "new_audit", gate)
    assert result["status"] == ("failed" if wrong_m_label else "passed")
    assert result["sample_mismatch_count"] == (1 if wrong_m_label else 0)
    assert result["checked_samples"] == 1 and result["primary_samples_confirmed"] == 1
    assert result["negative_bits_with_catalogue_and_seconds_coverage_evidence"] == 1
    assert result["all_inputs_hashes_unchanged"]
    saved = json.loads(gate.read_text(encoding="utf-8"))
    assert saved["prepared_manifest_sha256"] == m.sha(
        prepared / "prepared_manifest.json"
    )
    assert saved["operator_validation_not_JW_scientific_verdict"] is True

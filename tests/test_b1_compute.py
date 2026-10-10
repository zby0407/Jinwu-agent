"""Synthetic engineering checks only; no observed solar results are computed."""

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from jw.b1_compute import (
    B1ValidationError,
    _load,
    _select,
    _window,
    goes_flux,
    parse_utc,
    run_b1_analysis,
    tai_to_utc,
    validate_b1_inputs,
)


def write_csv(path, rows, fields=None):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def rewrite_csv(path, update):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    update(rows)
    fields += sorted({key for row in rows for key in row} - set(fields))
    write_csv(path, rows, fields)
    # The synthetic adapter updates its bound canonical hash after an intentional
    # fixture edit. Production raw lineage must instead be verified at download.
    manifest_path = path.parent / "sources.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest["sources"]:
            if source.get("input_name") == path.stem:
                source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path):
    """Four days of linear synthetic data, one exactly known half-window delta."""
    start = datetime(2014, 1, 1, tzinfo=timezone.utc)

    def stamp(value):
        return value.isoformat().replace("+00:00", "Z")

    magnetic = []
    for index in range(4 * 24 * 5):
        time = start + timedelta(minutes=12 * index)
        magnetic.append(
            {
                "time_utc": stamp(time),
                "harpnum": 1,
                "noaa_ar": 11111,
                "usflux_mx": index * 100,
                "totpot_erg_per_cm": index * 10,
                "meanshr_deg": index / 10,
                "quality_valid": "true",
                "support_end_utc": stamp(time + timedelta(minutes=6)),
                "source_record_id": f"synthetic_{index}",
                "err_usflux": 1,
            }
        )
    xrs = [
        {
            "time_utc": stamp(start + timedelta(minutes=index)),
            "flux_w_m2": 1e-7,
            "quality_valid": "true",
        }
        for index in range(4 * 24 * 60)
    ]
    events = [
        {
            "event_id": "synthetic_flare",
            "start_utc": "2014-01-03T00:30:00Z",
            "peak_utc": "2014-01-03T00:40:00Z",
            "end_utc": "2014-01-03T00:50:00Z",
            "goes_class": "M1.0",
            "noaa_ar": 11111,
            "region_verified": "true",
        }
    ]
    paths = [
        tmp_path / name
        for name in (
            "magnetic.csv",
            "events.csv",
            "xrs.csv",
            "parameters.json",
            "sources.json",
        )
    ]
    for path, rows in zip(paths[:3], (magnetic, events, xrs), strict=True):
        write_csv(path, rows)
    parameters = {
        "schema_version": "b1-flare-case-v1",
        "harpnum": 1,
        "noaa_ar": 11111,
        "target_start_utc": "2014-01-02T00:30:00Z",
        "target_end_utc": "2014-01-04T12:00:00Z",
        "synthetic": True,
    }
    paths[3].write_text(json.dumps(parameters), encoding="utf-8")
    raw = tmp_path / "synthetic_source.txt"
    raw.write_text("SYNTHETIC ENGINEERING FIXTURE. NOT OBSERVATIONS.", encoding="utf-8")
    sources = {
        "sources": [
            {
                "role": "raw_source",
                "url": "synthetic://fixture",
                "title": "Synthetic fixture",
                "license_or_terms": "Synthetic test data generated in this test",
                "citation": "test_b1_compute.py synthetic fixture; no scientific result",
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "path": raw.name,
                "synthetic": True,
            }
        ]
    }
    for path in paths[:4]:
        sources["sources"].append(
            {
                "role": "canonical_input",
                "input_name": path.stem,
                "url": "synthetic://fixture",
                "title": f"Synthetic {path.stem}",
                "license_or_terms": "Synthetic test fixture",
                "citation": "Synthetic engineering verification only",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "path": path.name,
                "synthetic": True,
            }
        )
    paths[4].write_text(json.dumps(sources), encoding="utf-8")
    return paths


def test_validation_is_readonly_and_no_result(inputs, tmp_path):
    before = {path: path.read_bytes() for path in inputs}
    files = set(tmp_path.iterdir())
    result = validate_b1_inputs(*inputs)
    assert result["status"] == "validated_structure_only_not_an_experiment"
    assert result["rows"] == {"magnetic": 480, "events": 1, "xrs": 5760}
    assert set(tmp_path.iterdir()) == files
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize(
    "value",
    [
        "2014-01-01",
        "2014-01-01T00:00:00",
        "2014-01-01T08:00:00+08:00",
        "2014-01-01T00:00:00_TAI",
        "2014-02-30T00:00:00Z",
    ],
)
def test_utc_must_be_explicit(value):
    with pytest.raises(B1ValidationError):
        parse_utc(value)


def test_tai_conversion_crosses_midnight_without_losing_seconds():
    pytest.importorskip("astropy")
    assert tai_to_utc("2014.10.20_00:00:00_TAI") == "2014-10-19T23:59:25.000Z"
    with pytest.raises(B1ValidationError):
        tai_to_utc("2014-10-20T00:00:00Z")


@pytest.mark.parametrize(
    "table,field,value,message",
    [
        (0, "noaa_ar", "11111,11112", "one positive integer"),
        (0, "noaa_ar", "11112", "unique HARP/NOAA"),
        (0, "time_utc", "2014-01-01T00:00:00", "explicit UTC"),
        (0, "quality_valid", "", "explicit true/false"),
        (0, "support_end_utc", "2013-12-31T00:00:00Z", "support_end precedes"),
        (0, "meanshr_deg", "181", "Out-of-range"),
        (1, "noaa_ar", "", "Verified event"),
        (1, "peak_utc", "2014-01-02T00:00:00Z", "start <= peak"),
        (2, "flux_w_m2", "-999", "Negative XRS"),
    ],
)
def test_invalid_semantics_are_rejected(inputs, table, field, value, message):
    rewrite_csv(inputs[table], lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(B1ValidationError, match=message):
        validate_b1_inputs(*inputs)


@pytest.mark.parametrize("table", [0, 1, 2])
def test_duplicate_timestamps_are_rejected(inputs, table):
    rewrite_csv(inputs[table], lambda rows: rows.append(rows[0].copy()))
    with pytest.raises(B1ValidationError, match="[Dd]uplicate"):
        validate_b1_inputs(*inputs)


def test_source_hash_mismatch_is_rejected(inputs):
    source = json.loads(inputs[4].read_text(encoding="utf-8"))
    source["sources"][1]["sha256"] = "0" * 64
    inputs[4].write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(B1ValidationError, match="SHA-256"):
        validate_b1_inputs(*inputs)


def test_parameter_bytes_must_match_source_manifest(inputs):
    parameters = json.loads(inputs[3].read_text(encoding="utf-8"))
    inputs[3].write_text(json.dumps(parameters | {"max_events": 1}), encoding="utf-8")
    with pytest.raises(
        B1ValidationError, match="Canonical input SHA-256 mismatches: parameters"
    ):
        validate_b1_inputs(*inputs)


def test_canonical_parameter_binding_cannot_be_omitted(inputs):
    sources = json.loads(inputs[4].read_text(encoding="utf-8"))
    sources["sources"] = [
        source
        for source in sources["sources"]
        if source.get("input_name") != "parameters"
    ]
    inputs[4].write_text(json.dumps(sources), encoding="utf-8")
    with pytest.raises(B1ValidationError, match="parameters canonical input hashes"):
        validate_b1_inputs(*inputs)


def test_raw_source_path_never_grants_file_access(inputs):
    source = json.loads(inputs[4].read_text(encoding="utf-8"))
    source["sources"][0]["path"] = "../../does-not-exist/secret.txt"
    # Even a nonexistent raw path is lineage metadata, not an instruction to read.
    inputs[4].write_text(json.dumps(source), encoding="utf-8")
    assert validate_b1_inputs(*inputs)["source_count"] == 5
    source["sources"][1]["path"] = "../../does-not-exist/another.txt"
    inputs[4].write_text(json.dumps(source), encoding="utf-8")
    assert validate_b1_inputs(*inputs)["source_count"] == 5


def test_midnight_window_has_hand_computed_medians_and_no_future(inputs):
    data = _load(*inputs)
    window = _window(
        data, parse_utc("2014-01-03T00:30:00Z"), "event", "synthetic_flare"
    )
    assert window["status"] == "eligible"
    assert window["window_start_utc"] == "2014-01-02T00:00:00Z"
    assert window["window_end_utc"] == "2014-01-03T00:00:00Z"
    # Indices 120..179, 180..239; difference = 60 * 100.
    assert window["metrics"]["usflux_mx"]["first_half_median"] == 14950
    assert window["metrics"]["usflux_mx"]["second_half_median"] == 20950
    assert window["metrics"]["usflux_mx"]["median_difference"] == 6000
    for row in data["magnetic"]:
        if row["time"] >= parse_utc(window["window_end_utc"]):
            row["usflux_mx"] = 1e99
    assert (
        _window(data, parse_utc("2014-01-03T00:30:00Z"), "event", "synthetic_flare")[
            "metrics"
        ]
        == window["metrics"]
    )


def test_time_average_support_cannot_leak_past_cutoff(inputs):
    data = _load(*inputs)
    row = data["magnetic"][239]  # 23:48 sample is inside the prewindow.
    row["support_end"] = parse_utc("2014-01-03T00:01:00Z")
    row["usflux_mx"] = 1e99
    window = _window(
        data, parse_utc("2014-01-03T00:30:00Z"), "event", "synthetic_flare"
    )
    assert window["excluded_future_support_rows"] == 1
    assert window["metrics"]["usflux_mx"]["n_second"] == 59
    assert window["metrics"]["usflux_mx"]["second_half_median"] == 20900


def test_xrs_average_support_excludes_future_flux_at_second_precision_cutoff(inputs):
    data = _load(*inputs)
    anchor = parse_utc("2014-01-02T00:30:30Z")
    crossing = next(
        row for row in data["xrs"] if row["time"] == parse_utc("2014-01-02T01:00:00Z")
    )
    crossing["flux"] = goes_flux("X1.0")
    window = _window(data, anchor, "control", "synthetic_control")
    assert window["excluded_future_xrs_support_rows"] == 1
    assert window["status"] == "eligible"
    assert (
        "full_disk_xrs_target_class_activity_no_region_attribution"
        not in window["reasons"]
    )


def test_xrs_explicit_support_endpoint_is_respected(inputs):
    rewrite_csv(
        inputs[2],
        lambda rows: [
            row.__setitem__("support_end_utc", "2014-01-05T00:00:00Z") for row in rows
        ],
    )
    data = _load(*inputs)
    window = _window(
        data, parse_utc("2014-01-02T00:30:00Z"), "control", "synthetic_control"
    )
    assert window["xrs_coverage"] == 0
    assert "insufficient_xrs_coverage" in window["reasons"]


def test_missing_and_bad_quality_are_not_zero_and_half_coverage_matters(inputs):
    data = _load(*inputs)
    # 12 absent points = 90% overall, but 80% in first half, so reject.
    for row in data["magnetic"][120:132]:
        row["usflux_mx"] = None
    result = _window(
        data, parse_utc("2014-01-03T00:30:00Z"), "event", "synthetic_flare"
    )
    metric = result["metrics"]["usflux_mx"]
    assert metric["coverage"] == 0.9
    assert metric["first_half_coverage"] == 0.8
    assert metric["median_difference"] is None
    assert result["status"] == "rejected"
    assert "insufficient_usflux_mx_coverage" in result["reasons"]
    for row in data["magnetic"][132:140]:
        row["quality"] = False
    assert (
        _window(data, parse_utc("2014-01-03T00:30:00Z"), "event", "synthetic_flare")[
            "excluded_quality_rows"
        ]
        == 8
    )


def test_unknown_region_not_inferred_from_full_disk_xrs(inputs):
    data = _load(*inputs)
    data["events"][0]["verified"] = False
    data["events"][0]["region"] = None
    windows, screening = _select(data)
    assert not any(window["kind"] == "event" for window in windows)
    assert screening[0]["reason"] == "unverified_region"


def test_control_requires_xrs_coverage_and_rejects_global_activity(inputs):
    data = _load(*inputs)
    anchor = parse_utc("2014-01-02T00:30:00Z")
    assert _window(data, anchor, "control", "control")["status"] == "eligible"
    data["xrs"][10]["flux"] = goes_flux("M1.0")
    result = _window(data, anchor, "control", "control")
    assert (
        "full_disk_xrs_target_class_activity_no_region_attribution" in result["reasons"]
    )
    for row in data["xrs"][:1000]:
        row["quality"] = False
    assert (
        "insufficient_xrs_coverage"
        in _window(data, anchor, "control", "control")["reasons"]
    )


def test_catalog_contamination_and_unknown_region_are_conservative(inputs):
    data = _load(*inputs)
    original = data["events"][0]
    earlier = dict(
        original,
        event_id="earlier",
        start=parse_utc("2014-01-02T12:00:00Z"),
        end=parse_utc("2014-01-02T12:10:00Z"),
        verified=False,
        region=None,
    )
    data["events"].append(earlier)
    result = _window(data, original["start"], "event", original["event_id"])
    assert result["contaminating_event_ids"] == ["earlier"]
    assert result["status"] == "rejected"


def test_missing_end_time_stays_missing_and_censors_contamination(inputs):
    rewrite_csv(inputs[1], lambda rows: rows[0].__setitem__("end_utc", ""))
    data = _load(*inputs)
    event = data["events"][0]
    assert event["end"] is None
    windows, _ = _select(data)
    selected = next(window for window in windows if window["kind"] == "event")
    assert selected["event_end_missing"] is True
    assert selected["status"] == "eligible"
    # Even when the reported start precedes the entire window, missing end
    # cannot be treated as proof that this M-class event has finished.
    event["start"] = parse_utc("2014-01-01T00:00:00Z")
    control = _window(
        data, parse_utc("2014-01-03T00:30:00Z"), "control", "later_control"
    )
    assert control["contaminating_event_ids"] == ["synthetic_flare"]
    assert control["status"] == "rejected"


def test_empty_event_catalog_does_not_fabricate_events(inputs):
    rewrite_csv(inputs[1], lambda rows: rows.clear())
    data = _load(*inputs)
    windows, _ = _select(data)
    assert all(window["kind"] == "control" for window in windows)
    assert sum(window["status"] == "eligible" for window in windows) == 3


def test_end_boundary_excluded_and_selection_is_not_metric_optimized(inputs):
    data = _load(*inputs)
    data["parameters"]["target_end_utc"] = "2014-01-03T00:30:00Z"
    assert not any(window["kind"] == "event" for window in _select(data)[0])
    data["parameters"]["target_end_utc"] = "2014-01-04T12:00:00Z"
    first = _select(data)
    for row in data["magnetic"]:
        row["usflux_mx"] = 1e8 - row["usflux_mx"]
    second = _select(data)
    assert [(row["window_id"], row["status"]) for row in first[0]] == [
        (row["window_id"], row["status"]) for row in second[0]
    ]


def test_rejected_chronological_candidate_is_not_replaced(inputs):
    data = _load(*inputs)
    data["parameters"]["max_events"] = 1
    later = dict(
        data["events"][0],
        event_id="later",
        start=parse_utc("2014-01-03T12:30:00Z"),
        end=parse_utc("2014-01-03T12:50:00Z"),
    )
    data["events"].append(later)
    for row in data["magnetic"]:
        row["quality"] = False
    windows, screening = _select(data)
    assert [
        (window["window_id"], window["status"])
        for window in windows
        if window["kind"] == "event"
    ] == [("synthetic_flare", "rejected")]
    assert {
        "event_id": "later",
        "status": "excluded",
        "reason": "beyond_first_chronological_event_limit",
    } in screening


def test_run_writes_sources_audit_and_never_overwrites(inputs, tmp_path):
    output = tmp_path / "synthetic_output"
    manifest = json.loads(inputs[4].read_text(encoding="utf-8"))
    manifest |= {
        "semantic_decisions": {
            "unit": "SYNTHETIC UNIT CONFLICT NOTE",
            "flare_class": "SYNTHETIC SCIENCE CLASS NOTE",
        },
        "transport_limitations": "SYNTHETIC HTTP LIMITATION NOTE",
        "frozen_scope": {"selection_reason": "SYNTHETIC SELECTION RATIONALE"},
    }
    inputs[4].write_text(json.dumps(manifest), encoding="utf-8")
    before = {path: path.read_bytes() for path in inputs}
    receipt = run_b1_analysis(*inputs, output)
    assert receipt["status"] == "computed_not_scientifically_reviewed"
    assert receipt["agent_run"] is False
    assert receipt["eligible_events"] == 1
    assert receipt["eligible_controls"] >= 1
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "synthetic://fixture" in report
    assert "SYNTHETIC TEST DATA" in report
    assert "not coronal free energy" in report
    assert "test_b1_compute.py synthetic fixture" in report
    for note in (
        "SYNTHETIC UNIT CONFLICT NOTE",
        "SYNTHETIC SCIENCE CLASS NOTE",
        "SYNTHETIC HTTP LIMITATION NOTE",
        "SYNTHETIC SELECTION RATIONALE",
    ):
        assert note in report
    assert "not a forecasting evaluation" in report
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert (
        provenance["source_context"]["semantic_decisions"]
        == manifest["semantic_decisions"]
    )
    assert (output / "timeseries.png").stat().st_size > 1000
    assert (output / "output_hashes.json").is_file()
    assert all(path.read_bytes() == content for path, content in before.items())
    with pytest.raises(FileExistsError):
        run_b1_analysis(*inputs, output)


def test_failed_run_preserves_failure_record(inputs, tmp_path):
    rewrite_csv(inputs[2], lambda rows: rows.clear())
    output = tmp_path / "synthetic_failure"
    with pytest.raises(B1ValidationError, match="XRS input is empty"):
        run_b1_analysis(*inputs, output)
    failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "failed_no_scientific_result"
    assert not (output / "receipt.json").exists()

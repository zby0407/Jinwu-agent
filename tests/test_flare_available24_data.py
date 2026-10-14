"""Hypothetical engineering QA only; no production data prep, HTTP or fitting."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from jw.flare_forecast.engine import load

data = load("available24_data")
UTC = timezone.utc


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EventIndex:
    def __init__(self, rows):
        self.rows = rows

    def window(self, start, end):
        return [row for row in self.rows if start <= row["start"] < end]


def minute_index(valid, start):
    prefix = [0]
    gaps, beginning = [], None
    for i, flag in enumerate(valid):
        prefix.append(prefix[-1] + int(flag))
        if not flag and beginning is None:
            beginning = i
        if flag and beginning is not None:
            gaps.append((beginning, i))
            beginning = None
    if beginning is not None:
        gaps.append((beginning, len(valid)))
    first = int(start.timestamp()) // 60
    return SimpleNamespace(
        first=first,
        end=first + len(valid),
        prefix=prefix,
        gap_starts=[item[0] for item in gaps],
        gap_ends=[item[1] for item in gaps],
    )


def raw_event(start, flux, *, peak=None, end=None, identity="hypothetical"):
    return {
        "flare_id": identity,
        "start_time": start,
        "time": peak or start,
        "end_time": end or "",
        "xrsb_irrad": str(flux),
        "peak_saturated": "0",
        "source_file": "synthetic.csv",
    }


def certificate(raw, first="2023-01-01T14:00:01Z"):
    rows, audit = data.normalize_events(raw)
    start = stamp(first)
    return data.label_certificate(
        start,
        start + timedelta(hours=24),
        EventIndex(rows),
        audit,
        stamp("2023-01-05T00:00:00Z"),
        stamp("2010-01-01T00:00:00Z"),
        stamp("2024-01-01T00:00:00Z"),
        {"qualified": True},
    )


def test_label_start_half_open_peak_can_follow_window_end_and_end_can_be_missing():
    result = certificate(
        [
            raw_event(
                "2023-01-01T13:59:00Z",
                1e-4,
                peak="2023-01-01T14:01:00Z",
                identity="old",
            ),
            raw_event("2023-01-01T14:00:01Z", 1e-6, identity="at-start"),
            raw_event(
                "2023-01-02T14:00:00Z",
                1e-5,
                peak="2023-01-02T14:30:00Z",
                identity="late-peak",
            ),
            raw_event("2023-01-02T14:00:01Z", 1e-4, identity="at-end"),
        ]
    )
    assert result["label_qualified"]
    assert (result["label_c1"], result["label_m1"], result["y_class"]) == (1, 1, 2)
    assert {row["flare_id"] for row in result["window_events"]} == {
        "at-start",
        "late-peak",
    }
    assert result["end_missing_peak_known_count"] == 2


@pytest.mark.parametrize(
    "flux, expected",
    [(1e-7, (0, 0, 0)), (1e-6, (1, 0, 1)), (1e-5, (1, 1, 2)), (1e-4, (1, 1, 2))],
)
def test_labels_are_nested_exact_thresholds_and_no_C_can_contain_B(flux, expected):
    result = certificate([raw_event("2023-01-01T15:00:00Z", flux)])
    assert (result["label_c1"], result["label_m1"], result["y_class"]) == expected


@pytest.mark.parametrize(
    "edit",
    [
        {"time": ""},
        {"xrsb_irrad": "nan"},
        {"start_time": ""},
        {"time": "2023-01-01T13:00:00Z"},
        {"end_time": "2023-01-01T13:00:00Z"},
    ],
)
def test_bad_event_cannot_create_negative(edit):
    row = raw_event("2023-01-01T15:00:00Z", 1e-6)
    row.update(edit)
    result = certificate([row])
    assert not result["label_qualified"]
    assert result["y_class"] is None
    assert result["label_m1"] is None


def test_unresolved_peak_and_unobserved_catalog_domain_do_not_become_quiet():
    result = certificate(
        [raw_event("2023-01-01T15:00:00Z", 1e-6, peak="2023-01-06T00:00:00Z")]
    )
    assert not result["label_qualified"] and result["pending_peak_count"] == 1
    empty = EventIndex([])
    first = stamp("2023-01-01T14:00:01Z")
    result = data.label_certificate(
        first,
        first + timedelta(hours=24),
        empty,
        [],
        first + timedelta(hours=23),
        first - timedelta(days=100),
        first + timedelta(days=2),
        {"qualified": True},
    )
    assert result["label_c1"] is None and result["label_m1"] is None


def test_seconds_coverage_preserves_target_and_clips_boundary_minute_intervals():
    first = stamp("2023-01-01T00:00:00Z")
    coverage = data.SecondsCoverage(minute_index([True, False, True], first))
    result = coverage.stats(
        first + timedelta(seconds=31), first + timedelta(seconds=151)
    )
    assert result["window_seconds"] == 120
    assert result["valid_seconds"] == 60
    assert result["max_gap_seconds"] == 60
    assert result["coverage_fraction"] == 0.5


@pytest.mark.parametrize("missing, expected", [(5, True), (6, False)])
def test_seconds_coverage_five_minute_gap_limit(missing, expected):
    first = stamp("2023-01-01T00:00:00Z")
    valid = [True] * 1442
    valid[100 : 100 + missing] = [False] * missing
    result = data.SecondsCoverage(minute_index(valid, first)).stats(
        first + timedelta(seconds=17), first + timedelta(days=1, seconds=17)
    )
    assert result["qualified"] is expected
    assert result["max_gap_seconds"] == missing * 60


def test_seconds_coverage_unknown_outside_domain_merges_with_boundary_gaps():
    first = stamp("2023-01-01T00:00:00Z")
    coverage = data.SecondsCoverage(minute_index([False, True, False], first))
    result = coverage.stats(
        first - timedelta(seconds=10), first + timedelta(seconds=200)
    )
    assert result["valid_seconds"] == 60
    assert result["max_gap_seconds"] == 80
    result = coverage.stats(
        first - timedelta(seconds=100), first - timedelta(seconds=50)
    )
    assert result["valid_seconds"] == 0 and result["max_gap_seconds"] == 50


class Gateway:
    @staticmethod
    def tai_utc(value):
        # These fixtures are explicitly synthetic2020+ timestamps with37 second TAI offset.
        return datetime.strptime(
            value.removesuffix("_TAI"), "%Y.%m.%d_%H:%M:%S"
        ).replace(tzinfo=UTC) - timedelta(seconds=37)

    @staticmethod
    def date_utc(value):
        if not value.endswith("Z"):
            raise ValueError("Explicit UTC required")
        return stamp(value)

    @staticmethod
    def quality_word(value):
        number = int(str(value), 16 if str(value).startswith("0x") else 10)
        if not 0 <= number < 2**32:
            raise ValueError("uint32 required")
        return number

    @staticmethod
    def decode_keywords(payload):
        return payload["hypothetical_rows"]


def magnetic(day="2020-01-01", identity="1", produced="14:00:00", **overrides):
    row = {
        "HARPNUM": identity,
        "T_REC": day.replace("-", ".") + "_13:00:00_TAI",
        "T_OBS": day.replace("-", ".") + "_13:00:00_TAI",
        "DATE": day + "T" + produced + "Z",
        "LON_FWT": "0",
        "QUALITY": "1024",
        "QUAL_S": "0",
        "QUALLEV1": "0",
        "ARM_QUAL": "0",
        "BLD_VERS": "synthetic",
        "CALVER64": "0",
        "CODEVER7": "hypothetical",
    }
    row.update({name: "1" for name in data.METRICS})
    row.update(overrides)
    return row


def test_clock_uses_latest_DATE_and_all_supports_plus_one_second():
    first = magnetic()
    second = magnetic(identity="2", produced="14:02:00", USFLUX="3", MEANALP="-1")
    group, audit = data.qualify_group(
        [first, second], Gateway(), first["T_REC"], "1" * 64
    )
    assert group["magnetic_qualified"]
    assert group["reference_utc"] == "2020-01-01T14:02:01Z"
    assert group["observation_utc"] == "2020-01-01T12:59:23Z"
    assert group["hmi_latest_support_end_utc"] == "2020-01-01T13:10:38Z"
    assert group["input_support_start_utc"] == "2020-01-01T12:48:08Z"
    assert group["USFLUX_max"] == 3 and group["USFLUX_median"] == 2
    assert group["MEANALP_max"] == 1 and group["MEANALP_median"] == 0
    assert all(row["QUALITY_raw"] == "1024" for row in audit)
    assert group["all_magnetic_quality_zero"] is False


def test_support_controls_clock_and_freshness_exact_boundary():
    raw = magnetic(produced="13:01:00")
    group, _ = data.qualify_group([raw], Gateway(), raw["T_REC"], "1" * 64)
    assert group["reference_utc"] == "2020-01-01T13:10:39Z"
    raw = magnetic(produced="15:59:22")
    group, _ = data.qualify_group([raw], Gateway(), raw["T_REC"], "1" * 64)
    assert group["magnetic_qualified"] and group["observation_age_seconds"] == 10800
    raw["DATE"] = "2020-01-01T15:59:23Z"
    group, _ = data.qualify_group([raw], Gateway(), raw["T_REC"], "1" * 64)
    assert not group["magnetic_qualified"]


@pytest.mark.parametrize(
    "edit",
    [
        {"QUALITY": "0x401"},
        {"QUALITY": "0"},
        {"LON_FWT": "nan"},
        {"R_VALUE": "MISSING"},
        {"USFLUX": "-1"},
        {"QUAL_S": "MISSING"},
        {"DATE": "2020-01-01T12:00:00Z"},
    ],
)
def test_one_bad_central_patch_rejects_complete_group(edit):
    good, bad = magnetic(), magnetic(identity="2", **edit)
    group, _ = data.qualify_group([good, bad], Gateway(), good["T_REC"], "1" * 64)
    assert not group["magnetic_qualified"]
    assert "USFLUX_max" not in group


def test_duplicate_HARP_all_copies_rejected_and_no_patch_is_unknown():
    raw = magnetic()
    group, _ = data.qualify_group([raw, dict(raw)], Gateway(), raw["T_REC"], "1" * 64)
    assert not group["magnetic_qualified"]
    group, _ = data.qualify_group([], Gateway(), raw["T_REC"], "1" * 64)
    assert not group["magnetic_qualified"]


@pytest.mark.parametrize("field", ["QUAL_S", "QUALLEV1", "ARM_QUAL"])
def test_secondary_highbit_is_conservatively_rejected_without_namespace_inference(
    field,
):
    raw = magnetic(**{field: "0x80000000"})
    group, audit = data.qualify_group([raw], Gateway(), raw["T_REC"], "1" * 64)
    assert not group["magnetic_qualified"]
    assert (
        "secondary_highbit_rejected_pending_source_review_" + field
        in audit[0]["reasons"]
    )
    # Bit30 is common in real QUALLEV1 and is not reinterpreted as primary QUALITY.
    raw[field] = "1073741824"
    group, _ = data.qualify_group([raw], Gateway(), raw["T_REC"], "1" * 64)
    assert group["magnetic_qualified"]


def test_missing_end_changes_past_baseline_but_not_future_label():
    rows, audit = data.normalize_events([raw_event("2023-01-01T15:00:00Z", 1e-6)])
    issue = stamp("2023-01-02T14:00:01Z")
    fake_coverage = SimpleNamespace(
        stats=lambda start, end: {
            "qualified": True,
            "coverage_fraction": 1.0,
            "max_gap_seconds": 0,
        }
    )
    values, flags = data.history_values(
        issue,
        EventIndex(rows),
        audit,
        fake_coverage,
        stamp("2023-01-05T00:00:00Z"),
        stamp("2010-01-01T00:00:00Z"),
        stamp("2024-01-01T00:00:00Z"),
    )
    assert flags["history_baseline_status"] == "unknown_not_issued"
    assert flags["history_unknown_reasons"] == "history_past_end_missing"
    assert flags["history_past168h_missing_end_count"] == 1
    assert all(value is None for value in values.values())
    assert certificate([raw_event("2023-01-01T15:00:00Z", 1e-6)])["label_qualified"]


def test_purge_is_dynamic_and_uses_support_or_history_cutoff():
    rows = []
    for role, day in (
        ("train", "2020-12-30"),
        ("train", "2020-12-31"),
        ("selection", "2021-01-01"),
        ("calibration", "2022-01-01"),
        ("test", "2023-01-01"),
    ):
        issue = stamp(day + "T14:00:01Z")
        rows.append(
            {
                "sample_id": role + day,
                "split": role,
                "status": "eligible",
                "reference_utc": data.utc(issue),
                "input_support_start_utc": day + "T12:48:08Z",
                "history_feature_cutoff_utc": day + "T11:00:01Z",
                "target_end_utc": data.utc(issue + timedelta(days=1)),
                "exclusion_reasons": "",
            }
        )
    receipt = data.apply_purge(rows)
    assert receipt["removed_count"] == 1
    assert rows[0]["status"] == "eligible" and rows[1]["status"] == "excluded"
    assert rows[1]["exclusion_reasons"] == "role_boundary_future_label_overlap"
    assert receipt["removed"][0]["following_input_cutoff_utc"] == "2021-01-01T11:00:01Z"


def contract():
    return {
        "schema_version": "available24-qualification-contract-v1",
        "source_mode": data.SOURCE_MODE,
        "splits": data.SPLITS,
        "accepted_QUALITY_words": [1024],
        "max_observation_age_seconds": 10800,
        "support_half_width_seconds": 675,
        "historical_clock_policy": data.CLOCK_POLICY,
        "issue_padding_seconds": 1,
        "target_duration_seconds": 86400,
        "history_completion_lag_hours": 3,
        "history_missing_end_policy": "unknown_not_zero",
        "coverage_fraction_min": 0.99,
        "coverage_max_gap_seconds": 300,
        "target_spatial_scope": "whole_sun_GOES_catalog_no_AR_filter",
        "historical_nominal_tai_hour": 13,
        "test_target_end_max_utc": "2026-01-01T00:00:00Z",
        "prepare_allowed": True,
        "input_readonly": True,
        "features_physical": data.FEATURES,
        "features_history_baseline": data.HISTORY,
        "source_manifest_sha256": "1" * 64,
        "quality_policy_sha256": "2" * 64,
        "label_policy": "start_half_open_peak_threshold_end_optional",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("splits", {"train": ["2011", "2025"]}),
        ("max_observation_age_seconds", 12000),
        ("accepted_QUALITY_words", [0, 1024]),
        ("label_policy", "peak_time_in_window"),
        ("coverage_fraction_min", 0.95),
        ("target_duration_seconds", 90000),
        ("test_target_end_max_utc", "2027-01-01T00:00:00Z"),
    ],
)
def test_contract_rejects_unregistered_policy_changes(key, value):
    value_contract = contract()
    value_contract[key] = value
    with pytest.raises(ValueError):
        data.check_contract(value_contract)


def test_existing_output_and_input_overlap_rejected_without_loading_production(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        data,
        "load_frozen",
        lambda *args: pytest.fail("No source loading after directory guard"),
    )
    source = tmp_path / "input"
    source.mkdir()
    with pytest.raises(ValueError, match="separate"):
        data.prepare(source, tmp_path / "absent_contract", source / "new")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="overwrite"):
        data.prepare(source, tmp_path / "absent_contract", output)


def test_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        data.safe_path(tmp_path, "../outside")


@pytest.mark.parametrize("past_end_missing", [False, True])
def test_full_synthetic_preparation_manifest_projection_and_readonly_receipts(
    tmp_path, monkeypatch, past_end_missing
):
    """Exercises the real prepare pipeline only with a tiny invented source bundle."""
    root, output = tmp_path / "hypothetical_input", tmp_path / "hypothetical_output"
    root.mkdir()
    files = []

    def entry(path, role, **extra):
        full = root / path
        files.append(
            {
                "path": path,
                "role": role,
                "sha256": data.sha(full),
                "bytes": full.stat().st_size,
                **extra,
            }
        )

    for year in range(2010, 2027):
        path = f"hypothetical_events{year}.csv"
        with (root / path).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
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
            if year == 2020 and past_end_missing:
                writer.writerow(
                    {
                        "flare_id": "hypothetical_missing_end",
                        "start_time": "2020-12-30T15:00:00Z",
                        "time": "2020-12-30T15:10:00Z",
                        "end_time": "",
                        "xrsb_irrad": "1e-6",
                        "peak_saturated": "0",
                    }
                )
        entry(path, "science_event_annual", parameters={"year": year})
    data.write_json(
        root / "metadata.json",
        {
            "global_attributes": {
                "time_coverage_start": "20100101",
                "time_coverage_end": "20261231",
            }
        },
    )
    entry("metadata.json", "science_event_metadata")
    for day in ("2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01", "2025-12-31"):
        path = "hypothetical_magnetic" + day + ".json"
        data.write_json(root / path, {"hypothetical_rows": [magnetic(day)]})
        entry(
            path,
            "magnetic_month",
            month=day[:7],
            status="success",
            retrieved_utc="2026-01-01T00:00:00Z",
        )
    manifest = {
        "input_readonly": True,
        "files": files,
        "source_versions_terms": {"version": "synthetic_not_science"},
    }
    data.write_json(root / "source_manifest.json", manifest)
    rules = contract()
    rules["source_manifest_sha256"] = data.sha(root / "source_manifest.json")
    rules["historical_catalog_cutoff_utc"] = "2026-12-31T00:00:00Z"
    proof_path = tmp_path / "hypothetical_quality_raw.txt"
    proof_path.write_text(
        "Hypothetical source-only quality evidence, not a native review",
        encoding="utf-8",
    )
    decision_path = tmp_path / "hypothetical_quality_decision.json"
    data.write_json(
        decision_path,
        {
            "schema": "physical-quality-source-decision-v1",
            "status": "accepted_with_limits",
            "series": [data.SOURCE_MODE],
            "accepted_words": [1024],
            "scope": {"fit_predict_plot_authorized_by_this_review": False},
            "proof_files": [
                {
                    "path": str(proof_path),
                    "sha256": data.sha(proof_path),
                    "bytes": proof_path.stat().st_size,
                }
            ],
        },
    )
    rules["quality_policy_sha256"] = data.sha(decision_path)
    rules["quality_decision_file"] = {
        "path": str(decision_path),
        "sha256": data.sha(decision_path),
    }
    contract_path = tmp_path / "hypothetical_contract.json"
    data.write_json(contract_path, rules)

    class Prefix:
        def __init__(self, length):
            self.length = length

        def __len__(self):
            return self.length

        def __getitem__(self, i):
            return i

    start = int(stamp("2010-01-01T00:00:00Z").timestamp()) // 60
    end = int(stamp("2026-12-31T00:00:00Z").timestamp()) // 60
    index = SimpleNamespace(
        first=start, end=end, prefix=Prefix(end - start + 1), gap_starts=[], gap_ends=[]
    )

    def checked_bundle(input_dir):
        before = {
            str(root / "source_manifest.json"): data.sha(root / "source_manifest.json")
        }
        roles = {}
        for item in files:
            assert data.sha(root / item["path"]) == item["sha256"]
            before[str(root / item["path"])] = item["sha256"]
            roles.setdefault(item["role"], []).append(item)
        return manifest, roles, before

    source = SimpleNamespace(
        checked_bundle=checked_bundle,
        load_history_sources=lambda *args: (
            None,
            None,
            index,
            [{"usage": "hypothetical_complete_minutes"}],
        ),
    )
    monkeypatch.setattr(
        data,
        "load_frozen",
        lambda *args: (source, Gateway(), None, SimpleNamespace(EventIndex=EventIndex)),
    )
    result = data.prepare(root, contract_path, output)
    assert result["physical_rows"] == 4
    assert result["common_rows"] == (3 if past_end_missing else 4)
    physical = list(
        csv.DictReader((output / "daily_physical.csv").open(encoding="utf-8"))
    )
    common = list(csv.DictReader((output / "daily_common.csv").open(encoding="utf-8")))
    assert {row["split"] for row in physical} == set(data.ROLES)
    assert {row["sample_id"] for row in common}.issubset(
        row["sample_id"] for row in physical
    )
    if past_end_missing:
        selection = next(row for row in physical if row["split"] == "selection")
        assert selection["history_baseline_status"] == "unknown_not_issued"
        assert all(selection[name] == "" for name in data.HISTORY)
        assert selection["label_qualified"] == "True"
    calendar = list(
        csv.DictReader((output / "calendar_audit.csv").open(encoding="utf-8"))
    )
    terminal = next(row for row in calendar if row["observation_date"] == "2025-12-31")
    assert terminal["status"] == "excluded"
    assert terminal["exclusion_reasons"] == "test_target_crosses2026_reserved_for_live"
    assert terminal["y_class"] == "" and terminal["future_valid_seconds"] == ""
    receipt = json.loads(
        (output / "terminal_test_gate_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["excluded_count"] == 1
    assert receipt["excluded"][0]["window_labels_and_coverage_not_evaluated"] is True
    assert all(row["reference_utc"] == row["target_start_utc"] for row in physical)
    assert all(row["y_class"] == "0" for row in physical)
    prepared = json.loads(
        (output / "prepared_manifest.json").read_text(encoding="utf-8")
    )
    for item in prepared["files"]:
        path = output / item["path"]
        assert data.sha(path) == item["sha256"] and path.stat().st_size == item["bytes"]
    receipt = json.loads(
        (output / "input_hashes_readonly.json").read_text(encoding="utf-8")
    )
    assert receipt["before"] == receipt["after"]
    assert all(data.sha(path) == digest for path, digest in receipt["before"].items())
    with pytest.raises(ValueError, match="overwrite"):
        data.prepare(root, contract_path, output)


def test_oldest_central_patch_controls_three_hour_age_not_newest():
    latest = magnetic(produced="15:59:00")
    oldest = magnetic(
        identity="2", produced="15:59:00", T_OBS="2020.01.01_12:58:00_TAI"
    )
    group, _ = data.qualify_group(
        [latest, oldest], Gateway(), latest["T_REC"], "1" * 64
    )
    assert not group["magnetic_qualified"]
    assert group["observation_age_seconds"] == 10898
    assert group["reasons"] == ["simulated_issue_observation_age_above3h"]


def test_history_lookbacks_end_at_three_hour_cutoff_and_keep_oldest_extra_hours():
    issue = stamp("2023-01-09T14:00:01Z")
    feature_cutoff = issue - timedelta(hours=3)
    fixtures = []
    for hours, kind in [(25, "24"), (73, "72"), (169, "168")]:
        start = issue - timedelta(hours=hours)
        fixtures.append(
            raw_event(
                data.utc(start),
                1e-6,
                peak=data.utc(start + timedelta(minutes=10)),
                end=data.utc(start + timedelta(minutes=20)),
                identity=kind,
            )
        )
    fixtures.append(
        raw_event(
            "2023-01-09T12:00:01Z",
            1e-4,
            peak="2023-01-09T12:10:01Z",
            end="2023-01-09T12:20:01Z",
            identity="latest3h_excluded",
        )
    )
    rows, audit = data.normalize_events(fixtures)
    observed_windows = []

    def coverage_stats(start, end):
        observed_windows.append((start, end))
        return {"qualified": True, "coverage_fraction": 1.0, "max_gap_seconds": 0}

    values, flags = data.history_values(
        issue,
        EventIndex(rows),
        audit,
        SimpleNamespace(stats=coverage_stats),
        stamp("2023-01-12T00:00:00Z"),
        stamp("2010-01-01T00:00:00Z"),
        stamp("2024-01-01T00:00:00Z"),
    )
    assert flags["history_baseline_status"] == "qualified"
    assert [values[f"history_{hours}h_c_count"] for hours in (24, 72, 168)] == [1, 2, 3]
    assert [values[f"history_{hours}h_m_count"] for hours in (24, 72, 168)] == [0, 0, 0]
    assert observed_windows == [(feature_cutoff - timedelta(hours=168), feature_cutoff)]
    assert flags["history_feature_cutoff_utc"] == data.utc(feature_cutoff)

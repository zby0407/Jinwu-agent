"""Quality detector tests: every anomaly is found, explained and classified."""

from __future__ import annotations

import pytest

from jw.scientific_data.contracts import (
    ConfidenceBand,
    DataRequirementContract,
    EntityRef,
    ExtractedTable,
    IssueCode,
    IssueSeverity,
    ObservationField,
    ObservationRecord,
    RepairMode,
    default_repair_mode,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.quality import (
    detect_all,
    detect_conflicts,
    detect_coverage_gaps,
    detect_duplicate_rows,
    detect_entity_ambiguity,
    detect_missing_coverage,
    detect_nonfinite,
    detect_out_of_range,
    detect_sign_suspect,
    detect_table_warnings,
    detect_unit_inconsistency,
    summarise,
)

PLUGIN = get_plugin("snia_light_curve")


def contract(**overrides) -> DataRequirementContract:
    fields: dict = {
        "contract_id": "c-quality",
        "research_goal": "Assess light curve completeness",
        "observables": (
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(
                name="apparent_magnitude", kind="quantity", unit="mag", required=True
            ),
            ObservationField(name="flux_density", kind="quantity", unit="mJy"),
        ),
        "identifier_fields": ("object",),
        "time_field": "time",
        "domain_plugin": "snia_light_curve",
        "min_records_per_entity": 2,
    }
    fields.update(overrides)
    return DataRequirementContract(**fields)


def record(
    *,
    uid="rec:1",
    entity="obj:sn2005a",
    observable="apparent_magnitude",
    value=16.5,
    unit="mag",
    time="2000-01-02",
    raw="16.5",
    conflict=False,
    uncertainty=None,
) -> ObservationRecord:
    return ObservationRecord(
        record_uid=uid,
        entity_id=entity,
        observable=observable,
        unit=unit,
        value=value,
        value_raw=raw,
        time=time,
        time_system="iso",
        uncertainty=uncertainty,
        conflict=conflict,
        source_ids=("src-a",),
    )


def table(
    rows, *, table_id="t1", warnings=(), columns=("object", "mag")
) -> ExtractedTable:
    return ExtractedTable(
        table_id=table_id,
        source_id="src-a",
        columns=tuple(columns),
        rows=tuple(dict(zip(columns, row, strict=True)) for row in rows),
        parse_warnings=tuple(warnings),
        confidence=ConfidenceBand.HIGH,
    )


def codes(issues) -> set[IssueCode]:
    return {issue.code for issue in issues}


def test_parser_warnings_become_structured_issues():
    issues = detect_table_warnings(
        [
            table(
                [("SN2005A", "16.5")],
                warnings=(
                    "possible_truncation",
                    "ragged_rows:2",
                    "extra_columns:1",
                    "encoding_suspect:cp1252",
                    "unknown_warning",
                ),
            )
        ]
    )

    found = codes(issues)
    assert IssueCode.TRUNCATED_TABLE in found
    assert IssueCode.DELIMITER_SUSPECT in found
    assert IssueCode.ENCODING_SUSPECT in found
    truncations = [issue for issue in issues if issue.code is IssueCode.TRUNCATED_TABLE]
    assert len(truncations) == 2, "each warning keeps its own evidence"
    assert all(issue.subject["table_id"] == "t1" for issue in issues)


def test_exact_duplicate_rows_are_reported_with_row_numbers():
    issues = detect_duplicate_rows(
        [
            table(
                [
                    ("SN2005A", "16.5"),
                    ("SN2005B", "17.1"),
                    ("SN2005A", "16.5"),
                ]
            )
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code is IssueCode.DUPLICATE_EXACT
    assert issue.evidence["rows"] == [3]
    assert issue.repair_mode is RepairMode.AUTO_SAFE


def test_nonfinite_values_are_caught_despite_looking_numeric():
    issues = detect_nonfinite(
        [record(value=float("inf"), raw="inf"), record(uid="rec:2", value=16.5)]
    )

    assert len(issues) == 1
    assert issues[0].code is IssueCode.NONFINITE_VALUE
    assert issues[0].evidence["value"] == "inf"


def test_mixed_units_without_exact_conversion_are_reported():
    mixed = [
        record(uid="rec:1", observable="flux_density", value=1.0, unit="mJy"),
        record(uid="rec:2", observable="flux_density", value=2.0, unit="erg/s/cm2/A"),
    ]

    issues = detect_unit_inconsistency(mixed, contract=contract(), plugin=PLUGIN)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code is IssueCode.UNIT_INCONSISTENCY
    assert set(issue.evidence["units"]) == {"mJy", "erg/s/cm2/A"}
    assert issue.repair_mode is RepairMode.AUTO_SAFE


def test_mixed_units_with_exact_conversion_need_no_issue():
    convertible = [
        record(uid="rec:1", observable="flux_density", value=1.0, unit="mJy"),
        record(uid="rec:2", observable="flux_density", value=2000.0, unit="uJy"),
    ]

    assert (
        detect_unit_inconsistency(convertible, contract=contract(), plugin=PLUGIN) == ()
    )


def test_units_with_different_dimensions_are_reported():
    broken = [
        record(uid="rec:1", observable="apparent_magnitude", value=16.5, unit="mag"),
        record(uid="rec:2", observable="apparent_magnitude", value=30.0, unit="mJy"),
    ]

    issues = detect_unit_inconsistency(broken, contract=contract(), plugin=PLUGIN)

    assert len(issues) == 1
    assert issues[0].evidence["exact"]["mJy"] is False


@pytest.mark.parametrize(
    ("value", "unit", "flagged"),
    [(16.5, "mag", False), (42.0, "mag", True), (-4.0, "mag", True)],
)
def test_out_of_range_uses_contract_ranges(value, unit, flagged):
    issues = detect_out_of_range(
        [record(value=value, unit=unit)], contract=contract(), plugin=PLUGIN
    )

    assert (len(issues) == 1) is flagged
    if flagged:
        assert issues[0].code is IssueCode.OUT_OF_RANGE
        assert issues[0].repair_mode is RepairMode.SUGGEST_ONLY


def test_negative_value_of_positive_quantity_needs_a_human():
    issues = detect_sign_suspect(
        [record(observable="magnitude_uncertainty", value=-0.5, unit="mag")],
        plugin=PLUGIN,
    )

    assert len(issues) == 1
    assert issues[0].code is IssueCode.SIGN_OR_PHASE_SUSPECT
    assert issues[0].repair_mode is RepairMode.HUMAN_REQUIRED
    assert (
        default_repair_mode(IssueCode.SIGN_OR_PHASE_SUSPECT)
        is RepairMode.HUMAN_REQUIRED
    )


def test_missing_required_observable_is_reported_per_entity():
    issues = detect_missing_coverage(
        [record(uid="rec:1"), record(uid="rec:2", time="2000-01-03")],
        contract=contract(),
        plugin=PLUGIN,
    )

    subjects = {
        (issue.subject["entity_id"], issue.subject.get("observable"))
        for issue in issues
    }
    assert ("obj:sn2005a", "apparent_magnitude") not in subjects
    assert all(issue.code is IssueCode.MISSING_COVERAGE for issue in issues)


def test_entity_below_minimum_record_count_is_flagged():
    sparse = contract(min_records_per_entity=5)
    issues = detect_missing_coverage(
        [record(uid="rec:1")], contract=sparse, plugin=PLUGIN
    )

    counts = [issue for issue in issues if issue.subject.get("scope") == "record_count"]
    assert counts, "record-count shortfall must be reported"
    assert counts[0].detail.endswith("below the contract minimum of 5.")


def test_coverage_gap_detects_the_largest_hole():
    points = [
        record(uid="rec:1", time="2000-01-02"),
        record(uid="rec:2", time="2000-01-03"),
        record(uid="rec:3", time="2000-03-03"),
    ]

    issues = detect_coverage_gaps(points, plugin=PLUGIN)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.code is IssueCode.COVERAGE_GAP
    assert issue.evidence["gap_days"] == pytest.approx(60.0)
    assert issue.repair_mode is RepairMode.SUGGEST_ONLY


def test_evenly_sampled_series_has_no_gap_issue():
    points = [
        record(uid=f"rec:{index}", time=f"2000-01-{day:02d}")
        for index, day in enumerate(range(1, 11), start=1)
    ]

    assert detect_coverage_gaps(points, plugin=PLUGIN) == ()


def test_conflicting_records_are_reported_as_errors():
    conflicting = record(conflict=True, raw="16.5 | 16.9")

    issues = detect_conflicts([conflicting, record(uid="rec:2")])

    assert len(issues) == 1
    assert issues[0].code is IssueCode.CROSS_SOURCE_CONFLICT
    assert issues[0].severity is IssueSeverity.ERROR
    assert issues[0].repair_mode is RepairMode.HUMAN_REQUIRED


def test_ambiguous_entities_are_reported():
    entity = EntityRef(
        entity_id="obj:sn2005a",
        label="SN 2005A",
        ambiguous_with=("obj:sn2005a#2",),
    )

    issues = detect_entity_ambiguity([entity])

    assert len(issues) == 1
    assert issues[0].code is IssueCode.ENTITY_IDENTITY_AMBIGUOUS
    assert issues[0].blocking is False


def test_detect_all_merges_every_detector():
    report = detect_all(
        records=[
            record(uid="rec:1"),
            record(uid="rec:2", time="2000-01-03"),
            record(uid="rec:3", value=float("nan"), raw="nan"),
            record(uid="rec:4", conflict=True, raw="16.5 | 16.9"),
        ],
        tables=[table([("SN2005A", "16.5"), ("SN2005A", "16.5")])],
        entities=[EntityRef(entity_id="obj:x", label="X", ambiguous_with=("obj:y",))],
        contract=contract(),
        plugin=PLUGIN,
    )

    found = codes(report.issues)
    assert IssueCode.NONFINITE_VALUE in found
    assert IssueCode.CROSS_SOURCE_CONFLICT in found
    assert IssueCode.DUPLICATE_EXACT in found
    assert IssueCode.ENTITY_IDENTITY_AMBIGUOUS in found
    assert report.stats["records"] == 4
    assert report.stats["missing_records"] == 1
    assert report.stats["by_severity"]["error"] >= 2


def test_detect_all_is_deterministic():
    first = detect_all(
        records=[record(uid="rec:1"), record(uid="rec:2", value=99.0)],
        contract=contract(),
        plugin=PLUGIN,
    )
    second = detect_all(
        records=[record(uid="rec:1"), record(uid="rec:2", value=99.0)],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert [issue.issue_id for issue in first.issues] == [
        issue.issue_id for issue in second.issues
    ]


def test_summarise_counts_codes_severities_and_modes():
    issues = detect_all(
        records=[record(uid="rec:1", value=float("inf"), raw="inf")],
        contract=contract(),
        plugin=PLUGIN,
    ).issues

    summary = summarise(issues)
    assert summary["by_code"]["nonfinite_value"] == 1
    assert summary["by_severity"]["error"] == 1
    assert summary["by_mode"]["auto_safe"] == 1


def test_detection_report_is_serialisable():
    report = detect_all(
        records=[record(uid="rec:1", value=float("inf"), raw="inf")],
        contract=contract(),
        plugin=PLUGIN,
    )

    payload = report.to_dict()
    assert payload["stats"]["records"] == 1
    assert payload["issues"][0]["code"] == "nonfinite_value"
    assert report.of_stage("quality")

"""Repair tests: automatic only where it is reversible, human where it matters."""

from __future__ import annotations

import pytest

from jw.scientific_data.contracts import (
    ConfidenceBand,
    DataRequirementContract,
    ExtractedTable,
    IssueCode,
    IssueState,
    MissingReason,
    ObservationField,
    ObservationRecord,
    QualityIssue,
    RepairMode,
    default_repair_mode,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.quality import (
    detect_duplicate_rows,
    detect_nonfinite,
    detect_table_warnings,
    detect_unit_inconsistency,
)
from jw.scientific_data.repair import (
    apply_auto_repairs,
    describe_repair,
    plan_repairs,
    repair_severity,
    unresolved_signals,
)

PLUGIN = get_plugin("snia_light_curve")


def contract(**overrides) -> DataRequirementContract:
    fields: dict = {
        "contract_id": "c-repair",
        "research_goal": "Repair safe defects only",
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
        "min_records_per_entity": 1,
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
    flags=(),
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
        conflict=conflict,
        flags=tuple(flags),
        source_ids=("src-a",),
    )


def table(
    rows, *, table_id="t1", warnings=(), columns=("object", "mag")
) -> ExtractedTable:
    return ExtractedTable(
        table_id=table_id,
        source_id="src-a",
        columns=tuple(columns),
        rows=tuple(
            {**dict(zip(columns, row, strict=True)), "_row_index": index + 2}
            for index, row in enumerate(rows)
        ),
        parse_warnings=tuple(warnings),
        confidence=ConfidenceBand.HIGH,
    )


def issue(code: IssueCode, *, subject=None, evidence=None) -> QualityIssue:
    return QualityIssue(
        issue_id=f"issue:test:{code}:{(subject or {}).get('record_uid', 'x')}",
        code=code,
        severity="error",
        stage="quality",
        subject=dict(subject or {}),
        detail="seeded",
        evidence=dict(evidence or {}),
        repair_mode=default_repair_mode(code),
    )


def test_only_reversible_repairs_are_planned_automatically():
    decisions = {
        decision.code: decision
        for decision in plan_repairs(
            [
                issue(IssueCode.DUPLICATE_EXACT),
                issue(IssueCode.NONFINITE_VALUE),
                issue(IssueCode.UNIT_INCONSISTENCY),
                issue(IssueCode.TRUNCATED_TABLE),
                issue(IssueCode.CROSS_SOURCE_CONFLICT),
                issue(IssueCode.SIGN_OR_PHASE_SUSPECT),
                issue(IssueCode.UNIT_UNKNOWN),
                issue(IssueCode.MISSING_VALUE),
            ],
            contract=contract(),
        )
    }

    automatic = {code for code, decision in decisions.items() if decision.automatic}
    assert automatic == {
        IssueCode.DUPLICATE_EXACT,
        IssueCode.NONFINITE_VALUE,
        IssueCode.UNIT_INCONSISTENCY,
        IssueCode.TRUNCATED_TABLE,
    }
    assert decisions[IssueCode.CROSS_SOURCE_CONFLICT].mode is RepairMode.HUMAN_REQUIRED
    assert decisions[IssueCode.SIGN_OR_PHASE_SUSPECT].automatic is False
    assert decisions[IssueCode.MISSING_VALUE].mode is RepairMode.FORBIDDEN
    assert decisions[IssueCode.MISSING_VALUE].reason == "forbidden_by_policy"


def test_duplicate_rows_are_dropped_and_logged_with_hashes():
    source = table(
        [
            ("SN2005A", "16.5"),
            ("SN2005B", "17.1"),
            ("SN2005A", "16.5"),
        ]
    )
    issues = detect_duplicate_rows([source])

    outcome = apply_auto_repairs(
        records=(),
        issues=issues,
        tables=[source],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert len(outcome.tables[0].rows) == 2
    assert len(outcome.applied) == 1
    entry = outcome.applied[0]
    assert entry.code is IssueCode.DUPLICATE_EXACT
    assert entry.reversible is True
    assert entry.revalidation == "passed"
    assert entry.before_hash.startswith("sha256:")
    assert entry.before_hash != entry.after_hash
    assert outcome.issues[0].state is IssueState.REVALIDATED
    assert outcome.issues[0].auto_applied is True


def test_truncated_table_drops_only_the_final_row():
    source = table(
        [("SN2005A", "16.5"), ("SN2005B", "17.1"), ("SN2005C", "")],
        warnings=("possible_truncation",),
    )
    issues = detect_table_warnings([source])
    truncations = [item for item in issues if item.code is IssueCode.TRUNCATED_TABLE]

    outcome = apply_auto_repairs(
        records=(),
        issues=truncations,
        tables=[source],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert [row["object"] for row in outcome.tables[0].rows] == [
        "SN2005A",
        "SN2005B",
    ]
    assert outcome.applied[0].before["last_row"]["object"] == "SN2005C"


def test_nonfinite_value_becomes_missing_and_keeps_raw_text():
    records = [record(value=float("inf"), raw="Infinity")]
    issues = detect_nonfinite(records)

    outcome = apply_auto_repairs(
        records=records, issues=issues, contract=contract(), plugin=PLUGIN
    )

    repaired = outcome.records[0]
    assert repaired.value is None
    assert repaired.missing_reason is MissingReason.NOT_REPORTED
    assert repaired.value_raw == "Infinity", "the raw text is never overwritten"
    assert "repaired:nonfinite_to_missing" in repaired.flags
    assert outcome.applied[0].code is IssueCode.NONFINITE_VALUE
    assert outcome.stats["auto_applied"] == 1


def test_partial_unit_repair_converts_what_is_exact_and_flags_the_rest():
    records = [
        record(uid="rec:jy", observable="flux_density", value=16.5, unit="Jy"),
        record(uid="rec:mjy", observable="flux_density", value=1000.0, unit="mJy"),
        record(uid="rec:mag", observable="flux_density", value=16.5, unit="mag"),
    ]
    issues = detect_unit_inconsistency(records, contract=contract(), plugin=PLUGIN)

    outcome = apply_auto_repairs(
        records=records, issues=issues, contract=contract(), plugin=PLUGIN
    )

    by_uid = {item.record_uid: item for item in outcome.records}
    assert by_uid["rec:jy"].value == pytest.approx(16500.0)
    assert by_uid["rec:jy"].unit == "mJy"
    assert by_uid["rec:jy"].value_raw == "16.5", "raw value survives the repair"
    assert any(
        flag.startswith("repaired:unit_converted:Jy->mJy")
        for flag in by_uid["rec:jy"].flags
    )
    assert by_uid["rec:mjy"].value == 1000.0, "already-canonical records stay put"
    assert by_uid["rec:mag"].unit == "mag", "a magnitude is not a flux; never guessed"
    assert outcome.issues[0].state is IssueState.REVALIDATION_FAILED
    assert outcome.pending, "the residual unit mix still needs a human"
    assert outcome.applied[0].revalidation == "failed"


def test_fully_convertible_mix_is_normalised_without_a_human():
    records = [
        record(uid="rec:jy", observable="flux_density", value=16.5, unit="Jy"),
        record(uid="rec:ujy", observable="flux_density", value=5.0, unit="uJy"),
    ]
    reported = [
        issue(IssueCode.UNIT_INCONSISTENCY, subject={"observable": "flux_density"})
    ]

    outcome = apply_auto_repairs(
        records=records, issues=reported, contract=contract(), plugin=PLUGIN
    )

    by_uid = {item.record_uid: item for item in outcome.records}
    assert by_uid["rec:jy"].value == pytest.approx(16500.0)
    assert by_uid["rec:ujy"].unit == "mJy"
    assert {entry.revalidation for entry in outcome.applied} == {"passed"}
    assert outcome.issues[0].state is IssueState.REVALIDATED
    assert outcome.pending == ()


def test_human_bound_issue_stays_pending_and_is_never_applied():
    records = [record(conflict=True, raw="16.5 | 16.9")]
    issues = [
        QualityIssue(
            issue_id="issue:test:conflict",
            code=IssueCode.CROSS_SOURCE_CONFLICT,
            severity="error",
            stage="quality",
            subject={"entity_id": "obj:sn2005a", "observable": "apparent_magnitude"},
            detail="two sources disagree",
            repair_mode=RepairMode.HUMAN_REQUIRED,
        )
    ]

    outcome = apply_auto_repairs(
        records=records, issues=issues, contract=contract(), plugin=PLUGIN
    )

    assert outcome.applied == ()
    assert [item.state for item in outcome.issues] == [IssueState.AWAITING_HUMAN]
    assert len(outcome.pending) == 1
    assert outcome.records[0].value == 16.5, "no silent averaging or deletion"
    assert unresolved_signals(outcome.records) == (
        "obj:sn2005a apparent_magnitude @ 2000-01-02: 16.5 | 16.9",
    )


def test_repair_is_deterministic_for_the_same_input():
    source = table([("SN2005A", "16.5"), ("SN2005A", "16.5")])
    issues = detect_duplicate_rows([source])
    kwargs = {
        "records": (),
        "issues": issues,
        "tables": [source],
        "contract": contract(),
        "plugin": PLUGIN,
    }

    first = apply_auto_repairs(**kwargs)
    second = apply_auto_repairs(**kwargs)

    assert first.applied[0].repair_id == second.applied[0].repair_id
    assert first.applied[0].after_hash == second.applied[0].after_hash


def test_describe_and_severity_are_report_ready():
    source = table([("SN2005A", "16.5"), ("SN2005A", "16.5")])
    outcome = apply_auto_repairs(
        records=(),
        issues=detect_duplicate_rows([source]),
        tables=[source],
        contract=contract(),
        plugin=PLUGIN,
    )
    entry = outcome.applied[0]

    assert describe_repair(entry).startswith("[applied] duplicate_exact")
    assert repair_severity(entry).value in {"info", "warning"}
    assert outcome.to_dict()["applied"] == [entry.repair_id]

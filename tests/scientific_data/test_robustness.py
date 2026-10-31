"""Regression tests for real-world header and unit spellings.

These cover the failures found by the SN Ia evaluation corpus: ``flux_ujy``
silently treated as mJy, ``SNID``/``OBSMJD`` dropping a whole published table,
a separate ``unit`` column being ignored, duplicated conflict questions, and
PDF blocks disappearing without a receipt.
"""

from __future__ import annotations

import json
from pathlib import Path

from jw.scientific_data.alignment import align_tables
from jw.scientific_data.contracts import (
    DataRequirementContract,
    ExtractedTable,
    IssueCode,
    ObservationField,
)
from jw.scientific_data.documents import tables_from_text_report
from jw.scientific_data.domain import (
    get_plugin,
    strip_unit_from_header,
    unit_from_header,
)
from jw.scientific_data.human_loop import RequestState
from jw.scientific_data.semantics import map_table, profile_column
from jw.scientific_data.workflow import (
    WorkflowOptions,
    apply_human_answers,
    contract_from_request,
    run_integration,
)

PLUGIN = get_plugin("snia_light_curve")


def contract(**overrides) -> DataRequirementContract:
    payload = {
        "contract_id": "c-robust",
        "research_goal": "SN Ia light curves from heterogeneous tables",
        "entity_class": "supernova",
        "observables": (
            ObservationField(name="object", kind="identifier", required=True),
            ObservationField(name="time", kind="time", required=True),
            ObservationField(name="band", kind="label"),
            ObservationField(
                name="apparent_magnitude",
                kind="quantity",
                unit="mag",
                required=True,
            ),
            ObservationField(name="magnitude_uncertainty", kind="quality", unit="mag"),
            ObservationField(name="flux_density", kind="quantity", unit="mJy"),
        ),
        "identifier_fields": ("object",),
        "time_field": "time",
        "domain_plugin": "snia_light_curve",
    }
    payload.update(overrides)
    return DataRequirementContract(**payload)


def table(columns, rows, *, table_id="t1", source_id="src-a"):
    return ExtractedTable(
        table_id=table_id,
        source_id=source_id,
        columns=tuple(columns),
        rows=tuple(dict(zip(columns, row, strict=True)) for row in rows),
    )


def test_unit_suffix_is_read_and_stripped():
    assert unit_from_header("flux_ujy") == "ujy"
    assert strip_unit_from_header("flux_ujy") == "flux"
    profile = profile_column("flux_ujy", ["15600", "14200"])
    assert profile.inferred_unit == "ujy"
    assert profile.unit_source == "header"
    # A band letter must not be mistaken for a unit.
    assert unit_from_header("mag_g") == ""
    # An uncertainty marker is not a value measured in mag.
    assert unit_from_header("e_Mag") == ""


def test_concatenated_published_headers_map():
    extracted = table(
        ("SNID", "FILTER", "OBSMJD", "MAG", "MAGERR"),
        [
            ("SN 2018oh", "g", "58160.30", "15.42", "0.05"),
            ("SN 2019np", "r", "58510.25", "14.42", "0.03"),
        ],
    )
    mappings, issues = map_table(extracted, contract=contract(), plugin=PLUGIN)
    by_column = {mapping.source_column: mapping for mapping in mappings}
    assert by_column["SNID"].observable == "object"
    assert by_column["OBSMJD"].observable == "time"
    assert by_column["MAGERR"].observable == "magnitude_uncertainty"
    assert all(mapping.applied for mapping in mappings)
    assert not [issue for issue in issues if issue.code is IssueCode.FIELD_UNMAPPED]


def test_missing_source_unit_is_an_issue_not_an_assumption():
    extracted = table(
        ("object", "band", "mjd", "flux"),
        [("SN 2018oh", "V", "58160.30", "1.5")],
    )
    result = align_tables([extracted], contract=contract(), plugin=PLUGIN)
    assert len(result.records) == 1
    record = result.records[0]
    assert record.value == 1.5
    assert record.unit == ""
    assert "unit_missing" in record.flags
    assert any(issue.code is IssueCode.UNIT_UNKNOWN for issue in result.issues)


def test_unit_column_is_applied_and_mismatch_is_reported():
    extracted = table(
        ("object", "band", "mjd", "flux", "unit"),
        [("SN 2021aef", "R", "59254.10", "1520.5", "counts/s")],
    )
    mappings, _issues = map_table(extracted, contract=contract(), plugin=PLUGIN)
    flux = next(mapping for mapping in mappings if mapping.source_column == "flux")
    assert flux.source_unit == "counts/s"
    result = align_tables([extracted], contract=contract(), plugin=PLUGIN)
    record = result.records[0]
    assert record.value == 1520.5
    assert record.unit == "counts/s"
    assert any(issue.code is IssueCode.UNIT_INCONSISTENCY for issue in result.issues)


def test_pdf_column_mismatch_is_reported_not_silent():
    text = "\n".join(
        [
            "Table 4. B-band photometry of SN 2021aef",
            "object      band  date        mag   err",
            "SN 2021aef  B     2021-03-09  14.90 0.04",
            "SN 2021aef  B     2021-03-11  14.84 0.04",
        ]
    )
    tables, rejected = tables_from_text_report(text, source_id="src-pdf")
    assert tables == ()
    assert rejected
    assert rejected[0]["reason"] == "inconsistent_columns"


def test_single_space_pdf_table_is_recovered():
    text = "\n".join(
        [
            "Table 5. B-band photometry of SN 2021aef",
            "object band date mag err",
            "SN2021aef B 2021-03-09 14.90 0.04",
            "SN2021aef B 2021-03-11 14.84 0.04",
        ]
    )
    tables, rejected = tables_from_text_report(text, source_id="src-pdf")
    assert len(tables) == 1
    assert rejected == ()
    assert len(tables[0].rows) == 2


def _conflict_inputs(root: Path) -> tuple[Path, Path]:
    first = root / "first.csv"
    first.write_text(
        "object,band,mjd,mag\nSN 2018oh,V,58160.30,15.10\n", encoding="utf-8"
    )
    second = root / "second.csv"
    second.write_text(
        "object,band,mjd,mag\nSN 2018oh,V,58160.30,15.40\n", encoding="utf-8"
    )
    return first, second


def test_conflict_is_asked_once_and_answer_closes_it(tmp_path):
    first, second = _conflict_inputs(tmp_path)
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(
            {
                "research_goal": "conflict",
                "domain": "snia_light_curve",
                "observables": ["apparent_magnitude", "time", "object", "band"],
                "entity_class": "supernova",
            }
        ),
        inputs=(first, second),
        options=WorkflowOptions(),
        run_id="conflict-once",
    )
    conflicts = [
        issue
        for issue in result.issues
        if issue.code is IssueCode.CROSS_SOURCE_CONFLICT
    ]
    assert len(conflicts) == 1
    questions = [
        request
        for request in result.queue.requests
        if request.code is IssueCode.CROSS_SOURCE_CONFLICT
    ]
    assert len(questions) == 1
    question = questions[0]
    assert question.blocking is True
    keep = next(option for option in question.options if option.startswith("keep:"))
    updated = apply_human_answers(
        workspace=tmp_path / "ws",
        run_id="conflict-once",
        answers={question.request_id: keep},
    )
    remaining = [
        request
        for request in updated.queue.requests
        if request.code is IssueCode.CROSS_SOURCE_CONFLICT
        and request.state is RequestState.OPEN
    ]
    assert remaining == []


def test_out_of_range_is_reported_once(tmp_path):
    path = tmp_path / "outlier.csv"
    path.write_text(
        "object,band,mjd,mag\nSN 2018oh,V,58164.29,35.20\n", encoding="utf-8"
    )
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(
            {
                "research_goal": "outlier",
                "domain": "snia_light_curve",
                "observables": ["apparent_magnitude", "time", "object", "band"],
                "entity_class": "supernova",
            }
        ),
        inputs=(path,),
        options=WorkflowOptions(),
        run_id="outlier-once",
    )
    ranges = [issue for issue in result.issues if issue.code is IssueCode.OUT_OF_RANGE]
    assert len(ranges) == 1
    assert result.records[0].value == 35.2


def test_column_override_repairs_an_unmapped_table(tmp_path):
    path = tmp_path / "cryptic.csv"
    path.write_text("KEY,FLT,EPOCH,MAG\nSN 2018oh,V,58160.30,15.10\n", encoding="utf-8")
    request = {
        "research_goal": "override",
        "domain": "snia_light_curve",
        "observables": ["apparent_magnitude", "time", "object", "band"],
        "entity_class": "supernova",
    }
    first = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(request),
        inputs=(path,),
        options=WorkflowOptions(),
        run_id="override-before",
    )
    assert not first.records
    assert any(issue.code is IssueCode.MERGE_KEY_ABSENT for issue in first.issues)
    fixed = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(request),
        inputs=(path,),
        options=WorkflowOptions(
            column_overrides=(
                {"source": "cryptic.csv", "column": "KEY", "observable": "object"},
                {
                    "source": "cryptic.csv",
                    "column": "EPOCH",
                    "observable": "time",
                },
            )
        ),
        run_id="override-after",
    )
    assert len(fixed.records) == 1
    record = fixed.records[0]
    assert record.entity_id
    assert record.time
    assert record.value == 15.1


def test_xlsx_supplementary_table_is_read(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "photometry"
    sheet.append(["SN Name", "Filter", "MJD", "Magnitude"])
    sheet.append(["SN 2018oh", "V", 58160.30, 15.10])
    sheet.append(["SN 2018oh", "V", 58162.30, 14.80])
    path = tmp_path / "supplement.xlsx"
    workbook.save(path)
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(
            {
                "research_goal": "xlsx",
                "domain": "snia_light_curve",
                "observables": ["apparent_magnitude", "time", "object", "band"],
                "entity_class": "supernova",
            }
        ),
        inputs=(path,),
        options=WorkflowOptions(),
        run_id="xlsx",
    )
    assert len(result.records) == 2
    assert {record.value for record in result.records} == {15.1, 14.8}


def test_unsupported_source_format_has_a_receipt(tmp_path):
    good = tmp_path / "good.csv"
    good.write_text(
        "object,band,mjd,mag\nSN 2018oh,V,58160.30,15.10\n", encoding="utf-8"
    )
    unsupported = tmp_path / "table.fits"
    unsupported.write_text("not a real fits file", encoding="utf-8")
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(
            {
                "research_goal": "formats",
                "domain": "snia_light_curve",
                "observables": ["apparent_magnitude", "time", "object", "band"],
                "entity_class": "supernova",
            }
        ),
        inputs=(good, unsupported),
        options=WorkflowOptions(),
        run_id="formats",
    )
    extract_issues = [issue for issue in result.issues if str(issue.stage) == "extract"]
    assert any(
        issue.code is IssueCode.EMPTY_EXTRACT and str(issue.subject.get("source_id"))
        for issue in extract_issues
    )
    assert "not supported" in " ".join(issue.detail for issue in extract_issues)


def test_report_records_dropped_rows_and_axis_suspect(tmp_path):
    good = tmp_path / "good.csv"
    good.write_text(
        "object,band,mjd,mag\nSN 2018oh,V,58160.30,15.10\n", encoding="utf-8"
    )
    # A table with rows but no entity column: it is extracted, then dropped.
    dropped = tmp_path / "dropped.csv"
    dropped.write_text("band,mjd,mag\ng,58160.30,15.10\n", encoding="utf-8")
    result = run_integration(
        workspace=tmp_path / "ws",
        contract=contract_from_request(
            {
                "research_goal": "report",
                "domain": "snia_light_curve",
                "observables": ["apparent_magnitude", "time", "object", "band"],
                "entity_class": "supernova",
            }
        ),
        inputs=(good, dropped),
        options=WorkflowOptions(),
        run_id="report",
    )
    report = Path(result.package.files["report"]).read_text(encoding="utf-8")
    assert "没有任何行进入合并表" in report
    assert "drop" in report or "dropped" in report or "dropped.csv" in report
    assert "本次来源均由用户提供" in report
    manifest = json.loads(
        Path(result.package.files["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["counts"]["records"] >= 1

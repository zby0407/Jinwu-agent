"""Package writer: the merged table, the log files and the manifest."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from jw.scientific_data.contracts import (
    DataRequirementContract,
    EntityRef,
    ExtractedTable,
    FieldMapping,
    IntegrationLedger,
    IssueCode,
    IssueSeverity,
    IssueState,
    ObservationField,
    ObservationRecord,
    ProvenanceRef,
    QualityIssue,
    RepairLedgerEntry,
    RepairMode,
    default_repair_mode,
    file_digest,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.figures import (
    DigitisedSeries,
    FigureCalibration,
    digitise_points,
    fit_axis,
    overlay_report,
)
from jw.scientific_data.human_loop import build_queue
from jw.scientific_data.package import FigurePackage, write_package
from jw.scientific_data.sources import SourceCatalog


def _contract() -> DataRequirementContract:
    return DataRequirementContract(
        contract_id="snia-demo",
        research_goal="Collect SN Ia light-curve photometry across sources",
        entity_class="supernova",
        observables=(
            ObservationField(name="apparent_magnitude", kind="quantity", unit="mag"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="object", kind="identifier"),
        ),
        domain_plugin="snia_light_curve",
    )


def _record(uid: str, value: float | None, **overrides) -> ObservationRecord:
    payload = {
        "record_uid": uid,
        "entity_id": "obj:sn2005cf",
        "observable": "apparent_magnitude",
        "unit": "mag",
        "value": value,
        "value_raw": "" if value is None else f"{value}",
        "time": "2005-06-01T00:00:00Z",
        "time_system": "iso",
        "source_ids": ("src:local:001",),
        "provenance": (
            ProvenanceRef(
                source_id="src:local:001",
                locator={"path": "table1.csv", "line_start": 2, "line_end": 2},
                extractor="jw.scientific_data.tabular.read_table",
                extractor_version="1",
                confidence_score=0.9,
            ),
        ),
    }
    payload.update(overrides)
    return ObservationRecord(**payload)


def _ledger() -> IntegrationLedger:
    ledger = IntegrationLedger(run_id="run-demo", contract=_contract())
    catalog = SourceCatalog()
    source = catalog.add(
        url="file://table1.csv",
        title="Table 1 photometry",
        role="authored_table",
        provider="local",
    )
    ledger.add_source(source)
    ledger.add_table(
        ExtractedTable(
            table_id="table1",
            source_id=source.source_id,
            columns=("MJD", "mag"),
            rows=({"MJD": "53520", "mag": "16.5"},),
        )
    )
    ledger.add_entity(EntityRef(entity_id="obj:sn2005cf", label="SN 2005cf"))
    ledger.add_record(_record("rec:1", 16.5))
    ledger.add_record(_record("rec:2", None, missing_reason="not_reported"))
    ledger.mappings.append(
        FieldMapping(
            mapping_id="map:table1:mag",
            source_id="src:local:001",
            table_id="table1",
            source_column="mag",
            observable="apparent_magnitude",
            evidence="exact_head",
            target_unit="mag",
        )
    )
    conflict = QualityIssue(
        issue_id="issue:quality:cross_source_conflict:x",
        code=IssueCode.CROSS_SOURCE_CONFLICT,
        severity=IssueSeverity.ERROR,
        stage="quality",
        subject={"entity_id": "obj:sn2005cf"},
        detail="Two sources disagree on the magnitude.",
        repair_mode=default_repair_mode(IssueCode.CROSS_SOURCE_CONFLICT),
    )
    ledger.add_issue(conflict)
    duplicate = QualityIssue(
        issue_id="issue:quality:duplicate_exact:y",
        code=IssueCode.DUPLICATE_EXACT,
        severity=IssueSeverity.WARNING,
        stage="quality",
        state=IssueState.REVALIDATED,
        auto_applied=True,
        subject={"rows": [4, 5]},
        detail="Two identical rows.",
        repair_mode=RepairMode.AUTO_SAFE,
    )
    ledger.add_issue(duplicate)
    ledger.add_repair(
        RepairLedgerEntry(
            repair_id="repair:1",
            issue_id=duplicate.issue_id,
            code=IssueCode.DUPLICATE_EXACT,
            mode=RepairMode.AUTO_SAFE,
            actor="kernel:auto",
            target="table1",
            before="rows=5",
            after="rows=4",
            rationale="identical rows carry the same information",
            revalidation="passed",
        )
    )
    return ledger


def _figure() -> FigurePackage:
    calibration = FigureCalibration(
        x=fit_axis([(100.0, 53500.0), (300.0, 53540.0)], axis="x", unit="MJD"),
        y=fit_axis([(400.0, 20.0), (200.0, 10.0)], axis="y", unit="mag"),
        pixel_noise=1.0,
        figure_id="fig1",
        source_id="src:local:002",
    )
    series = DigitisedSeries(label="V", points=((200.0, 300.0),))
    records = digitise_points(
        series, calibration, entity_id="obj:sn2005cf", observable="apparent_magnitude"
    )
    return FigurePackage(
        figure_id="fig1",
        source_id="src:local:002",
        calibration=calibration,
        records=records,
        overlay=overlay_report(records, calibration),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_package_writes_every_standard_file(tmp_path):
    ledger = _ledger()
    result = write_package(
        directory=tmp_path / "pkg",
        ledger=ledger,
        plugin=get_plugin("snia_light_curve"),
        queue=build_queue(ledger.issues.values()),
        figures=(_figure(),),
    )
    root = Path(result.directory)
    for name in (
        "integrated_observations.csv",
        "source_catalog.csv",
        "field_mapping.csv",
        "quality_issues.csv",
        "repair_log.csv",
        "provenance.jsonl",
        "data_quality_report.md",
        "run_manifest.json",
        "human_review.json",
        "README_data.md",
    ):
        assert (root / name).exists(), name
    assert (root / "figure_overlays" / "fig1.csv").exists()


def test_observations_keep_missing_rows_and_provenance(tmp_path):
    result = write_package(directory=tmp_path / "pkg", ledger=_ledger())
    rows = _read_csv(Path(result.files["observations"]))
    assert len(rows) == 2
    assert rows[0]["record_uid"] == "rec:1"
    assert rows[0]["value"] == "16.5"
    assert rows[0]["unit"] == "mag"
    assert rows[0]["source_count"] == "1"
    assert "table1.csv" in rows[0]["provenance"]
    missing = next(row for row in rows if row["record_uid"] == "rec:2")
    assert missing["value"] == ""
    assert missing["missing_reason"] == "not_reported"


def test_issue_and_repair_rows_are_ordered_and_complete(tmp_path):
    result = write_package(directory=tmp_path / "pkg", ledger=_ledger())
    issues = _read_csv(Path(result.files["issues"]))
    assert [row["issue_id"] for row in issues] == sorted(
        row["issue_id"] for row in issues
    )
    conflict = next(row for row in issues if row["code"] == "cross_source_conflict")
    assert conflict["blocking"] == "yes"
    assert conflict["repair_mode"] == "human_required"
    duplicates = next(row for row in issues if row["code"] == "duplicate_exact")
    assert duplicates["auto_applied"] == "yes"
    assert json.loads(duplicates["subject"]) == {"rows": [4, 5]}
    repairs = _read_csv(Path(result.files["repairs"]))
    assert repairs[0]["actor"] == "kernel:auto"
    assert repairs[0]["revalidation"] == "passed"


def test_catalog_and_mapping_rows_round_trip(tmp_path):
    result = write_package(directory=tmp_path / "pkg", ledger=_ledger())
    catalog = _read_csv(Path(result.files["sources"]))
    assert catalog[0]["source_id"] == "src:local:001"
    assert catalog[0]["role"] == "authored_table"
    mappings = _read_csv(
        Path(result.files["sources"].replace("source_catalog", "field_mapping"))
    )
    assert mappings[0]["source_column"] == "mag"
    assert mappings[0]["observable"] == "apparent_magnitude"
    assert mappings[0]["applied"] == "yes"


def test_provenance_jsonl_records_each_layer(tmp_path):
    result = write_package(directory=tmp_path / "pkg", ledger=_ledger())
    lines = Path(result.files["provenance"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["record_uid"] == "rec:1"
    assert payload["provenance"][0]["locator"]["line_start"] == 2


def test_manifest_hashes_match_the_files(tmp_path):
    result = write_package(directory=tmp_path / "pkg", ledger=_ledger())
    root = Path(result.directory)
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    listed = {item["role"]: item["sha256"] for item in manifest["artifacts"]}
    observations = listed["observations"]
    assert observations == file_digest(root / "integrated_observations.csv")
    assert manifest["contract_fingerprint"] == _contract().fingerprint
    assert manifest["counts"]["records"] == 2


def test_report_and_readme_describe_the_run(tmp_path):
    result = write_package(
        directory=tmp_path / "pkg",
        ledger=_ledger(),
        plugin=get_plugin("snia_light_curve"),
        figures=(_figure(),),
    )
    report = Path(result.files["report"]).read_text(encoding="utf-8")
    assert "# 数据整合质量报告" in report
    assert "图表识别、提取与校验" in report
    assert "snia_light_curve" in report
    assert "cross_source_conflict" in report
    assert "digitized" in report
    readme = Path(result.files["readme"]).read_text(encoding="utf-8")
    assert "integrated_observations.csv" in readme
    assert "missing_reason" in readme


def test_figure_overlay_file_carries_residuals(tmp_path):
    result = write_package(
        directory=tmp_path / "pkg", ledger=_ledger(), figures=(_figure(),)
    )
    rows = _read_csv(Path(result.directory) / "figure_overlays" / "fig1.csv")
    assert rows[0]["series"] == "V"
    assert rows[0]["pixel_x"] == "200.0"
    assert float(rows[0]["pixel_residual"]) == 0.0
    assert "figure_overlays/fig1" in result.digests


def test_two_runs_are_byte_identical(tmp_path):
    first = write_package(directory=tmp_path / "a", ledger=_ledger())
    second = write_package(directory=tmp_path / "b", ledger=_ledger())
    assert first.digests["observations"] == second.digests["observations"]
    assert first.digests["issues"] == second.digests["issues"]
    assert first.digests["repairs"] == second.digests["repairs"]
    assert first.digests["provenance"] == second.digests["provenance"]

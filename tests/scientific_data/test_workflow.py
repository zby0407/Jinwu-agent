"""End-to-end workflow: two tabulated sources plus one figure, one package."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from jw.scientific_data.contracts import IssueCode, IssueState, RepairMode
from jw.scientific_data.human_loop import RequestState
from jw.scientific_data.workflow import (
    FigureInput,
    WorkflowOptions,
    contract_from_request,
    run_integration,
)

SOURCE_A = """\
# Table 1: V-band photometry of SN 2005cf
name,mjd,band,mag,mag_err,flux (mJy)
SN 2005cf,53520.0,V,15.10,0.05,12.30
SN 2005cf,53522.0,V,14.80,0.04,21.40
SN 2005cf,53522.0,V,14.80,0.04,21.40
SN 2005cf,53524.0,V,14.60,0.04,30.10
SN 2005cf,53526.0,V,inf,0.04,41.00
SN 2005cf,53528.0,V,14.30,0.05,n/a
"""

SOURCE_B = """\
object,time,filter,magnitude,flux [uJy]
SN 2005cf,53520.0,V,15.40,12300
SN 2005cf,53530.0,V,14.20,52000
"""

FIGURE_PAYLOAD = {
    "figure_id": "fig-lightcurve",
    "pixel_noise": 1.0,
    "x_ticks": [{"pixel": 100, "value": 53536}, {"pixel": 300, "value": 53576}],
    "y_ticks": [{"pixel": 400, "value": 16.0}, {"pixel": 200, "value": 15.0}],
    "y_scale": "linear",
    "y_unit": "mag",
    "observable": "apparent_magnitude",
    "unit": "mag",
    "entity": "SN 2005cf",
    "series": [
        {
            "label": "V",
            "colour": "#0000ff",
            "points": [[200, 350], [250, 300]],
        }
    ],
}


def _write(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    first = tmp_path / "table_a.csv"
    first.write_text(SOURCE_A, encoding="utf-8")
    second = tmp_path / "table_b.csv"
    second.write_text(SOURCE_B, encoding="utf-8")
    return first, second


def _contract():
    return contract_from_request(
        {
            "research_goal": "Collect SN Ia light-curve photometry across sources",
            "domain": "snia_light_curve",
            "observables": [
                "apparent_magnitude",
                "flux_density",
                "time",
                "object",
                "band",
            ],
            "entity_class": "supernova",
        }
    )


def _run(tmp_path: Path, **option_overrides):
    first, second = _write(tmp_path)
    options = WorkflowOptions(
        figure_inputs=(FigureInput("fig-lightcurve", FIGURE_PAYLOAD),),
        **option_overrides,
    )
    return run_integration(
        workspace=tmp_path / "ws",
        contract=_contract(),
        inputs=(first, second),
        options=options,
        run_id="run-e2e",
    )


def test_contract_compilation_records_ambiguities():
    contract = contract_from_request(
        {"research_goal": "", "domain": "snia_light_curve", "observables": ["bogus"]}
    )
    assert "research_goal_missing" in contract.ambiguities
    assert "unknown_observable:bogus" in contract.ambiguities
    assert contract.domain_plugin == "snia_light_curve"
    explicit = contract_from_request(
        {
            "goal": "light curves",
            "domain": "snia_light_curve",
            "observables": ["apparent_magnitude"],
            "coverage_window": ["2005-01-01", "2006-01-01"],
        }
    )
    assert explicit.research_goal == "light curves"
    assert explicit.coverage_window == ("2005-01-01", "2006-01-01")
    assert explicit.observables[0].unit == "mag"


def test_end_to_end_pipeline_produces_a_package(tmp_path):
    result = _run(tmp_path)
    summary = result.summary()
    assert summary["counts"]["records"] > 0
    for stage in (
        "discover",
        "acquire",
        "extract",
        "semantics",
        "align",
        "quality",
        "repair",
        "human",
        "revalidate",
        "package",
    ):
        assert stage in summary["stages"], stage
    package = result.package
    assert package is not None
    root = Path(package.directory)
    assert (root / "integrated_observations.csv").is_file()
    assert (root / "data_quality_report.md").is_file()
    assert (root / "run_manifest.json").is_file()
    assert (root / "figure_overlays" / "fig-lightcurve.csv").is_file()


def test_records_carry_source_and_digitised_provenance(tmp_path):
    result = _run(tmp_path)
    records = list(result.records)
    assert {record.observable for record in records} >= {
        "apparent_magnitude",
        "flux_density",
    }
    digitised = [record for record in records if str(record.value_kind) == "digitized"]
    assert digitised, "figure points must be digitised records"
    assert all("digitised_from_figure" in record.flags for record in digitised)
    assert all(record.provenance for record in records)


def test_unit_conversion_happens_and_is_flagged(tmp_path):
    result = _run(tmp_path)
    flux = [record for record in result.records if record.observable == "flux_density"]
    assert flux
    units = {record.unit for record in flux}
    assert units == {"mJy"}
    converted = [
        record
        for record in flux
        if any(flag.startswith("unit_converted:") for flag in record.flags)
    ]
    assert converted, "uJy values must be converted to the canonical mJy"
    values = sorted(record.value for record in converted)
    assert values == [12.3, 52.0]


def test_cross_source_conflict_waits_for_a_human(tmp_path):
    result = _run(tmp_path)
    conflicts = [
        issue
        for issue in result.issues
        if issue.code is IssueCode.CROSS_SOURCE_CONFLICT
    ]
    assert conflicts, "two sources disagree on the same magnitude"
    question = next(
        request
        for request in result.queue.requests
        if request.code is IssueCode.CROSS_SOURCE_CONFLICT
    )
    assert question.blocking is True
    assert question.state is RequestState.OPEN
    assert any(option.startswith("keep:") for option in question.options)
    conflict_records = [record for record in result.records if record.conflict]
    assert conflict_records
    assert "|" in conflict_records[0].value_raw


def test_duplicate_row_and_nonfinite_value_are_repaired(tmp_path):
    result = _run(tmp_path)
    duplicated = [
        issue for issue in result.issues if issue.code is IssueCode.DUPLICATE_EXACT
    ]
    assert duplicated
    assert all(issue.state is IssueState.REVALIDATED for issue in duplicated)
    nonfinite = [
        entry
        for entry in result.ledger.repairs.values()
        if str(entry.code) == "nonfinite_value"
    ]
    assert nonfinite
    assert all(entry.applied and entry.reversible for entry in nonfinite)
    assert str(nonfinite[0].mode) == str(RepairMode.AUTO_SAFE)


def test_missing_value_keeps_its_reason(tmp_path):
    result = _run(tmp_path)
    missing = [record for record in result.records if record.value is None]
    assert missing
    assert {str(record.missing_reason) for record in missing} <= {
        "not_reported",
        "not_parsed",
        "not_applicable",
    }


def test_human_answers_are_applied(tmp_path):
    first = _run(tmp_path)
    question = next(
        request
        for request in first.queue.requests
        if request.code is IssueCode.CROSS_SOURCE_CONFLICT
    )
    option = next(item for item in question.options if item.startswith("keep:"))
    answered = run_integration(
        workspace=tmp_path / "ws-answered",
        contract=_contract(),
        inputs=_write(tmp_path / "answered"),
        options=WorkflowOptions(answers={question.request_id: option}),
        run_id="run-answered",
    )
    assert answered.queue is not None
    settled = answered.queue.for_request(question.request_id)
    assert settled is not None
    assert settled.state is not RequestState.OPEN
    approved = [
        issue for issue in answered.issues if issue.state is IssueState.HUMAN_APPROVED
    ]
    assert approved, "the human decision must be recorded on the issue"


def test_package_is_reproducible_for_frozen_inputs(tmp_path):
    first = _run(tmp_path / "one")
    second = _run(tmp_path / "two")
    rows_first = _observation_rows(first)
    rows_second = _observation_rows(second)
    assert rows_first == rows_second
    assert (
        first.ledger.summary_counts()["records"]
        == second.ledger.summary_counts()["records"]
    )


def _observation_rows(result) -> list[tuple[str, str, str]]:
    path = Path(result.package.files["observations"])
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            (row["record_uid"], row["observable"], row["value"])
            for row in csv.DictReader(handle)
        ]


def test_remote_source_is_refused_by_default(tmp_path):
    first, _second = _write(tmp_path)
    result = run_integration(
        workspace=tmp_path / "ws-net",
        contract=_contract(),
        inputs=(first,),
        options=WorkflowOptions(
            extra_sources=({"url": "https://example.org/table.csv", "title": "remote"},)
        ),
        run_id="run-net",
    )
    denied = [
        issue for issue in result.issues if issue.code is IssueCode.SOURCE_HOST_DENIED
    ]
    assert denied
    assert denied[0].blocking is True
    assert "network_disabled" in json.dumps(denied[0].evidence) or denied[0].evidence


def test_manifest_lists_stage_receipts(tmp_path):
    result = _run(tmp_path)
    manifest = json.loads(
        (Path(result.package.directory) / "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["summary"]["records"] == result.ledger.summary_counts()["records"]
    assert "package" in manifest["stages"]
    assert manifest["artifacts"]

"""SN Ia benchmark: a frozen multi-source folder scored against gold tables.

The fixtures were written before the run, so this test measures the kernel
instead of describing it: how many gold measurements come back, whether units
are normalised, whether columns map as expected, which injected defects are
caught, whether anything risky was fixed without asking, and whether the same
inputs produce the same answer twice.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

import pytest

from jw.scientific_data.contracts import IssueState
from jw.scientific_data.domain import normalise_identifier
from jw.scientific_data.workflow import FigureInput, WorkflowOptions, run_integration

FIXTURES = Path(__file__).parent / "fixtures" / "snia_benchmark"
SOURCES = FIXTURES / "sources"
GOLD = FIXTURES / "gold"


REQUEST = {
    "research_goal": (
        "Assemble V and B band light curves of three nearby Type Ia supernovae "
        "from published tables, supplementary fluxes and one published figure"
    ),
    "domain": "snia_light_curve",
    "entity_class": "supernova",
    "observables": [
        {
            "name": "apparent_magnitude",
            "kind": "quantity",
            "unit": "mag",
            "required": True,
            "aliases": ["mag", "magnitude", "星等"],
        },
        {
            "name": "flux_density",
            "kind": "quantity",
            "unit": "mJy",
            "aliases": ["flux", "flux density"],
        },
        {"name": "time", "kind": "time", "unit": "mjd"},
        {
            "name": "object",
            "kind": "identifier",
            "aliases": ["name", "object", "天体名称"],
        },
        {"name": "band", "kind": "label", "aliases": ["band", "filter", "滤光片"]},
    ],
}


def _contract():
    from jw.scientific_data.workflow import contract_from_request

    return contract_from_request(REQUEST)


def _run(workspace: Path, run_id: str):
    figure = json.loads(
        (FIXTURES / "figure_lightcurve.json").read_text(encoding="utf-8")
    )
    return run_integration(
        workspace=workspace,
        contract=_contract(),
        inputs=tuple(sorted(str(path) for path in SOURCES.iterdir())),
        options=WorkflowOptions(
            figure_inputs=(FigureInput(figure["figure_id"], figure),)
        ),
        run_id=run_id,
    )


def _entity_labels(result) -> dict[str, str]:
    return {
        ref.entity_id: normalise_identifier(ref.label)
        for ref in result.ledger.entities.values()
    }


def _measurement_keys(result) -> dict[tuple[str, str, str, str], list[float]]:
    labels = _entity_labels(result)
    collected: dict[tuple[str, str, str, str], list[float]] = {}
    for record in result.records:
        if record.value is None:
            continue
        key = (
            labels.get(record.entity_id, record.entity_id),
            record.observable,
            record.attributes.get("band", ""),
            record.time,
        )
        collected.setdefault(key, []).append(float(record.value))
    return collected


def _gold_rows() -> list[dict[str, str]]:
    with (GOLD / "measurements.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _claims(result) -> dict[tuple[str, str, str, str], set[str]]:
    """Every numeric claim the kernel still carries for one merge key.

    ``value_raw`` keeps the disputed author strings (``"14.6 | 14.62"``), so a
    conflict only counts as preserved when both claims remain readable.
    """
    labels = _entity_labels(result)
    claims: dict[tuple[str, str, str, str], set[str]] = {}
    for record in result.records:
        key = (
            labels.get(record.entity_id, record.entity_id),
            record.observable,
            record.attributes.get("band", ""),
            record.time,
        )
        bucket = claims.setdefault(key, set())
        for chunk in str(record.value_raw or "").split("|"):
            text = chunk.strip()
            if text:
                bucket.add(text)
        if record.value is not None:
            bucket.add(repr(round(float(record.value), 6)))
    return claims


def _numbers(text: str) -> list[float]:
    found: list[float] = []
    for token in text.replace("pixel(", " ").replace(")", " ").split():
        try:
            found.append(float(token))
        except ValueError:
            continue
    return found


def _score_measurements(result) -> dict[str, float]:
    recovered = _measurement_keys(result)
    claims = _claims(result)
    gold = _gold_rows()
    matched = 0
    pairs_kept = 0
    pairs_expected = 0
    for row in gold:
        key = (
            normalise_identifier(row["entity"]),
            row["observable"],
            row["band"],
            row["time"],
        )
        target = float(row["value"])
        tolerance = max(1e-6, abs(target) * 1e-6)
        if row["kind"] == "conflict":
            pairs_expected += 1
            numbers = _numbers(" ".join(claims.get(key, set())))
            wanted = {target, float(row["alternative"])}
            if all(
                any(abs(value - other) <= tolerance for other in numbers)
                for value in wanted
            ):
                pairs_kept += 1
                matched += 1
            continue
        if any(abs(value - target) <= tolerance for value in recovered.get(key, [])):
            matched += 1
    return {
        "gold_rows": float(len(gold)),
        "matched": float(matched),
        "measurement_recall": matched / len(gold) if gold else 0.0,
        "conflict_pairs_kept": float(pairs_kept),
        "conflict_pairs_expected": float(pairs_expected),
    }


def _score_units(result) -> dict[str, float]:
    expected = {
        "apparent_magnitude": "mag",
        "flux_density": "mJy",
    }
    considered = 0
    normalised = 0
    for record in result.records:
        target = expected.get(record.observable)
        if target is None:
            continue
        considered += 1
        if record.unit == target:
            normalised += 1
    return {
        "unit_rows": float(considered),
        "unit_normalisation": normalised / considered if considered else 0.0,
    }


def _score_mappings(result) -> dict[str, float]:
    gold = json.loads((GOLD / "mappings.json").read_text(encoding="utf-8"))
    # The catalog keeps the name the user supplied; the fetched copy is hashed.
    source_by_id = {
        source.source_id: source.title for source in result.ledger.sources.values()
    }
    considered = 0
    correct = 0
    for mapping in result.ledger.mappings:
        filename = source_by_id.get(mapping.source_id, "")
        expected = gold.get(filename, {}).get(mapping.source_column)
        if expected is None:
            continue
        considered += 1
        if mapping.observable == expected:
            correct += 1
    return {
        "mapped_columns": float(considered),
        "mapping_accuracy": correct / considered if considered else 0.0,
    }


def _score_anomalies(result) -> dict[str, float]:
    spec = json.loads((GOLD / "anomalies.json").read_text(encoding="utf-8"))
    injected = spec["injected"]
    must_not = set(spec["must_not_appear"])
    tolerated = set(spec["tolerated_extra"])
    codes: dict[str, int] = {}
    for issue in result.issues:
        codes[str(issue.code)] = codes.get(str(issue.code), 0) + 1
    caught = sum(1 for code in injected if codes.get(code, 0) >= injected[code])
    false_alarms = sorted(
        code for code in codes if code not in injected and code not in tolerated
    )
    forbidden = sorted(code for code in must_not if codes.get(code, 0))
    return {
        "injected_classes": float(len(injected)),
        "caught_classes": float(caught),
        "anomaly_recall": caught / len(injected) if injected else 0.0,
        "anomaly_false_alarm_codes": float(len(false_alarms)),
        "false_alarm_codes": false_alarms,
        "forbidden_codes": forbidden,
        "detected_codes": sorted(codes),
    }


def _score_repairs(result) -> dict[str, float]:
    auto_safe = {"duplicate_exact", "nonfinite_value", "truncated_table"}
    safe = 0
    for entry in result.ledger.repairs.values():
        if str(entry.mode) == "auto_safe" and str(entry.code) not in auto_safe:
            continue
        if str(entry.actor) == "user:reviewer" or str(entry.mode) != "auto_safe":
            continue
        if entry.before_hash and entry.after_hash:
            safe += 1
    human_issues = {
        issue.issue_id
        for issue in result.issues
        if str(issue.repair_mode) == "human_required"
    }
    auto_applied = {
        entry.issue_id
        for entry in result.ledger.repairs.values()
        if str(entry.mode) == "auto_safe"
    }
    asked = {
        request.issue_ids[0]
        for request in (result.queue.requests if result.queue is not None else ())
        if request.issue_ids
    }
    unsafe = sorted(human_issues & auto_applied)
    unasked = sorted(human_issues - asked)
    return {
        "auto_repairs_with_hashes": float(safe),
        "human_required_auto_applied": float(len(unsafe)),
        "human_required_issues": float(len(human_issues)),
        "human_required_not_asked": float(len(unasked)),
    }


def _score_provenance(result) -> dict[str, float]:
    records = list(result.records)
    covered = sum(
        1
        for record in records
        if record.provenance and record.source_ids and record.provenance[0].source_id
    )
    return {
        "records": float(len(records)),
        "provenance_coverage": covered / len(records) if records else 0.0,
    }


def _score_figure(result) -> dict[str, float]:
    labels = _entity_labels(result)
    digitised = [
        record for record in result.records if str(record.value_kind) == "digitized"
    ]
    errors: list[float] = []
    for row in _gold_rows():
        if row["kind"] != "digitized":
            continue
        wanted = normalise_identifier(row["entity"])
        matches = [
            record
            for record in digitised
            if record.time == row["time"]
            and normalise_identifier(labels.get(record.entity_id, "")) == wanted
        ]
        if not matches:
            continue
        errors.append(
            min(abs(float(record.value) - float(row["value"])) for record in matches)
        )
    return {
        "digitised_records": float(len(digitised)),
        "digitised_scored": float(len(errors)),
        "figure_median_abs_error_mag": statistics.median(errors) if errors else 99.0,
    }


def _digest(result) -> list[tuple[str, str, str, str, float]]:
    rows = [
        (
            record.entity_id,
            record.observable,
            record.attributes.get("band", ""),
            record.time,
            round(float(record.value), 6),
        )
        for record in result.records
        if record.value is not None
    ]
    return sorted(rows)


def test_snia_benchmark_round_trip(tmp_path):
    started = time.monotonic()
    result = _run(tmp_path / "first", "benchmark-1")
    elapsed = time.monotonic() - started
    second = _run(tmp_path / "second", "benchmark-2")
    thresholds = json.loads((GOLD / "thresholds.json").read_text(encoding="utf-8"))

    measurements = _score_measurements(result)
    units = _score_units(result)
    mappings = _score_mappings(result)
    anomalies = _score_anomalies(result)
    repairs = _score_repairs(result)
    provenance = _score_provenance(result)
    figure = _score_figure(result)
    determinism = 1.0 if _digest(result) == _digest(second) else 0.0

    metrics = {
        "measurement_recall": measurements["measurement_recall"],
        "gold_rows": measurements["gold_rows"],
        "matched_rows": measurements["matched"],
        "conflict_pairs_kept": measurements["conflict_pairs_kept"],
        "unit_normalisation": units["unit_normalisation"],
        "mapping_accuracy": mappings["mapping_accuracy"],
        "mapped_columns": mappings["mapped_columns"],
        "anomaly_recall": anomalies["anomaly_recall"],
        "caught_classes": anomalies["caught_classes"],
        "injected_classes": anomalies["injected_classes"],
        "anomaly_false_alarm_codes": anomalies["anomaly_false_alarm_codes"],
        "false_alarm_codes": anomalies["false_alarm_codes"],
        "forbidden_codes": anomalies["forbidden_codes"],
        "auto_repairs_with_hashes": repairs["auto_repairs_with_hashes"],
        "human_required_auto_applied": repairs["human_required_auto_applied"],
        "human_required_issues": repairs["human_required_issues"],
        "human_required_not_asked": repairs["human_required_not_asked"],
        "provenance_coverage": provenance["provenance_coverage"],
        "records": provenance["records"],
        "figure_median_abs_error_mag": figure["figure_median_abs_error_mag"],
        "digitised_records": figure["digitised_records"],
        "determinism": determinism,
        "wall_seconds": round(elapsed, 2),
        "detected_codes": anomalies["detected_codes"],
    }
    (tmp_path / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    assert metrics["measurement_recall"] >= thresholds["measurement_recall"], metrics
    assert metrics["conflict_pairs_kept"] == measurements["conflict_pairs_expected"]
    assert metrics["unit_normalisation"] >= thresholds["unit_normalisation"], metrics
    assert metrics["mapping_accuracy"] >= thresholds["mapping_accuracy"], metrics
    assert metrics["anomaly_recall"] >= thresholds["anomaly_recall"], metrics
    assert (
        metrics["anomaly_false_alarm_codes"] <= thresholds["anomaly_false_alarm_codes"]
    ), metrics
    assert metrics["forbidden_codes"] == [], metrics
    assert metrics["provenance_coverage"] >= thresholds["provenance_coverage"], metrics
    assert (
        metrics["human_required_auto_applied"]
        <= thresholds["human_required_auto_applied"]
    )
    assert metrics["human_required_not_asked"] == 0, metrics
    assert (
        metrics["figure_median_abs_error_mag"]
        <= thresholds["figure_median_abs_error_mag"]
    )
    assert metrics["determinism"] >= thresholds["determinism"], metrics
    assert metrics["wall_seconds"] <= thresholds["wall_seconds"]


def test_benchmark_package_is_self_explaining(tmp_path):
    result = _run(tmp_path / "doc", "benchmark-doc")
    package = Path(result.package.directory)
    report = (package / "data_quality_report.md").read_text(encoding="utf-8")
    for heading in (
        "数据来源",
        "质量问题",
        "修正与人工确认",
        "图表识别、提取与校验",
        "复现性",
    ):
        assert heading in report, heading
    manifest = json.loads((package / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["records"] > 0
    assert manifest["contract_fingerprint"] == result.ledger.contract.fingerprint
    repairs = (package / "repair_log.csv").read_text(encoding="utf-8")
    assert "nonfinite_value" in repairs
    issues = (package / "quality_issues.csv").read_text(encoding="utf-8")
    assert "cross_source_conflict" in issues


def test_answering_the_conflict_changes_the_delivered_package(tmp_path):
    from jw.scientific_data.workflow import apply_human_answers

    first = _run(tmp_path / "answer", "benchmark-answer")
    question = next(
        request
        for request in first.queue.requests
        if str(request.code) == "cross_source_conflict"
    )
    keep = next(option for option in question.options if option.startswith("keep:"))
    updated = apply_human_answers(
        workspace=tmp_path / "answer",
        run_id="benchmark-answer",
        answers={question.request_id: keep},
    )
    approved = [
        issue for issue in updated.issues if issue.state is IssueState.HUMAN_APPROVED
    ]
    assert approved
    assert any(
        str(entry.actor).startswith("user") for entry in updated.ledger.repairs.values()
    )
    rows = (Path(updated.package.directory) / "integrated_observations.csv").read_text(
        encoding="utf-8"
    )
    assert "|" in rows or "conflict" in rows


def _figure_payload(**overrides):
    payload = json.loads(
        (FIXTURES / "figure_lightcurve.json").read_text(encoding="utf-8")
    )
    payload.update(overrides)
    return payload


def _run_figure(workspace: Path, payload: dict, figure_id: str):
    return run_integration(
        workspace=workspace,
        contract=_contract(),
        inputs=(),
        options=WorkflowOptions(
            figure_inputs=(FigureInput(figure_id, dict(payload, figure_id=figure_id)),)
        ),
        run_id=f"figure-{figure_id}",
    )


def test_axis_calibration_drives_the_digitised_values(tmp_path):
    """The axis ticks, not a hard-coded scale, decide the recovered numbers."""
    base = _run_figure(tmp_path / "base", _figure_payload(), "axis-base")
    shifted_payload = _figure_payload()
    for tick in shifted_payload["y_ticks"]:
        tick["value"] += 0.10
    shifted = _run_figure(tmp_path / "shifted", shifted_payload, "axis-shifted")

    def values(result):
        return [
            round(float(record.value), 6)
            for record in result.records
            if str(record.value_kind) == "digitized"
        ]

    baseline_values = values(base)
    shifted_values = values(shifted)
    assert baseline_values and len(baseline_values) == len(shifted_values)
    for before, after in zip(baseline_values, shifted_values, strict=True):
        assert after - before == pytest.approx(0.10, abs=1e-6)

    log_payload = _figure_payload(
        observable="flux_density",
        unit="mJy",
        x_scale="linear",
        y_scale="log",
        y_ticks=[
            {"pixel": 400.0, "value": 1.0},
            {"pixel": 200.0, "value": 100.0},
        ],
        series=[
            {
                "label": "V",
                "points": [[150.0, 300.0], [250.0, 250.0]],
                "marker": "circle",
            }
        ],
    )
    logarithmic = _run_figure(tmp_path / "log", log_payload, "axis-log")
    log_values = [
        round(float(record.value), 6)
        for record in logarithmic.records
        if str(record.value_kind) == "digitized"
    ]
    # A log axis puts the midpoint at 10 mJy (not the linear midpoint 50.5),
    # and the three-quarter pixel at 10**1.5 mJy.
    assert log_values[0] == pytest.approx(10.0, abs=1e-6)
    assert log_values[1] == pytest.approx(10.0**1.5, abs=1e-5)


def test_misdeclared_axis_scale_is_reported(tmp_path):
    """A figure the reader believes is log-scaled but was fitted linearly is flagged."""
    payload = _figure_payload(
        observable="flux_density",
        unit="mJy",
        x_scale="linear",
        y_scale="linear",
        y_ticks=[
            {"pixel": 400.0, "value": 1.0},
            {"pixel": 200.0, "value": 100.0},
        ],
        expected_y_log=True,
    )
    result = _run_figure(tmp_path / "misdeclared", payload, "axis-misdeclared")
    codes = {str(issue.code) for issue in result.issues}
    assert "axis_scale_suspect" in codes
    flagged = next(
        issue for issue in result.issues if str(issue.code) == "axis_scale_suspect"
    )
    # An axis reading error changes the science, so it is never auto-fixed.
    assert str(flagged.repair_mode) == "human_required"

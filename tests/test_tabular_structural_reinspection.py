"""CSV parsing and a read-only structural view of legacy immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from automatic_experiment import paths, service  # noqa: E402
from automatic_experiment.contracts import default_request  # noqa: E402
from automatic_experiment.state import task_workspace  # noqa: E402


@pytest.mark.parametrize(("suffix", "delimiter"), [("csv", ","), ("tsv", "\t")])
def test_named_generic_table_retains_header_and_empty_field_counts(
    tmp_path, suffix, delimiter
):
    table = tmp_path / f"table.{suffix}"
    table.write_text(
        f"time{delimiter}value\n1{delimiter}2\n2{delimiter}\n", encoding="utf-8"
    )
    profile = paths._tabular_profile(table)
    assert profile["columns"] == ["time", "value"]
    assert profile["row_count"] == 2
    assert profile["missing_value_counts"] == {"time": 0, "value": 1}
    assert profile["delimiter"] == delimiter
    assert not profile["column_semantics_verified"]


def test_comment_preamble_does_not_become_a_one_column_header(tmp_path):
    table = tmp_path / "polar.csv"
    table.write_text(
        "# observational source\n# column definitions are external\n\n"
        "date,north,south\n1900.1,-1.2,NaN\n1901.1,0,1.2\n",
        encoding="utf-8",
    )
    profile = paths._tabular_profile(table)
    assert profile["columns"] == ["date", "north", "south"]
    assert profile["row_count"] == 2
    assert profile["leading_comment_lines_skipped"] == 2
    assert profile["missing_value_counts"] == {"date": 0, "north": 0, "south": 0}


def test_numeric_semicolon_table_retains_first_data_record(tmp_path):
    table = tmp_path / "smoothed.csv"
    table.write_text(
        "1749;01;1749.042;-1;-1;0;1\n1749;02;1749.123;-1;-1;0;1\n",
        encoding="utf-8",
    )
    profile = paths._tabular_profile(table)
    assert profile["delimiter"] == ";"
    assert profile["header_status"] == "absent_numeric_first_row"
    assert profile["columns"] == [f"column_{i}" for i in range(1, 8)]
    assert profile["row_count"] == 2
    assert profile["column_names_are_positional"]
    assert not any(profile["missing_value_counts"].values())


def test_quoted_header_and_multiline_values_use_csv_rules(tmp_path):
    table = tmp_path / "quoted.csv"
    table.write_text('"name,label",value\n"two\nlines",3\n', encoding="utf-8")
    profile = paths._tabular_profile(table)
    assert profile["columns"] == ["name,label", "value"]
    assert profile["row_count"] == 1
    assert profile["field_count_consistent"]


def test_ragged_rows_are_reported_without_guessing_additional_columns(tmp_path):
    table = tmp_path / "ragged.csv"
    table.write_text("a,b\n1,2\n3\n", encoding="utf-8")
    profile = paths._tabular_profile(table)
    assert profile["columns"] == ["a", "b"]
    assert profile["row_count"] == 2
    assert profile["ragged_row_count"] == 1
    assert not profile["field_count_consistent"]


def test_profile_bounds_remain_explicit(tmp_path, monkeypatch):
    table = tmp_path / "bounded.csv"
    table.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    monkeypatch.setattr(paths, "TABULAR_PROFILE_MAX_ROWS", 1)
    profile = paths._tabular_profile(table)
    assert not profile["profile_complete"]
    assert profile["rows_scanned"] == 1
    assert profile["row_count"] is None
    monkeypatch.setattr(paths, "TABULAR_PROFILE_MAX_BYTES", 1)
    assert not paths._tabular_profile(table)["profile_complete"]


def _legacy_snapshot(workspace):
    source = workspace / "inputs/table.csv"
    source.parent.mkdir(parents=True)
    source.write_text("100;1;3\n101;2;4\n", encoding="utf-8")
    request = default_request("Inspect this synthetic bounded structural fixture only.")
    request["input_refs"] = [
        {
            "id": "table",
            "path": "inputs/table.csv",
            "description": "Synthetic structural fixture, no scientific result.",
            "required": True,
        }
    ]
    with task_workspace(workspace):
        bound = service.bind_request({"request": request})
        first = service.inspect_inputs(bound["run_id"])
    run_root = workspace / "experiment/runs" / bound["run_id"]
    manifest_path = run_root / "input_snapshot.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["inputs"][0]["files"][0]["profile"] = {
        "kind": "tabular",
        "columns": ["100;1;3"],
        "row_count": 1,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return bound["run_id"], run_root, source, first


def test_cached_native_inspection_exposes_new_view_without_rewriting_old_snapshot(
    tmp_path,
):
    run_id, run_root, _source, _first = _legacy_snapshot(tmp_path)
    manifest = run_root / "input_snapshot.json"
    state = run_root / "state.json"
    before = manifest.read_bytes(), state.read_bytes()
    with task_workspace(tmp_path):
        result = service.inspect_inputs(run_id)
    assert result["status"] == "already_snapshotted"
    assert (
        result["input_snapshot"]["inputs"][0]["files"][0]["profile"]["row_count"] == 1
    )
    current = result["tabular_structure_reinspection"]
    assert current["files"][0]["profile"]["row_count"] == 2
    assert current["files"][0]["profile"]["columns"] == [
        "column_1",
        "column_2",
        "column_3",
    ]
    assert (
        current["input_snapshot_file_sha256"] == hashlib.sha256(before[0]).hexdigest()
    )
    assert current["original_input_snapshot_unchanged"]
    assert not current["scientific_qualification_completed"]
    assert before == (manifest.read_bytes(), state.read_bytes())


@pytest.mark.parametrize("changed", ["source", "snapshot"])
def test_hash_mismatch_rejects_before_reprofiling(tmp_path, monkeypatch, changed):
    run_id, run_root, source, _first = _legacy_snapshot(tmp_path)
    path = source if changed == "source" else run_root / "inputs/table/table.csv"
    path.write_text("100;1;9\n101;2;4\n", encoding="utf-8")
    before = (run_root / "input_snapshot.json").read_bytes()
    monkeypatch.setattr(
        paths, "_tabular_profile", lambda _path: pytest.fail("must reject first")
    )
    with (
        task_workspace(tmp_path),
        pytest.raises(paths.PathPolicyError, match="bytes/SHA"),
    ):
        service.inspect_inputs(run_id)
    assert (run_root / "input_snapshot.json").read_bytes() == before


def test_manifest_source_must_still_match_the_bound_request(tmp_path):
    run_id, run_root, _source, _first = _legacy_snapshot(tmp_path)
    manifest = run_root / "input_snapshot.json"
    body = json.loads(manifest.read_text("utf-8"))
    body["inputs"][0]["source_path"] = "inputs/another.csv"
    manifest.write_text(json.dumps(body), encoding="utf-8")
    with (
        task_workspace(tmp_path),
        pytest.raises(paths.PathPolicyError, match="identity"),
    ):
        service.inspect_inputs(run_id)

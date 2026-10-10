"""Tool boundary tests use synthetic files; these are not B1 observations."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jw import paths
from jw.tools import b1_flare
from jw.tools.registry import (
    get_builtin_tool_registry,
    get_main_agent_tools,
    get_tool_bundles,
)
from jw.utils import load_subagents
from jw.workspaces import ensure_thread_workspace


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _register(root):
    records = [
        {
            "path": "/inputs/" + path.name,
            "sha256": _sha(path),
            "bytes": path.stat().st_size,
            # Explicit test harness binding; fixture bytes are not production data.
            "role": "user_input",
            "dataset_id": b1_flare.B1_INPUT_DATASET_IDS[path.stem],
        }
        for path in sorted((root / "inputs").iterdir())
        if path.is_file()
    ]
    _json(root / "input_manifest.json", {"inputs": records, "project_inputs": []})


@pytest.fixture
def bound_case(tmp_path, monkeypatch):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path / "workspace")
    paths.WORKSPACE_ROOT.mkdir()
    binding = ensure_thread_workspace("b1-tool-synthetic", paths.WORKSPACE_ROOT)
    root = Path(binding.workspace)
    config = {
        "configurable": {
            "thread_id": "b1-tool-synthetic",
            "workspace_thread_id": "b1-tool-synthetic",
        }
    }
    _json(
        root / "task.json",
        {
            "thread_id": "b1-tool-synthetic",
            "research_question": "B1 synthetic calculation-tool validation, not solar conclusions",
        },
    )
    input_root = root / "inputs"
    input_root.mkdir(exist_ok=True)
    begin = datetime(2020, 1, 1, tzinfo=UTC)
    magnetic = []
    for step in range(240):
        time = begin + timedelta(minutes=12 * step)
        magnetic.append(
            {
                "time_utc": _iso(time),
                "harpnum": 1,
                "noaa_ar": 1,
                "usflux_mx": 1e22 + step * 1e17,
                "totpot_erg_per_cm": 1e23 + step * 1e18,
                "meanshr_deg": 20 + step / 1000,
                "quality_valid": "true",
                "support_end_utc": _iso(time + timedelta(minutes=6)),
                "source_record_id": f"synthetic-{step}",
            }
        )
    _csv(input_root / "magnetic.csv", magnetic)
    _csv(
        input_root / "events.csv",
        [
            {
                "event_id": "synthetic-event",
                "start_utc": "2020-01-02T12:00:00Z",
                "peak_utc": "2020-01-02T12:05:00Z",
                "end_utc": "2020-01-02T12:20:00Z",
                "goes_class": "M1.0",
                "noaa_ar": 1,
                "region_verified": "true",
            }
        ],
    )
    _csv(
        input_root / "xrs.csv",
        [
            {
                "time_utc": _iso(begin + timedelta(minutes=5 * step)),
                "flux_w_m2": 1e-7,
                "quality_valid": "true",
            }
            for step in range(576)
        ],
    )
    _json(
        input_root / "parameters.json",
        {
            "schema_version": "b1-flare-case-v1",
            "noaa_ar": 1,
            "harpnum": 1,
            "target_start_utc": "2020-01-02T00:00:00Z",
            "target_end_utc": "2020-01-03T00:00:00Z",
            "window_hours": 4,
            "xrs_cadence_seconds": 300,
            "synthetic": True,
        },
    )
    _json(
        input_root / "sources.json",
        {
            "sources": [
                {
                    "role": "canonical_input",
                    "input_name": name,
                    "url": f"https://example.invalid/{name}",
                    "title": f"SYNTHETIC-{name}",
                    "license_or_terms": "Test fixture only; not observations",
                    "citation": f"Synthetic test source: {name}",
                    "sha256": _sha(
                        input_root
                        / f"{name}.{'json' if name == 'parameters' else 'csv'}"
                    ),
                    "path": f"{name}.{'json' if name == 'parameters' else 'csv'}",
                    "synthetic": True,
                }
                for name in ("magnetic", "events", "xrs", "parameters")
            ]
        },
    )
    _register(root)
    args = {
        f"{name}_path": f"/inputs/{name}.{'json' if name in ('parameters', 'sources') else 'csv'}"
        for name in ("magnetic", "events", "xrs", "parameters", "sources")
    }
    return root, config, args


def _invoke(config, args):
    return json.loads(b1_flare.run_b1_flare_case.invoke(args, config=config))


def _assert_audited(root, result, status):
    assert result["status"] == status
    assert result["scientific_status"] == "not_reviewed"
    assert result["scientific_not_reviewed"] is True
    assert len(result["receipt_refs"]) == 2
    for ref in result["receipt_refs"]:
        persisted = json.loads((root / ref).read_text(encoding="utf-8"))
        assert persisted["attempt_id"] == result["attempt_id"]
        assert persisted["status"] == status
        assert persisted["scientific_not_reviewed"] is True


def test_real_tool_calculation_retains_sources_and_does_not_claim_scientific_success(
    bound_case,
):
    root, config, args = bound_case
    before = {path.name: _sha(path) for path in (root / "inputs").iterdir()}
    result = _invoke(config, args)
    _assert_audited(root, result, "verified")
    assert result["calculation_status"] == "completed"
    assert result["execution_kind"] == "deterministic_tool"
    assert len(result["inputs"]) == 5
    assert result["parameters_sha256"] == before["parameters.json"]
    assert result["sources_sha256"] == before["sources.json"]
    assert result["task_sha256"] == _sha(root / "task.json")
    assert result["input_manifest_sha256"] == _sha(root / "input_manifest.json")
    report = next(
        root / ref for ref in result["artifact_refs"] if ref.endswith("/report.md")
    )
    text = report.read_text(encoding="utf-8")
    for kind in ("magnetic", "events", "xrs"):
        assert f"https://example.invalid/{kind}" in text
        assert f"SYNTHETIC-{kind}" in text
    assert {path.name: _sha(path) for path in (root / "inputs").iterdir()} == before
    for item in result["outputs"]:
        assert item["sha256"] == _sha(root / item["path"])


@pytest.mark.parametrize("kind", ["magnetic", "events", "xrs", "parameters", "sources"])
def test_changed_input_is_rejected_before_computation(bound_case, kind, monkeypatch):
    from jw import b1_compute

    root, config, args = bound_case
    monkeypatch.setattr(
        b1_compute, "run_b1_analysis", lambda **kwargs: pytest.fail("Core must not run")
    )
    path = root / args[f"{kind}_path"].lstrip("/")
    path.write_bytes(path.read_bytes() + b" ")
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert result["error_type"] == "PermissionError"
    assert result["artifact_refs"] == []


def test_unregistered_input_and_arbitrary_host_path_are_rejected(bound_case, tmp_path):
    root, config, args = bound_case
    unbound = tmp_path / "unregistered.csv"
    unbound.write_text("not an input", encoding="utf-8")
    result = _invoke(config, args | {"magnetic_path": str(unbound)})
    _assert_audited(root, result, "error")
    assert result["error_type"] == "PermissionError"
    assert unbound.read_text(encoding="utf-8") == "not an input"


@pytest.mark.parametrize("kind", ["magnetic", "events", "xrs", "parameters", "sources"])
def test_wrong_dataset_id_is_rejected_before_computation(bound_case, kind, monkeypatch):
    from jw import b1_compute

    root, config, args = bound_case
    monkeypatch.setattr(
        b1_compute, "run_b1_analysis", lambda **kwargs: pytest.fail("Core must not run")
    )
    manifest_path = root / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = next(
        item for item in manifest["inputs"] if item["path"] == args[f"{kind}_path"]
    )
    record["dataset_id"] = "unrelated-dataset"
    _json(manifest_path, manifest)
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert result["error_type"] == "PermissionError"
    assert "requires registered dataset_id" in result["error_message"]


def test_foreign_task_metadata_is_rejected_and_audited(bound_case):
    root, config, args = bound_case
    task = json.loads((root / "task.json").read_text())
    task["thread_id"] = "some-other-task"
    _json(root / "task.json", task)
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert "runtime thread_id" in result["error_message"]


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "not-b1"},
        {"window_hours": -4},
        {"max_events": 99},
        {"invented_option": True},
    ],
)
def test_illegal_parameters_are_audited(bound_case, change):
    root, config, args = bound_case
    parameters = root / "inputs/parameters.json"
    _json(parameters, json.loads(parameters.read_text()) | change)
    _register(root)
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert result["artifact_refs"] == []
    assert result["calculation_status"] == "failed"


def test_failed_core_preserves_partial_artifacts_and_each_attempt(
    bound_case, monkeypatch
):
    from jw import b1_compute

    root, config, args = bound_case

    def fail(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir()
        (output / "failure.txt").write_text("synthetic failure", encoding="utf-8")
        raise RuntimeError("intentional synthetic core failure")

    monkeypatch.setattr(b1_compute, "run_b1_analysis", fail)
    first, second = _invoke(config, args), _invoke(config, args)
    assert first["attempt_id"] != second["attempt_id"]
    for result in (first, second):
        _assert_audited(root, result, "error")
        assert result["error_message"] == "intentional synthetic core failure"
        assert result["artifact_refs"] == []
        assert len(result["partial_outputs"]) == 1
        item = result["partial_outputs"][0]
        assert item["sha256"] == _sha(root / item["path"])


@pytest.mark.parametrize(
    "target", ["inputs/magnetic.csv", "task.json", "input_manifest.json"]
)
def test_input_or_binding_mutation_during_core_invalidates_result(
    bound_case, monkeypatch, target
):
    from jw import b1_compute

    root, config, args = bound_case

    def tamper(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir()
        (output / "report.md").write_text("Untrusted partial report", encoding="utf-8")
        path = root / target
        path.write_bytes(path.read_bytes() + b" ")

    monkeypatch.setattr(b1_compute, "run_b1_analysis", tamper)
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert result["error_type"] == "PermissionError"
    assert result["artifact_refs"] == []


def test_tool_available_to_main_and_solar_data_with_b1_prompt():
    registry = get_builtin_tool_registry()
    assert registry["run_b1_flare_case"] is b1_flare.run_b1_flare_case
    assert "run_b1_flare_case" in {tool.name for tool in get_main_agent_tools()}
    specs = load_subagents(
        Path(__file__).resolve().parents[1] / "jw/subagents",
        tool_registry=registry,
        tool_bundles=get_tool_bundles(),
    )
    data = next(spec for spec in specs if spec["name"] == "solar-data")
    assert "run_b1_flare_case" in {tool.name for tool in data["tools"]}
    assert "scientific_not_reviewed=true" in data["system_prompt"]
    assert "required_data_product=b1_flare_case_v1" in data["system_prompt"]


def test_verified_summary_exposes_only_eligible_numbers_and_is_receipt_bound(
    bound_case,
):
    root, config, args = bound_case
    result = _invoke(config, args)
    _assert_audited(root, result, "verified")
    summary = result["result_summary"]
    assert summary["attempt_id"] == result["attempt_id"]
    assert summary["scientific_status"] == "not_reviewed"
    assert summary["eligible_window_results"]
    assert all(
        row["status"] == "eligible" for row in summary["eligible_window_results"]
    )
    assert all("metrics" not in row for row in summary["all_window_screening"])
    assert result["verified_receipt_sha256"] == _sha(root / summary["receipt_ref"])
    persisted = json.loads((root / summary["receipt_ref"]).read_text("utf-8"))
    assert persisted["result_summary"] == summary
    encoded = json.dumps(
        summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert result["result_summary_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert len(json.dumps(summary, ensure_ascii=False)) <= 50_000


def test_summary_rejects_product_tampering(bound_case):
    root, config, args = bound_case
    result = _invoke(config, args)
    output = root / f"work/solar_data/b1_flare/{result['attempt_id']}/products"
    (output / "windows.json").write_bytes((output / "windows.json").read_bytes() + b" ")
    with pytest.raises(PermissionError, match="hash/size mismatch"):
        b1_flare._verified_result_summary(root, output, result)


def test_summary_preserves_zero_eligible_without_returning_rejected_metrics(bound_case):
    root, config, args = bound_case
    magnetic_path = root / "inputs/magnetic.csv"
    with magnetic_path.open(encoding="utf-8", newline="") as stream:
        magnetic = list(csv.DictReader(stream))
    for row in magnetic:
        row["quality_valid"] = "false"
    _csv(magnetic_path, magnetic)
    source_path = root / "inputs/sources.json"
    sources = json.loads(source_path.read_text("utf-8"))
    next(item for item in sources["sources"] if item["input_name"] == "magnetic")[
        "sha256"
    ] = _sha(magnetic_path)
    _json(source_path, sources)
    _register(root)
    result = _invoke(config, args)
    _assert_audited(root, result, "verified")
    summary = result["result_summary"]
    assert summary["eligible_events"] == summary["eligible_controls"] == 0
    assert summary["eligible_window_results"] == []
    assert summary["overlapping_eligible_windows"] == []
    assert summary["all_window_screening"]
    assert all("metrics" not in row for row in summary["all_window_screening"])
    assert all(
        row["status"] == "rejected" and row["reasons"]
        for row in summary["all_window_screening"]
    )


def test_product_tampering_before_summary_fails_closed_in_tool_return(
    bound_case, monkeypatch
):
    root, config, args = bound_case
    original = b1_flare._artifacts
    tampered = False

    def artifact_then_tamper(current_root, output):
        nonlocal tampered
        rows = original(current_root, output)
        if not tampered:
            tampered = True
            target = output / "windows.json"
            target.write_bytes(target.read_bytes() + b" ")
        return rows

    monkeypatch.setattr(b1_flare, "_artifacts", artifact_then_tamper)
    result = _invoke(config, args)
    _assert_audited(root, result, "error")
    assert "hash/size mismatch" in result["error_message"]
    assert "result_summary" not in result
    assert "result_summary_sha256" not in result
    assert "verified_receipt_sha256" not in result


def test_failed_input_does_not_return_result_summary(bound_case):
    root, config, args = bound_case
    (root / "inputs/magnetic.csv").write_bytes(b"tampered")
    result = _invoke(config, args)
    assert result["status"] == "error"
    assert "result_summary" not in result
    assert "result_summary_sha256" not in result
    assert "verified_receipt_sha256" not in result


def test_b1_report_numeric_table_survives_native_hypothesis_handoff(bound_case):
    from types import SimpleNamespace
    from jw.middleware.research_review_orchestration import (
        _reviewed_text_result_excerpt,
    )

    root, config, args = bound_case
    result = _invoke(config, args)
    report = next(
        item for item in result["outputs"] if item["path"].endswith("/report.md")
    )
    artifact = {
        "payload": {
            "source_manifest": [
                {
                    "source_ref": report["path"],
                    "sha256": report["sha256"],
                    "bytes": report["bytes"],
                }
            ]
        }
    }
    excerpt = _reviewed_text_result_excerpt(
        SimpleNamespace(workspace_root=root), artifact
    )
    assert "## Descriptive results" in excerpt
    assert "Eligible events:" in excerpt
    assert "First-half median" in excerpt
    assert "synthetic-event" in excerpt

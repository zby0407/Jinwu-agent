"""Task-bound descriptor dispatch, computation and review integrity."""

import hashlib
import json
from pathlib import Path

import pytest

from jw.research_protocols import (
    detect_analysis_protocol,
    required_dataset_ids_for_protocol,
)
from jw.middleware.research_router import _fallback_route, _with_analysis_protocol
from jw.tools.solar_feature import run_silso_cycle_descriptive
from jw.tools.cycle_descriptive import receipt_defect
from jw.workspaces import ensure_thread_workspace

PROMPT = "使用 SILSO 月均和平滑数据，计算各周期均值、累计活动量与低活动月份占比。只做描述性指标。"


def test_descriptors_use_bounded_data_review_not_legacy_morphology():
    assert detect_analysis_protocol(PROMPT) == "silso_cycle_descriptive_v1"
    route = _with_analysis_protocol(_fallback_route(PROMPT), text=PROMPT)
    assert route["mode"] == "verified_analysis"
    assert route["required_specialist"] == "solar-data"
    assert (
        len(required_dataset_ids_for_protocol(route["required_analysis_protocol"])) == 3
    )


def test_real_bounded_request_regression():
    prompt = (
        Path(__file__).parent / "fixtures/sc3_sc6_descriptive_request.md"
    ).read_text(encoding="utf-8")
    route = _with_analysis_protocol({"mode": "full_research"}, text=prompt)
    assert route["required_analysis_protocol"] == "silso_cycle_descriptive_v1"
    assert route["mode"] == "verified_analysis"


def test_excluded_calculation_does_not_select_new_protocol():
    assert (
        detect_analysis_protocol("仅做文献综述。不要计算 SILSO 周期均值或累计活动量。")
        == "none"
    )


@pytest.fixture
def task(tmp_path, monkeypatch):
    base = tmp_path / "runtime"
    base.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr("jw.paths.WORKSPACE_ROOT", base)
    binding = ensure_thread_workspace("descriptor-test", base, first_request=PROMPT)
    root = Path(binding.workspace)
    inputs = root / "inputs"
    monthly = "\n".join(
        f"1800 {m} 1800.{m:02d} {v} -1 -1"
        for m, v in enumerate([0, 2, 2, 4, 6, 0, 0], 1)
    )
    smooth = monthly.replace(" ", ";")
    boundaries = "1 1800 1 0 1800 2 2 0 3\n2 1800 4 4 1800 5 6 0 3\n3 1800 7 0\n"
    params = {
        "cycles": [1, 2],
        "targets": [2],
        "plot_cycles": [1, 2],
        "comparison_groups": {"prior": [1]},
        "thresholds": [2],
        "primary_threshold": 2,
        "boundary": "left_closed_right_open",
        "peak_tie": "earliest",
        "missing_policy": "fail_incomplete_cycle",
    }
    files = [
        ("monthly.txt", monthly, "silso-monthly-total-v2"),
        ("smoothed.csv", smooth, "silso-monthly-smoothed-v2"),
        ("extrema.txt", boundaries, "silso-cycle-extrema-v2"),
        ("parameters.json", json.dumps(params), None),
    ]
    records = []
    for name, content, dataset in files:
        p = inputs / name
        p.write_text(content, encoding="utf-8")
        rec = {
            "path": "inputs/" + name,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
            "role": "user_input",
        }
        if dataset:
            rec["dataset_id"] = dataset
        records.append(rec)
    manifest_path = root / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = records
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    args = dict(
        zip(
            [
                "monthly_total_path",
                "smoothed_path",
                "official_extrema_path",
                "parameters_path",
            ],
            [r["path"] for r in records],
        )
    )
    return root, args, {"configurable": {"thread_id": "descriptor-test"}}


def test_tool_real_files_and_review_rejects_changed_artifacts(task):
    root, args, config = task
    result = json.loads(run_silso_cycle_descriptive.invoke(args, config=config))
    assert result["status"] == "verified", result
    assert result["review_status"] == "not_reviewed"
    assert result["row_count"] == 2
    refs = result["artifact_refs"] + result["receipt_refs"] + list(args.values())
    manifest = {
        ref: {"sha256": hashlib.sha256((root / ref).read_bytes()).hexdigest()}
        for ref in refs
    }
    assert receipt_defect(root, "descriptor-test", manifest) is None
    from jw.research_review import ResearchReviewStore

    store = ResearchReviewStore(root, "descriptor-test")
    sources = store._canonical_stage_sources("data")
    assert sources
    assert store._canonical_stage_ready("data", sources, phase="bounded_data")
    csv_ref = next(ref for ref in refs if ref.endswith("cycle_metrics.csv"))
    (root / csv_ref).write_text("fabricated", encoding="utf-8")
    assert "changed" in receipt_defect(root, "descriptor-test", manifest)


def test_tool_hash_failure_is_audited_without_output(task):
    root, args, config = task
    (root / args["monthly_total_path"]).write_text("changed", encoding="utf-8")
    result = json.loads(run_silso_cycle_descriptive.invoke(args, config=config))
    assert result["status"] == "error"
    audits = list((root / "receipts/attempts").glob("cycle-descriptive-*.json"))
    assert len(audits) == 1
    assert json.loads(audits[0].read_text(encoding="utf-8"))["status"] == "failed"
    assert not list((root / "outputs").rglob("cycle_metrics.csv"))


def test_unbound_parameter_cannot_substitute_settings(task):
    root, args, config = task
    (root / "inputs/unbound.json").write_text("{}", encoding="utf-8")
    args["parameters_path"] = "inputs/unbound.json"
    result = json.loads(run_silso_cycle_descriptive.invoke(args, config=config))
    assert result["status"] == "error"


def test_receipt_does_not_make_itself_scientific_approval():
    assert receipt_defect(Path("."), "missing", {}) is not None


def test_data_review_gate_rejects_context_only_and_accepts_integrity(task):
    from jw.research_review import ResearchReviewStore
    from jw.tools.solar_feature import open_bounded_solar_data_context

    root, args, config = task
    context = open_bounded_solar_data_context(
        config, analysis_protocol="silso_cycle_descriptive_v1"
    )
    assert context["required_data_product"] == "silso_cycle_descriptive_v1"
    store = ResearchReviewStore(root, "descriptor-test")

    def targets():
        return [
            {
                "stage": "data",
                "payload": {
                    "phase": "bounded_data",
                    "source_manifest": [
                        {
                            "source_ref": p.relative_to(root).as_posix(),
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        }
                        for p in store._canonical_stage_sources("data")
                    ],
                },
            }
        ]

    issues = store._data_input_boundary_issues(targets())
    assert issues and issues[0]["severity"] == "critical"
    assert (
        json.loads(run_silso_cycle_descriptive.invoke(args, config=config))["status"]
        == "verified"
    )
    # No deterministic integrity issue does not grant scientific approval.
    assert store._data_input_boundary_issues(targets()) == []
    csv = next((root / "outputs").rglob("cycle_metrics.csv"))
    csv.write_text("changed", encoding="utf-8")
    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"

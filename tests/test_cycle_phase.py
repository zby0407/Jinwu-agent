"""Scientific invariants and task-bound review rejection for B preparation."""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from jw import cycle_metrics as core, cycle_phase as phase


def settings():
    return {
        "schema": "cycle-phase-parameters-v1",
        "cycles": [1, 2, 3],
        "targets": [3],
        "references": [1, 2],
        "plot_cycles": [1, 2, 3],
        "grid_intervals": 100,
        "interpolation": "piecewise_linear",
        "endpoint": "next_minimum_smoothed",
        "normalization": "A_within_cycle_peak_no_baseline_subtraction",
        "reference_summary": "equal_cycle_mean",
        "distance": "equal_grid_rms_inclusive_endpoints",
        "background": "reference_leave_one_out",
        "decision": "strictly_above_all_background",
        "comparison_tolerance": {"absolute": 1e-12, "relative": 1e-12},
        "missing_policy": "fail_no_gap_interpolation_no_extrapolation",
    }


def series(values):
    return {i: {"value": float(v)} for i, v in enumerate(values)}


@pytest.fixture
def package(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    values = [1, 10, 5, 1, 2, 20, 10, 2, 1, 4, 10, 1, 1]
    lines = []
    for i, v in enumerate(values):
        year, month = 1800 + i // 12, i % 12 + 1
        lines.append(f"{year} {month} {year + (month - 0.5) / 12:.4f} {v} -1 -1")
    (inputs / "monthly.txt").write_text("\n".join(lines), encoding="utf-8")
    (inputs / "smoothed.csv").write_text(
        "\n".join(lines).replace(" ", ";"), encoding="utf-8"
    )
    (inputs / "boundaries.txt").write_text(
        "1 1800 1 1 1800 2 10 0 4\n2 1800 5 2 1800 6 20 0 4\n3 1800 9 1 1800 11 10 0 4\n4 1801 1 1\n",
        encoding="utf-8",
    )
    a = {
        "cycles": [1, 2, 3],
        "targets": [3],
        "plot_cycles": [1, 2, 3],
        "thresholds": [5, 20],
        "primary_threshold": 20,
        "boundary": "left_closed_right_open",
        "peak_tie": "earliest",
        "missing_policy": "fail_incomplete_cycle",
        "comparison_groups": {"reference": [1, 2]},
    }
    core.save_json(inputs / "a_parameters.json", a)
    rows = core.calculate(
        core.load_monthly(inputs / "monthly.txt"),
        core.load_monthly(inputs / "smoothed.csv", ";"),
        core.load_boundaries(inputs / "boundaries.txt"),
        a["cycles"],
        a["thresholds"],
    )
    core.write_csv(inputs / "a_metrics.csv", rows)
    files = [(key, next(inputs.glob(key + ".*"))) for key in sorted(phase.INPUT_KEYS)]
    core.save_json(
        inputs / "input_manifest.json",
        {
            "files": [
                {
                    "key": key,
                    "path": p.name,
                    "bytes": p.stat().st_size,
                    "sha256": core.digest(p),
                }
                for key, p in files
            ]
        },
    )
    params = tmp_path / "phase_parameters.json"
    core.save_json(params, settings())
    return inputs, params


def test_scaling_preserves_normalized_shape_and_endpoints():
    g = np.linspace(0, 1, 101)
    a, an = phase.align_curve(series([2, 10, 4, 3]), 0, 3, 10, g)
    b, bn = phase.align_curve(series([6, 30, 12, 9]), 0, 3, 30, g)
    assert phase.rms(a, b) > 0
    assert phase.rms(an, bn) < 1e-15
    assert a[0] == 2 and a[-1] == 3
    # The old A peak is the denominator, not the resampled maximum.
    coarse = phase.align_curve(series([0, 10, 0, 0]), 0, 3, 10, [0, 0.5, 1])[1]
    assert max(coarse) == pytest.approx(0.5, abs=1e-15)


def test_peak_shift_changes_normalized_distance():
    grid = np.linspace(0, 1, 101)
    a = phase.align_curve(series([0, 10, 0, 0]), 0, 3, 10, grid)[1]
    b = phase.align_curve(series([0, 0, 10, 0]), 0, 3, 10, grid)[1]
    assert phase.rms(a, b) > 0.3


@pytest.mark.parametrize(
    "fault", ["missing", "null", "endpoint", "zero_peak", "extrapolate"]
)
def test_invalid_curve_is_not_filled(fault):
    source = series([1, 10, 4, 1])
    grid, peak = [0, 0.5, 1], 10
    if fault == "missing":
        del source[1]
    elif fault == "null":
        source[1]["value"] = None
    elif fault == "endpoint":
        del source[3]
    elif fault == "zero_peak":
        peak = 0
    else:
        grid = [-0.1, 0.5, 1]
    with pytest.raises(ValueError):
        phase.align_curve(source, 0, 3, peak, grid)


def test_independent_scalar_interpolation():
    values = [2, 5, 1, 7, 3]
    grid = np.linspace(0, 1, 101)
    actual, _ = phase.align_curve(series(values), 0, 4, 7, grid)
    expected = []
    for phi in grid:
        x = float(phi) * 4
        left = min(int(x), 3)
        expected.append(values[left] * (left + 1 - x) + values[left + 1] * (x - left))
    np.testing.assert_allclose(actual, expected, atol=1e-14, rtol=0)


def test_actual_distance_controls_Q2_and_loo_excludes_self():
    p = settings()
    a = np.array([0.0, 1.0, 0.0])
    curves = {c: (a.copy(), a.copy()) for c in p["cycles"]}
    _, background, targets, pairs = phase.compare(curves, p)
    assert targets[0]["status"] == "not_outside_reference_background"
    assert [r["reference_cycles"] for r in background] == ["2", "1"]
    assert len(pairs) == 3
    curves[3] = (a.copy(), a + 1e-15)
    assert (
        phase.compare(curves, p)[2][0]["status"] == "not_outside_reference_background"
    )
    curves[3] = (np.array([1.0, 0.0, 1.0]), np.array([1.0, 0.0, 1.0]))
    assert phase.compare(curves, p)[2][0]["status"] == "outside_reference_background"


def test_readiness_does_not_compute_B_and_repeat_outputs_match(package, tmp_path):
    inputs, params = package
    result = phase.run(inputs, params, tmp_path / "preflight", check_only=True)
    assert result["status"] == "inputs_ready_no_B_results"
    assert not (tmp_path / "preflight/phase_curves.csv").exists()
    phase.run(inputs, params, tmp_path / "run1")
    phase.run(inputs, params, tmp_path / "run2")
    for path in (tmp_path / "run1").glob("*"):
        if path.suffix in (".csv", ".png") or path.name in (
            "hypothesis_Q2.json",
            "B_phase_report.md",
        ):
            assert path.read_bytes() == (tmp_path / "run2" / path.name).read_bytes()
    with (tmp_path / "run1/phase_curves.csv").open(encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 303


def test_changed_A_baseline_is_rejected_even_if_manifest_rehashed(package, tmp_path):
    inputs, params = package
    file = inputs / "a_metrics.csv"
    file.write_text(
        file.read_text(encoding="utf-8").replace("10.0", "11.0"), encoding="utf-8"
    )
    manifest = json.loads((inputs / "input_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["files"]:
        if rec["key"] == "a_metrics":
            rec.update(sha256=core.digest(file), bytes=file.stat().st_size)
    core.save_json(inputs / "input_manifest.json", manifest)
    with pytest.raises(ValueError, match="A baseline differs"):
        phase.run(inputs, params, tmp_path / "failed")
    assert (
        json.loads((tmp_path / "failed/run_manifest.json").read_text(encoding="utf-8"))[
            "status"
        ]
        == "failed"
    )


def test_missing_endpoint_readiness_blocks(package, tmp_path):
    inputs, params = package
    f = inputs / "smoothed.csv"
    f.write_text(
        "\n".join(f.read_text(encoding="utf-8").splitlines()[:-1]), encoding="utf-8"
    )
    manifest = json.loads((inputs / "input_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["files"]:
        if rec["key"] == "smoothed":
            rec.update(sha256=core.digest(f), bytes=f.stat().st_size)
    core.save_json(inputs / "input_manifest.json", manifest)
    with pytest.raises(ValueError, match="endpoint"):
        phase.run(inputs, params, tmp_path / "failed", check_only=True)


def test_phase_route_and_literature_exclusion():
    from jw.research_protocols import detect_analysis_protocol
    from jw.middleware.research_router import _fallback_route, _with_analysis_protocol

    prompt = "使用 SILSO 完成 B 阶段相位与幅度归一化，基于 A 周期均值和峰值核验结果比较形态。"
    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)
    assert route["required_analysis_protocol"] == phase.PROTOCOL
    assert (
        route["mode"] == "verified_analysis"
        and route["required_specialist"] == "solar-data"
    )
    assert (
        detect_analysis_protocol("仅做文献综述。禁止计算 SILSO 相位归一化。") == "none"
    )
    fixture = Path(__file__).parent / "fixtures/sc3_sc6_phase_request.md"
    assert (
        detect_analysis_protocol(fixture.read_text(encoding="utf-8")) == phase.PROTOCOL
    )


def test_task_adapter_and_review_gate(package, tmp_path, monkeypatch):
    import shutil
    from jw.workspaces import ensure_thread_workspace
    from jw.tools.solar_feature import (
        run_silso_cycle_phase,
        open_bounded_solar_data_context,
    )
    from jw.research_review import ResearchReviewStore

    inputs, params = package
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr("jw.paths.WORKSPACE_ROOT", runtime)
    binding = ensure_thread_workspace(
        "phase-test", runtime, first_request="使用 SILSO 进行相位归一化研究。"
    )
    root = Path(binding.workspace)
    records = []
    for key, f in [
        (k, next(inputs.glob(k + ".*"))) for k in sorted(phase.INPUT_KEYS)
    ] + [("parameters", params)]:
        dest = root / "inputs" / f.name
        shutil.copy2(f, dest)
        r = {
            "path": dest.relative_to(root).as_posix(),
            "bytes": f.stat().st_size,
            "sha256": core.digest(f),
            "role": "user_input",
        }
        dataset = {
            "monthly": "silso-monthly-total-v2",
            "smoothed": "silso-monthly-smoothed-v2",
            "boundaries": "silso-cycle-extrema-v2",
        }.get(key)
        if dataset:
            r["dataset_id"] = dataset
        records.append(r)
    manifest_path = root / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = records
    core.save_json(manifest_path, manifest)
    config = {"configurable": {"thread_id": "phase-test"}}
    context = open_bounded_solar_data_context(config, analysis_protocol=phase.PROTOCOL)
    assert context["required_data_product"] == phase.PROTOCOL
    store = ResearchReviewStore(root, "phase-test")

    def targets():
        return [
            {
                "stage": "data",
                "payload": {
                    "phase": "bounded_data",
                    "source_manifest": [
                        {
                            "source_ref": p.relative_to(root).as_posix(),
                            "sha256": core.digest(p),
                        }
                        for p in store._canonical_stage_sources("data")
                    ],
                },
            }
        ]

    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"
    args = {
        "monthly_total_path": "inputs/monthly.txt",
        "smoothed_path": "inputs/smoothed.csv",
        "official_extrema_path": "inputs/boundaries.txt",
        "a_metrics_path": "inputs/a_metrics.csv",
        "a_parameters_path": "inputs/a_parameters.json",
        "parameters_path": "inputs/phase_parameters.json",
    }
    result = json.loads(run_silso_cycle_phase.invoke(args, config=config))
    assert result["status"] == "verified", result
    assert result["review_status"] == "not_reviewed"
    assert store._data_input_boundary_issues(targets()) == []
    output = next((root / "outputs").rglob("phase_curves.csv"))
    output.write_text("fabricated", encoding="utf-8")
    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"
    (root / "inputs/phase_parameters.json").write_text("{}", encoding="utf-8")
    error = json.loads(run_silso_cycle_phase.invoke(args, config=config))
    assert error["status"] == "error"
    audits = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (root / "receipts/attempts").glob("cycle-phase-*.json")
    ]
    assert {a["status"] for a in audits} == {"failed", "computation_completed"}

"""C preparation: synthetic science, offline tool binding, fail-closed review."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from jw import (
    cross_series as c1,
    cycle_sensitivity as c2,
    cycle_metrics as core,
    cycle_phase as phase,
)

FIXTURES = Path(__file__).parent / "fixtures"


def settings(batch="C2a"):
    return {
        "schema": "cycle-sensitivity-execution-v1",
        "protocol": c2.PROTOCOL,
        "batch": batch,
        "plan": json.loads(
            (FIXTURES / "c_stage_sensitivity_plan.json").read_text("utf-8")
        ),
    }


@pytest.fixture
def synthetic(tmp_path):
    """Entirely artificial 11-year cycles, GN exactly twice day-weighted SN."""
    inputs = tmp_path / "synthetic_inputs"
    inputs.mkdir()
    lines = []
    for year in range(1749, 2027):
        for month in range(1, 13):
            index = year * 12 + month - 1
            step = (index - (1755 * 12 + 1)) % 132
            value = 1 + min(step, 132 - step) + ((year - 1749) // 11) % 4
            lines.append(f"{year} {month} {year + 0.5} {value} -1 -1")
    (inputs / "monthly.txt").write_text("\n".join(lines), "utf-8")
    (inputs / "smoothed.csv").write_text("\n".join(lines).replace(" ", ";"), "utf-8")
    (inputs / "boundaries.txt").write_text(
        "\n".join(
            f"{i} {1755 + (i - 1) * 11} 2 1 {1760 + (i - 1) * 11} 8 70 11 0"
            for i in range(1, 25)
        )
        + "\n25 2019 2 1\n",
        "utf-8",
    )
    a = json.loads((FIXTURES / "c_stage_A_parameters.json").read_text("utf-8"))
    b = json.loads((FIXTURES / "c_stage_B_parameters.json").read_text("utf-8"))
    core.save_json(inputs / "a_parameters.json", a)
    core.save_json(inputs / "b_parameters.json", b)
    monthly = core.load_monthly(inputs / "monthly.txt")
    smoothed = core.load_monthly(inputs / "smoothed.csv", ";")
    bounds = core.load_boundaries(inputs / "boundaries.txt")
    a_rows = core.calculate(monthly, smoothed, bounds, a["cycles"], a["thresholds"])
    core.write_csv(inputs / "a_metrics.csv", a_rows)
    curves = {
        r["cycle"]: phase.align_curve(
            smoothed,
            bounds[r["cycle"]]["start"],
            bounds[r["cycle"] + 1]["start"],
            r["smoothed_peak"],
            np.linspace(0, 1, 101),
        )
        for r in a_rows
    }
    core.write_csv(inputs / "b_targets.csv", phase.compare(curves, b)[2])
    (inputs / "gnbb2.txt").write_text(
        "\n".join(
            f"{y}.5 {2 * c1.annualize(monthly, y, 'calendar_days'):.14g} -1"
            for y in range(1749, 2016)
        ),
        "utf-8",
    )
    return inputs


def package(synthetic, tmp_path, batch="C2a"):
    keys = c2.INPUT_KEYS if batch != "C1" else (phase.INPUT_KEYS | {"gnbb2"})
    inputs = tmp_path / (batch + "_input")
    inputs.mkdir()
    records = []
    for key in sorted(keys):
        f = next(synthetic.glob(key + ".*"))
        shutil.copy2(f, inputs / f.name)
        records.append(
            {
                "key": key,
                "path": f.name,
                "sha256": core.digest(f),
                "bytes": f.stat().st_size,
            }
        )
    core.save_json(
        inputs / "input_manifest.json",
        {
            "schema": "cross-series-inputs-v1"
            if batch == "C1"
            else "cycle-sensitivity-inputs-v1",
            "files": records,
        },
    )
    params = tmp_path / (batch + "_parameters.json")
    core.save_json(
        params,
        json.loads((FIXTURES / "cross_series_parameters.json").read_text("utf-8"))
        if batch == "C1"
        else settings(batch),
    )
    return inputs, params


def test_frozen_batches_are_24_unique_single_factor_scenarios():
    batches = [c2.validate_parameters(settings(batch)) for batch in c2.BATCHES]
    assert [len(rows) for rows in batches] == [5, 13, 8]
    assert len({r["id"] for rows in batches for r in rows}) == 24
    assert all(len(row["changes"]) <= 1 for rows in batches for row in rows)
    p = settings()
    p["plan"]["scenarios"][-1]["changes"]["low_monthly_threshold"] = 31
    with pytest.raises(ValueError):
        c2.validate_parameters(p)


def test_smoothing_weights_and_missing_halo():
    monthly = {i: {"value": float(i * i)} for i in range(-12, 13)}
    assert c2.centered(monthly, 0, 0, 9)[0]["value"] == pytest.approx(20 / 3)
    assert c2.centered(monthly, 0, 0, 13, silso_weights=True)[0][
        "value"
    ] == pytest.approx(73 / 6)
    assert c2.centered(monthly, 0, 0, 25)[0]["value"] == 52
    del monthly[-12]
    with pytest.raises(ValueError, match="padding"):
        c2.centered(monthly, 0, 0, 25)


@pytest.mark.parametrize("batch", list(c2.BATCHES))
def test_scenarios_obey_single_factor_invariants(synthetic, tmp_path, batch):
    inputs, param = package(synthetic, tmp_path, batch)
    p = json.loads(param.read_text("utf-8"))
    m, s, b, a, bp, bt, _ = c2.load_inputs(inputs, p)
    tables = c2.calculate(m, s, b, a, bp, bt, p)
    n = {"C2a": 5, "C2b": 13, "C2c": 8}[batch]
    assert len(tables["scenario_metrics.csv"]) == 24 * n
    assert len(tables["scenario_phase_curves.csv"]) == 2424 * n
    assert len(tables["scenario_targets.csv"]) == 4 * n
    assert len(tables["scenario_Q1.csv"]) == 30 * n
    base = {
        r["cycle"]: r
        for r in tables["scenario_metrics.csv"]
        if r["scenario"] == "baseline"
    }
    for r in tables["scenario_metrics.csv"]:
        original = base[r["cycle"]]
        if r["family"] in {"threshold", "references"}:
            assert all(
                r[k] == original[k]
                for k in (
                    "months",
                    "smoothed_peak",
                    "monthly_mean",
                    "activity_sn_month",
                    "peak_phase",
                )
            )
        if r["family"] == "boundary_global":
            assert r["months"] == original["months"]
        if r["family"] == "boundary_local":
            boundary = int(r["scenario"].split("_")[1][2:])
            if r["cycle"] not in {boundary - 1, boundary}:
                assert all(
                    r[k] == original[k]
                    for k in (
                        "months",
                        "smoothed_peak",
                        "monthly_mean",
                        "activity_sn_month",
                        "peak_phase",
                    )
                )
    base_t = {
        r["cycle"]: r
        for r in tables["scenario_targets.csv"]
        if r["scenario"] == "baseline"
    }
    for r in tables["scenario_targets.csv"]:
        if r["family"] == "threshold":
            assert r["normalized_rms"] == base_t[r["cycle"]]["normalized_rms"]
    bad = copy.deepcopy(bt)
    bad[0]["normalized_rms"] = "999"
    with pytest.raises(ValueError, match="frozen B"):
        c2.calculate(m, s, b, a, bp, bad, p)


@pytest.mark.parametrize("batch", ["C1", *c2.BATCHES])
def test_check_only_never_creates_C_results(synthetic, tmp_path, batch):
    inputs, param = package(synthetic, tmp_path, batch)
    mod = c1 if batch == "C1" else c2
    before = {p.name: core.digest(p) for p in inputs.iterdir()}
    info = mod.run(inputs, param, tmp_path / "check", check_only=True)
    assert info["check_only"] is True and info["agent_run"] is False
    assert {f.name for f in (tmp_path / "check").glob("*.csv")} <= {
        "calendar_membership_plan.csv"
    }
    assert before == {p.name: core.digest(p) for p in inputs.iterdir()}


@pytest.mark.parametrize("batch", ["C1", "C2a"])
def test_portable_replay_and_repeatability(synthetic, tmp_path, batch):
    inputs, param = package(synthetic, tmp_path, batch)
    mod = c1 if batch == "C1" else c2
    out = tmp_path / "run"
    import matplotlib.pyplot as plt

    # Simulate style pollution from an unrelated caller/test, then ensure
    # byte-identical portable output and restoration of the caller style.
    with plt.rc_context(
        {"axes.prop_cycle": plt.cycler(color=["magenta"]), "font.size": 18}
    ):
        mod.run(inputs, param, out)
        assert plt.rcParams["font.size"] == 18
    script = (
        "analyze_cross_series.py" if batch == "C1" else "analyze_cycle_sensitivity.py"
    )
    subprocess.run(
        [
            sys.executable,
            str(out / "code" / script),
            "--inputs",
            str(out / "inputs"),
            "--parameters",
            str(
                out / ("C1_parameters.json" if batch == "C1" else "C2_parameters.json")
            ),
            "--output",
            str(tmp_path / "replay"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for p in out.iterdir():
        if p.suffix in {".csv", ".png"} or p.name in {
            "C1_report.md",
            "C2_report.md",
            "hypothesis_Q3.json",
            "hypotheses_C2.json",
        }:
            assert p.read_bytes() == (tmp_path / "replay" / p.name).read_bytes()


@pytest.mark.parametrize("batch", ["C1", "C2a", "C2b", "C2c"])
def test_route_keeps_normal_independent_data_review(batch):
    from jw.middleware.research_router import _fallback_route, _with_analysis_protocol
    from jw.research_protocols import detect_analysis_protocol

    protocol = c1.PROTOCOL if batch == "C1" else c2.PROTOCOL
    question = f"[C_PROTOCOL={protocol}] [C2_BATCH={batch}] 使用 SILSO 进行有界计算。"
    route = _with_analysis_protocol(_fallback_route(question), text=question)
    assert (
        route["mode"] == "verified_analysis"
        and route["required_specialist"] == "solar-data"
    )
    assert route["required_analysis_protocol"] == protocol
    assert (
        detect_analysis_protocol(
            f"仅做文献综述。禁止计算 [C_PROTOCOL={protocol}] SILSO。"
        )
        == "none"
    )


@pytest.mark.parametrize("batch", ["C1", "C2a"])
def test_JW_tool_and_review_integrity(synthetic, tmp_path, monkeypatch, batch):
    from jw.workspaces import ensure_thread_workspace
    from jw.tools.solar_feature import (
        run_silso_gnbb2_annual,
        run_silso_cycle_sensitivity,
        open_bounded_solar_data_context,
    )
    from jw.research_review import ResearchReviewStore
    from jw.tools.c_stage import IDS, NAMES

    inputs, param = package(synthetic, tmp_path, batch)
    mod = c1 if batch == "C1" else c2
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr("jw.paths.WORKSPACE_ROOT", runtime)
    task_id = "synthetic-c-test"
    question = f"[C_PROTOCOL={mod.PROTOCOL}] [C2_BATCH={batch}] 使用 SILSO 实际计算并独立审查。"
    root = Path(
        ensure_thread_workspace(task_id, runtime, first_request=question).workspace
    )
    records = []
    args = {}
    names = {
        "monthly": "monthly_total_path",
        "smoothed": "smoothed_path",
        "boundaries": "official_extrema_path",
    }
    files = [
        (r["key"], inputs / r["path"])
        for r in json.loads((inputs / "input_manifest.json").read_text("utf-8"))[
            "files"
        ]
    ] + [("parameters", param)]
    for key, f in files:
        dest = root / "inputs" / f.name
        shutil.copy2(f, dest)
        rec = {
            "path": dest.relative_to(root).as_posix(),
            "sha256": core.digest(f),
            "bytes": f.stat().st_size,
            "role": "user_input",
        }
        if key in IDS:
            rec["dataset_id"] = IDS[key]
        records.append(rec)
        args[names.get(key, key + "_path")] = rec["path"]
    manifest = root / "input_manifest.json"
    data = json.loads(manifest.read_text("utf-8"))
    data["inputs"] = records
    core.save_json(manifest, data)
    config = {"configurable": {"thread_id": task_id}}
    context = open_bounded_solar_data_context(config, analysis_protocol=mod.PROTOCOL)
    assert context["required_data_product"] == mod.PROTOCOL
    store = ResearchReviewStore(root, task_id)

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
    tool = run_silso_gnbb2_annual if batch == "C1" else run_silso_cycle_sensitivity
    result = json.loads(tool.invoke(args, config=config))
    assert result["status"] == "verified", result
    assert result["review_status"] == "not_reviewed" and result["key_results"]
    assert store._data_input_boundary_issues(targets()) == []
    # Local unreviewed receipt cannot turn a dry-run into scientific computation.
    rp = root / f"receipts/datasets/{NAMES[mod.PROTOCOL]}.json"
    original_receipt = rp.read_bytes()
    receipt = json.loads(original_receipt)
    out = root / receipt["output_dir"]
    run = out / "run_manifest.json"
    original_run = run.read_bytes()
    info = json.loads(original_run)
    info["check_only"] = True
    core.save_json(run, info)
    for rec in receipt["outputs"]:
        if rec["path"] == run.relative_to(root).as_posix():
            rec.update(sha256=core.digest(run), bytes=run.stat().st_size)
    core.save_json(rp, receipt)
    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"
    run.write_bytes(original_run)
    rp.write_bytes(original_receipt)
    if batch == "C2a":
        task = root / "task.json"
        original_task = task.read_bytes()
        body = json.loads(original_task)
        body["research_question"] = question.replace("C2_BATCH=C2a", "C2_BATCH=C2b")
        core.save_json(task, body)
        assert json.loads(tool.invoke(args, config=config))["status"] == "error"
        task.write_bytes(original_task)
    # A missing binding must fail even if the physical file remains available.
    before_manifest = manifest.read_bytes()
    body = json.loads(before_manifest)
    removed = args["gnbb2_path"] if batch == "C1" else args["b_targets_path"]
    body["inputs"] = [r for r in body["inputs"] if r["path"] != removed]
    core.save_json(manifest, body)
    assert json.loads(tool.invoke(args, config=config))["status"] == "error"
    manifest.write_bytes(before_manifest)
    # Updating every hash still cannot make fabricated numeric results pass.
    rp = root / f"receipts/datasets/{NAMES[mod.PROTOCOL]}.json"
    receipt = json.loads(rp.read_text("utf-8"))
    out = root / receipt["output_dir"]
    target = out / ("annual_matched.csv" if batch == "C1" else "scenario_targets.csv")
    target.write_text(
        target.read_text("utf-8").replace("1755", "9999")
        if batch == "C1"
        else target.read_text("utf-8").replace(
            "not_outside_reference_background", "outside_reference_background"
        ),
        "utf-8",
    )
    run = out / "run_manifest.json"
    info = json.loads(run.read_text("utf-8"))
    for rec in info["artifacts"]:
        f = out / rec["path"]
        rec.update(sha256=core.digest(f), bytes=f.stat().st_size)
    core.save_json(run, info)
    for rec in receipt["outputs"]:
        f = root / rec["path"]
        rec.update(sha256=core.digest(f), bytes=f.stat().st_size)
    core.save_json(rp, receipt)
    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"
    (root / args["parameters_path"]).write_text("{}", "utf-8")
    assert json.loads(tool.invoke(args, config=config))["status"] == "error"
    audits = [
        json.loads(p.read_text("utf-8"))
        for p in (root / "receipts/attempts").glob("*.json")
    ]
    assert {a["status"] for a in audits} >= {"failed", "computation_completed"}


def test_batch_marker_requires_exactly_one():
    from jw.tools.c_stage import batch_from_question

    assert batch_from_question("[C2_BATCH=C2b]") == "C2b"
    for q in ["", "[C2_BATCH=C2a] [C2_BATCH=C2b]", "[C2_BATCH=C2x]"]:
        with pytest.raises(ValueError):
            batch_from_question(q)


@pytest.mark.parametrize("protocol", [c1.PROTOCOL, c2.PROTOCOL])
def test_C_marker_in_source_inventory_is_not_a_computation_request(protocol):
    from jw.research_protocols import detect_analysis_protocol

    for question in [
        f"[C_PROTOCOL={protocol}]",
        f"请计算均值。\n来源: [C_PROTOCOL={protocol}]",
        f"请总结论文。\n> 请计算 [C_PROTOCOL={protocol}]",
        f"仅做文献综述。禁止计算 [C_PROTOCOL={protocol}] SILSO。",
    ]:
        assert detect_analysis_protocol(question) == "none"

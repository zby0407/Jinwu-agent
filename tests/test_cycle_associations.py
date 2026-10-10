"""Synthetic-only D preparation, independent statistics, binding and review gates."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from scipy.stats import pearsonr, spearmanr, linregress

from jw import cycle_associations as d, cycle_metrics as core, cycle_sensitivity as c2
from tests.test_c_stage import synthetic as _synthetic_fixture, package

# Expose the shared fixture to pytest without shadowing an unused import.
synthetic = _synthetic_fixture


@pytest.fixture
def prepared(synthetic, tmp_path):
    target = tmp_path / "D_inputs"
    target.mkdir()
    for key in ["monthly", "smoothed", "boundaries", "a_metrics", "a_parameters"]:
        path = next(synthetic.glob(key + ".*"))
        shutil.copy2(path, target / path.name)
    for batch in ["C2a", "C2b"]:
        inp, par = package(synthetic, tmp_path, batch)
        p = json.loads(par.read_text("utf-8"))
        loaded = c2.load_inputs(inp, p)
        tables = c2.calculate(*loaded[:-1], p)
        core.write_csv(
            target / (batch.lower() + "_metrics.csv"), tables["scenario_metrics.csv"]
        )
        shutil.copy2(par, target / (batch.lower() + "_parameters.json"))
    records = []
    for key in sorted(d.INPUT_KEYS):
        path = next(target.glob(key + ".*"))
        records.append(
            {
                "key": key,
                "path": path.name,
                "sha256": core.digest(path),
                "bytes": path.stat().st_size,
            }
        )
    core.save_json(
        target / "input_manifest.json", {"schema": "D-inputs-v1", "files": records}
    )
    return target


def test_exact_effects_ties_and_undefined():
    result = d.effect([1, 2, 3, 4], [4, 3, 2, 1])
    assert result["pearson_r"] == result["spearman_rho"] == result["ols_slope"] == -1
    assert result["ols_intercept"] == 5
    assert d.ranks([4, 1, 1, 3]) == [4, 1.5, 1.5, 3]
    assert d.effect([2] * 4, [1, 2, 3, 4])["status"] == "constant_variable"
    assert d.effect([1, 2, 3], [1, 2, 3])["status"] == "insufficient_pairs"
    with pytest.raises(ValueError):
        d.effect([1, 2, float("nan"), 4], [1, 2, 3, 4])


@pytest.mark.parametrize(
    "xs,ys",
    [
        ([5, 2, 7, 2, 9, 3], [1, 1, 6, 4, 8, 9]),
        ([1, 2, 3, 4, 5, 6], [2, 4, 7, 8, 10, 13]),
        ([1, 1, 2, 3, 4, 5], [10, 8, 5, 6, 4, 1]),
    ],
)
def test_effects_against_independent_scipy(xs, ys):
    value = d.effect(xs, ys)
    assert value["pearson_r"] == pytest.approx(pearsonr(xs, ys).statistic, abs=1e-14)
    assert value["spearman_rho"] == pytest.approx(
        spearmanr(xs, ys).statistic, abs=1e-14
    )
    assert value["ols_slope"] == pytest.approx(linregress(xs, ys).slope, abs=1e-14)


def test_indexing_and_shared_cycle_leave_out():
    rows = [
        {"cycle": c, "months": c + 100, "smoothed_peak": 25 - c} for c in range(1, 25)
    ]
    pairs = d.paired(rows, d.SPECS["D1"][0])
    assert len(pairs) == 23
    assert (pairs[0]["x_cycle"], pairs[0]["y_cycle"], pairs[0]["y"]) == (1, 2, 23)
    by = {r["subset"]: r for r in d.summaries(pairs, d.parameters("D1"))}
    assert by["leave_cycle_5"]["n"] == 21
    assert (
        "4:5" not in by["leave_cycle_5"]["included_pairs"]
        and "5:6" not in by["leave_cycle_5"]["included_pairs"]
    )
    assert by["leave_pair_4_5"]["n"] == 22
    assert by["early_y_SC1_12"]["n"] == 11 and by["late_y_SC13_24"]["n"] == 12
    assert by["exclude_physical_SC3_6"]["n"] == 18
    assert by["leave_cycle_1"]["n"] == 22
    assert len(d.paired(rows, d.SPECS["D1"][2])) == 24


def test_parameters_are_explicit_and_frozen():
    assert len(d.SCENARIOS) == 15 and len(set(d.SCENARIOS)) == 15
    for field, value in [
        ("forecast", True),
        ("cycles", [3, 4, 5, 6]),
        ("p_values", True),
        ("late_period_first_y_cycle", 10),
    ]:
        p = d.parameters("D1")
        p[field] = value
        with pytest.raises(ValueError):
            d.validate_parameters(p)


@pytest.mark.parametrize("batch", ["D1", "D2"])
def test_check_only_has_no_associations(prepared, tmp_path, monkeypatch, batch):
    par = tmp_path / "p.json"
    core.save_json(par, d.parameters(batch))
    monkeypatch.setattr(
        d, "calculate", lambda *a: pytest.fail("check-only ran D associations")
    )
    info = d.run(prepared, par, tmp_path / "check", check_only=True)
    assert info["status"] == "inputs_ready_no_D_results" and info["agent_run"] is False
    assert not list((tmp_path / "check").glob("*.csv"))


@pytest.mark.parametrize("batch", ["D1", "D2"])
def test_portable_run_all_subsets_and_input_preservation(prepared, tmp_path, batch):
    par = tmp_path / "p.json"
    core.save_json(par, d.parameters(batch))
    before = {p.name: core.digest(p) for p in prepared.iterdir()}
    out = tmp_path / "out"
    d.run(prepared, par, out)
    from jw.cycle_association_verify import verify

    assert verify(out)["status"] == "pass"
    stats = c2.table(out / "association_statistics.csv")
    assert len({r["scenario"] for r in stats}) == 15
    assert len({r["relation"] for r in stats}) == len(d.SPECS[batch])
    features = c2.table(out / "cycle_features.csv")
    assert len(features) == 360
    for r in features:
        if float(r["decline_months"]):
            assert float(r["rise_decline_ratio"]) == float(r["rise_months"]) / float(
                r["decline_months"]
            )
    assert before == {p.name: core.digest(p) for p in prepared.iterdir()}
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(out / "code/cycle_associations.py"),
            "--inputs",
            str(out / "inputs"),
            "--parameters",
            str(out / "D_parameters.json"),
            "--output",
            str(tmp_path / "replay"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    for p in out.iterdir():
        if p.suffix in {".csv", ".png", ".md"} or p.name == "hypotheses_D.json":
            assert p.read_bytes() == (tmp_path / "replay" / p.name).read_bytes()
    if batch == "D2":
        assert len(c2.table(out / "coupling_diagnostic.csv")) == 345
    with pytest.raises(ValueError, match="fresh"):
        d.run(prepared, par, out)


def test_tampered_C_even_with_updated_hash_fails(prepared, tmp_path):
    target = prepared / "c2b_metrics.csv"
    text = target.read_text("utf-8")
    target.write_text(text.replace(",136,", ",137,"), "utf-8")
    # Alter one observed synthetic value explicitly even if the string above did not match.
    rows = c2.table(target)
    rows[24]["smoothed_peak"] = "9999"
    core.write_csv(target, rows)
    manifest = json.loads((prepared / "input_manifest.json").read_text("utf-8"))
    for r in manifest["files"]:
        if r["path"] == target.name:
            r.update(sha256=core.digest(target), bytes=target.stat().st_size)
    core.save_json(prepared / "input_manifest.json", manifest)
    p = d.parameters("D1")
    loaded = d.load_inputs(prepared, p)
    with pytest.raises(ValueError, match="C metrics do not reproduce"):
        d.calculate(*loaded[:-1], p)


def test_D_route_and_no_source_marker_activation():
    from jw.middleware.research_router import _with_analysis_protocol, _fallback_route
    from jw.research_protocols import detect_analysis_protocol

    q = f"请计算 [D_PROTOCOL={d.PROTOCOL}] [D_BATCH=D1] 有界关联。"
    route = _with_analysis_protocol(_fallback_route(q), text=q)
    assert route["required_analysis_protocol"] == d.PROTOCOL
    assert (
        route["required_specialist"] == "solar-data"
        and route["mode"] == "verified_analysis"
    )
    for text in [
        f"[D_PROTOCOL={d.PROTOCOL}]",
        f"仅做文献综述。禁止计算 [D_PROTOCOL={d.PROTOCOL}] SILSO。",
        f"请总结资料。\n来源: {q}",
        f"请总结资料。\n> {q}",
    ]:
        assert detect_analysis_protocol(text) == "none"


@pytest.mark.parametrize("batch", ["D1", "D2"])
def test_actual_tool_normal_review_gate_and_forged_results(
    prepared, tmp_path, monkeypatch, batch
):
    from jw.workspaces import ensure_thread_workspace
    from jw.tools.solar_feature import (
        run_silso_cycle_associations,
        open_bounded_solar_data_context,
    )
    from jw.tools.c_stage import IDS
    from jw.research_review import ResearchReviewStore

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "bindings"))
    monkeypatch.setattr("jw.paths.WORKSPACE_ROOT", runtime)
    tid = "synthetic-d-" + batch
    question = (
        f"请计算 [D_PROTOCOL={d.PROTOCOL}] [D_BATCH={batch}] 本批关联并独立审查。"
    )
    root = Path(ensure_thread_workspace(tid, runtime, first_request=question).workspace)
    par = tmp_path / "parameters.json"
    core.save_json(par, d.parameters(batch))
    files = [
        (r["key"], prepared / r["path"])
        for r in json.loads((prepared / "input_manifest.json").read_text("utf-8"))[
            "files"
        ]
    ] + [("parameters", par)]
    records = []
    paths = {}
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
        paths[key] = rec["path"]
    manifest = root / "input_manifest.json"
    body = json.loads(manifest.read_text("utf-8"))
    body["inputs"] = records
    core.save_json(manifest, body)
    config = {"configurable": {"thread_id": tid}}
    ctx = open_bounded_solar_data_context(config, analysis_protocol=d.PROTOCOL)
    assert ctx["required_data_product"] == d.PROTOCOL
    store = ResearchReviewStore(root, tid)

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

    assert store._data_input_boundary_issues(targets())
    result = json.loads(
        run_silso_cycle_associations.invoke({"input_paths": paths}, config=config)
    )
    assert result["status"] == "verified", result
    assert result["review_status"] == "not_reviewed"
    assert store._data_input_boundary_issues(targets()) == []
    broken = copy.deepcopy(paths)
    del broken["c2a_metrics"]
    assert (
        json.loads(
            run_silso_cycle_associations.invoke({"input_paths": broken}, config=config)
        )["status"]
        == "error"
    )
    receipt_path = root / "receipts/datasets/d_associations.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    out = root / receipt["output_dir"]
    target = out / "association_statistics.csv"
    rows = c2.table(target)
    rows[0]["pearson_r"] = "0.1234567"
    core.write_csv(target, rows)
    run = out / "run_manifest.json"
    info = json.loads(run.read_text("utf-8"))
    for record in info["artifacts"]:
        path = out / record["path"]
        record.update(sha256=core.digest(path), bytes=path.stat().st_size)
    core.save_json(run, info)
    for record in receipt["outputs"]:
        path = root / record["path"]
        record.update(sha256=core.digest(path), bytes=path.stat().st_size)
    core.save_json(receipt_path, receipt)
    assert store._data_input_boundary_issues(targets())[0]["severity"] == "critical"

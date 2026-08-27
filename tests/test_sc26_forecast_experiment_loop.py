from __future__ import annotations

import json
from pathlib import Path

from jw.tools import automatic_experiment as experiment_tools
from automatic_experiment import service
from automatic_experiment.contracts import default_request
from automatic_experiment.state import task_workspace


def _bind_forecast_run(workspace: Path) -> str:
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    files = {
        "features": inputs / "sc26_cycle_features.csv",
        "predictions": inputs / "sc26_forecast_predictions.csv",
        "forecast": inputs / "sc26_formal_forecast.json",
        "summary": inputs / "run_summary.json",
        "manifest": inputs / "data_manifest.json",
    }
    for path in files.values():
        path.write_text(
            "{}\n" if path.suffix == ".json" else "fixture\n", encoding="utf-8"
        )
    request = default_request("执行太阳活动周第26周预测历史回测的独立结果复核。")
    request["input_refs"] = [
        {
            "id": f"input_{index:02d}",
            "path": path.relative_to(workspace).as_posix(),
            "description": "已接受的 SC26 数据阶段产物。",
            "required": True,
        }
        for index, path in enumerate(files.values(), start=1)
    ]
    bound = service.bind_request({"request": request})
    service.inspect_inputs(bound["run_id"])
    return str(bound["run_id"])


def test_specialized_sc26_design_validates_without_model_schema_retries(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_forecast_run(tmp_path)
        checked = experiment_tools._create_solar_cycle_26_forecast_design(run_id)
        run_root, state = service.load_state(run_id)
        design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))

    assert checked["status"] == "design_validated", checked
    assert state["phase"] == "design_validated"
    assert design["experiment_stages"][0]["execution"]["seed"] == 20260827
    assert design["experiment_stages"][0]["execution"]["expected_artifacts"] == [
        "sc26_forecast_independent_check.json"
    ]
    assert {row["name"] for row in design["measurement_plan"]} >= {
        "candidate_mae",
        "baseline_mae",
        "cycle26_point_estimate",
    }
    assert {row["id"] for row in design["result_plan"]} >= {
        "hypothesis_relation",
        "negative_result_preserved",
        "source_scope_silso_only",
    }


def test_host_protocol_enriches_sc26_experiment_request() -> None:
    request = default_request("复核第26周预测结果。")

    enriched = experiment_tools._apply_host_analysis_protocol(
        request,
        {"analysis_protocol": "solar_cycle_26_forecast_backtest_v1"},
    )

    assert "rolling-origin" in enriched["task"]
    assert "10000" in enriched["task"]
    assert "negative result" in enriched["task"]


def test_specialized_sc26_prepare_uses_bound_outputs_and_valid_worker(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_forecast_run(tmp_path)
        experiment_tools._create_solar_cycle_26_forecast_design(run_id)
        prepared = experiment_tools._prepare_solar_cycle_26_forecast_attempt(run_id)
        run_root, _state = service.load_state(run_id)
        source = (
            run_root / "attempts" / prepared["attempt_id"] / "code" / "experiment.py"
        ).read_text(encoding="utf-8")

    assert prepared["status"] == "attempt_prepared"
    assert 'PREDICTIONS_INPUT_ID = "input_02"' in source
    assert 'FORECAST_INPUT_ID = "input_03"' in source
    assert "REPETITIONS = 10000" in source
    assert "np.random.default_rng(SEED)" in source
    assert '"schema_version": "automatic-experiment-worker-result-v1"' in source

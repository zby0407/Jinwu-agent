from __future__ import annotations

import json
import runpy
from pathlib import Path

from automatic_experiment import service
from automatic_experiment.contracts import default_request
from automatic_experiment.state import task_workspace
from jw.solar_forecast import validate_forecast_experiment_receipt
from jw.tools import automatic_experiment as experiment_tools


def _bind_polar_run(workspace: Path) -> str:
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    table = inputs / "solar_precursor_cycle_features.csv"
    receipt = inputs / "solar_precursor_cycle_table.json"
    values = [0.8, 1.4, 1.0, 1.8, 1.2, 2.0, 0.9, 1.6, 1.1, 1.9]
    targets = [92.0, 151.0, 111.0, 190.0, 132.0, 207.0, 101.0, 171.0, 121.0, 198.0]
    table.write_text(
        "row_role,cycle_number,peak_smoothed_sunspot_number\n"
        + "\n".join(
            f"analysis,{cycle},{target}"
            for cycle, target in zip(range(15, 25), targets, strict=True)
        )
        + "\n",
        encoding="utf-8",
    )
    feature_records = [
        {
            "feature_id": f"polar-minimum-cycle-{cycle}",
            "hypothesis_id": "h2_polar_precursor",
            "target_cycle_id": cycle,
            "value": value,
            "measurement_regime": "MWO" if cycle <= 21 else "WSO",
            "observable_kind": "polar_aperture_field",
            "status": "available",
        }
        for cycle, value in zip(range(15, 25), values, strict=True)
    ]
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "status": "verified",
                "feature_records": feature_records,
                "unavailable_feature_records": [
                    {
                        "hypothesis_id": "h3_axial_dipole_discriminator",
                        "observable_kind": "axial_dipole_moment",
                        "status": "blocked_by_data",
                        "value": None,
                        "data_gap": (
                            "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    request = default_request("检验极小期极区场对下一太阳活动周峰值的历史预测技能。")
    request["input_refs"] = [
        {
            "id": "polar_table",
            "path": table.relative_to(workspace).as_posix(),
            "description": "已接受的逐活动周极区前兆表。",
            "required": True,
        },
        {
            "id": "polar_receipt",
            "path": receipt.relative_to(workspace).as_posix(),
            "description": "已接受的类型化数据回执。",
            "required": True,
        },
    ]
    bound = service.bind_request({"request": request})
    service.inspect_inputs(bound["run_id"])
    return str(bound["run_id"])


def test_specialized_polar_design_freezes_tournament_contract(tmp_path: Path) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_polar_run(tmp_path)
        checked = experiment_tools._create_polar_forecast_design(run_id)
        run_root, state = service.load_state(run_id)
        design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))

    assert checked["status"] == "design_validated", checked
    assert state["phase"] == "design_validated"
    execution = design["experiment_stages"][0]["execution"]
    assert execution["seed"] == 20260828
    assert execution["expected_artifacts"] == experiment_tools.POLAR_FORECAST_OUTPUTS
    assert {row["name"] for row in design["measurement_plan"]} >= {
        "candidate_mae",
        "training_mean_mae",
        "persistence_mae",
        "mae_improvement",
    }
    assert {row["id"] for row in design["result_plan"]} >= {
        "forecast_skill_status",
        "regime_consistent",
        "axial_data_status",
    }


def test_host_protocol_enriches_polar_request() -> None:
    request = default_request("复核极区前兆预测。")

    enriched = experiment_tools._apply_host_analysis_protocol(
        request,
        {"analysis_protocol": "solar_polar_precursor_v1"},
    )

    assert "five initial training cycles" in enriched["task"]
    assert "20260828" in enriched["task"]
    assert "blocked_by_data" in enriched["task"]


def test_specialized_polar_prepare_uses_bound_inputs_and_fixed_worker(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_polar_run(tmp_path)
        experiment_tools._create_polar_forecast_design(run_id)
        prepared = experiment_tools._prepare_polar_forecast_attempt(run_id)
        run_root, _state = service.load_state(run_id)
        source = (
            run_root / "attempts" / prepared["attempt_id"] / "code" / "experiment.py"
        ).read_text(encoding="utf-8")

    assert prepared["status"] == "attempt_prepared"
    assert 'TABLE_INPUT_ID = "polar_table"' in source
    assert 'RECEIPT_INPUT_ID = "polar_receipt"' in source
    assert "REPETITIONS = 10000" in source
    assert '"schema_version": "solar-forecast-experiment-receipt-v1"' in source
    assert "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT" in source


def test_specialized_polar_worker_executes_and_emits_valid_receipt(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_polar_run(tmp_path)
        experiment_tools._create_polar_forecast_design(run_id)
        prepared = experiment_tools._prepare_polar_forecast_attempt(run_id)
        run_root, _state = service.load_state(run_id)
        source_path = (
            run_root / "attempts" / prepared["attempt_id"] / "code" / "experiment.py"
        )
        output_dir = tmp_path / "worker-output"
        output_dir.mkdir()
        namespace = runpy.run_path(str(source_path))
        result = namespace["run_experiment"](
            {
                "input_path_by_id": {
                    "polar_table": tmp_path
                    / "inputs"
                    / "solar_precursor_cycle_features.csv",
                    "polar_receipt": tmp_path
                    / "inputs"
                    / "solar_precursor_cycle_table.json",
                },
                "output_dir": output_dir,
            }
        )

    assert result["execution_completed"] is True
    assert {row["path"] for row in result["artifacts"]} == set(
        experiment_tools.POLAR_FORECAST_OUTPUTS
    )
    receipt = json.loads(
        (output_dir / "forecast_experiment_receipt.json").read_text(encoding="utf-8")
    )
    validated = validate_forecast_experiment_receipt(receipt)
    assert validated["test_cycles"] == [20, 21, 22, 23, 24]
    assert validated["observable_kinds"] == ["polar_aperture_field"]
    assert validated["h3_data_status"]["status"] == "blocked_by_data"
    assert sum(1 for _ in (output_dir / "bootstrap_mae_improvement.csv").open()) == 10001

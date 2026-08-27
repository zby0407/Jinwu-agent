from __future__ import annotations

import json
from pathlib import Path

from jw.tools import automatic_experiment as experiment_tools

from automatic_experiment import service
from automatic_experiment.contracts import default_request
from automatic_experiment.state import task_workspace

_create_silso_cycle_morphology_design = (
    experiment_tools._create_silso_cycle_morphology_design
)
_prepare_silso_cycle_morphology_attempt = (
    experiment_tools._prepare_silso_cycle_morphology_attempt
)


def _bind_morphology_run(workspace: Path) -> str:
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    staged = {
        "extrema": inputs / "a4b5b8812c9e966f-TableCyclesMiMa.txt",
        "smoothed": inputs / "1289e5922889f26f-SN_ms_tot_V2.0.csv",
        "monthly": inputs / "e83932c7a47a12c4-SN_m_tot_V2.0.txt",
        "table": inputs / "19d01a07a0aae775-cycle_morphology_table.csv",
    }
    staged["extrema"].write_text(
        "01 1755 02 14.0 1761 06 144.1 11 04\n", encoding="utf-8"
    )
    staged["smoothed"].write_text(
        "1761;06;1761.455;144.1;-1.0;-1;1\n", encoding="utf-8"
    )
    staged["monthly"].write_text("1761 06 1761.455 100.0 -1.0 -1\n", encoding="utf-8")
    staged["table"].write_text(
        "cycle_number,minimum_date,maximum_date,next_minimum_date,"
        "cycle_length_years,rise_time_years,decline_time_years,"
        "peak_smoothed_sunspot_number,observation_period_group,data_quality_note\n"
        "1,1755-02,1761-06,1766-06,11.333333333333334,"
        "6.333333333333333,5.0,144.1,early,fixture\n",
        encoding="utf-8",
    )
    request = default_request(
        "按 silso_cycle_morphology_v1 对第 1—24 周完成一次独立统计复核；"
        "三组关系均报告 Pearson 和 Spearman 系数、双侧 p 值、95% bootstrap "
        "区间、逐周期留一和固定早期/较现代分组结果。"
    )
    request["input_refs"] = [
        {
            "id": input_id,
            "path": path.relative_to(workspace).as_posix(),
            "description": "已接受的任务内输入。",
            "required": True,
        }
        for input_id, path in (
            ("input_01", staged["extrema"]),
            ("input_02", staged["smoothed"]),
            ("input_03", staged["monthly"]),
            ("input_08", staged["table"]),
        )
    ]
    bound = service.bind_request({"request": request})
    service.inspect_inputs(bound["run_id"])
    return str(bound["run_id"])


def test_specialized_morphology_design_is_valid_on_first_submission(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_morphology_run(tmp_path)
        checked = _create_silso_cycle_morphology_design(run_id)
        assert checked["status"] == "design_validated", checked
        run_root, state = service.load_state(run_id)
        design = json.loads((run_root / "design.json").read_text(encoding="utf-8"))
        response = json.loads((run_root / "response.json").read_text(encoding="utf-8"))

    assert state["phase"] == "design_validated"
    assert response["blockers"] == []
    assert response["clarifications"] == []
    assert design["experiment_stages"][0]["execution"]["seed"] == 20260826
    assert design["experiment_stages"][0]["execution"]["expected_artifacts"] == [
        "cycle_morphology_independent_check.json"
    ]
    measurement_names = {row["name"] for row in design["measurement_plan"]}
    assert {
        "cycle_length_pearson_r",
        "rise_time_spearman_rho",
        "decline_time_spearman_ci_high",
    } <= measurement_names
    assert len(design["criteria"]) == 1
    assert design["criteria"][0]["endpoint_refs"] == ["analysis_endpoint"]


def test_host_morphology_protocol_preserves_required_two_sided_p_values() -> None:
    request = default_request(
        "Compute Pearson and Spearman correlations for the three SILSO cycle "
        "morphology relationships, with bootstrap and leave-one-cycle-out checks."
    )

    enriched = experiment_tools._apply_host_analysis_protocol(
        request,
        {"analysis_protocol": "silso_cycle_morphology_v1"},
    )

    assert "two-sided p-values" in enriched["task"]
    assert request["task"] not in {"", enriched["task"]}


def test_specialized_morphology_prepare_uses_bound_inputs_and_valid_worker(
    tmp_path: Path,
) -> None:
    with task_workspace(tmp_path):
        run_id = _bind_morphology_run(tmp_path)
        _create_silso_cycle_morphology_design(run_id)
        prepared = _prepare_silso_cycle_morphology_attempt(run_id)
        run_root, _state = service.load_state(run_id)
        source = (
            run_root / "attempts" / prepared["attempt_id"] / "code" / "experiment.py"
        ).read_text(encoding="utf-8")

    assert prepared["status"] == "attempt_prepared"
    assert 'EXTREMA_INPUT_ID = "input_01"' in source
    assert 'SMOOTHED_INPUT_ID = "input_02"' in source
    assert 'MONTHLY_INPUT_ID = "input_03"' in source
    assert 'TABLE_INPUT_ID = "input_08"' in source
    assert "BOOTSTRAP_REPETITIONS = 10000" in source
    assert "264.3" not in source
    assert '"schema_version": "automatic-experiment-worker-result-v1"' in source

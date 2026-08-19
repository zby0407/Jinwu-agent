from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from automatic_experiment import service
from automatic_experiment.contracts import RESPONSE_VERSION, default_request
from automatic_experiment.policy import CodePolicyError, scan_python
from automatic_experiment.state import task_workspace
from jw.middleware import closed_loop_orchestration as guard_module
from jw.middleware.closed_loop_orchestration import (
    ClosedLoopOrchestrationGuardMiddleware,
)
from jw.middleware.task_cancellation import TaskCancellationMiddleware
from jw.middleware.virtual_path_code_guard import (
    VirtualPathCodeGuardMiddleware,
)
from jw.tools.automatic_experiment import _request_from_model_object


@dataclass
class _Runtime:
    config: dict[str, object]


class _Request:
    def __init__(self, tool_call: dict[str, object], state: dict[str, object]) -> None:
        self.tool_call = tool_call
        self.state = state
        self.runtime = _Runtime(config={})


def _integrated_state(*, delegated: tuple[str, ...] = ()) -> dict[str, object]:
    messages: list[dict[str, object]] = [
        {
            "type": "human",
            "content": (
                "请做一遍完整研究。先看现有数据和文献，再制定计划、提出假设、"
                "完成实验，最后整理成完整报告。"
            ),
        }
    ]
    messages.extend(
        {
            "type": "ai",
            "tool_calls": [
                {
                    "name": "task",
                    "args": {"subagent_type": agent, "description": "stage"},
                }
            ],
        }
        for agent in delegated
    )
    return {"messages": messages}


def test_natural_integrated_request_allows_planner_before_data_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    request = _Request(
        {
            "name": "task",
            "id": "planner",
            "args": {"subagent_type": "solar-planner", "description": "plan"},
        },
        _integrated_state(),
    )
    sentinel = object()
    result = ClosedLoopOrchestrationGuardMiddleware().wrap_tool_call(
        request, lambda _request: sentinel
    )
    assert result is sentinel


def test_natural_integrated_request_allows_adaptive_main_agent_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        guard_module, "workspace_root_from_config", lambda _config: tmp_path
    )
    request = _Request(
        {
            "name": "write_file",
            "id": "code",
            "args": {"file_path": "/work/experiment.py", "content": "print(1)"},
        },
        _integrated_state(delegated=("solar-data",)),
    )
    sentinel = object()
    result = ClosedLoopOrchestrationGuardMiddleware().wrap_tool_call(
        request, lambda _request: sentinel
    )
    assert result is sentinel


def test_virtual_path_guard_rejects_literal_work_path_in_python() -> None:
    request = _Request(
        {
            "name": "write_file",
            "id": "path",
            "args": {
                "file_path": "/work/experiment.py",
                "content": "open('/work/data.csv').read()",
            },
        },
        {"messages": []},
    )
    result = VirtualPathCodeGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )
    assert result.status == "error"
    assert "CODE CONTRACT BLOCKED" in str(result.content)
    assert "Write executable source under /work/" in str(result.content)
    assert "python /work/analyze.py /inputs/data.csv" in str(result.content)


def test_virtual_path_guard_rejects_literal_project_path_in_python() -> None:
    request = _Request(
        {
            "name": "write_file",
            "id": "project-path",
            "args": {
                "file_path": "/work/experiment.py",
                "content": "open('/project/data/source.csv').read()",
            },
        },
        {"messages": []},
    )

    result = VirtualPathCodeGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )

    assert result.status == "error"
    assert "/project" in str(result.content)
    assert "sys.argv" in str(result.content)


def test_virtual_path_guard_rejects_generated_code_under_inputs() -> None:
    request = _Request(
        {
            "name": "write_file",
            "id": "code-under-inputs",
            "args": {
                "file_path": "/inputs/analyze.py",
                "content": "print('analysis')",
            },
        },
        {"messages": []},
    )

    result = VirtualPathCodeGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )

    assert result.status == "error"
    assert "reserved for source data" in str(result.content)
    assert "under /work/" in str(result.content)


def test_virtual_path_guard_rejects_embedded_scientific_boundary_table() -> None:
    request = _Request(
        {
            "name": "write_file",
            "id": "embedded-boundaries",
            "args": {
                "file_path": "/work/analyze.py",
                "content": (
                    "import csv\n"
                    "with open(sys.argv[1]) as handle:\n"
                    "    rows = list(csv.reader(handle))\n"
                    "cycle_minima = {1: 1901, 2: 1912, 3: 1923, "
                    "4: 1934, 5: 1945}\n"
                ),
            },
        },
        {"messages": []},
    )

    result = VirtualPathCodeGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )

    assert result.status == "error"
    assert "embeds a multi-value scientific" in str(result.content)
    assert "`cycle_minima`" in str(result.content)
    assert "declared primary input" in str(result.content)


def test_virtual_path_guard_allows_algorithm_and_forecast_target_constants() -> None:
    request = _Request(
        {
            "name": "write_file",
            "id": "algorithmic-analysis",
            "args": {
                "file_path": "/work/analyze.py",
                "content": (
                    "import csv\n"
                    "with open(sys.argv[1]) as handle:\n"
                    "    rows = list(csv.reader(handle))\n"
                    "forecast_targets = [19, 20, 21, 22, 23, 24]\n"
                    "print(len(rows), forecast_targets)\n"
                ),
            },
        },
        {"messages": []},
    )
    sentinel = object()

    result = VirtualPathCodeGuardMiddleware().wrap_tool_call(
        request, lambda _request: sentinel
    )

    assert result is sentinel


def test_cancelled_task_blocks_new_subagent_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "receipts/task_cancelled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "jw.middleware.task_cancellation.workspace_root_from_config",
        lambda _config: tmp_path,
    )
    request = _Request(
        {"name": "automatic_experiment_bind_request", "id": "new-run", "args": {}},
        {"messages": []},
    )
    result = TaskCancellationMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )
    assert result.status == "error"
    assert "TASK CANCELLED" in str(result.content)


def test_structured_bind_preserves_compact_inputs() -> None:
    request = _request_from_model_object(
        {
            "task": "Analyze the supplied cycle data in one bounded run.",
            "inputs": [{"id": "sunspots", "path": "/inputs/SN_m_tot.csv"}],
            "resource_budget": {"wall_seconds": 60},
        }
    )
    assert request["input_refs"] == [
        {
            "id": "sunspots",
            "path": "inputs/SN_m_tot.csv",
            "description": "Input explicitly supplied in the structured bind request.",
            "required": True,
        }
    ]
    assert request["resource_budget"]["wall_seconds"] == 60


def test_natural_language_bind_extracts_absolute_virtual_input_paths() -> None:
    request = default_request(
        "Analyze `/inputs/cycle_slope_features.csv` together with "
        "/inputs/SN_m_tot.csv using the supplied real observations."
    )
    assert request["input_refs"] == [
        {
            "id": "input_01",
            "path": "inputs/cycle_slope_features.csv",
            "description": (
                "Explicit input path referenced in the natural-language task."
            ),
            "required": True,
        },
        {
            "id": "input_02",
            "path": "inputs/SN_m_tot.csv",
            "description": (
                "Explicit input path referenced in the natural-language task."
            ),
            "required": True,
        },
    ]


def test_natural_language_bind_deduplicates_slash_variants() -> None:
    request = default_request(
        "Analyze /inputs/data.csv and confirm the same `inputs/data.csv` file."
    )
    assert [row["path"] for row in request["input_refs"]] == ["inputs/data.csv"]


def test_natural_language_bind_normalizes_work_mount_input_paths() -> None:
    request = default_request(
        "Analyze /work/inputs/data.csv and compare it with "
        "`/work/inputs/metadata.json`."
    )
    assert [row["path"] for row in request["input_refs"]] == [
        "inputs/data.csv",
        "inputs/metadata.json",
    ]


def test_natural_language_bind_deduplicates_input_mount_aliases() -> None:
    request = default_request(
        "Analyze /inputs/data.csv and the same `/work/inputs/data.csv` file."
    )
    assert [row["path"] for row in request["input_refs"]] == ["inputs/data.csv"]


def test_design_repair_guide_is_issue_scoped_and_bounded() -> None:
    guide = service._design_repair_guide(
        [
            {"field_path": "design.measurement_plan[2]"},
            {"field_path": "design.paired_comparison_audits[0]"},
        ]
    )
    assert set(guide["object_shapes"]) == {
        "measurement_plan_item",
        "paired_comparison_item",
    }
    assert "stage_nested_shapes" not in guide
    assert len(json.dumps(guide, ensure_ascii=False)) < 5_000


def test_design_repair_guide_includes_stage_contract_only_when_needed() -> None:
    guide = service._design_repair_guide(
        [{"field_path": "design.experiment_stages[0].transitions.completed"}]
    )
    assert "stage_fields" in guide
    assert "stage_nested_shapes" in guide


def test_compact_design_accepts_preregistered_bounded_numeric_rule() -> None:
    request = default_request(
        "Analyze inputs/cycles.csv with a pre-registered small-sample comparison."
    )
    response = {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "task": request["task"],
        "response_kind": "experiment_ready",
        "normalized_task": request["task"],
        "design_summary": "比较两个逐周期预测模型并报告不确定性。",
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }
    compact = {
        "design_summary": response["design_summary"],
        "primary_question": "加入交互项是否改善逐周期预测？",
        "analysis_mode": "逐周期小样本比较分析。",
        "claim_scope": "结论只适用于声明的完整活动周转换对。",
        "method_outline": (
            "按时间顺序拟合加性模型和交互模型，报告交互估计、"
            "逐折预测误差、留一影响和周期级置换结果。"
        ),
        "measurements": [
            {
                "name": "interaction_estimate",
                "display_name": "交互作用估计",
                "role": "primary",
                "unit": "标准化振幅",
                "scientific_meaning": "周期长度变化对应的前兆预测斜率改变量。",
            },
            {
                "name": "additive_mae",
                "display_name": "加性模型平均绝对误差",
                "role": "secondary",
                "unit": "太阳黑子数",
                "scientific_meaning": "按时间顺序逐折预测的加性模型绝对误差平均值。",
            },
            {
                "name": "interaction_mae",
                "display_name": "交互模型平均绝对误差",
                "role": "secondary",
                "unit": "太阳黑子数",
                "scientific_meaning": "按时间顺序逐折预测的交互模型绝对误差平均值。",
            },
            {
                "name": "mae_difference",
                "display_name": "两模型平均绝对误差之差",
                "role": "secondary",
                "unit": "太阳黑子数",
                "scientific_meaning": "交互模型误差减去加性模型误差。",
            },
        ],
        "results": [
            {
                "id": "conclusion_branch",
                "display_name": "结论类别",
                "value_kind": "category",
                "role": "primary",
                "unit": "",
                "scientific_meaning": "支持、削弱或样本不足三类预先声明结论之一。",
            }
        ],
        "criteria": [
            {
                "id": "predictive_comparison",
                "statement": (
                    "交互模型平均绝对误差至多比加性模型高 5%，"
                    "才满足预先声明的不劣条件。"
                ),
                "basis_kind": "bounded_pragmatic_choice",
                "basis_text": (
                    "5% 是在查看结果前固定的有界比较约定；"
                    "它不代表领域通用显著性标准，结论同时受样本量和稳健性检查限制。"
                ),
                "source_refs": [],
                "artifact_refs": ["analysis_summary.json"],
                "measurement_refs": [
                    "additive_mae",
                    "interaction_mae",
                    "mae_difference",
                ],
                "result_refs": ["conclusion_branch"],
                "endpoint_refs": ["analysis_endpoint"],
            },
            {
                "id": "interaction_direction",
                "statement": "报告交互作用方向及其不确定性，不预设方向成立。",
                "basis_kind": "qualitative_no_fixed_threshold",
                "basis_text": "方向和区间直接由声明输入上的计算得到。",
                "source_refs": [],
                "artifact_refs": ["analysis_summary.json"],
                "measurement_refs": ["interaction_estimate"],
                "result_refs": ["conclusion_branch"],
                "endpoint_refs": ["analysis_endpoint"],
            },
        ],
        "method_decisions": [
            {
                "id": "comparison_tolerance",
                "decision_key": "comparison_tolerance",
                "decision": "在查看结果前固定百分之五的不劣容差。",
                "rationale": "九个独立周期对不足以支持精细调参，固定容差避免事后选择。",
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": [],
                "alternatives": ["仅比较误差方向，不设置容差"],
                "claim_limit": "该容差只用于本次探索性分类，不构成领域标准。",
            }
        ],
        "artifacts": [
            {
                "path": "analysis_summary.json",
                "kind": "json",
                "description": "估计量、逐折误差、诊断和结论类别。",
            }
        ],
        "primary_estimand": "周期长度对前兆预测斜率的交互作用估计。",
        "threats_to_validity": ["独立周期对很少，单个周期可能改变估计方向。"],
        "literature_basis": "knowledge_gap：本设计不据此新增文献结论。",
    }

    design = service._compact_single_stage_design(request, response, compact)
    assert service._design_schema_issues(design, request) == []
    assert (
        service.validate_design(design, request, response)["criteria"][0]["basis_kind"]
        == "bounded_pragmatic_choice"
    )


def test_structured_bind_rejects_work_or_ad_hoc_data_paths() -> None:
    with pytest.raises(ValueError, match="must be staged under inputs"):
        _request_from_model_object(
            {
                "task": "Analyze the supplied cycle data in one bounded run.",
                "inputs": [{"id": "sunspots", "path": "./data/SN_m_tot.csv"}],
            }
        )


def test_data_experiment_cannot_validate_with_zero_inputs(tmp_path: Path) -> None:
    with task_workspace(tmp_path):
        bound = service.bind_request(
            {
                "request_input": (
                    "Use the existing workspace CSV data to predict the solar-cycle peak."
                )
            }
        )
        run_id = bound["run_id"]
        request = bound["request"]
        response = {
            "schema_version": RESPONSE_VERSION,
            "task_name": request["task_name"],
            "task": request["task"],
            "response_kind": "experiment_ready",
            "normalized_task": request["task"],
            "design_summary": "Fit one bounded predictive model.",
            "clarifications": [],
            "blockers": [],
            "method_fit": "suitable",
        }
        checked = service.validate_and_store_design(run_id, response, {})
    assert checked["status"] == "terminal"
    assert checked["outcome"] == "input_missing"
    assert "0 字节输入快照" in checked["blockers"][0]


def test_experiment_policy_blocks_fake_fallback_and_centered_prediction() -> None:
    source = """
def run_experiment(context):
    try:
        values = context["input_path_by_id"]["data"]
    except Exception:
        values = [1, 2, 3]
    series.rolling(13, center=True).mean()
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [], "result_items": [], "artifacts": [],
        "warnings": [], "endpoint_results": [], "scientific_payload": {}
    }
"""
    with pytest.raises(CodePolicyError) as caught:
        scan_python(source + "\n# LOOCV prediction\n")
    message = str(caught.value)
    assert "broad exception handling" in message
    assert "centered rolling windows" in message

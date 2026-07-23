from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from EvoScientist.middleware import closed_loop_orchestration as guard_module
from EvoScientist.middleware.closed_loop_orchestration import (
    ClosedLoopOrchestrationGuardMiddleware,
)
from EvoScientist.middleware.virtual_path_code_guard import (
    VirtualPathCodeGuardMiddleware,
)
from EvoScientist.middleware.task_cancellation import TaskCancellationMiddleware
from EvoScientist.tools.automatic_experiment import _request_from_model_object

from automatic_experiment import service
from automatic_experiment.contracts import RESPONSE_VERSION, default_request
from automatic_experiment.policy import CodePolicyError, scan_python
from automatic_experiment.state import task_workspace


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


def test_natural_integrated_request_requires_data_agent_before_planner(
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
    result = ClosedLoopOrchestrationGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )
    assert result.status == "error"
    assert "solar-data or data-analysis-agent" in str(result.content)


def test_natural_integrated_request_blocks_main_agent_experiment_code(
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
    result = ClosedLoopOrchestrationGuardMiddleware().wrap_tool_call(
        request, lambda _request: object()
    )
    assert result.status == "error"
    assert "solar-experiment" in str(result.content)


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
    assert "VIRTUAL PATH BLOCKED" in str(result.content)


def test_cancelled_task_blocks_new_subagent_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "receipts/task_cancelled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "EvoScientist.middleware.task_cancellation.workspace_root_from_config",
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
    source = '''
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
'''
    with pytest.raises(CodePolicyError) as caught:
        scan_python(source + "\n# LOOCV prediction\n")
    message = str(caught.value)
    assert "broad exception handling" in message
    assert "centered rolling windows" in message

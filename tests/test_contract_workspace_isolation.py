from __future__ import annotations

import json
from pathlib import Path

from jw import paths
from jw.tools import automatic_experiment as experiment_tools
from jw.tools import research_planner as planner_tools
from jw.tools import scientific_hypothesis as hypothesis_tools
from jw.workspaces import ensure_thread_workspace
from scientific_hypothesis.contracts import canonical_json_sha256

QUESTION_A = (
    "固定随机种子 20260722，生成两组独立 Poisson 合成计数并比较均值差异；"
    "这是方法测试，不代表真实太阳活动结论。"
)
QUESTION_B = (
    "固定随机种子 7，生成两组独立正态合成样本并比较方差差异；这是第二个隔离方法测试。"
)


def _task_config(tmp_path: Path, monkeypatch, thread_id: str):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(thread_id, tmp_path)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "workspace_thread_id": thread_id,
        }
    }
    return binding, config


def test_planner_contract_state_and_freeze_root_are_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    binding_a, config_a = _task_config(tmp_path, monkeypatch, "planner-a")
    _binding_b, config_b = _task_config(tmp_path, monkeypatch, "planner-b")

    brief_a = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_A}, config=config_a
        )
    )
    json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": QUESTION_B}, config=config_b
        )
    )
    sha_a = brief_a["request_sha256"]
    assert (
        planner_tools._lookup_request("", config_a)["research_question"]
        != (planner_tools._lookup_request("", config_b)["research_question"])
    )

    planner_tools._VALIDATED_RESPONSES[("planner-a", sha_a)] = {"checked": True}
    captured: dict[str, Path] = {}

    def fake_freeze(request, response, *, runs_root, path_root):
        assert request["research_question"] == QUESTION_A
        assert response == {"checked": True}
        captured["runs_root"] = runs_root
        captured["path_root"] = path_root
        return {"status": "frozen_and_valid"}

    monkeypatch.setattr(planner_tools, "freeze_research_plan", fake_freeze)
    outcome = json.loads(
        planner_tools.research_planner_freeze_plan.invoke(
            {"request_sha256": sha_a}, config=config_a
        )
    )
    task_root = Path(binding_a.workspace)
    assert outcome["status"] == "frozen_and_valid"
    assert captured == {
        "runs_root": task_root / "planner" / "runs",
        "path_root": task_root,
    }


def test_hypothesis_state_and_freeze_root_are_task_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    binding_a, config_a = _task_config(tmp_path, monkeypatch, "hypothesis-a")
    _binding_b, config_b = _task_config(tmp_path, monkeypatch, "hypothesis-b")

    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": QUESTION_A}, config=config_a
    )
    hypothesis_tools.scientific_hypothesis_bind_request.invoke(
        {"request_input": QUESTION_B}, config=config_b
    )
    state_a = hypothesis_tools._STATES["hypothesis-a"]
    state_b = hypothesis_tools._STATES["hypothesis-b"]
    assert state_a.request != state_b.request

    checked = {"checked": True}
    state_a.validated_response = checked
    state_a.preflight_response_sha256 = canonical_json_sha256(checked)
    state_a.preflight_attempts = 1
    captured: dict[str, Path] = {}

    def fake_freeze(request, response, register, *, runs_root, path_root):
        assert request == state_a.request
        assert response == checked
        assert register is state_a.evidence_register
        captured["runs_root"] = runs_root
        captured["path_root"] = path_root
        return {"status": "frozen_and_valid"}

    monkeypatch.setattr(hypothesis_tools, "freeze_hypothesis_portfolio", fake_freeze)
    outcome = json.loads(
        hypothesis_tools.scientific_hypothesis_freeze.invoke({}, config=config_a)
    )
    task_root = Path(binding_a.workspace)
    assert outcome["status"] == "frozen_and_valid"
    assert captured == {
        "runs_root": task_root / "hypothesis" / "runs",
        "path_root": task_root,
    }


def test_automatic_experiment_run_is_created_inside_task_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    binding, config = _task_config(tmp_path, monkeypatch, "experiment-a")
    repository_runs = Path(experiment_tools._PROJECT_ROOT) / "experiment" / "runs"
    before = (
        {path.name for path in repository_runs.iterdir()}
        if repository_runs.is_dir()
        else set()
    )

    outcome = json.loads(
        experiment_tools.automatic_experiment_bind_request.invoke(
            {"request_input": QUESTION_A}, config=config
        )
    )

    run_id = outcome["run_id"]
    task_run = Path(binding.workspace) / "experiment" / "runs" / run_id
    assert (task_run / "request.json").is_file()
    assert (task_run / "state.json").is_file()
    after = (
        {path.name for path in repository_runs.iterdir()}
        if repository_runs.is_dir()
        else set()
    )
    assert after == before

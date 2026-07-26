from pathlib import Path

import research_layout


def test_versioned_resources_and_mutable_state_are_separated(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("JW_WORKSPACE_DIR", str(workspace))

    assert research_layout.PLANNER_RESOURCE_ROOT == (
        research_layout.PROJECT_ROOT / "research" / "planner"
    )
    assert research_layout.EXPERIMENT_RESOURCE_ROOT == (
        research_layout.PROJECT_ROOT / "research" / "experiment"
    )
    assert research_layout.contract_runs_root("planner") == (
        workspace / "runtime" / "contracts" / "planner" / "runs"
    )
    assert research_layout.knowledge_export_root() == workspace / "knowledge_base"

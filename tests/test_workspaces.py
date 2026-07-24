"""Tests for persistent project/run/thread workspace isolation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from EvoScientist.middleware.task_workspace import TaskWorkspaceMiddleware
from EvoScientist.workspaces import (
    binding_path,
    bootstrap_legacy_bindings,
    ensure_thread_workspace,
    ensure_workspace_for_config,
    first_human_request,
    get_cached_binding,
    preload_bindings,
    read_binding,
    scope_thread_id,
)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def test_new_threads_get_stable_distinct_run_workspaces(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv("EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(registry))

    first = ensure_thread_workspace(
        "thread-one", base, project_id="solar cycles", first_request="Question one?"
    )
    repeated = ensure_thread_workspace("thread-one", base, project_id="ignored")
    second = ensure_thread_workspace("thread-two", base, project_id="solar cycles")

    assert repeated == first
    assert first.workspace != second.workspace
    assert first.project_shared == second.project_shared
    assert _is_relative_to(Path(first.workspace), base / "projects")
    assert Path(first.workspace, "inputs").is_dir()
    assert Path(first.workspace, "work").is_dir()
    assert Path(first.workspace, "outputs").is_dir()
    assert Path(first.workspace, "receipts").is_dir()
    task = json.loads(Path(first.workspace, "task.json").read_text())
    assert task["research_question"] == "Question one?"
    context = json.loads(Path(first.workspace, "context_snapshot.json").read_text())
    assert context["prior_runs_implicitly_loaded"] is False


def test_binding_filename_cannot_be_controlled_by_thread_id(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    monkeypatch.setenv("EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(registry))
    path = binding_path("../../outside/\nthread", tmp_path / "base")
    assert path.parent == registry
    assert path.suffix == ".json"
    assert "outside" not in path.name


def test_same_thread_id_is_scoped_independently_per_base_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry")
    )
    first_base = tmp_path / "project-one"
    second_base = tmp_path / "project-two"
    first_base.mkdir()
    second_base.mkdir()

    first = ensure_thread_workspace("shared-thread-id", first_base)
    second = ensure_thread_workspace("shared-thread-id", second_base)

    assert first.base_workspace != second.base_workspace
    assert first.workspace != second.workspace
    assert read_binding("shared-thread-id", first_base) == first
    assert read_binding("shared-thread-id", second_base) == second


def test_persisted_binding_can_be_preloaded_after_process_restart(
    tmp_path, monkeypatch
):
    import EvoScientist.workspaces as workspace_module

    monkeypatch.setenv(
        "EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry")
    )
    base = tmp_path / "project"
    base.mkdir()
    created = ensure_thread_workspace("resume-thread", base)
    workspace_module._BINDING_CACHE.clear()

    assert get_cached_binding("resume-thread", base) is None
    assert preload_bindings(base) == 1
    assert get_cached_binding("resume-thread", base) == created


def test_runtime_scope_prefers_parent_workspace_thread(tmp_path, monkeypatch):
    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv(
        "EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry")
    )
    config = {
        "configurable": {
            "thread_id": "async-child",
            "workspace_thread_id": "main-thread",
            "project_id": "project-a",
        }
    }
    assert scope_thread_id(config) == "main-thread"
    binding = ensure_workspace_for_config(
        config,
        base,
        state={"messages": [HumanMessage(content="Bound research question")]},
    )
    assert binding is not None
    assert binding.thread_id == "main-thread"
    assert read_binding("async-child", base) is None
    task = json.loads(Path(binding.workspace, "task.json").read_text())
    assert task["research_question"] == "Bound research question"


def test_first_human_request_handles_text_blocks():
    state = {
        "messages": [
            {"role": "assistant", "content": "ignore"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "question"},
                ],
            },
        ]
    }
    assert first_human_request(state) == "first question"


def test_first_human_request_handles_checkpoint_type_dicts_and_tuples():
    state = {
        "messages": (
            {"type": "ai", "content": "ignore"},
            {"type": "human", "content": "checkpoint research question"},
        )
    }
    assert first_human_request(state) == "checkpoint research question"


def test_task_workspace_middleware_hydrates_precreated_blank_task(
    tmp_path, monkeypatch
):
    import EvoScientist.middleware.task_workspace as task_workspace_module

    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv(
        "EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry")
    )
    binding = ensure_thread_workspace("middleware-thread", base)
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "middleware-thread",
                "workspace_thread_id": "middleware-thread",
            }
        }
    )
    monkeypatch.setattr(task_workspace_module, "get_config", lambda: runtime.config)

    TaskWorkspaceMiddleware(base).before_agent(
        {"messages": [HumanMessage(content="hydrated question")]}, runtime
    )

    task = json.loads(Path(binding.workspace, "task.json").read_text())
    assert task["research_question"] == "hydrated question"


def test_legacy_bootstrap_preserves_existing_threads_and_is_idempotent(
    tmp_path, monkeypatch
):
    base = tmp_path / "workspace"
    base.mkdir()
    registry = tmp_path / "registry"
    monkeypatch.setenv("EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(registry))
    db = tmp_path / "sessions.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE checkpoints "
            "(thread_id TEXT, checkpoint_id TEXT, metadata TEXT)"
        )
        conn.executemany(
            "INSERT INTO checkpoints VALUES (?, ?, ?)",
            [
                ("old-main", "1", json.dumps({"workspace_dir": str(base)})),
                ("old-main", "2", json.dumps({"workspace_dir": str(base)})),
                ("old-child", "3", json.dumps({"workspace_dir": str(base)})),
                ("other-project", "4", json.dumps({"workspace_dir": "/elsewhere"})),
            ],
        )

    result = bootstrap_legacy_bindings(base, db)
    assert result == {"status": "bootstrapped", "created": 2}
    for thread_id in ("old-main", "old-child"):
        binding = read_binding(thread_id, base)
        assert binding is not None
        assert binding.legacy is True
        assert binding.workspace == str(base.resolve())
    assert read_binding("other-project", base) is None

    assert bootstrap_legacy_bindings(base, db) == {
        "status": "already_bootstrapped",
        "created": 0,
    }


def test_corrupt_or_mismatched_binding_is_not_trusted(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    monkeypatch.setenv("EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(registry))
    base = tmp_path / "base"
    base.mkdir()
    path = binding_path("expected", base)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"thread_id": "another"}), encoding="utf-8")
    assert read_binding("expected", base) is None


def test_async_child_run_config_inherits_parent_workspace_scope(monkeypatch):
    import langgraph.config

    import EvoScientist.EvoScientist as agent_module
    from EvoScientist.llm import patches

    monkeypatch.setattr(
        agent_module,
        "_ensure_config",
        lambda: SimpleNamespace(model="model-a", provider="provider-a"),
    )
    monkeypatch.setattr(
        langgraph.config,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "main-thread",
                "project_id": "project-a",
            }
        },
    )

    configurable = patches._read_cfg_configurable()

    assert configurable["workspace_thread_id"] == "main-thread"
    assert configurable["project_id"] == "project-a"


async def test_api_checkpointer_primes_workspace_before_first_graph_node(
    tmp_path, monkeypatch
):
    import aiosqlite

    import EvoScientist.workspaces as workspace_module
    from EvoScientist.sessions import _ApiPruningCheckpointer

    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv("EVOSCIENTIST_WORKSPACE_DIR", str(base))
    monkeypatch.setenv(
        "EVOSCIENTIST_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry")
    )
    workspace_module._BINDING_CACHE.clear()
    config = {
        "configurable": {
            "thread_id": "api-thread",
            "project_id": "project-a",
        }
    }

    async with aiosqlite.connect(tmp_path / "sessions.db") as connection:
        saver = _ApiPruningCheckpointer(connection)
        assert await saver.aget_tuple(config) is None

    binding = get_cached_binding("api-thread", base)
    assert binding is not None
    assert binding.thread_id == "api-thread"
    assert binding.base_workspace == str(base.resolve())

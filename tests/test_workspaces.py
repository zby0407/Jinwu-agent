"""Tests for persistent project/run/thread workspace isolation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from jw.middleware.task_workspace import TaskWorkspaceMiddleware
from jw.workspaces import (
    binding_path,
    bootstrap_legacy_bindings,
    ensure_thread_workspace,
    ensure_workspace_for_config,
    first_human_request,
    get_cached_binding,
    preload_bindings,
    read_binding,
    register_project_data_file,
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
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

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


def test_legacy_data_is_imported_as_manifested_project_input(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    data = base / "data"
    data.mkdir(parents=True)
    source = data / "nested" / "observations.csv"
    source.parent.mkdir()
    source.write_text("date,value\n2026-01,72.4\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    binding = ensure_thread_workspace("data-thread", base)

    imported = Path(binding.project_shared) / "data/nested/observations.csv"
    assert imported.read_bytes() == source.read_bytes()
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    shared_manifest = json.loads(
        Path(binding.project_shared, "data_manifest.json").read_text()
    )
    assert shared_manifest["mode"] == "non_destructive_copy"
    assert shared_manifest["conflicts"] == []
    assert shared_manifest["files"] == [
        {
            "bytes": source.stat().st_size,
            "path": "nested/observations.csv",
            "role": "primary_data",
            "sha256": expected_sha256,
            "status": "imported",
            "virtual_path": "/project/data/nested/observations.csv",
        }
    ]
    run_manifest = json.loads(
        Path(binding.workspace, "input_manifest.json").read_text()
    )
    assert run_manifest["project_inputs"] == [
        {
            "bytes": source.stat().st_size,
            "path": "/project/data/nested/observations.csv",
            "role": "primary_data",
            "sha256": expected_sha256,
            "source": "project_shared_data",
        }
    ]
    context = json.loads(Path(binding.workspace, "context_snapshot.json").read_text())
    assert context["project_data_virtual_path"] == "/project/data/"
    assert context["project_input_count"] == 1


def test_registered_project_data_is_immutable_and_available_to_new_runs(
    tmp_path, monkeypatch
):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    source = tmp_path / "authoritative.csv"
    source.write_text("year,value\n2024,1.25\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    record = register_project_data_file(
        base,
        source,
        "solar/silso_v2/authoritative.csv",
        dataset_id="silso-monthly-v2",
        provenance={"source_url": "https://example.test/official"},
    )
    binding = ensure_thread_workspace("registered-data-thread", base)
    manifest = json.loads(Path(binding.workspace, "input_manifest.json").read_text())

    assert record["role"] == "primary_data"
    assert manifest["project_inputs"] == [
        {
            "bytes": source.stat().st_size,
            "dataset_id": "silso-monthly-v2",
            "path": "/project/data/solar/silso_v2/authoritative.csv",
            "provenance_ref": (
                "/project/data/solar/silso_v2/authoritative.csv.provenance.json"
            ),
            "role": "primary_data",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source": "registered_project_data",
        }
    ]
    staged = Path(binding.project_shared, "data/solar/silso_v2/authoritative.csv")
    staged.write_text("changed\n", encoding="utf-8")
    repeated = ensure_thread_workspace("registered-data-thread", base)
    refreshed = json.loads(Path(repeated.workspace, "input_manifest.json").read_text())
    assert refreshed["project_inputs"] == []


def test_registered_project_data_path_cannot_be_overwritten(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("value\n1\n", encoding="utf-8")
    second.write_text("value\n2\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))
    register_project_data_file(
        base,
        first,
        "solar/versioned.csv",
        dataset_id="solar-data-v1",
        provenance={},
    )

    with pytest.raises(FileExistsError, match="immutable"):
        register_project_data_file(
            base,
            second,
            "solar/versioned.csv",
            dataset_id="solar-data-v2",
            provenance={},
        )


def test_legacy_code_and_outputs_are_preserved_but_not_declared_as_inputs(
    tmp_path, monkeypatch
):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    data = base / "data"
    (data / "outputs").mkdir(parents=True)
    (data / "observations.csv").write_text("date,value\n", encoding="utf-8")
    (data / "analyze.py").write_text("print('reference')\n", encoding="utf-8")
    (data / "example_observations.csv").write_text(
        "date,value\n2026-01,1\n", encoding="utf-8"
    )
    (data / "F107_estimated.csv").write_text(
        "date,value\n2026-01,72.4\n", encoding="utf-8"
    )
    (data / "observations.provenance.json").write_text(
        '{"source_url":"https://example.test/observations"}\n',
        encoding="utf-8",
    )
    (data / "outputs/stats.json").write_text('{"mae": 12.3}\n', encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    binding = ensure_thread_workspace("classified-input-thread", base)

    shared = Path(binding.project_shared)
    assert (shared / "data/analyze.py").is_file()
    assert (shared / "data/outputs/stats.json").is_file()
    manifest = json.loads((shared / "data_manifest.json").read_text())
    roles = {item["path"]: item["role"] for item in manifest["files"]}
    assert roles == {
        "analyze.py": "reference_code",
        "example_observations.csv": "test_fixture",
        "F107_estimated.csv": "derived_artifact",
        "observations.csv": "primary_data",
        "observations.provenance.json": "provenance",
        "outputs/stats.json": "derived_artifact",
    }
    run_manifest = json.loads(
        Path(binding.workspace, "input_manifest.json").read_text()
    )
    assert [item["path"] for item in run_manifest["project_inputs"]] == [
        "/project/data/observations.csv"
    ]


def test_legacy_data_import_never_overwrites_project_conflict(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    (base / "data").mkdir(parents=True)
    (base / "data/observations.csv").write_text("legacy\n", encoding="utf-8")
    project_data = base / "projects/default/shared/data"
    project_data.mkdir(parents=True)
    destination = project_data / "observations.csv"
    destination.write_text("project-owned\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    binding = ensure_thread_workspace("conflict-thread", base)

    assert destination.read_text(encoding="utf-8") == "project-owned\n"
    manifest = json.loads(
        Path(binding.project_shared, "data_manifest.json").read_text()
    )
    assert manifest["files"] == []
    assert manifest["conflicts"][0]["path"] == "observations.csv"
    assert manifest["conflicts"][0]["reason"] == "destination_content_differs"
    run_manifest = json.loads(
        Path(binding.workspace, "input_manifest.json").read_text()
    )
    assert run_manifest["project_inputs"] == []


def test_removed_legacy_data_prunes_only_unchanged_managed_copy(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    base = tmp_path / "workspace"
    data = base / "data"
    data.mkdir(parents=True)
    removed_source = data / "removed.csv"
    removed_source.write_text("date,value\n2026-01,1\n", encoding="utf-8")
    edited_source = data / "edited.csv"
    edited_source.write_text("date,value\n2026-01,2\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    first = ensure_thread_workspace("first-thread", base)
    project_data = Path(first.project_shared) / "data"
    removed_copy = project_data / "removed.csv"
    edited_copy = project_data / "edited.csv"
    assert removed_copy.is_file()
    edited_copy.write_text("project-owned\n", encoding="utf-8")
    removed_source.unlink()
    edited_source.unlink()

    second = ensure_thread_workspace("second-thread", base)

    assert not removed_copy.exists()
    assert edited_copy.read_text(encoding="utf-8") == "project-owned\n"
    manifest = json.loads(Path(second.project_shared, "data_manifest.json").read_text())
    assert manifest["pruned"] == [{"path": "removed.csv", "reason": "source_removed"}]
    assert manifest["conflicts"] == [
        {
            "path": "edited.csv",
            "reason": "source_removed_destination_modified",
        }
    ]


def test_binding_filename_cannot_be_controlled_by_thread_id(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))
    path = binding_path("../../outside/\nthread", tmp_path / "base")
    assert path.parent == registry
    assert path.suffix == ".json"
    assert "outside" not in path.name


def test_same_thread_id_is_scoped_independently_per_base_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
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
    import jw.workspaces as workspace_module

    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
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
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
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
    import jw.middleware.task_workspace as task_workspace_module

    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    binding = ensure_thread_workspace("middleware-thread", base)
    graph_config = {
        "configurable": {
            "thread_id": "middleware-thread",
            "workspace_thread_id": "middleware-thread",
        }
    }
    # LangGraph's Runtime view can lag the context config during the first
    # before_agent pass; workspace routing must use the latter.
    runtime = SimpleNamespace(config={})
    monkeypatch.setattr(task_workspace_module, "get_config", lambda: graph_config)

    prepared = []
    TaskWorkspaceMiddleware(
        base,
        backend_factory=lambda active_runtime: prepared.append(active_runtime),
    ).before_agent({"messages": [HumanMessage(content="hydrated question")]}, runtime)

    task = json.loads(Path(binding.workspace, "task.json").read_text())
    assert task["research_question"] == "hydrated question"
    assert len(prepared) == 1
    assert prepared[0].config == graph_config


def test_scoped_backend_factory_avoids_filesystem_resolution_on_event_loop(
    tmp_path, monkeypatch
):
    """Static skill/memory routes are prepared before DeepAgents calls the factory."""
    from blockbuster import BlockBuster

    import jw.agent as agent_module

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    linked_workspace = tmp_path / "linked-workspace"
    try:
        linked_workspace.symlink_to(real_workspace, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    data_dir = tmp_path / "data"
    memories_dir = data_dir / "memories"
    global_skills_dir = data_dir / "skills"
    memories_dir.mkdir(parents=True)
    global_skills_dir.mkdir()

    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(agent_module._paths_mod, "WORKSPACE_ROOT", linked_workspace)
    monkeypatch.setattr(
        agent_module._paths_mod, "USER_SKILLS_DIR", linked_workspace / "skills"
    )
    monkeypatch.setattr(agent_module._paths_mod, "GLOBAL_SKILLS_DIR", global_skills_dir)
    monkeypatch.setattr(agent_module._paths_mod, "MEMORIES_DIR", memories_dir)
    monkeypatch.setattr(agent_module._paths_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(
        agent_module,
        "_ensure_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            sandbox_execute_timeout=30,
            dangerous_mode=False,
        ),
    )

    factory = agent_module._get_scoped_backend_factory()
    runtime = SimpleNamespace(config={})
    factory(runtime)

    async def resolve_from_async_node():
        blocker = BlockBuster()
        blocker.activate()
        try:
            return factory(runtime)
        finally:
            blocker.deactivate()

    backend = asyncio.run(resolve_from_async_node())
    assert backend.routes["/skills/"]._primary.cwd == real_workspace / "skills"
    assert backend.routes["/memories/"].cwd == memories_dir


def test_scoped_backend_factory_prewarms_persisted_thread_for_tools_resume(
    tmp_path, monkeypatch
):
    """A post-restart tools checkpoint must resolve without before_agent."""
    from blockbuster import BlockBuster

    import jw.agent as agent_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    memories_dir = data_dir / "memories"
    global_skills_dir = data_dir / "skills"
    memories_dir.mkdir(parents=True)
    global_skills_dir.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    binding = ensure_thread_workspace("resume-tools-thread", workspace)
    monkeypatch.setattr(agent_module._paths_mod, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(
        agent_module._paths_mod, "USER_SKILLS_DIR", workspace / "skills"
    )
    monkeypatch.setattr(agent_module._paths_mod, "GLOBAL_SKILLS_DIR", global_skills_dir)
    monkeypatch.setattr(agent_module._paths_mod, "MEMORIES_DIR", memories_dir)
    monkeypatch.setattr(agent_module._paths_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(
        agent_module,
        "_ensure_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            sandbox_execute_timeout=30,
            dangerous_mode=False,
        ),
    )

    factory = agent_module._get_scoped_backend_factory()
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": binding.thread_id,
                "workspace_thread_id": binding.thread_id,
            }
        }
    )

    async def resolve_from_resumed_tools_node():
        blocker = BlockBuster()
        blocker.activate()
        try:
            return factory(runtime)
        finally:
            blocker.deactivate()

    backend = asyncio.run(resolve_from_resumed_tools_node())
    assert backend.default.cwd == Path(binding.workspace)


def test_legacy_bootstrap_preserves_existing_threads_and_is_idempotent(
    tmp_path, monkeypatch
):
    base = tmp_path / "workspace"
    base.mkdir()
    registry = tmp_path / "registry"
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))
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
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))
    base = tmp_path / "base"
    base.mkdir()
    path = binding_path("expected", base)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"thread_id": "another"}), encoding="utf-8")
    assert read_binding("expected", base) is None


def test_async_child_run_config_inherits_parent_workspace_scope(monkeypatch):
    import langgraph.config

    import jw.agent as agent_module
    from jw.llm import patches

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


def test_async_child_run_config_surfaces_invalid_model_configuration(monkeypatch):
    import jw.agent as agent_module
    from jw.llm import patches

    monkeypatch.setattr(
        agent_module,
        "_ensure_config",
        lambda: (_ for _ in ()).throw(ValueError("invalid model configuration")),
    )

    with pytest.raises(ValueError, match="invalid model configuration"):
        patches._read_cfg_configurable()


async def test_api_checkpointer_primes_workspace_before_first_graph_node(
    tmp_path, monkeypatch
):
    import aiosqlite

    import jw.workspaces as workspace_module
    from jw.sessions import _ApiPruningCheckpointer

    base = tmp_path / "workspace"
    base.mkdir()
    monkeypatch.setenv("JW_WORKSPACE_DIR", str(base))
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
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

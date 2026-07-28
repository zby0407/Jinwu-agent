"""Initialize and hydrate the per-thread task workspace before agent work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.config import get_config
from langgraph.runtime import Runtime

from .. import paths
from ..research_integrity import transition_task
from ..workspaces import ensure_workspace_for_config, scope_thread_id


class TaskWorkspaceMiddleware(AgentMiddleware):
    """Ensure task metadata sees the first human request before tools run."""

    name = "task_workspace"

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        *,
        backend_factory: Callable[[Any], object] | None = None,
    ) -> None:
        self._base_workspace = Path(
            paths.WORKSPACE_ROOT if workspace_dir is None else workspace_dir
        ).expanduser()
        self._backend_factory = backend_factory

    def _ensure(self, state: AgentState[object], runtime: Runtime) -> None:
        try:
            current = get_config()
        except RuntimeError:
            return
        config = current if isinstance(current, dict) else None
        if not scope_thread_id(config):
            return
        binding = ensure_workspace_for_config(
            config,
            self._base_workspace,
            state=state,
        )
        transition_task(
            Path(binding.workspace),
            "running",
            summary="Research task is active; completion is receipt-gated.",
        )
        if self._backend_factory is not None:
            # DeepAgents' backend factory protocol is synchronous even from its
            # async SkillsMiddleware node.  Prewarm the task-scoped composite
            # here: ``abefore_agent`` runs this method in ``asyncio.to_thread``,
            # so FilesystemBackend path canonicalization cannot block ASGI.
            #
            # ``Runtime.config`` can lag LangGraph's context config during
            # ``before_agent`` (notably on the first API run).  The latter is
            # what initialized the binding above and is the canonical source
            # for thread/workspace routing, so hand that same config to the
            # synchronous factory.
            runtime_config = getattr(runtime, "config", None)
            factory_runtime = (
                runtime
                if isinstance(runtime_config, dict)
                and scope_thread_id(runtime_config) == scope_thread_id(config)
                else SimpleNamespace(config=config)
            )
            self._backend_factory(factory_runtime)

    def before_agent(
        self,
        state: AgentState[object],
        runtime: Runtime,
    ) -> dict[str, object] | None:
        self._ensure(state, runtime)
        return None

    async def abefore_agent(
        self,
        state: AgentState[object],
        runtime: Runtime,
    ) -> dict[str, object] | None:
        await asyncio.to_thread(self._ensure, state, runtime)
        return None


__all__ = ["TaskWorkspaceMiddleware"]

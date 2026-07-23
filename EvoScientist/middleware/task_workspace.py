"""Initialize and hydrate the per-thread task workspace before agent work."""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.config import get_config
from langgraph.runtime import Runtime

from .. import paths
from ..workspaces import ensure_workspace_for_config, scope_thread_id


class TaskWorkspaceMiddleware(AgentMiddleware):
    """Ensure task metadata sees the first human request before tools run."""

    name = "task_workspace"

    def __init__(self, workspace_dir: str | Path | None = None) -> None:
        self._base_workspace = Path(
            paths.WORKSPACE_ROOT if workspace_dir is None else workspace_dir
        ).expanduser()

    def _ensure(self, state: AgentState[object], runtime: Runtime) -> None:
        del runtime
        try:
            current = get_config()
        except RuntimeError:
            return
        config = current if isinstance(current, dict) else None
        if not scope_thread_id(config):
            return
        ensure_workspace_for_config(
            config,
            self._base_workspace,
            state=state,
        )

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

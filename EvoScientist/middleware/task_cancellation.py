"""Stop orphaned sub-agents after the parent task is cancelled."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..workspaces import workspace_root_from_config

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest


class TaskCancellationMiddleware(AgentMiddleware[Any, Any, Any]):
    """Fail every new child tool call once the task cancellation marker exists."""

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage | None:
        config = getattr(request.runtime, "config", None)
        try:
            cancelled = (
                workspace_root_from_config(config)
                / "receipts"
                / "task_cancelled.json"
            ).is_file()
        except (OSError, RuntimeError, ValueError):
            cancelled = False
        if not cancelled:
            return None
        name = str(request.tool_call.get("name") or "unknown_tool")
        return ToolMessage(
            content=(
                "[TASK CANCELLED] The parent task was stopped. Do not start, retry, "
                "or bind more work; return immediately with cancelled status."
            ),
            tool_call_id=str(request.tool_call.get("id") or "cancelled-tool-call"),
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest], Awaitable[ToolMessage | Command[Any]]
        ],
    ) -> ToolMessage | Command[Any]:
        blocked = await asyncio.to_thread(self._blocked, request)
        return blocked if blocked is not None else await handler(request)


__all__ = ["TaskCancellationMiddleware"]

"""Stop orphaned sub-agents after the parent task is cancelled."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..task_cancellation import task_is_cancelled

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest


class TaskCancellationMiddleware(AgentMiddleware[Any, Any, Any]):
    """Fail every new child tool call once the task cancellation marker exists."""

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage | None:
        config = getattr(request.runtime, "config", None)
        if not task_is_cancelled(config):
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

    @classmethod
    def _cancelled(cls, request: ToolCallRequest) -> Command[Any] | None:
        blocked = cls._blocked(request)
        if blocked is None:
            return None
        return Command(update={"messages": [blocked]}, goto="__end__")

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        cancelled = self._cancelled(request)
        return cancelled if cancelled is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        cancelled = await asyncio.to_thread(self._cancelled, request)
        return cancelled if cancelled is not None else await handler(request)


__all__ = ["TaskCancellationMiddleware"]

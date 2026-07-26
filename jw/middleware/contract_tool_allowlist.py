"""Tool-boundary middleware for capability-restricted specialist agents.

DeepAgents injects generic filesystem and shell tools through middleware even
when a sub-agent declares an explicit tool set. Prompt instructions are not a
security boundary, so a ``restrict_tools: true`` sub-agent passes its resolved
capability tools into this middleware. The boundary therefore follows the
registry and YAML declaration automatically instead of duplicating tool names
in Python.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest


def _tool_name(tool: BaseTool | dict[str, Any]) -> str | None:
    if isinstance(tool, dict):
        value = tool.get("name")
    else:
        value = getattr(tool, "name", None)
    return value if isinstance(value, str) else None


class ContractToolAllowlistMiddleware(AgentMiddleware[Any, Any, Any]):
    """Expose and execute only the named contract tools for a specialist.

    Filtering the model request is useful but is not by itself a security
    boundary: another middleware can inject tools later, and a model can emit a
    tool name that was not advertised.  The tool-call wrappers therefore apply
    the same allowlist immediately before execution and fail closed.
    """

    def __init__(self, allowed: frozenset[str]) -> None:
        if not allowed:
            raise ValueError("contract tool allowlist must not be empty")
        self.allowed = allowed

    def _filter(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        return request.override(
            tools=[tool for tool in request.tools if _tool_name(tool) in self.allowed]
        )

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._filter(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT] | AIMessage | ExtendedModelResponse[ResponseT]:
        return await handler(self._filter(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_tool_message(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_tool_message(request)
        return blocked if blocked is not None else await handler(request)

    def _blocked_tool_message(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name")
        if isinstance(name, str) and name in self.allowed:
            return None
        safe_name = name if isinstance(name, str) and name else "unknown_tool"
        return ToolMessage(
            content=(
                f"[CONTRACT TOOL BLOCKED] '{safe_name}' is outside this "
                "specialist's bounded tool set. Continue with the advertised "
                "tools, or return an honest partial/blocked result; do not "
                "create a manual substitute for an execution artifact."
            ),
            tool_call_id=str(request.tool_call.get("id") or "blocked-tool-call"),
            name=safe_name,
            status="error",
        )


__all__ = [
    "ContractToolAllowlistMiddleware",
]

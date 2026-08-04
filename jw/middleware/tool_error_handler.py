"""Middleware that catches tool execution exceptions and converts them to error ToolMessages.

Without this, an MCP tool (or any tool) that raises an exception at runtime
crashes the entire agent loop because LangGraph's default ToolNode error handler
only catches argument-validation errors (ToolInvocationError), not execution
errors.  The full traceback stays in server logs; persisted conversation state
receives only a bounded, hash-addressable error capsule.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
import traceback
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

# GraphInterrupt must propagate — never catch it as a tool error.
try:
    from langgraph.errors import GraphInterrupt as _GraphInterrupt
except ImportError:  # older langgraph versions
    _GraphInterrupt = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

logger = logging.getLogger(__name__)


class ToolErrorHandlerMiddleware(AgentMiddleware):
    """Catch tool execution exceptions and return them as error ToolMessages."""

    name = "tool_error_handler"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except Exception as exc:
            if _GraphInterrupt is not None and isinstance(exc, _GraphInterrupt):
                raise
            return _build_error_message(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except Exception as exc:
            if _GraphInterrupt is not None and isinstance(exc, _GraphInterrupt):
                raise
            return _build_error_message(request)


_SENSITIVE_FRAGMENT = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_-]?key\s*[:=]\s*|"
    r"token\s*[:=]\s*|password\s*[:=]\s*)[^\s,;]+"
)


def _safe_exception_summary(exc: BaseException | None) -> str:
    if exc is None:
        return "Exception: tool execution failed"
    rendered = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    rendered = _SENSITIVE_FRAGMENT.sub(r"\1[REDACTED]", rendered)
    return rendered[:600] or f"{type(exc).__name__}: tool execution failed"


def _build_error_message(request: ToolCallRequest) -> ToolMessage:
    tb = traceback.format_exc()
    tool_name = request.tool_call.get("name", "unknown_tool")
    logger.error("Tool %r raised an exception:\n%s", tool_name, tb)
    exc = sys.exc_info()[1]
    summary = _safe_exception_summary(exc)
    fingerprint = hashlib.sha256(f"{tool_name}\0{summary}".encode()).hexdigest()
    content = (
        "[TOOL ERROR CAPSULE]\n"
        f"fingerprint={fingerprint}\n"
        f"tool={tool_name}\n"
        f"error={summary}\n"
        "retry_policy=one identical graph-level retry is allowed; after two "
        "identical failures stop"
    )
    return ToolMessage(
        content=content,
        tool_call_id=request.tool_call["id"],
        name=tool_name,
        status="error",
    )

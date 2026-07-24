"""Reject source files that embed virtual workspace paths as host absolutes."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest


_CODE_SUFFIXES = (".py", ".ipynb", ".js", ".mjs", ".ts", ".r", ".jl", ".m")
_EMBEDDED_VIRTUAL_PATH = re.compile(
    r"(?P<quote>['\"])/(?:work|inputs|outputs|receipts|skills|memories)(?:/|['\"])",
    re.IGNORECASE,
)


class VirtualPathCodeGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Make the virtual-path boundary fail before a child process is launched."""

    @staticmethod
    def _blocked(request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name") or "")
        if name not in {"write_file", "edit_file"}:
            return None
        args = request.tool_call.get("args", {})
        if not isinstance(args, Mapping):
            return None
        path = str(args.get("file_path") or args.get("path") or "").casefold()
        if not path.endswith(_CODE_SUFFIXES):
            return None
        fragments = (
            [args.get("content")] if name == "write_file" else [args.get("new_string")]
        )
        if not any(
            isinstance(fragment, str) and _EMBEDDED_VIRTUAL_PATH.search(fragment)
            for fragment in fragments
        ):
            return None
        return ToolMessage(
            content=(
                "[VIRTUAL PATH BLOCKED] Source code cannot embed /work, /inputs, "
                "/outputs, /receipts, /skills, or /memories as literal absolute "
                "paths. Those names are translated only in shell arguments. Use a "
                "path relative to Path(__file__).resolve().parent, context-provided "
                "paths, or pass the virtual path as a command-line argument."
            ),
            tool_call_id=str(
                request.tool_call.get("id") or "virtual-path-blocked-tool-call"
            ),
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
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked(request)
        return blocked if blocked is not None else await handler(request)


__all__ = ["VirtualPathCodeGuardMiddleware"]

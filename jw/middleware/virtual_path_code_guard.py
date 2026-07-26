"""Reject source files that embed virtual workspace paths as host absolutes."""

from __future__ import annotations

import ast
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
    r"(?P<quote>['\"])/(?:work|inputs|outputs|receipts|skills|memories|project)"
    r"(?:/|['\"])",
    re.IGNORECASE,
)
_DATA_READ_MARKER = re.compile(
    r"\b(?:csv\.reader|open\s*\(|read_(?:csv|excel|json|parquet|table)|"
    r"loadtxt|genfromtxt)\b",
    re.IGNORECASE,
)
_DERIVED_DATA_NAME = re.compile(
    r"(?:boundar|minima|maxima|cycle_(?:start|end|peak|param)|"
    r"measurements?|observations?)",
    re.IGNORECASE,
)


def _assignment_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _numeric_literal_count(node: ast.AST) -> int:
    return sum(
        isinstance(child, ast.Constant)
        and isinstance(child.value, (int, float))
        and not isinstance(child.value, bool)
        for child in ast.walk(node)
    )


def _embedded_derived_data_name(content: str) -> str | None:
    """Find a data-reading Python script that embeds a domain data table."""

    if not _DATA_READ_MARKER.search(content):
        return None
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
        else:
            continue
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            continue
        if _numeric_literal_count(value) < 5:
            continue
        for target in targets:
            name = _assignment_name(target)
            if name and _DERIVED_DATA_NAME.search(name):
                return name
    return None


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
        content_fragments = [fragment for fragment in fragments if isinstance(fragment, str)]
        blocked_reason: str | None = None
        if path == "/inputs" or path.startswith("/inputs/"):
            blocked_reason = (
                "Generated code cannot be stored under /inputs/. That directory "
                "is reserved for source data."
            )
        elif any(
            _EMBEDDED_VIRTUAL_PATH.search(fragment)
            for fragment in content_fragments
        ):
            blocked_reason = (
                "Source code cannot embed /work, /inputs, /outputs, /receipts, "
                "/skills, /memories, or /project as literal absolute paths."
            )
        else:
            embedded_name = next(
                (
                    candidate
                    for fragment in content_fragments
                    if (candidate := _embedded_derived_data_name(fragment)) is not None
                ),
                None,
            )
            if embedded_name is not None:
                blocked_reason = (
                    "Generated analysis code embeds a multi-value scientific "
                    f"measurement or boundary table in `{embedded_name}`. Derive "
                    "those values from the declared primary input with a "
                    "reproducible rule, or read them from a cited metadata file."
                )
        if blocked_reason is None:
            return None
        return ToolMessage(
            content=(
                f"[CODE CONTRACT BLOCKED] {blocked_reason} "
                "Write executable source under /work/ (never /inputs/ or a "
                "host temporary directory), accept data/output paths through "
                "sys.argv or equivalent, then invoke it with a shell command such "
                "as `python /work/analyze.py /inputs/data.csv /outputs/result.csv`. "
                "Virtual paths are translated in that shell command."
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

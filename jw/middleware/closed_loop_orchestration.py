"""Safety boundaries for explicitly declared end-to-end research tasks.

The middleware deliberately does *not* prescribe a planner → hypothesis →
experiment sequence. Research stages may be skipped, revisited, or returned as
partial work. The remaining deterministic checks protect real execution inputs
and prevent a receipt from claiming an artifact that does not exist.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..workspaces import workspace_root_from_config

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

_SPECIALISTS = ("solar-planner", "solar-hypothesis", "solar-experiment")
_CLOSED_LOOP_MARKERS = (
    "完整研究",
    "完整整合",
    "科研闭环",
    "形成论文",
    "完整报告",
    "complete research",
    "end-to-end research",
    "full research",
)
_CLOSED_LOOP_STAGES = (
    ("数据", "data", "文献", "literature"),
    ("计划", "规划", "plan"),
    ("假设", "解释", "hypothesis"),
    ("实验", "验证", "experiment"),
    ("报告", "论文", "report", "paper"),
)
_DATA_DEPENDENT = re.compile(
    r"(?:现有|已有|workspace|本地|provided|existing).{0,20}(?:数据|文件|data|file)|"
    r"(?:数据|文件|data|file).{0,20}(?:分析|读取|使用|检验|预测|analy|read|use|test|predict)|"
    r"\.(?:csv|tsv|parquet|json|fits?|nc|h5)\b",
    re.IGNORECASE,
)


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return " ".join(
            str(part.get("text", ""))
            for part in value
            if isinstance(part, Mapping) and part.get("text")
        )
    return ""


def _todo_text(todos: object) -> str:
    if not isinstance(todos, Sequence) or isinstance(todos, (str, bytes)):
        return ""
    return " ".join(
        str(todo.get("content", "")) for todo in todos if isinstance(todo, Mapping)
    )


def _state_text(state: object) -> str:
    """Return user/todo text used only for deterministic intent gates."""

    if not isinstance(state, Mapping):
        return ""
    chunks = [_todo_text(state.get("todos"))]
    messages = state.get("messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            role = str(_field(message, "type", _field(message, "role", "")))
            if role in {"human", "user"}:
                chunks.append(_content_text(_field(message, "content", "")))
    return " ".join(chunks)


def _natural_closed_loop_intent(text: str) -> bool:
    folded = text.casefold()
    if not any(marker in folded for marker in _CLOSED_LOOP_MARKERS):
        return False
    covered = sum(
        any(marker in folded for marker in alternatives)
        for alternatives in _CLOSED_LOOP_STAGES
    )
    return covered >= 4


def _declares_full_closed_loop(state: object) -> bool:
    if not isinstance(state, Mapping):
        return False
    route = state.get("research_route")
    if isinstance(route, Mapping) and route.get("mode") == "full_research":
        return True
    chunks = [_todo_text(state.get("todos"))]
    messages = state.get("messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            role = str(_field(message, "type", _field(message, "role", "")))
            if role in {"human", "user"}:
                chunks.append(_content_text(_field(message, "content", "")))
            calls = _field(message, "tool_calls", [])
            if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
                continue
            for call in calls:
                if _field(call, "name", "") != "write_todos":
                    continue
                args = _field(call, "args", {})
                if isinstance(args, Mapping):
                    chunks.append(_todo_text(args.get("todos")))
    declared = " ".join(chunks).casefold()
    return all(
        name in declared for name in _SPECIALISTS
    ) or _natural_closed_loop_intent(_state_text(state))


def _has_staged_input(workspace_root: Path, description: str = "") -> bool:
    if re.search(r"(?:^|[\s'\"`])(?:/?inputs/|runs/[^\s]+/public/)", description):
        return True
    root = workspace_root / "inputs"
    try:
        return root.is_dir() and any(path.is_file() for path in root.rglob("*"))
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_valid(
    paths: Sequence[Path], predicate: Callable[[dict[str, Any], Path], bool]
) -> Path | None:
    def modified_at(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            # A contract directory may disappear between globbing and sorting.
            # Treat that race as an invalid candidate instead of failing the
            # orchestration guard itself.
            return float("-inf")

    for path in sorted(paths, key=modified_at, reverse=True):
        payload = _read_json(path)
        if payload is not None and predicate(payload, path):
            return path
    return None


def closed_loop_receipts(workspace_root: Path) -> dict[str, Path | None]:
    """Return verified task-local artifacts for the three contract stages."""

    planner = _latest_valid(
        list((workspace_root / "planner" / "runs").glob("*/research_plan.json")),
        lambda payload, _path: payload.get("status") == "frozen",
    )
    hypothesis = _latest_valid(
        list(
            (workspace_root / "hypothesis" / "runs").glob("*/hypothesis_portfolio.json")
        ),
        lambda payload, _path: payload.get("status") == "frozen",
    )

    def finalized(payload: dict[str, Any], path: Path) -> bool:
        return (
            payload.get("phase") == "report_finalized"
            and isinstance(payload.get("verified_record_sha256"), str)
            and bool(payload.get("verified_record_sha256"))
            and isinstance(payload.get("report_sha256"), str)
            and bool(payload.get("report_sha256"))
            and (path.parent / "entry_result.json").is_file()
        )

    experiment = _latest_valid(
        list((workspace_root / "experiment" / "runs").glob("*/state.json")),
        finalized,
    )
    return {
        "solar-planner": planner,
        "solar-hypothesis": hypothesis,
        "solar-experiment": experiment,
    }


def _claims_success(value: object) -> bool:
    text = str(value or "").casefold()
    negative = ("incomplete", "failed", "failure", "blocked", "error")
    positive = ("success", "frozen", "finalized", "completed")
    return any(word in text for word in positive) and not any(
        word in text for word in negative
    )


class ClosedLoopOrchestrationGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Protect execution boundaries without forcing a fixed research route."""

    def _blocked(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        name = str(request.tool_call.get("name") or "unknown_tool")
        return ToolMessage(
            content=f"[CLOSED LOOP BLOCKED] {reason}",
            tool_call_id=str(
                request.tool_call.get("id") or "closed-loop-blocked-tool-call"
            ),
            name=name,
            status="error",
        )

    def _preflight(self, request: ToolCallRequest) -> ToolMessage | None:
        if not _declares_full_closed_loop(request.state):
            return None
        config = getattr(request.runtime, "config", None)
        workspace_root = workspace_root_from_config(config)
        receipts = closed_loop_receipts(workspace_root)
        state_text = _state_text(request.state)
        name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args", {})
        args = args if isinstance(args, Mapping) else {}

        if name == "task":
            specialist = str(args.get("subagent_type") or "")
            if (
                specialist == "solar-experiment"
                and _DATA_DEPENDENT.search(state_text)
                and not _has_staged_input(
                    workspace_root, str(args.get("description") or "")
                )
            ):
                return self._blocked(
                    request,
                    "the experiment is data-dependent but no immutable input is staged. "
                    "Copy the exact source file under /inputs/ (or reference a completed "
                    "runs/<id>/public/ artifact), then include that path in the "
                    "solar-experiment task description.",
                )

        if name == "write_file":
            file_path = str(args.get("file_path") or args.get("path") or "")
            if file_path.rstrip("/").endswith("receipts/closed_loop_receipts.json"):
                try:
                    summary = json.loads(str(args.get("content") or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return self._blocked(
                        request,
                        "closed-loop receipt summary must be valid JSON.",
                    )
                statuses = (
                    summary.get("contract_status", {})
                    if isinstance(summary, Mapping)
                    else {}
                )
                if isinstance(statuses, Mapping):
                    for specialist in _SPECIALISTS:
                        row = statuses.get(specialist)
                        if not isinstance(row, Mapping):
                            continue
                        if (
                            _claims_success(row.get("status"))
                            and receipts[specialist] is None
                        ):
                            return self._blocked(
                                request,
                                f"receipt summary falsely claims {specialist} success; "
                                "no verified task-local artifact exists.",
                            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._preflight(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        # Artifact discovery reads task-local JSON and scans three small run
        # directories.  LangGraph dev rejects those blocking filesystem calls
        # on its event loop, so keep the async middleware path genuinely
        # non-blocking while sharing the exact same fail-closed logic.
        blocked = await asyncio.to_thread(self._preflight, request)
        return blocked if blocked is not None else await handler(request)


__all__ = [
    "ClosedLoopOrchestrationGuardMiddleware",
    "closed_loop_receipts",
]

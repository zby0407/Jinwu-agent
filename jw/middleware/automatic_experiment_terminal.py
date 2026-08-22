"""End a solar-experiment sub-agent after a deterministic terminal result."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest


def _terminal_payload(
    request: ToolCallRequest, result: object
) -> dict[str, Any] | None:
    name = str(request.tool_call.get("name") or "")
    if not name.startswith("automatic_experiment_") or not isinstance(
        result, ToolMessage
    ):
        return None
    content = result.content
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("must_stop") is not True:
        return None
    return payload


def _terminal_summary(payload: Mapping[str, Any]) -> str:
    projection = {
        key: payload[key]
        for key in (
            "run_id",
            "status",
            "phase",
            "outcome",
            "record_path",
            "report_path",
            "audit_path",
        )
        if payload.get(key) is not None
    }
    return (
        "The existing experiment run has reached its deterministic terminal "
        "state. No additional experiment attempt was executed. Existing result "
        "files and outcome:\n"
        + json.dumps(projection, ensure_ascii=False, sort_keys=True)
    )


def _end(result: ToolMessage, payload: Mapping[str, Any]) -> Command[Any]:
    return Command(
        update={
            "messages": [
                result,
                AIMessage(content=_terminal_summary(payload)),
            ]
        },
        goto="__end__",
    )


class AutomaticExperimentTerminalGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Turn ``must_stop=true`` tool results into a terminal graph transition."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        payload = _terminal_payload(request, result)
        return _end(result, payload) if payload is not None else result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        payload = _terminal_payload(request, result)
        return _end(result, payload) if payload is not None else result


__all__ = ["AutomaticExperimentTerminalGuardMiddleware"]

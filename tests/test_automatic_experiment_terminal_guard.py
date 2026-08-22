from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from jw.middleware.automatic_experiment_terminal import (
    AutomaticExperimentTerminalGuardMiddleware,
)


@dataclass
class _Request:
    tool_call: dict[str, object]


def test_terminal_inspection_ends_the_experiment_subagent() -> None:
    middleware = AutomaticExperimentTerminalGuardMiddleware()
    request = _Request(
        tool_call={
            "name": "automatic_experiment_inspect_inputs",
            "id": "inspect-terminal",
            "args": {"run_id": "run-1"},
        }
    )
    terminal = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "status": "terminal",
                "run_id": "run-1",
                "phase": "report_finalized",
                "outcome": "budget_stopped",
                "must_stop": True,
                "record_path": "record.json",
                "report_path": "report.md",
            }
        ),
        tool_call_id="inspect-terminal",
        name="automatic_experiment_inspect_inputs",
    )

    async def invoke() -> ToolMessage | Command:
        async def handler(_request: _Request) -> ToolMessage:
            return terminal

        return await middleware.awrap_tool_call(request, handler)

    result = asyncio.run(invoke())

    assert isinstance(result, Command)
    assert result.goto == "__end__"
    assert isinstance(result.update, dict)
    messages = result.update["messages"]
    assert messages[0] is terminal
    assert isinstance(messages[1], AIMessage)
    assert "budget_stopped" in messages[1].text
    assert "record.json" in messages[1].text
    assert "report.md" in messages[1].text


def test_nonterminal_automatic_result_continues_normally() -> None:
    middleware = AutomaticExperimentTerminalGuardMiddleware()
    request = _Request(
        tool_call={
            "name": "automatic_experiment_inspect_inputs",
            "id": "inspect-ready",
            "args": {"run_id": "run-1"},
        }
    )
    ready = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "status": "already_snapshotted",
                "run_id": "run-1",
                "must_stop": False,
            }
        ),
        tool_call_id="inspect-ready",
        name="automatic_experiment_inspect_inputs",
    )

    result = middleware.wrap_tool_call(request, lambda _request: ready)

    assert result is ready

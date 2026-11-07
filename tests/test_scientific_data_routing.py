import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from jw.middleware.research_router import (
    _ROUTE_SCHEMA,
    ResearchRouterMiddleware,
    _validated_route,
)


def test_router_accepts_data_integration_specialist():
    value = {
        "mode": "verified_analysis",
        "source_mode": "external",
        "needs_computation": True,
        "task_intent": "data_integration",
        "required_specialist": "scientific-data",
        "reason": "Acquire and merge data",
    }
    actual = _validated_route(value, fallback_text="查找SILSO与NOAA公开数据并按月整合")
    assert actual["required_specialist"] == "scientific-data"
    assert actual["task_intent"] == "data_integration"
    assert (
        "scientific-data" in _ROUTE_SCHEMA["properties"]["required_specialist"]["enum"]
    )


def test_integration_route_does_not_require_bound_solar_inputs(monkeypatch):
    monkeypatch.setattr("jw.middleware.research_router.disable_thinking", lambda x: x)
    middleware = ResearchRouterMiddleware(model=MagicMock())

    def task(description: str, subagent_type: str):
        return description

    tool = StructuredTool.from_function(task, description="Delegate")
    request = ModelRequest(
        model=MagicMock(),
        tools=[tool],
        messages=[HumanMessage("查找并整合两个公开数据源")],
        state={
            "research_route": {
                "mode": "verified_analysis",
                "source_mode": "external",
                "needs_computation": True,
                "task_intent": "data_integration",
                "required_specialist": "scientific-data",
            }
        },
    )
    prepared = middleware._prepare_request(request)
    assert prepared.tool_choice == "task"
    text = str(prepared.system_message.content)
    assert "subagent_type='scientific-data'" in text
    assert "solar_data_open_context" not in text
    handler = MagicMock()
    dispatched = middleware.wrap_model_call(request, handler)
    handler.assert_not_called()
    dispatch = dispatched.result[0].tool_calls[0]
    assert dispatch["args"]["subagent_type"] == "scientific-data"
    assert dispatch["args"]["description"] == "查找并整合两个公开数据源"
    async_handler = AsyncMock()
    async_dispatch = asyncio.run(middleware.awrap_model_call(request, async_handler))
    async_handler.assert_not_called()
    assert async_dispatch.result[0].tool_calls[0]["args"] == dispatch["args"]

    completed = request.override(
        messages=[
            *request.messages,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "scientific-data",
                            "description": "integrate",
                        },
                        "id": "integration-1",
                    }
                ],
            ),
            ToolMessage(
                content='{"status":"acquisition_failed"}', tool_call_id="integration-1"
            ),
        ]
    )
    final = middleware._prepare_request(completed)
    assert final.tools == []
    assert final.tool_choice is None
    assert "If it failed" in str(final.system_message.content)

    def write_file(file_path: str, content: str):
        return file_path

    def execute(command: str):
        return command

    report_tool = StructuredTool.from_function(
        write_file, description="Save a requested report"
    )
    execution_tool = StructuredTool.from_function(execute, description="Run code")
    report_request = completed.override(tools=[tool, report_tool, execution_tool])
    report_prepared = middleware._prepare_request(report_request)
    assert [entry.name for entry in report_prepared.tools] == ["write_file", "execute"]
    assert report_prepared.tool_choice is None
    assert "preserving prior" in str(report_prepared.system_message.content)
    handler.return_value = ModelResponse(result=[AIMessage(content="获取失败")])
    middleware.wrap_model_call(completed, handler)
    handler.assert_called_once()

    receipt = {"kind": "solar_monthly_integration", "summary": "已完成180个月整合。"}
    completed.messages[-1].content = json.dumps(receipt)
    handler.reset_mock()
    final_response = middleware.wrap_model_call(completed, handler)
    handler.assert_not_called()
    assert final_response.result[0].content == receipt["summary"]


@pytest.mark.parametrize("is_qwen", [True, False])
def test_delivery_tools_survive_router_selector_and_model_compatibility(
    monkeypatch, is_qwen
):
    from langchain_core.messages import SystemMessage
    from jw.middleware.qwen_compat import QwenToolCompatibilityMiddleware
    from jw.middleware.tool_selector import _ConditionalToolSelectorMiddleware

    monkeypatch.setattr(
        "jw.middleware.research_router.disable_thinking", lambda model: model
    )

    def task(description: str, subagent_type: str):
        return description

    def write_file(file_path: str, content: str):
        return file_path

    def execute(command: str):
        return command

    def read_file(file_path: str):
        return file_path

    tools = [
        StructuredTool.from_function(fn, description=fn.__name__)
        for fn in (task, write_file, execute, read_file)
    ]
    user = HumanMessage("整理现有数据并生成ZIP", id="delivery-user")
    messages = [
        user,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "scientific-data"},
                    "id": "delegated",
                }
            ],
        ),
        ToolMessage("真实报告", tool_call_id="delegated"),
    ]
    route = {
        "mode": "verified_analysis",
        "source_mode": "external",
        "task_intent": "data_integration",
        "required_specialist": "scientific-data",
    }
    request = ModelRequest(
        model=MagicMock(),
        tools=tools,
        messages=messages,
        system_message=SystemMessage("base"),
        state={"messages": messages, "research_route": route},
    )
    router = ResearchRouterMiddleware(model=MagicMock())
    prepared = router._prepare_request(request)
    selector = _ConditionalToolSelectorMiddleware(lambda names: None, threshold=0)
    selected = selector._retain_required_tools(prepared, prepared.override(tools=[]))
    compatibility = QwenToolCompatibilityMiddleware()
    monkeypatch.setattr(compatibility, "_active_model_is_qwen", lambda request: is_qwen)
    final = compatibility._prepare_request(selected)
    assert {tool.name for tool in final.tools} >= {"write_file", "read_file", "execute"}
    assert "task" not in {tool.name for tool in final.tools}

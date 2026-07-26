from __future__ import annotations

from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from jw.middleware.research_router import ResearchRouterMiddleware


def _tool(name: str):
    def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        run,
        name=name,
        description=f"{name} test tool",
    )


def _middleware(monkeypatch) -> ResearchRouterMiddleware:
    model = MagicMock()
    monkeypatch.setattr(
        "jw.middleware.research_router.disable_thinking",
        lambda value: value,
    )
    return ResearchRouterMiddleware(model=model)


def _request(
    *,
    route: dict[str, object],
    messages=None,
    tools=None,
) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=messages or [HumanMessage("test", id="human-1")],
        tools=tools or [],
        state={"research_route": route},
    )


def _prepared(middleware: ResearchRouterMiddleware, request: ModelRequest):
    captured: list[ModelRequest] = []

    def handler(inner: ModelRequest):
        captured.append(inner)
        return MagicMock()

    middleware.wrap_model_call(request, handler)
    return captured[0]


def _route(
    mode: str,
    *,
    source_mode: str = "none",
    needs_computation: bool = False,
) -> dict[str, object]:
    return {
        "mode": mode,
        "source_mode": source_mode,
        "needs_computation": needs_computation,
        "reason": "test",
    }


def test_router_runs_once_per_human_turn_and_persists_decision(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route("verified_analysis", source_mode="local")
    middleware._model.with_structured_output.return_value = structured
    state = {"messages": [HumanMessage("读取本地数据", id="turn-1")]}

    update = middleware.before_agent(state, runtime=None)

    assert update == {
        "research_route": _route("verified_analysis", source_mode="local"),
        "research_route_turn": "turn-1",
    }
    persisted = {**state, **update}
    assert middleware.before_agent(persisted, runtime=None) is None
    structured.invoke.assert_called_once()


def test_verified_local_route_forces_discovery_then_read_then_compute(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="local",
        needs_computation=True,
    )
    tools = [_tool("ls"), _tool("read_file"), _tool("execute")]
    human = HumanMessage("读取并计算", id="turn-1")

    first = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=tools),
    )
    assert first.tool_choice is None
    assert [tool.name for tool in first.tools] == ["ls"]

    after_ls = [
        human,
        AIMessage(
            "",
            tool_calls=[
                {"name": "ls", "args": {"path": "/project"}, "id": "ls-1"}
            ],
        ),
        ToolMessage("a.csv", tool_call_id="ls-1", name="ls"),
    ]
    second = _prepared(
        middleware,
        _request(route=route, messages=after_ls, tools=tools),
    )
    assert second.tool_choice is None
    assert [tool.name for tool in second.tools] == ["read_file"]

    after_read = [
        *after_ls,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/project/a.csv"},
                    "id": "read-1",
                }
            ],
        ),
        ToolMessage("x,y", tool_call_id="read-1", name="read_file"),
    ]
    third = _prepared(
        middleware,
        _request(route=route, messages=after_read, tools=tools),
    )
    assert third.tool_choice is None
    assert [tool.name for tool in third.tools] == ["execute"]


def test_full_research_route_advances_explicit_specialist_graph(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    task = _tool("task")
    human = HumanMessage("完成端到端研究", id="turn-1")

    first = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[task]),
    )
    assert first.tool_choice is None
    assert [tool.name for tool in first.tools] == ["task"]
    assert "mandatory next graph node is solar-planner" in first.system_message.text

    after_planner = [
        human,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "solar-planner"},
                    "id": "planner-1",
                }
            ],
        ),
        ToolMessage("frozen", tool_call_id="planner-1", name="task"),
    ]
    second = _prepared(
        middleware,
        _request(route=route, messages=after_planner, tools=[task]),
    )
    assert second.tool_choice is None
    assert [tool.name for tool in second.tools] == ["task"]
    assert "mandatory next graph node is solar-hypothesis" in second.system_message.text


def test_failed_required_stage_stops_after_two_attempts(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    human = HumanMessage("完成端到端研究", id="turn-1")
    messages = [human]
    for index in (1, 2):
        call_id = f"planner-{index}"
        messages.extend(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-planner"},
                            "id": call_id,
                        }
                    ],
                ),
                ToolMessage(
                    "failed",
                    tool_call_id=call_id,
                    name="task",
                    status="error",
                ),
            ]
        )

    prepared = _prepared(
        middleware,
        _request(route=route, messages=messages, tools=[_tool("task")]),
    )

    assert prepared.tool_choice is None
    assert "failed twice. Do not loop" in prepared.system_message.text


def test_fast_answer_does_not_force_tool_use(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)

    prepared = _prepared(
        middleware,
        _request(
            route=_route("fast_answer"),
            tools=[_tool("read_file"), _tool("execute")],
        ),
    )

    assert prepared.tool_choice is None
    assert "direct-answer path" in prepared.system_message.text

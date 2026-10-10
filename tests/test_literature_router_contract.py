"""Regression for literature requests containing registered computation inputs."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from jw.middleware.research_router import (
    ResearchRouterMiddleware,
    _fallback_route,
    _passthrough_accepted_bounded_stage,
)
from jw.tools.literature_artifacts import LITERATURE_ARTIFACT_TOOLS


def _prompt():
    return (
        Path(__file__).parent / "fixtures" / "literature_scope_request.md"
    ).read_text(encoding="utf-8")


def _middleware(monkeypatch):
    monkeypatch.setattr(
        "jw.middleware.research_router.disable_thinking", lambda model: model
    )
    model = MagicMock()
    model.with_structured_output.side_effect = AssertionError(
        "No classification API needed for explicit scope"
    )
    return ResearchRouterMiddleware(model=model), model


def _assert_literature(route):
    assert route["mode"] == "verified_analysis"
    assert route["literature_only"] is True
    assert route["needs_computation"] is False
    assert route["required_specialist"] == "none"
    assert route.get("required_analysis_protocol", "none") == "none"
    assert not route.get("preliminary_stages")


def test_complete_user_request_routes_without_remote_classifier(monkeypatch):
    middleware, model = _middleware(monkeypatch)
    state = {"messages": [HumanMessage(_prompt(), id="literature-turn")]}
    update = middleware.before_agent(state, runtime=None)
    _assert_literature(update["research_route"])
    model.with_structured_output.assert_not_called()
    assert middleware.before_agent({**state, **update}, runtime=None) is None


@pytest.mark.asyncio
async def test_complete_user_request_routes_without_async_classifier(monkeypatch):
    middleware, model = _middleware(monkeypatch)
    update = await middleware.abefore_agent(
        {"messages": [HumanMessage(_prompt(), id="literature-turn")]}, runtime=None
    )
    _assert_literature(update["research_route"])
    model.with_structured_output.assert_not_called()


def test_complete_request_fallback_preserves_scope():
    _assert_literature(_fallback_route(_prompt()))


def test_real_literature_tools_remain_available_without_stage_tools(monkeypatch):
    middleware, _ = _middleware(monkeypatch)
    state = {"messages": [HumanMessage(_prompt(), id="literature-turn")]}
    state.update(middleware.before_agent(state, runtime=None))
    blocked_tools = [
        StructuredTool.from_function(lambda: "unused", name=name, description=name)
        for name in ("task", "execute", "reproduce_silso_cycle_extrema")
    ]
    request = ModelRequest(
        model=MagicMock(),
        messages=state["messages"],
        tools=[*LITERATURE_ARTIFACT_TOOLS, *blocked_tools],
        state=state,
    )
    response = ModelResponse(
        result=[
            AIMessage(
                content="INPUT_BINDING_BLOCKED: a required input could not be read"
            )
        ]
    )
    captured = []

    def handler(prepared):
        captured.append(prepared)
        return response

    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _: pytest.fail("Literature must not touch bounded stage release"),
    )
    assert middleware.wrap_model_call(request, handler) is response
    assert {tool.name for tool in captured[0].tools} == {
        "literature_sources_list",
        "literature_source_read",
        "literature_report_save",
    }
    assert "literature-only" in str(captured[0].system_message.content)


@pytest.mark.asyncio
async def test_async_literature_response_cannot_be_overwritten_by_old_stage(
    monkeypatch,
):
    middleware, _ = _middleware(monkeypatch)
    state = {"messages": [HumanMessage(_prompt(), id="literature-turn")]}
    state.update(await middleware.abefore_agent(state, runtime=None))
    request = ModelRequest(
        model=MagicMock(),
        messages=state["messages"],
        tools=LITERATURE_ARTIFACT_TOOLS,
        state=state,
    )
    response = ModelResponse(
        result=[AIMessage(content="INPUT_BINDING_BLOCKED: unavailable evidence")]
    )

    async def handler(_):
        return response

    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _: pytest.fail("Must not read previous accepted stage"),
    )
    assert await middleware.awrap_model_call(request, handler) is response


def test_stale_data_route_never_renders_accepted_table_for_literature(monkeypatch):
    middleware, _ = _middleware(monkeypatch)
    route = {
        "mode": "verified_analysis",
        "required_specialist": "solar-data",
        "task_intent": "data_preparation",
        "required_analysis_protocol": "silso_cycle_reproduction_v1",
    }
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(_prompt())],
        tools=LITERATURE_ARTIFACT_TOOLS,
        state={"research_route": route},
    )
    response = ModelResponse(
        result=[AIMessage(content="INPUT_BINDING_BLOCKED: incompatible workflow")]
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _: pytest.fail("Stale stage must not be used"),
    )
    prepared = middleware._prepare_request(request)
    assert prepared.tools == []
    assert "ROUTING BLOCKER" in str(prepared.system_message.content)
    assert _passthrough_accepted_bounded_stage(request, response) is response

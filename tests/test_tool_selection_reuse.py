"""Exercise selection reuse through real multi-step LangGraph executions."""

import asyncio

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import LLMToolSelectorMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from jw.middleware.tool_selector import _ConditionalToolSelectorMiddleware


@tool
def echo(value: str) -> str:
    """Return a supplied value."""
    return value


class LocalModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_graph():
    calls = []

    class SelectionModel(LocalModel):
        def with_structured_output(self, schema, **kwargs):
            def select(messages):
                calls.append(messages[-1].content)
                return {"tools": ["echo"]}

            return RunnableLambda(select)

    selector_model = SelectionModel(responses=[AIMessage(content="unused")])
    middleware = _ConditionalToolSelectorMiddleware(
        selector_factory=lambda names: LLMToolSelectorMiddleware(
            model=selector_model, always_include=names
        ),
        threshold=0,
        track_stream_selection=False,
    )
    model = LocalModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"value": "one"}, "id": "one"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"value": "two"}, "id": "two"}],
            ),
            AIMessage(content="complete"),
        ]
    )
    return create_agent(model, tools=[echo], middleware=[middleware]), calls


@pytest.mark.parametrize("use_async", [False, True])
def test_three_model_steps_select_once_per_user_turn_and_thread(use_async):
    graph, calls = make_graph()

    def invoke(thread, message):
        state = {"messages": [message]}
        config = {"configurable": {"thread_id": thread}}
        return (
            asyncio.run(graph.ainvoke(state, config))
            if use_async
            else graph.invoke(state, config)
        )

    first = HumanMessage(content="run both steps", id="user-one")
    result = invoke("first", first)
    assert result["messages"][-1].content == "complete"
    assert [m.content for m in result["messages"] if m.type == "tool"] == ["one", "two"]
    assert len(calls) == 1
    invoke("second", first)
    assert len(calls) == 2
    invoke("first", HumanMessage(content="run both steps", id="user-two"))
    assert len(calls) == 3


def test_changed_inputs_and_current_provider_tools(monkeypatch):
    from langchain.agents.middleware.types import ModelRequest

    import jw.middleware.tool_selector as module

    scope = {"thread_id": "first", "checkpoint_ns": "child:one|model:one"}
    monkeypatch.setattr(module, "get_config", lambda: {"configurable": scope})
    calls = []

    class Selector:
        def wrap_model_call(self, request, handler):
            calls.append(1)
            return handler(request.override(tools=[request.tools[0]]))

    middleware = _ConditionalToolSelectorMiddleware(lambda _: Selector(), threshold=0)
    model = LocalModel(responses=[AIMessage(content="unused")])
    message = HumanMessage(content="first", id="user")
    request = ModelRequest(model=model, messages=[message], tools=[echo])

    def handler(request):
        return request

    middleware.wrap_model_call(request, handler)
    scope["checkpoint_ns"] = "child:one|model:two"
    provider = {"type": "web_search"}
    reused = middleware.wrap_model_call(
        request.override(tools=[echo, provider]), handler
    )
    assert reused.tools == [echo, provider]
    assert reused.tools[-1] is provider
    assert len(calls) == 1

    # Same message id with edited content, renamed/described capabilities,
    # replacement models and separate child graphs require fresh selection.
    edited = request.override(messages=[HumanMessage(content="edited", id="user")])
    middleware.wrap_model_call(edited, handler)
    described = echo.model_copy(update={"description": "A different capability"})
    middleware.wrap_model_call(edited.override(tools=[described]), handler)
    middleware.wrap_model_call(
        edited.override(model=LocalModel(responses=[AIMessage(content="other")])),
        handler,
    )
    scope["checkpoint_ns"] = "child:two|model:one"
    middleware.wrap_model_call(request, handler)
    assert len(calls) == 5


def test_failed_selection_is_retried_and_downstream_failure_not_replayed(monkeypatch):
    from langchain.agents.middleware.types import ModelRequest

    import jw.middleware.tool_selector as module

    monkeypatch.setattr(
        module, "get_config", lambda: {"configurable": {"thread_id": "first"}}
    )
    calls = []

    class Selector:
        def wrap_model_call(self, request, handler):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("selection unavailable")
            return handler(request)

    middleware = _ConditionalToolSelectorMiddleware(lambda _: Selector(), threshold=0)
    request = ModelRequest(
        model=LocalModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="first", id="user")],
        tools=[echo],
    )
    seen = []

    def handler(request):
        seen.append(request.tools)
        if len(seen) > 1:
            raise ValueError("downstream failed")
        return "fallback"

    assert middleware.wrap_model_call(request, handler) == "fallback"
    for _ in range(2):
        with pytest.raises(ValueError, match="downstream failed"):
            middleware.wrap_model_call(request, handler)
    assert len(calls) == 2
    assert len(seen) == 3


def test_mandatory_tools_follow_changed_request():
    from unittest.mock import MagicMock

    from langchain.agents.middleware.types import ModelRequest

    factories = []

    def factory(names):
        factories.append(names)
        selector = MagicMock()
        selector.wrap_model_call.side_effect = lambda request, handler: handler(request)
        return selector

    middleware = _ConditionalToolSelectorMiddleware(
        factory, threshold=0, always_include=frozenset({"echo"})
    )
    request = ModelRequest(
        model=LocalModel(responses=[AIMessage(content="unused")]),
        messages=[],
        tools=[echo],
    )
    middleware.wrap_model_call(request, lambda r: r)
    middleware.wrap_model_call(
        request.override(tools=[echo.model_copy(update={"name": "other"})]), lambda r: r
    )
    assert factories == [["echo"], []]


@pytest.mark.asyncio
async def test_concurrent_conversations_keep_their_selections(monkeypatch):
    from contextvars import ContextVar

    from langchain.agents.middleware.types import ModelRequest

    import jw.middleware.tool_selector as module

    thread = ContextVar("test_thread")
    monkeypatch.setattr(
        module, "get_config", lambda: {"configurable": {"thread_id": thread.get()}}
    )
    calls = []

    class Selector:
        async def awrap_model_call(self, request, handler):
            calls.append(thread.get())
            await asyncio.sleep(0)
            chosen = request.tools[0 if thread.get() == "first" else 1]
            return await handler(request.override(tools=[chosen]))

    middleware = _ConditionalToolSelectorMiddleware(
        lambda _: Selector(), threshold=0, track_stream_selection=False
    )
    model = LocalModel(responses=[AIMessage(content="unused")])
    request = ModelRequest(
        model=model,
        messages=[HumanMessage(content="same", id="same")],
        tools=[echo, echo.model_copy(update={"name": "other"})],
    )

    async def run(name):
        token = thread.set(name)
        try:

            async def handler(request):
                return [t.name for t in request.tools]

            return [
                await middleware.awrap_model_call(request, handler) for _ in range(3)
            ]
        finally:
            thread.reset(token)

    assert await asyncio.gather(run("first"), run("second")) == [
        [["echo"]] * 3,
        [["other"]] * 3,
    ]
    assert sorted(calls) == ["first", "second"]


def test_selection_cache_is_bounded(monkeypatch):
    from langchain.agents.middleware.types import ModelRequest

    import jw.middleware.tool_selector as module

    scope = {"thread_id": "first"}
    monkeypatch.setattr(module, "get_config", lambda: {"configurable": scope})

    class Selector:
        def wrap_model_call(self, request, handler):
            return handler(request)

    middleware = _ConditionalToolSelectorMiddleware(lambda _: Selector(), threshold=0)
    request = ModelRequest(
        model=LocalModel(responses=[AIMessage(content="unused")]),
        messages=[HumanMessage(content="same", id="same")],
        tools=[echo],
    )
    for index in range(140):
        scope["thread_id"] = str(index)
        middleware.wrap_model_call(request, lambda r: r)
    assert len(middleware._selections) == 128

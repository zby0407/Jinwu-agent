"""Construction reuse must preserve runnable behavior and state boundaries."""

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from jw.graph_build import create_deep_agent


class LocalModel(FakeListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_each_subagent_compiles_once_and_is_ready_before_first_call(monkeypatch):
    from deepagents.middleware import subagents

    original = subagents.create_agent
    compiled = Counter()

    def counted(*args, **kwargs):
        compiled[kwargs.get("name")] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(subagents, "create_agent", counted)
    model = LocalModel(responses=["ready"])
    graph = create_deep_agent(
        model=model,
        middleware=[ModelCallLimitMiddleware(run_limit=5)],
        subagents=[
            {
                "name": "worker",
                "description": "A local worker",
                "system_prompt": "Work.",
                "middleware": [ModelCallLimitMiddleware(run_limit=5)],
            }
        ],
    )
    assert compiled == {"worker": 1, "general-purpose": 1}
    before = compiled.copy()
    assert (
        graph.invoke({"messages": [("user", "hello")]})["messages"][-1].content
        == "ready"
    )
    assert compiled == before
    # The upstream second task-tool closure must still remove parent counters.
    # A leaked count of 100 would make the worker immediately hit its limit.
    task = graph.nodes["tools"].bound.tools_by_name["task"]
    runtime = ToolRuntime(
        state={
            "messages": [],
            "run_model_call_count": 100,
            "thread_model_call_count": 100,
        },
        context=None,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="test-call",
        store=None,
    )
    result = task.func("hello", "worker", runtime)
    assert result.update["messages"][-1].content == "ready"
    result = asyncio.run(task.coroutine("hello", "worker", runtime))
    assert result.update["messages"][-1].content == "ready"
    assert compiled == before


def test_separate_graphs_keep_separate_models(monkeypatch):
    from deepagents.middleware import subagents

    original = subagents.create_agent
    seen = []

    def counted(*args, **kwargs):
        seen.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(subagents, "create_agent", counted)
    for answer in ("first", "second"):
        model = LocalModel(responses=[answer])
        graph = create_deep_agent(model=model)
        assert (
            graph.invoke({"messages": [("user", "hello")]})["messages"][-1].content
            == answer
        )
    assert len(seen) == 2
    assert seen[0] is not seen[1]


def test_construction_failure_does_not_poison_next_build():
    model = LocalModel(responses=["ready"])
    with pytest.raises(KeyError, match="system_prompt"):
        create_deep_agent(
            model=model, subagents=[{"name": "invalid", "description": "invalid"}]
        )
    assert (
        create_deep_agent(model=model)
        .invoke({"messages": [("user", "hello")]})["messages"][-1]
        .content
        == "ready"
    )


def test_concurrent_constructions_keep_models_separate():
    def run(answer):
        graph = create_deep_agent(model=LocalModel(responses=[answer]))
        return graph.invoke({"messages": [("user", "hello")]})["messages"][-1].content

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["one", "two"])) == ["one", "two"]


def test_dynamic_response_schemas_still_compile_on_task_request(monkeypatch):
    from deepagents.middleware import subagents
    from pydantic import BaseModel

    class Answer(BaseModel):
        answer: str

    original = subagents.create_agent
    formats = []

    def counted(*args, **kwargs):
        formats.append(kwargs.get("response_format"))
        return original(*args, **kwargs)

    monkeypatch.setattr(subagents, "create_agent", counted)
    spec = {
        "name": "worker",
        "description": "Worker",
        "system_prompt": "Work.",
        "model": LocalModel(responses=["ready"]),
        "tools": [],
    }
    first = subagents.create_sub_agent(spec, response_format=Answer)
    second = subagents.create_sub_agent(spec, response_format=Answer)
    assert first is not second
    assert formats == [Answer, Answer]

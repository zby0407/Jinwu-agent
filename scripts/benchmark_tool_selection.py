"""Offline multi-step execution: count selection calls separately from main calls.

Run this script with each source tree's PYTHONPATH in a fresh process. Both
models are local fakes; elapsed time is not a provider latency measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("steps must be positive")

    def offline(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Network access is disabled in this benchmark")

    sys.addaudithook(offline)
    from langchain.agents import create_agent
    from langchain.agents.middleware import LLMToolSelectorMiddleware
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool

    import jw
    from jw.middleware.tool_selector import _ConditionalToolSelectorMiddleware

    @tool
    def echo(value: str) -> str:
        """Return a supplied value."""
        return value

    class LocalModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    results = []
    for mode in ("sync", "async"):
        calls = []

        class SelectionModel(LocalModel):
            def with_structured_output(self, schema, calls=calls, **kwargs):
                def select(messages):
                    calls.append(1)
                    return {"tools": ["echo"]}

                return RunnableLambda(select)

        selection_model = SelectionModel(responses=[AIMessage(content="unused")])
        middleware = _ConditionalToolSelectorMiddleware(
            lambda names, selection_model=selection_model: LLMToolSelectorMiddleware(
                model=selection_model, always_include=names
            ),
            track_stream_selection=False,
        )
        responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "echo", "args": {"value": str(index)}, "id": str(index)}
                ],
            )
            for index in range(args.steps)
        ] + [AIMessage(content="complete")]
        # Exercise the production threshold with a larger tool menu. Extra
        # tools remain callable, but this deterministic scenario needs echo.
        tools = [
            echo,
            *(echo.model_copy(update={"name": f"extra_{i}"}) for i in range(29)),
        ]
        graph = create_agent(
            LocalModel(responses=responses), tools=tools, middleware=[middleware]
        )
        started = time.perf_counter()
        config = {"configurable": {"thread_id": "isolated-benchmark"}}
        state = {
            "messages": [
                HumanMessage(content="Execute each step.", id="benchmark-user")
            ]
        }
        output = (
            graph.invoke(state, config)
            if mode == "sync"
            else asyncio.run(graph.ainvoke(state, config))
        )
        elapsed = time.perf_counter() - started
        tool_results = [m.content for m in output["messages"] if m.type == "tool"]
        assert tool_results == [str(index) for index in range(args.steps)]
        assert output["messages"][-1].content == "complete"
        results.append(
            {
                "mode": mode,
                "available_tools": len(tools),
                "selection_calls": len(calls),
                "main_calls": sum(m.type == "ai" for m in output["messages"]),
                "tool_results": tool_results,
                "final": output["messages"][-1].content,
                "local_execution_seconds": elapsed,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "source": jw.__file__,
                "models": "local fakes; no network",
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(args.output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

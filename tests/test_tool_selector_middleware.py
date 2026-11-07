"""Tests for LLMToolSelectorMiddleware integration."""

from typing import Any
from unittest.mock import MagicMock, patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import BaseTool, StructuredTool

from jw.middleware.tool_selector import (
    _ConditionalToolSelectorMiddleware,
    _ToolSelectionTrackerMiddleware,
    create_tool_selector_middleware,
)


def _local_computation_request(*, specialist="none", computation=True, model=None):
    from langchain_core.messages import HumanMessage

    return ModelRequest(
        model=model or MagicMock(),
        messages=[
            HumanMessage(
                content="Edit local files and verify checksums", id="same-turn"
            )
        ],
        tools=[
            _tool("read_file"),
            _tool("write_file"),
            _tool("execute"),
            _tool("other"),
        ],
        state={
            "research_route": {
                "mode": "verified_analysis",
                "source_mode": "local",
                "needs_computation": computation,
                "required_specialist": specialist,
            }
        },
    )


def _narrow_selector_factory(required):
    class Selector:
        def wrap_model_call(self, request, handler):
            return handler(
                request.override(
                    tools=[
                        tool
                        for tool in request.tools
                        if tool.name in {"read_file", *required}
                    ]
                )
            )

        async def awrap_model_call(self, request, handler):
            return await handler(
                request.override(
                    tools=[
                        tool
                        for tool in request.tools
                        if tool.name in {"read_file", *required}
                    ]
                )
            )

    return Selector()


def test_local_computation_keeps_execution_after_selection_and_cache(monkeypatch):
    import jw.middleware.tool_selector as module

    monkeypatch.setattr(
        module, "get_config", lambda: {"configurable": {"thread_id": "local-task"}}
    )
    condition = _ConditionalToolSelectorMiddleware(
        _narrow_selector_factory, threshold=0
    )
    request = _local_computation_request()
    seen = []
    for _ in range(2):
        condition.wrap_model_call(
            request, lambda r: seen.append({t.name for t in r.tools})
        )
    assert seen == [{"read_file", "write_file", "execute"}] * 2
    assert "other" not in seen[0]


def test_route_change_does_not_reuse_read_only_selection(monkeypatch):
    import jw.middleware.tool_selector as module

    monkeypatch.setattr(
        module, "get_config", lambda: {"configurable": {"thread_id": "local-task"}}
    )
    condition = _ConditionalToolSelectorMiddleware(
        _narrow_selector_factory, threshold=0
    )
    request = _local_computation_request(computation=False)
    seen = []
    condition.wrap_model_call(request, lambda r: seen.append({t.name for t in r.tools}))
    route = {**request.state["research_route"], "needs_computation": True}
    condition.wrap_model_call(
        request.override(state={"research_route": route}),
        lambda r: seen.append({t.name for t in r.tools}),
    )
    assert seen[0] == {"read_file"}
    assert seen[1] == {"read_file", "write_file", "execute"}


def test_specialist_route_does_not_gain_general_execution():
    condition = _ConditionalToolSelectorMiddleware(
        _narrow_selector_factory, threshold=0
    )
    seen = []
    condition.wrap_model_call(
        _local_computation_request(specialist="scientific-data"),
        lambda r: seen.append({t.name for t in r.tools}),
    )
    assert seen == [{"read_file"}]


def test_async_selection_keeps_required_local_runtime():
    import asyncio

    condition = _ConditionalToolSelectorMiddleware(
        _narrow_selector_factory, threshold=0
    )
    seen = []

    async def handler(request):
        seen.append({t.name for t in request.tools})

    asyncio.run(condition.awrap_model_call(_local_computation_request(), handler))
    assert seen == [{"read_file", "write_file", "execute"}]


def _tool(name: str) -> BaseTool:
    def _func(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        func=_func,
        name=name,
        description=f"{name} test tool",
    )


def _request(tools: list[BaseTool | dict[str, Any]]) -> ModelRequest:
    return ModelRequest(model=MagicMock(), messages=[], tools=tools)


def _mock_model():
    """Create a MagicMock model compatible with disable_thinking()."""
    m = MagicMock(profile={"max_input_tokens": 200_000})
    m.thinking = None
    m.reasoning = None
    return m


def _patched_create():
    """Create tool selector middleware without real LLM init."""
    return [
        _ConditionalToolSelectorMiddleware(
            selector_factory=MagicMock(return_value=MagicMock()),
            threshold=20,
        ),
        _ToolSelectionTrackerMiddleware(),
    ]


# Helper: patches needed to call create_tool_selector_middleware without LLM
def _factory_patches():
    return (
        patch(
            "jw.middleware.tool_selector.disable_thinking",
            return_value=MagicMock(),
            create=True,
        ),
        patch("jw.agent._ensure_chat_model", return_value=MagicMock()),
        patch(
            "langchain.agents.middleware.LLMToolSelectorMiddleware",
            return_value=MagicMock(),
        ),
    )


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_create_tool_selector_returns_list():
    p1, p2, p3 = _factory_patches()
    with p1, p2, p3:
        result = create_tool_selector_middleware()
        assert isinstance(result, list)
        assert len(result) == 2


def test_create_tool_selector_always_include():
    p1, p2, p3 = _factory_patches()
    with p1, p2, p3 as mock_cls:
        result = create_tool_selector_middleware(threshold=0)
        request = _request(
            [
                _tool("think_tool"),
                _tool("search_observations"),
                _tool("read_memory"),
                _tool("unrelated_tool"),
            ]
        )
        result[0].wrap_model_call(request, MagicMock())
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["always_include"] == [
            "read_memory",
            "search_observations",
            "think_tool",
        ]


def test_custom_threshold():
    p1, p2, p3 = _factory_patches()
    with p1, p2, p3:
        result = create_tool_selector_middleware(threshold=5)
        assert result[0]._threshold == 5


# ---------------------------------------------------------------------------
# Conditional + tracker unit tests
# ---------------------------------------------------------------------------


def test_conditional_skips_below_threshold():
    """When tools <= threshold, selector is skipped."""
    mock_selector = MagicMock()
    selector_factory = MagicMock(return_value=mock_selector)
    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=selector_factory,
        threshold=10,
    )

    request = MagicMock()
    request.tools = [MagicMock() for _ in range(5)]
    handler = MagicMock()

    cond.wrap_model_call(request, handler)
    handler.assert_called_once_with(request)
    selector_factory.assert_not_called()
    mock_selector.wrap_model_call.assert_not_called()


def test_conditional_runs_above_threshold():
    """When tools > threshold, selector runs."""
    mock_selector = MagicMock()
    selector_factory = MagicMock(return_value=mock_selector)
    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=selector_factory,
        threshold=10,
    )

    request = MagicMock()
    request.tools = [MagicMock() for _ in range(15)]
    handler = MagicMock()

    cond.wrap_model_call(request, handler)
    selector_factory.assert_called_once_with([])
    mock_selector.wrap_model_call.assert_called_once()
    handler.assert_not_called()


def test_selector_active_flag():
    """_selector_active flag is True during selection, False after."""
    import jw.middleware.tool_selector as ts_mod

    mock_selector = MagicMock()

    def fake_selector_call(request, handler):
        assert ts_mod._selector_active is True
        return handler(request)

    mock_selector.wrap_model_call.side_effect = fake_selector_call
    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=MagicMock(return_value=mock_selector),
        threshold=5,
    )

    request = MagicMock()
    request.tools = [MagicMock() for _ in range(10)]
    handler = MagicMock()

    cond.wrap_model_call(request, handler)
    assert ts_mod._selector_active is False


def test_selector_can_disable_stream_tracking():
    """Selection can run without touching the main-agent stream/UI globals."""
    import jw.middleware.tool_selector as ts_mod

    mock_selector = MagicMock()

    def fake_selector_call(request, handler):
        assert ts_mod._selector_active is False
        return handler(request)

    mock_selector.wrap_model_call.side_effect = fake_selector_call
    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=MagicMock(return_value=mock_selector),
        threshold=5,
        track_stream_selection=False,
    )

    ts_mod._total_tools_count = 99
    request = MagicMock()
    request.tools = [MagicMock() for _ in range(10)]
    handler = MagicMock()

    cond.wrap_model_call(request, handler)

    mock_selector.wrap_model_call.assert_called_once()
    handler.assert_called_once()
    assert ts_mod._selector_active is False
    assert ts_mod._total_tools_count == 99


def test_selector_always_includes_available_memory_tools():
    """Adaptive selection must mark available memory tools as mandatory."""
    calls = []

    class FakeSelector:
        def __init__(self, always_include):
            self.always_include = always_include

        def wrap_model_call(self, request, handler):
            calls.append(self.always_include)
            return handler(request)

    def selector_factory(always_include):
        return FakeSelector(always_include)

    request = _request(
        [
            _tool("think_tool"),
            _tool("search_observations"),
            _tool("read_memory"),
            _tool("unrelated_tool"),
        ]
    )

    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=selector_factory,
        threshold=0,
        always_include=frozenset(
            {
                "think_tool",
                "task",
                "search_observations",
                "read_memory",
                "record_observation",
            }
        ),
    )
    handler = MagicMock()

    cond.wrap_model_call(request, handler)

    assert calls == [
        [
            "read_memory",
            "search_observations",
            "think_tool",
        ]
    ]


def test_selector_resolved_once_across_repeated_requests():
    """Agent tools are stable, so build the selector once and reuse it."""
    mock_selector = MagicMock()
    mock_selector.wrap_model_call.side_effect = lambda request, handler: handler(
        request
    )
    selector_factory = MagicMock(return_value=mock_selector)

    cond = _ConditionalToolSelectorMiddleware(
        selector_factory=selector_factory,
        threshold=0,
        always_include=frozenset({"think_tool", "search_observations"}),
    )
    tools = [
        _tool("think_tool"),
        _tool("search_observations"),
        _tool("unrelated_tool"),
    ]

    for _ in range(3):
        cond.wrap_model_call(_request(tools), MagicMock())

    selector_factory.assert_called_once_with(["search_observations", "think_tool"])
    assert mock_selector.wrap_model_call.call_count == 3


def test_tracker_captures_tools():
    """Tracker middleware captures tool names from request."""
    tracker = _ToolSelectionTrackerMiddleware()
    tool1 = _tool("read_file")
    tool2 = _tool("execute")

    request = MagicMock()
    request.tools = [tool1, tool2]
    handler = MagicMock()

    tracker.wrap_model_call(request, handler)
    handler.assert_called_once_with(request)

    import jw.middleware.tool_selector as ts_mod

    assert ts_mod._current_selected_tools == ["read_file", "execute"]


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@patch(
    "jw.middleware.create_tool_selector_middleware",
    side_effect=lambda *a, **kw: _patched_create(),
)
@patch("jw.agent._ensure_chat_model")
@patch("jw.agent._ensure_config")
def test_default_middleware_includes_tool_selector(mock_config, mock_model, mock_ts):
    mock_model.return_value = _mock_model()
    cfg = MagicMock()
    cfg.enable_ask_user = False
    cfg.auto_approve = False
    cfg.auxiliary_model = ""
    cfg.auxiliary_provider = ""
    mock_config.return_value = cfg

    from jw.agent import _get_default_middleware

    mw = _get_default_middleware()
    type_names = [type(m).__name__ for m in mw]
    assert "_ConditionalToolSelectorMiddleware" in type_names
    assert "_ToolSelectionTrackerMiddleware" in type_names


@patch("jw.agent._ensure_chat_model")
def test_subagent_no_tool_selector(mock_model):
    mock_model.return_value = _mock_model()

    from jw.agent import _inject_subagent_middleware

    subs = [{"name": "test-agent"}]
    _inject_subagent_middleware(subs)

    type_names = [type(m).__name__ for m in subs[0]["middleware"]]
    assert "_ConditionalToolSelectorMiddleware" not in type_names


@patch(
    "jw.middleware.create_tool_selector_middleware",
    side_effect=lambda *a, **kw: _patched_create(),
)
@patch("jw.agent._ensure_chat_model")
@patch("jw.agent._ensure_config")
def test_tool_selector_ordering(mock_config, mock_model, mock_ts):
    """ToolSelector should come after ToolErrorHandler and before Memory."""
    mock_model.return_value = _mock_model()
    cfg = MagicMock()
    cfg.enable_ask_user = False
    cfg.auto_approve = False
    cfg.auxiliary_model = ""
    cfg.auxiliary_provider = ""
    mock_config.return_value = cfg

    from jw.agent import _get_default_middleware

    mw = _get_default_middleware()
    type_names = [type(m).__name__ for m in mw]

    ts_idx = type_names.index("_ConditionalToolSelectorMiddleware")
    tracker_idx = type_names.index("_ToolSelectionTrackerMiddleware")
    te_idx = type_names.index("ToolErrorHandlerMiddleware")
    mem_idx = type_names.index("JWMemoryMiddleware")
    assert te_idx < ts_idx < tracker_idx < mem_idx

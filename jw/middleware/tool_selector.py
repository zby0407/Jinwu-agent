"""LLMToolSelectorMiddleware configuration for JW.

Wraps LangChain's built-in ``LLMToolSelectorMiddleware`` with project-specific
defaults and an optional stream tracker that captures which tools were selected.

The selector only activates when the agent has more than ``threshold`` tools
(default 26).  Below that, the extra LLM call isn't worth the token savings.

Within a conversation, reuse successful selections for unchanged user messages
and tool menus. Intermediate tool results are not inputs to the upstream
selector, so they do not require another selection request.

Usage::

    from jw.middleware import create_tool_selector_middleware

    middleware = create_tool_selector_middleware()  # returns [selector, tracker]
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AIMessage,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config

logger = logging.getLogger(__name__)

# Module-level storage for main-agent tool-selection UI state.
# Updated only when stream tracking is enabled; read by stream/events.py.
_current_selected_tools: list[str] = []
_last_emitted_tools: list[str] = []  # last selection shown to user
_total_tools_count: int = 0  # total tools before selection
_selector_active: bool = False

# Default threshold: only run tool selection when tools exceed this count.
# Base tools are ~14; selector activates when MCP tools push count above 26.
DEFAULT_TOOL_THRESHOLD = 26
DEFAULT_ALWAYS_INCLUDE_TOOLS: frozenset[str] = frozenset(
    {
        "think_tool",
        "task",
        "read_memory",
        "record_observation",
        "search_observations",
    }
)


def _tool_name(tool: BaseTool | dict[str, Any]) -> str | None:
    if isinstance(tool, BaseTool):
        return tool.name or None
    name = tool.get("name")
    return name if isinstance(name, str) and name else None


def _available_always_include(
    tools: Iterable[BaseTool | dict[str, Any]],
    candidates: frozenset[str],
) -> list[str]:
    """Return mandatory BaseTool names that exist on this request."""
    available_names = {tn for tool in tools if (tn := _tool_name(tool))}
    return sorted(candidates & available_names)


class _ConditionalToolSelectorMiddleware(AgentMiddleware):
    """Wraps LLMToolSelectorMiddleware with a tool-count threshold.

    Skips the selection LLM call when ``len(request.tools) <= threshold``,
    avoiding unnecessary overhead for agents with few tools.

    When stream tracking is enabled, sets ``_selector_active`` during the
    selector's internal LLM call so the streaming layer can suppress its output.
    """

    name = "conditional_tool_selector"

    def __init__(
        self,
        selector_factory: Callable[[list[str]], AgentMiddleware],
        threshold: int = DEFAULT_TOOL_THRESHOLD,
        *,
        always_include: frozenset[str] | None = None,
        track_stream_selection: bool = True,
    ):
        super().__init__()
        self._selector_factory = selector_factory
        self._threshold = threshold
        self._always_include = always_include or frozenset()
        self._track_stream_selection = track_stream_selection
        self._selector: AgentMiddleware | None = None
        self._selector_names: list[str] = []
        self._cache_lock = threading.Lock()
        # Process-local and bounded; store only selection inputs' digest and
        # tool names, never messages/results or a modified ModelRequest.
        self._selections: OrderedDict[tuple, tuple[BaseChatModel, tuple[str, ...]]] = (
            OrderedDict()
        )

    def _build_selector(self, request: ModelRequest) -> AgentMiddleware:
        names = _available_always_include(request.tools, self._always_include)
        with self._cache_lock:
            if self._selector is None or names != self._selector_names:
                self._selector = self._selector_factory(names)
                self._selector_names = names
            return self._selector

    def _selection_key(self, request: ModelRequest) -> tuple | None:
        # The upstream selector sees only the last HumanMessage and the
        # ordered tool names/descriptions, not intermediate AI/tool messages.
        user = next(
            (m for m in reversed(request.messages) if isinstance(m, HumanMessage)), None
        )
        if user is None or not user.id:
            return None
        try:
            config = get_config().get("configurable", {})
        except RuntimeError:
            return None  # Direct middleware calls have no conversation scope.
        thread = config.get("thread_id")
        if not thread:
            return None
        # LangGraph appends the current node's task id to the namespace. Its
        # parent identifies the graph invocation and stays stable across steps.
        namespace = config.get("checkpoint_ns", "").rpartition("|")[0]
        inputs = [
            user.model_dump_json(),
            [(t.name, t.description) for t in request.tools if isinstance(t, BaseTool)],
        ]
        digest = hashlib.sha256(
            json.dumps(inputs, ensure_ascii=False).encode()
        ).digest()
        return (str(thread), namespace, id(request.model), digest)

    def _reuse_selection(
        self, key: tuple | None, request: ModelRequest
    ) -> ModelRequest | None:
        if key is None:
            return None
        with self._cache_lock:
            entry = self._selections.get(key)
            if entry is None:
                return None
            self._selections.move_to_end(key)
            names = entry[1]
        tools = {t.name: t for t in request.tools if isinstance(t, BaseTool)}
        # Rebind to this request's objects; provider dictionaries pass through.
        return request.override(
            tools=[
                *(tools[name] for name in names),
                *(t for t in request.tools if isinstance(t, dict)),
            ]
        )

    def _remember_selection(
        self, key: tuple | None, request: ModelRequest, selected: ModelRequest
    ) -> None:
        if key is None:
            return
        names = tuple(t.name for t in selected.tools if isinstance(t, BaseTool))
        with self._cache_lock:
            # Keep the model alive while its identity is part of a cache key.
            self._selections[key] = (request.model, names)
            self._selections.move_to_end(key)
            if len(self._selections) > 128:
                self._selections.popitem(last=False)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | AIMessage | ExtendedModelResponse:
        if len(request.tools) <= self._threshold:
            return handler(request)

        key = self._selection_key(request)
        cached = self._reuse_selection(key, request)
        if cached is not None:
            return handler(cached)

        if self._track_stream_selection:
            global _selector_active, _total_tools_count
            _selector_active = True
            _total_tools_count = len(request.tools)

        # Track whether handler was called — if so, any exception is from
        # the downstream model, not the selector, and must propagate.
        _handler_called = False

        def _handler_after_selection(req: ModelRequest) -> ModelResponse:
            nonlocal _handler_called
            _handler_called = True
            self._remember_selection(key, request, req)
            if self._track_stream_selection:
                global _selector_active
                _selector_active = False
            return handler(req)

        try:
            return self._build_selector(request).wrap_model_call(
                request, _handler_after_selection
            )
        except Exception:
            if _handler_called:
                raise  # Error from downstream model — don't retry
            # Selector itself failed (e.g., structured output not supported).
            logger.debug("Tool selector failed, using all tools", exc_info=True)
            if self._track_stream_selection:
                _selector_active = False
            return handler(request)
        finally:
            if self._track_stream_selection:
                _selector_active = False

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage | ExtendedModelResponse:
        if len(request.tools) <= self._threshold:
            return await handler(request)

        key = self._selection_key(request)
        cached = self._reuse_selection(key, request)
        if cached is not None:
            return await handler(cached)

        if self._track_stream_selection:
            global _selector_active, _total_tools_count
            _selector_active = True
            _total_tools_count = len(request.tools)

        _handler_called = False

        async def _handler_after_selection(req: ModelRequest) -> ModelResponse:
            nonlocal _handler_called
            _handler_called = True
            self._remember_selection(key, request, req)
            if self._track_stream_selection:
                global _selector_active
                _selector_active = False
            return await handler(req)

        try:
            return await self._build_selector(request).awrap_model_call(
                request, _handler_after_selection
            )
        except Exception:
            if _handler_called:
                raise
            logger.debug("Tool selector failed, using all tools", exc_info=True)
            if self._track_stream_selection:
                _selector_active = False
            return await handler(request)
        finally:
            if self._track_stream_selection:
                _selector_active = False


class _ToolSelectionTrackerMiddleware(AgentMiddleware):
    """Captures which tools the model actually receives after filtering.

    Sits right AFTER the selector in the middleware chain (more inner),
    so ``request.tools`` already contains only the selected tools when
    this middleware's ``wrap_model_call`` runs.
    """

    name = "tool_selection_tracker"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        global _current_selected_tools
        tools = [name for tool in request.tools if (name := _tool_name(tool))]
        _current_selected_tools = tools
        if tools:
            logger.debug("Selected tools: %s", tools)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        global _current_selected_tools
        tools = [name for tool in request.tools if (name := _tool_name(tool))]
        _current_selected_tools = tools
        if tools:
            logger.debug("Selected tools: %s", tools)
        return await handler(request)


def create_tool_selector_middleware(
    threshold: int = DEFAULT_TOOL_THRESHOLD,
    *,
    model: BaseChatModel | None = None,
    track_stream_selection: bool = True,
):
    """Build LLMToolSelectorMiddleware + tracker with JW defaults.

    Returns middleware for adaptive tool selection:
    1. Conditional wrapper around ``LLMToolSelectorMiddleware`` — only
       activates when ``len(tools) > threshold``
    2. Optional ``_ToolSelectionTrackerMiddleware`` — captures selected tool
       names for the main-agent stream UI when ``track_stream_selection`` is true

    Args:
        model: Chat model for tool selection.  If *None*, the default
            model is resolved via ``_ensure_chat_model()``.
        threshold: Minimum number of tools to trigger selection.
            Default 26.  Set to 0 to always run selection.
        track_stream_selection: Whether to update process-global stream/UI
            state. Disable for async sub-agents that should still select tools
            but should not drive the main-agent tool-selection widget.

    ``think_tool``, ``task``, and memory tools are always included because:

    - ``think_tool``: required every step for structured reflection
    - ``task``: core delegation mechanism; tested and confirmed the selector
      model never auto-selects it (0/5 complex queries)
    - memory tools: referenced by memory prompts; filtering them makes the
      agent unable to use memory even when the prompt tells it to
    """
    from langchain.agents.middleware import LLMToolSelectorMiddleware

    from .utils import disable_thinking

    if model is None:
        from jw.agent import _ensure_chat_model

        model = _ensure_chat_model()
    safe_model = disable_thinking(model)

    system_prompt = (
        "You are selecting tools for a scientific research agent. "
        "Tasks often involve multi-step workflows. "
        "Select tools that cover both the immediate need and "
        "likely follow-up steps. "
        "If the query is broad or all tools seem relevant, "
        "select all of them — filtering is not always necessary."
    )

    def selector_factory(always_include: list[str]) -> AgentMiddleware:
        return LLMToolSelectorMiddleware(
            model=safe_model,
            system_prompt=system_prompt,
            always_include=always_include,
        )

    middleware: list[AgentMiddleware] = [
        _ConditionalToolSelectorMiddleware(
            selector_factory=selector_factory,
            threshold=threshold,
            always_include=DEFAULT_ALWAYS_INCLUDE_TOOLS,
            track_stream_selection=track_stream_selection,
        ),
    ]
    if track_stream_selection:
        middleware.append(_ToolSelectionTrackerMiddleware())
    return middleware


def reset_tool_selection_state_for_tests() -> None:
    """Reset the process-global tool-selection state.

    The selector/tracker record the last selected tools and the selector-active
    flag in module globals that ``stream/tool_selection.py`` reads to suppress
    selector chatter. Tests that drive the selector must not leak that state
    into later tests; an autouse fixture resets it around every test.
    """
    global _current_selected_tools, _last_emitted_tools
    global _total_tools_count, _selector_active

    _current_selected_tools = []
    _last_emitted_tools = []
    _total_tools_count = 0
    _selector_active = False

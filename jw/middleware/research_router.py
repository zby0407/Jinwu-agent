"""Qwen-routed research modes with deterministic workflow entry points.

The architecture combines two mature open-source patterns without replacing
JW's existing Deep Agents/LangGraph stack:

* Qwen-Agent's Router decides whether the main agent can answer directly or
  needs a specialist/tool path.
* LangChain's open_deep_research keeps the expensive research workflow as
  explicit graph stages instead of hoping the chat model improvises them.

The routing decision is made once per user turn by the configured Qwen model
and persisted in graph state. Deterministic enforcement is deliberately thin:
it only guarantees the first evidence operation for verified analysis and the
three specialist graph nodes for an explicitly selected full research loop.
Domain answers, datasets, hypotheses, and experiment outcomes remain entirely
model/tool produced.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage

from .utils import append_to_system_message, disable_thinking

ResearchMode = Literal["fast_answer", "verified_analysis", "full_research"]
SourceMode = Literal["none", "local", "external", "mixed"]

_ROUTE_SCHEMA: dict[str, Any] = {
    "title": "ResearchRoute",
    "description": "Select the smallest sufficient research workflow.",
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["fast_answer", "verified_analysis", "full_research"],
            "description": (
                "fast_answer for stable conversational or conceptual answers; "
                "verified_analysis when claims depend on files, sources, data, "
                "or computation; full_research only for an end-to-end research "
                "deliverable spanning planning, hypotheses, experiments, and report."
            ),
        },
        "source_mode": {
            "type": "string",
            "enum": ["none", "local", "external", "mixed"],
            "description": (
                "Where evidence must come from. local means user/workspace files; "
                "external means literature or official online sources; mixed means both."
            ),
        },
        "needs_computation": {
            "type": "boolean",
            "description": (
                "Whether answering requires executing a calculation, model, "
                "transformation, statistical test, or reproducible code."
            ),
        },
        "reason": {
            "type": "string",
            "description": "One short operational reason for the selected mode.",
        },
    },
    "required": ["mode", "source_mode", "needs_computation", "reason"],
    "additionalProperties": False,
}

_ROUTER_PROMPT = """You are the routing node for a solar-science research agent.
Choose the smallest workflow that can truthfully satisfy the latest user request.

Routes:
- fast_answer: stable conceptual explanation, conversation, editing, or a simple
  answer that does not depend on checking a source, local file, dataset, current
  fact, or calculation.
- verified_analysis: any bounded task whose answer depends on inspecting local
  files, retrieving an official/literature source, reading data, or executing a
  reproducible calculation. Difficulty alone does not make it full research.
- full_research: only an explicitly end-to-end research deliverable, or a request
  that genuinely requires the complete chain of research brief/planning,
  competing hypotheses, experiment/validation, and final research report.

Classify the actual evidence dependency, not the phrasing style. A short question
about a named dataset is verified_analysis. A long conceptual explanation may
still be fast_answer. If the user requests local and external evidence, choose
source_mode=mixed. Set needs_computation only when code or calculation is part of
the requested result."""

_FULL_RESEARCH_STAGES = (
    "solar-planner",
    "solar-hypothesis",
    "solar-experiment",
)
_LOCAL_DISCOVERY_TOOLS = ("ls", "glob")
_LOCAL_READ_TOOLS = ("read_file",)
_EXTERNAL_EVIDENCE_TOOLS = (
    "tavily_search",
    "lit_search",
    "research_planner_search_literature",
    "web_search",
)
_COMPUTE_TOOLS = ("execute",)
_DATA_FALLBACK = re.compile(
    r"(?:数据|文件|表格|本地|workspace|dataset|data|file|csv|tsv|parquet|"
    r"json|fits?|计算|预测|回归|检验|calculate|predict|regression|test)",
    re.IGNORECASE,
)
_FULL_FALLBACK = re.compile(
    r"(?:完整研究|完整科研|科研闭环|端到端研究|end-to-end research|"
    r"full research)",
    re.IGNORECASE,
)


class ResearchRoutingState(AgentState):
    """Graph state persisted for one routing decision per human turn."""

    research_route: NotRequired[dict[str, Any]]
    research_route_turn: NotRequired[str]


def _message_text(message: object) -> str:
    content = (
        message.get("content", "")
        if isinstance(message, Mapping)
        else getattr(message, "content", "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return "\n".join(
            str(part.get("text", ""))
            if isinstance(part, Mapping)
            else str(part)
            for part in content
        )
    return str(content)


def _message_role(message: object) -> str:
    if isinstance(message, Mapping):
        return str(message.get("type") or message.get("role") or "")
    return str(getattr(message, "type", "") or getattr(message, "role", ""))


def _latest_human(messages: Sequence[object]) -> tuple[int, object] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage) or _message_role(message) in {
            "human",
            "user",
        }:
            return index, message
    return None


def _turn_key(message: object) -> str:
    message_id = (
        message.get("id")
        if isinstance(message, Mapping)
        else getattr(message, "id", None)
    )
    if isinstance(message_id, str) and message_id:
        return message_id
    return hashlib.sha256(_message_text(message).encode("utf-8")).hexdigest()


def _fallback_route(text: str) -> dict[str, Any]:
    """Fail conservatively if the auxiliary routing call is unavailable."""

    if _FULL_FALLBACK.search(text):
        return {
            "mode": "full_research",
            "source_mode": "mixed",
            "needs_computation": True,
            "reason": "router unavailable; explicit full-research intent preserved",
        }
    if _DATA_FALLBACK.search(text):
        local = bool(re.search(r"(?:本地|现有|已有|workspace|local|provided)", text))
        return {
            "mode": "verified_analysis",
            "source_mode": "local" if local else "mixed",
            "needs_computation": bool(
                re.search(
                    r"(?:计算|预测|回归|检验|拟合|calculate|predict|regression|test|fit)",
                    text,
                    re.IGNORECASE,
                )
            ),
            "reason": "router unavailable; evidence-dependent request kept verified",
        }
    return {
        "mode": "fast_answer",
        "source_mode": "none",
        "needs_computation": False,
        "reason": "router unavailable; no explicit evidence dependency detected",
    }


def _validated_route(value: object, *, fallback_text: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return _fallback_route(fallback_text)
    mode = value.get("mode")
    source_mode = value.get("source_mode")
    needs_computation = value.get("needs_computation")
    reason = value.get("reason")
    if (
        mode not in {"fast_answer", "verified_analysis", "full_research"}
        or source_mode not in {"none", "local", "external", "mixed"}
        or not isinstance(needs_computation, bool)
        or not isinstance(reason, str)
    ):
        return _fallback_route(fallback_text)
    if mode == "full_research" and source_mode == "none":
        source_mode = "mixed"
    return {
        "mode": mode,
        "source_mode": source_mode,
        "needs_computation": needs_computation,
        "reason": reason[:300],
    }


def _tool_name(tool: object) -> str | None:
    if isinstance(tool, Mapping):
        value = tool.get("name")
        if not value and isinstance(tool.get("function"), Mapping):
            value = tool["function"].get("name")
        return value if isinstance(value, str) else None
    value = getattr(tool, "name", None)
    return value if isinstance(value, str) else None


def _available_tool(
    tools: Sequence[object],
    candidates: Sequence[str],
) -> str | None:
    available = {name for tool in tools if (name := _tool_name(tool))}
    return next((name for name in candidates if name in available), None)


def _calls_since_latest_human(
    messages: Sequence[object],
) -> tuple[list[dict[str, Any]], set[str]]:
    latest = _latest_human(messages)
    start = latest[0] + 1 if latest else 0
    calls: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    successful_ids: set[str] = set()
    successful_names: set[str] = set()

    for message in messages[start:]:
        raw_calls = (
            message.get("tool_calls", [])
            if isinstance(message, Mapping)
            else getattr(message, "tool_calls", [])
        )
        if isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes)):
            for raw in raw_calls:
                if not isinstance(raw, Mapping):
                    continue
                name = raw.get("name")
                call_id = raw.get("id")
                args = raw.get("args")
                if not isinstance(name, str):
                    continue
                row = {
                    "name": name,
                    "id": call_id if isinstance(call_id, str) else "",
                    "args": dict(args) if isinstance(args, Mapping) else {},
                }
                calls.append(row)
                if row["id"]:
                    call_names[row["id"]] = name

        if isinstance(message, ToolMessage) or _message_role(message) == "tool":
            status = (
                message.get("status")
                if isinstance(message, Mapping)
                else getattr(message, "status", None)
            )
            if status == "error":
                continue
            call_id = (
                message.get("tool_call_id")
                if isinstance(message, Mapping)
                else getattr(message, "tool_call_id", None)
            )
            name = (
                message.get("name")
                if isinstance(message, Mapping)
                else getattr(message, "name", None)
            )
            if isinstance(call_id, str):
                successful_ids.add(call_id)
            if isinstance(name, str) and name:
                successful_names.add(name)

    successful_names.update(
        call_names[call_id] for call_id in successful_ids if call_id in call_names
    )
    return calls, successful_names


def _successful_specialists(
    calls: Sequence[Mapping[str, Any]],
    successful_names: set[str],
    messages: Sequence[object],
) -> set[str]:
    if "task" not in successful_names:
        return set()
    successful_call_ids: set[str] = set()
    for message in messages:
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        status = (
            message.get("status")
            if isinstance(message, Mapping)
            else getattr(message, "status", None)
        )
        if status == "error":
            continue
        call_id = (
            message.get("tool_call_id")
            if isinstance(message, Mapping)
            else getattr(message, "tool_call_id", None)
        )
        if isinstance(call_id, str):
            successful_call_ids.add(call_id)
    return {
        str(call.get("args", {}).get("subagent_type"))
        for call in calls
        if call.get("name") == "task"
        and call.get("id") in successful_call_ids
        and call.get("args", {}).get("subagent_type") in _FULL_RESEARCH_STAGES
    }


def _attempt_count(
    calls: Sequence[Mapping[str, Any]],
    tool_name: str,
    *,
    specialist: str | None = None,
) -> int:
    return sum(
        1
        for call in calls
        if call.get("name") == tool_name
        and (
            specialist is None
            or call.get("args", {}).get("subagent_type") == specialist
        )
    )


class ResearchRouterMiddleware(
    AgentMiddleware[ResearchRoutingState, Any, Any]
):
    """Route each user turn and enforce the selected workflow's entry nodes."""

    state_schema = ResearchRoutingState
    name = "research_router"

    def __init__(self, *, model: BaseChatModel) -> None:
        super().__init__()
        self._model = disable_thinking(model)

    def _route_sync(self, text: str) -> dict[str, Any]:
        try:
            response = self._model.with_structured_output(_ROUTE_SCHEMA).invoke(
                [
                    {"role": "system", "content": _ROUTER_PROMPT},
                    {"role": "user", "content": text},
                ]
            )
        except Exception:
            return _fallback_route(text)
        return _validated_route(response, fallback_text=text)

    async def _route_async(self, text: str) -> dict[str, Any]:
        try:
            response = await self._model.with_structured_output(_ROUTE_SCHEMA).ainvoke(
                [
                    {"role": "system", "content": _ROUTER_PROMPT},
                    {"role": "user", "content": text},
                ]
            )
        except Exception:
            return _fallback_route(text)
        return _validated_route(response, fallback_text=text)

    def before_agent(
        self,
        state: ResearchRoutingState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        latest = _latest_human(messages)
        if latest is None:
            return None
        key = _turn_key(latest[1])
        if state.get("research_route_turn") == key and state.get("research_route"):
            return None
        return {
            "research_route": self._route_sync(_message_text(latest[1])),
            "research_route_turn": key,
        }

    async def abefore_agent(
        self,
        state: ResearchRoutingState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        latest = _latest_human(messages)
        if latest is None:
            return None
        key = _turn_key(latest[1])
        if state.get("research_route_turn") == key and state.get("research_route"):
            return None
        return {
            "research_route": await self._route_async(_message_text(latest[1])),
            "research_route_turn": key,
        }

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        route = request.state.get("research_route")
        if not isinstance(route, Mapping):
            return request
        mode = route.get("mode")
        source_mode = route.get("source_mode")
        needs_computation = route.get("needs_computation") is True
        messages = list(request.messages)
        calls, successful_names = _calls_since_latest_human(messages)

        directive = [
            "<research_route>",
            f"mode={mode}; source_mode={source_mode}; "
            f"needs_computation={str(needs_computation).lower()}",
        ]
        forced_tool: str | None = None

        if mode == "fast_answer":
            directive.append(
                "Use the direct-answer path. Tools remain available when useful, "
                "but do not manufacture a research workflow."
            )
        elif mode == "verified_analysis":
            directive.append(
                "Use the verified-analysis path. Inspect the actual evidence before "
                "substantive claims; distinguish retrieved facts from computed results."
            )
            local_required = source_mode in {"local", "mixed"}
            external_required = source_mode in {"external", "mixed"}
            local_seen = bool(
                successful_names
                & {*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS}
            )
            read_seen = bool(successful_names & set(_LOCAL_READ_TOOLS))
            external_seen = bool(
                successful_names & set(_EXTERNAL_EVIDENCE_TOOLS)
            )
            compute_seen = bool(successful_names & set(_COMPUTE_TOOLS))

            if local_required and not local_seen:
                forced_tool = _available_tool(
                    request.tools,
                    (*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS),
                )
            elif local_required and not read_seen:
                forced_tool = _available_tool(request.tools, _LOCAL_READ_TOOLS)
            elif external_required and not external_seen:
                forced_tool = _available_tool(
                    request.tools, _EXTERNAL_EVIDENCE_TOOLS
                )
            elif needs_computation and not compute_seen:
                forced_tool = _available_tool(request.tools, _COMPUTE_TOOLS)

            if forced_tool and _attempt_count(calls, forced_tool) >= 2:
                directive.append(
                    f"The required {forced_tool} operation already failed twice. "
                    "Stop retrying it and report the evidence blocker precisely."
                )
                forced_tool = None
        elif mode == "full_research":
            directive.append(
                "Use the explicit full-research graph: solar-planner creates the "
                "research brief/plan, solar-hypothesis produces falsifiable competing "
                "hypotheses, solar-experiment validates with staged evidence, then the "
                "main agent writes the final report from verified receipts. Do not "
                "replace these graph nodes with generic prose or generic execute calls."
            )
            completed = _successful_specialists(calls, successful_names, messages)
            next_stage = next(
                (stage for stage in _FULL_RESEARCH_STAGES if stage not in completed),
                None,
            )
            if next_stage is not None:
                attempts = _attempt_count(calls, "task", specialist=next_stage)
                if attempts < 2 and _available_tool(request.tools, ("task",)):
                    forced_tool = "task"
                    directive.append(
                        f"The mandatory next graph node is {next_stage}. Call task now "
                        f"with subagent_type={next_stage!r}; give it the user request, "
                        "current verified artifacts, and the exact required handoff."
                    )
                elif attempts >= 2:
                    directive.append(
                        f"{next_stage} failed twice. Do not loop; return a partial "
                        "research report that names this graph node as the blocker."
                    )

        directive.append("</research_route>")
        prepared = request.override(
            system_message=append_to_system_message(
                request.system_message,
                "\n".join(directive),
            )
        )
        if forced_tool:
            # DashScope thinking models reject required/object ``tool_choice``.
            # Qwen-Agent's native pattern keeps tool choice automatic and makes
            # the intended capability the only visible function instead. This
            # preserves thinking-mode tool use while still removing every
            # competing action from the current graph node.
            selected = [
                tool for tool in request.tools if _tool_name(tool) == forced_tool
            ]
            if selected:
                prepared = prepared.override(tools=selected, tool_choice=None)
        return prepared

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._prepare_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._prepare_request(request))


__all__ = [
    "ResearchMode",
    "ResearchRouterMiddleware",
    "ResearchRoutingState",
    "SourceMode",
]

"""Qwen-routed research modes with deterministic workflow entry points.

The architecture combines two mature open-source patterns without replacing
JW's existing Deep Agents/LangGraph stack:

* Qwen-Agent's Router decides whether the main agent can answer directly or
  needs a specialist/tool path.
* LangChain's open_deep_research keeps the expensive research workflow as
  explicit graph stages instead of hoping the chat model improvises them.

The routing decision is made once per user turn by the configured Qwen model
and persisted in graph state. Deterministic enforcement is deliberately thin:
it guarantees the required specialist for a bounded professional intent, the
first evidence operation for other verified analysis, and the three specialist
graph nodes for an explicitly selected full research loop. Domain answers,
datasets, hypotheses, and experiment outcomes remain entirely model/tool
produced.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_config

from jw.research_integrity import (
    derive_external_evidence_policy,
    normalize_tool_outcome,
    record_task_route,
)
from jw.workspaces import workspace_root_from_config

from .utils import append_to_system_message, disable_thinking

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

ResearchMode = Literal["fast_answer", "verified_analysis", "full_research"]
SourceMode = Literal["none", "local", "external", "mixed"]
TaskIntent = Literal[
    "general",
    "hypothesis_generation",
    "hypothesis_comparison",
    "hypothesis_update",
]
RequiredSpecialist = Literal["none", "solar-hypothesis"]

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
        "task_intent": {
            "type": "string",
            "enum": [
                "general",
                "hypothesis_generation",
                "hypothesis_comparison",
                "hypothesis_update",
            ],
            "description": (
                "The bounded professional intent. Select a hypothesis intent when "
                "the user asks to generate, compare/review, or update scientific "
                "hypotheses; otherwise select general."
            ),
        },
        "required_specialist": {
            "type": "string",
            "enum": ["none", "solar-hypothesis"],
            "description": (
                "The specialist that must handle this bounded request. All three "
                "hypothesis intents require solar-hypothesis. Use none for general "
                "requests and for full_research, whose graph owns its own stages."
            ),
        },
        "reason": {
            "type": "string",
            "description": "One short operational reason for the selected mode.",
        },
    },
    "required": [
        "mode",
        "source_mode",
        "needs_computation",
        "task_intent",
        "required_specialist",
        "reason",
    ],
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

Professional intents:
- hypothesis_generation: generate or formulate scientific hypotheses.
- hypothesis_comparison: compare, rank, or review competing hypotheses.
- hypothesis_update: revise or update hypotheses using new evidence.
All three are bounded verified_analysis requests with
required_specialist=solar-hypothesis. They must not be promoted to full_research
unless the user explicitly asks for the complete planner -> hypothesis ->
experiment workflow. Use task_intent=general and required_specialist=none for
full_research because that route owns its fixed specialist graph.

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
_RECEIPT_SPECIALISTS = (*_FULL_RESEARCH_STAGES, "solar-data", "solar-evidence")
_LOCAL_DISCOVERY_TOOLS = ("ls", "glob")
_LOCAL_READ_TOOLS = ("read_file",)
_EXTERNAL_SEARCH_TOOLS = (
    "tavily_search",
    "lit_search",
    "research_planner_search_literature",
    "web_search",
)
_EXTERNAL_FETCH_TOOLS = ("lit_fetch", "fetch_evidence_source")
_EXTERNAL_EVIDENCE_TOOLS = ("submit_evidence_receipt",)
_CLAIM_VALIDATION_TOOLS = ("validate_research_claims",)
_FINALIZE_TOOLS = ("finalize_research_task",)
_DRAFT_COMPUTE_TOOLS = ("execute",)
_READ_ONLY_PLAIN_RESULT_TOOLS = {
    "ls",
    "glob",
    "read_file",
    "tavily_search",
    "web_search",
}
_F107_PATTERN = re.compile(r"(?:f\s*10[.]?7|10[.]7\s*cm|太阳射电流量)", re.IGNORECASE)
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
_NEGATED_FULL_FALLBACK = re.compile(
    r"(?:"
    r"(?:不要|无需|不需要|不必|不用|避免|跳过|禁止)"
    r"[^\n。.!?]{0,20}(?:完整研究|完整科研|科研闭环|端到端研究)"
    r"|(?:no|not|without|skip|avoid|don't|do not)"
    r"(?:\s+[\w'-]+){0,5}\s+(?:full|end-to-end)\s+research"
    r")",
    re.IGNORECASE,
)
_HYPOTHESIS_INTENT_PATTERNS: tuple[tuple[TaskIntent, re.Pattern[str]], ...] = (
    (
        "hypothesis_update",
        re.compile(
            r"(?:"
            r"(?:更新|修订|修改|完善|校准|重估|重排|迭代)[^\n。.!?]{0,24}(?:科学)?假设"
            r"|(?:科学)?假设[^\n。.!?]{0,24}(?:更新|修订|修改|完善|校准|重估|重排|迭代)"
            r"|(?:update|revise|refine|reassess|re-rank|iterate)"
            r"(?:\s+[\w-]+){0,5}\s+hypoth(?:esis|eses)"
            r"|hypoth(?:esis|eses)(?:\s+[\w-]+){0,5}\s+"
            r"(?:update|revision|refinement|reassessment)"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "hypothesis_comparison",
        re.compile(
            r"(?:"
            r"(?:比较|对比|排序|评估|评审|审查|筛选|复核)[^\n。.!?]{0,24}(?:科学)?假设"
            r"|(?:科学)?假设[^\n。.!?]{0,24}(?:比较|对比|排序|评估|评审|审查|筛选|复核)"
            r"|(?:compare|rank|evaluate|review|assess)"
            r"(?:\s+[\w-]+){0,5}\s+hypoth(?:esis|eses)"
            r"|hypoth(?:esis|eses)(?:\s+[\w-]+){0,5}\s+"
            r"(?:comparison|ranking|evaluation|review|assessment)"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "hypothesis_generation",
        re.compile(
            r"(?:"
            r"(?:生成|形成|提出|构建|制定|设计|列出|撰写)[^\n。.!?]{0,24}(?:科学)?假设"
            r"|(?:科学)?假设[^\n。.!?]{0,24}(?:生成|形成|提出|构建|制定|设计)"
            r"|(?:generate|formulate|propose|develop|create|draft)"
            r"(?:\s+[\w-]+){0,5}\s+hypoth(?:esis|eses)"
            r"|hypoth(?:esis|eses)\s+(?:generation|formulation|development)"
            r")",
            re.IGNORECASE,
        ),
    ),
)


class ResearchRoutingState(AgentState):
    """Graph state persisted for one routing decision per human turn."""

    research_route: NotRequired[dict[str, Any]]
    research_route_turn: NotRequired[str]


def _with_research_obligations(
    route: Mapping[str, Any],
    *,
    text: str,
) -> dict[str, Any]:
    """Derive enforceable obligations from a backward-compatible route."""

    enriched = dict(route)
    mode = enriched.get("mode")
    source_mode = enriched.get("source_mode")
    computation = enriched.get("needs_computation") is True
    local_data = source_mode in {"local", "mixed"} and computation
    audited = mode in {"verified_analysis", "full_research"} and computation
    adapter = "f107" if _F107_PATTERN.search(text) else "none"
    deliverable = "audited_report" if audited else (
        "draft" if mode == "verified_analysis" else "chat"
    )
    evidence_policy = derive_external_evidence_policy(
        text,
        enriched,
        required_domain_adapter=adapter,
        deliverable=deliverable,
    )
    enriched.update(
        {
            "requires_dataset_semantics": local_data,
            "requires_computation_receipt": audited,
            **evidence_policy,
            "required_domain_adapter": adapter,
            "deliverable": deliverable,
        }
    )
    return enriched


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
            str(part.get("text", "")) if isinstance(part, Mapping) else str(part)
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


def _hypothesis_intent(text: str) -> TaskIntent | None:
    """Return an explicit bounded hypothesis intent, if present."""

    for intent, pattern in _HYPOTHESIS_INTENT_PATTERNS:
        if pattern.search(text):
            return intent
    return None


def _explicit_full_research(text: str) -> bool:
    return bool(_FULL_FALLBACK.search(text) and not _NEGATED_FULL_FALLBACK.search(text))


def _specialize_hypothesis_route(
    route: Mapping[str, Any],
    *,
    text: str,
) -> dict[str, Any]:
    """Normalize bounded hypothesis work to its deterministic specialist route."""

    normalized = dict(route)
    if normalized.get("mode") == "full_research" and _explicit_full_research(text):
        normalized["task_intent"] = "general"
        normalized["required_specialist"] = "none"
        return normalized

    text_intent = _hypothesis_intent(text)
    routed_intent = normalized.get("task_intent")
    required_specialist = normalized.get("required_specialist")
    if text_intent is not None:
        routed_intent = text_intent
    elif (
        routed_intent
        not in {
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
        }
        and required_specialist != "solar-hypothesis"
    ):
        normalized["task_intent"] = "general"
        normalized["required_specialist"] = "none"
        return normalized

    if routed_intent not in {
        "hypothesis_generation",
        "hypothesis_comparison",
        "hypothesis_update",
    }:
        routed_intent = "hypothesis_generation"
    normalized.update(
        {
            "mode": "verified_analysis",
            "task_intent": routed_intent,
            "required_specialist": "solar-hypothesis",
        }
    )
    if normalized.get("source_mode") == "none":
        normalized["source_mode"] = "mixed"
    return normalized


def _fallback_route(text: str) -> dict[str, Any]:
    """Fail conservatively if the auxiliary routing call is unavailable."""

    if _explicit_full_research(text):
        return {
            "mode": "full_research",
            "source_mode": "mixed",
            "needs_computation": True,
            "task_intent": "general",
            "required_specialist": "none",
            "reason": "router unavailable; explicit full-research intent preserved",
        }
    hypothesis_intent = _hypothesis_intent(text)
    if hypothesis_intent is not None:
        return {
            "mode": "verified_analysis",
            "source_mode": "mixed",
            "needs_computation": False,
            "task_intent": hypothesis_intent,
            "required_specialist": "solar-hypothesis",
            "reason": "router unavailable; explicit hypothesis intent kept specialized",
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
            "task_intent": "general",
            "required_specialist": "none",
            "reason": "router unavailable; evidence-dependent request kept verified",
        }
    return {
        "mode": "fast_answer",
        "source_mode": "none",
        "needs_computation": False,
        "task_intent": "general",
        "required_specialist": "none",
        "reason": "router unavailable; no explicit evidence dependency detected",
    }


def _validated_route(value: object, *, fallback_text: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return _fallback_route(fallback_text)
    mode = value.get("mode")
    source_mode = value.get("source_mode")
    needs_computation = value.get("needs_computation")
    task_intent = value.get("task_intent", "general")
    required_specialist = value.get("required_specialist", "none")
    reason = value.get("reason")
    if (
        mode not in {"fast_answer", "verified_analysis", "full_research"}
        or source_mode not in {"none", "local", "external", "mixed"}
        or not isinstance(needs_computation, bool)
        or task_intent
        not in {
            "general",
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
        }
        or required_specialist not in {"none", "solar-hypothesis"}
        or not isinstance(reason, str)
    ):
        return _fallback_route(fallback_text)
    if mode == "full_research" and source_mode == "none":
        source_mode = "mixed"
    route = {
        "mode": mode,
        "source_mode": source_mode,
        "needs_computation": needs_computation,
        "task_intent": task_intent,
        "required_specialist": required_specialist,
        "reason": reason[:300],
    }
    return _specialize_hypothesis_route(route, text=fallback_text)


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
            content = (
                message.get("content")
                if isinstance(message, Mapping)
                else getattr(message, "content", "")
            )
            outcome = normalize_tool_outcome(
                content,
                transport_status=status,
                allow_plain_success=isinstance(name, str)
                and name in _READ_ONLY_PLAIN_RESULT_TOOLS,
            )
            if not outcome.succeeded:
                continue
            if isinstance(call_id, str):
                successful_ids.add(call_id)
            if isinstance(name, str) and name:
                successful_names.add(name)

    successful_names.update(
        call_names[call_id] for call_id in successful_ids if call_id in call_names
    )
    return calls, successful_names


def _successful_call_ids(messages: Sequence[object]) -> set[str]:
    successful: set[str] = set()
    for message in messages:
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        name = (
            message.get("name")
            if isinstance(message, Mapping)
            else getattr(message, "name", None)
        )
        status = (
            message.get("status")
            if isinstance(message, Mapping)
            else getattr(message, "status", None)
        )
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", "")
        )
        outcome = normalize_tool_outcome(
            content,
            transport_status=status,
            allow_plain_success=isinstance(name, str)
            and name in _READ_ONLY_PLAIN_RESULT_TOOLS,
        )
        call_id = (
            message.get("tool_call_id")
            if isinstance(message, Mapping)
            else getattr(message, "tool_call_id", None)
        )
        if outcome.succeeded and isinstance(call_id, str):
            successful.add(call_id)
    return successful


def _successful_specialists(
    calls: Sequence[Mapping[str, Any]],
    successful_names: set[str],
    messages: Sequence[object],
) -> set[str]:
    routed: set[str] = set()
    for message in messages:
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        status = (
            message.get("status")
            if isinstance(message, Mapping)
            else getattr(message, "status", None)
        )
        metadata = (
            message.get("additional_kwargs", {})
            if isinstance(message, Mapping)
            else getattr(message, "additional_kwargs", {})
        )
        specialist = (
            metadata.get("research_router_specialist")
            if isinstance(metadata, Mapping)
            else None
        )
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", "")
        )
        outcome = normalize_tool_outcome(content, transport_status=status)
        receipt_refs = (
            metadata.get("receipt_refs", ())
            if isinstance(metadata, Mapping)
            else ()
        )
        if (
            (outcome.succeeded or receipt_refs)
            and (outcome.receipt_refs or receipt_refs)
            and specialist in _RECEIPT_SPECIALISTS
        ):
            routed.add(str(specialist))
    if "task" not in successful_names:
        return routed
    successful_call_ids: set[str] = set()
    for message in messages:
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        status = (
            message.get("status")
            if isinstance(message, Mapping)
            else getattr(message, "status", None)
        )
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", "")
        )
        outcome = normalize_tool_outcome(content, transport_status=status)
        if not outcome.has_verified_receipt:
            continue
        call_id = (
            message.get("tool_call_id")
            if isinstance(message, Mapping)
            else getattr(message, "tool_call_id", None)
        )
        if isinstance(call_id, str):
            successful_call_ids.add(call_id)
    routed.update(
        {
            str(call.get("args", {}).get("subagent_type"))
            for call in calls
            if call.get("name") == "task"
            and call.get("id") in successful_call_ids
            and call.get("args", {}).get("subagent_type") in _RECEIPT_SPECIALISTS
        }
    )
    return routed


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


def _routed_specialist_attempt_count(
    messages: Sequence[object],
    specialist: str,
) -> int:
    count = 0
    for message in messages:
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        metadata = (
            message.get("additional_kwargs", {})
            if isinstance(message, Mapping)
            else getattr(message, "additional_kwargs", {})
        )
        if (
            isinstance(metadata, Mapping)
            and metadata.get("research_router_specialist") == specialist
        ):
            count += 1
    return count


def _is_bounded_hypothesis_route(state: object) -> bool:
    if not isinstance(state, Mapping):
        return False
    route = state.get("research_route")
    return bool(
        isinstance(route, Mapping)
        and route.get("mode") == "verified_analysis"
        and route.get("task_intent")
        in {
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
        }
        and route.get("required_specialist") == "solar-hypothesis"
    )


def _state_messages(state: object) -> list[object]:
    if not isinstance(state, Mapping):
        return []
    messages = state.get("messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        return list(messages)
    return []


def _latest_ai_task_call_ids(state: object) -> list[str]:
    """Return task ids from the current model turn, preserving model order."""

    messages = _state_messages(state)
    latest = _latest_human(messages)
    start = latest[0] + 1 if latest else 0
    for message in reversed(messages[start:]):
        if not (
            isinstance(message, AIMessage)
            or _message_role(message) in {"ai", "assistant"}
        ):
            continue
        raw_calls = (
            message.get("tool_calls", [])
            if isinstance(message, Mapping)
            else getattr(message, "tool_calls", [])
        )
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
            return []
        return [
            str(call.get("id"))
            for call in raw_calls
            if isinstance(call, Mapping)
            and call.get("name") == "task"
            and isinstance(call.get("id"), str)
        ]
    return []


def _latest_user_request(state: object) -> str:
    messages = _state_messages(state)
    latest = _latest_human(messages)
    return _message_text(latest[1]) if latest is not None else ""


def _direct_hypothesis_task_request(
    request: ToolCallRequest,
) -> tuple[ToolCallRequest | None, ToolMessage | None]:
    """Enforce one direct, user-bound solar-hypothesis delegation."""

    if not _is_bounded_hypothesis_route(request.state):
        return request, None
    call = request.tool_call
    call_id = str(call.get("id") or "hypothesis-routing-blocked")
    call_name = str(call.get("name") or "unknown")
    if call_name != "task":
        return None, ToolMessage(
            content=(
                "[HYPOTHESIS ROUTING BLOCKED] This bounded hypothesis request must "
                "enter through one direct solar-hypothesis delegation. The parent "
                f"agent may not call {call_name!r}, pre-read Wiki or memory files, "
                "or assemble an evidence summary first. Call task now; the specialist "
                "owns request binding, Wiki discovery, evidence binding, and draft "
                "persistence."
            ),
            tool_call_id=call_id,
            name=call_name,
            status="error",
        )

    task_ids = _latest_ai_task_call_ids(request.state)
    if len(task_ids) > 1 and call_id != task_ids[0]:
        return None, ToolMessage(
            content=(
                "[HYPOTHESIS ROUTING BLOCKED] A bounded hypothesis request permits "
                "exactly one direct task delegation. The first task call is already "
                "being routed to solar-hypothesis; generic Wiki-reader or compensating "
                "subagents are not allowed."
            ),
            tool_call_id=call_id,
            name="task",
            status="error",
        )

    user_request = _latest_user_request(request.state)
    description = (
        "Handle this as the bounded solar-hypothesis specialist. Use only the "
        "verbatim user request below as the task contract; parent prose is not "
        "evidence. Execute this order:\n"
        "1. Call scientific_hypothesis_bind_request immediately, before discovery.\n"
        "2. Call kb_query for this question. Select only the smallest relevant Wiki "
        "bundle: target 5 entries, hard maximum 7. For each selected entry, call "
        "kb_read and then scientific_hypothesis_bind_wiki_evidence immediately. "
        "After three successful bindings cover one mechanism, one method/data "
        "constraint, and the proxy/measurement null, persist H0 or the first "
        "complete candidate before reading any remaining optional entries. Stop "
        "Wiki browsing once the mechanism, scope, data, and test constraints needed "
        "for the candidates are covered.\n"
        "3. Bind non-Wiki material only when it is a traceable inspected artifact; "
        "scenario premises in the request are assumptions, not empirical support.\n"
        "4. Persist the first complete candidate as soon as it is ready with "
        "scientific_hypothesis_update_draft, then upsert the remaining mechanically "
        "distinct candidates. Prefer a smaller persisted portfolio over a larger "
        "prose-only answer if budget is tight. Confidence must be exactly high, "
        "medium, or low; Wiki grounding alone cannot justify high confidence.\n"
        "5. Call scientific_hypothesis_get_draft. Return only a concise rendering "
        "of that persisted draft, including its real draft_sha256, candidate count, "
        "bound Wiki entry ids, warnings, and one portfolio-level most discriminating "
        "next test. Never claim reads or bindings without tool receipts.\n"
        "Resolve avoidable draft warnings. Do not rely on a parent-written Wiki "
        "summary or unbound 'verified facts'. Do not publish or freeze unless the "
        "user explicitly requests it.\n\n"
        "<latest_user_request>\n"
        f"{user_request}\n"
        "</latest_user_request>"
    )
    args = call.get("args")
    rewritten_args = dict(args) if isinstance(args, Mapping) else {}
    rewritten_args["subagent_type"] = "solar-hypothesis"
    rewritten_args["description"] = description
    rewritten_call = {**call, "args": rewritten_args}
    return request.override(tool_call=rewritten_call), None


def _latest_specialist_result(
    messages: Sequence[object],
    specialist: str,
) -> str | None:
    calls, _ = _calls_since_latest_human(messages)
    specialist_call_ids = {
        str(call.get("id"))
        for call in calls
        if call.get("name") == "task"
        and call.get("args", {}).get("subagent_type") == specialist
        and call.get("id")
    }
    for message in reversed(messages):
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        status = (
            message.get("status")
            if isinstance(message, Mapping)
            else getattr(message, "status", None)
        )
        call_id = (
            message.get("tool_call_id")
            if isinstance(message, Mapping)
            else getattr(message, "tool_call_id", None)
        )
        metadata = (
            message.get("additional_kwargs", {})
            if isinstance(message, Mapping)
            else getattr(message, "additional_kwargs", {})
        )
        routed_specialist = (
            metadata.get("research_router_specialist")
            if isinstance(metadata, Mapping)
            else None
        )
        if status == "error" or (
            call_id not in specialist_call_ids and routed_specialist != specialist
        ):
            continue
        outcome = normalize_tool_outcome(
            (
                message.get("content")
                if isinstance(message, Mapping)
                else getattr(message, "content", "")
            ),
            transport_status=status,
        )
        receipt_refs = (
            metadata.get("receipt_refs", ())
            if isinstance(metadata, Mapping)
            else ()
        )
        if not outcome.has_verified_receipt and not receipt_refs:
            continue
        content = _message_text(message)
        if content.strip():
            return content
    return None


def _mark_routed_specialist_result(result: object) -> object:
    if not isinstance(result, ToolMessage):
        return result
    metadata = dict(result.additional_kwargs)
    metadata["research_router_specialist"] = "solar-hypothesis"
    return result.model_copy(update={"additional_kwargs": metadata})


def _persisted_hypothesis_draft_status(config: object) -> tuple[bool, str] | None:
    """Return draft receipt status, or None when no task workspace is bound."""

    try:
        root = workspace_root_from_config(config)
    except RuntimeError:
        return None
    state_path = root / "work" / "scientific_hypothesis_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, f"draft state is missing or unreadable at {state_path}: {exc}"
    if not isinstance(payload, Mapping):
        return False, f"draft state at {state_path} is not a JSON object"
    draft = payload.get("latest_draft")
    draft_sha = payload.get("latest_draft_sha256")
    candidates = draft.get("candidates") if isinstance(draft, Mapping) else None
    if not isinstance(draft_sha, str) or not draft_sha.strip():
        return False, "latest_draft_sha256 is missing"
    if not isinstance(candidates, list) or not candidates:
        return False, "latest_draft has no persisted candidates"
    return True, str(state_path)


def _require_persisted_hypothesis_draft(
    result: object,
    config: object,
) -> object:
    if not isinstance(result, ToolMessage) or result.status == "error":
        return result
    receipt = _persisted_hypothesis_draft_status(config)
    if receipt is None:
        return result
    if receipt[0]:
        metadata = dict(result.additional_kwargs)
        metadata["research_router_specialist"] = "solar-hypothesis"
        metadata["receipt_refs"] = [receipt[1]]
        return result.model_copy(update={"additional_kwargs": metadata})
    metadata = dict(result.additional_kwargs)
    metadata["research_router_specialist"] = "solar-hypothesis"
    return result.model_copy(
        update={
            "content": (
                "[HYPOTHESIS DRAFT INCOMPLETE] solar-hypothesis returned prose but "
                f"did not leave a usable persisted draft: {receipt[1]}. Retry the "
                "same specialist once. It must recover the bound request, call "
                "scientific_hypothesis_update_draft, then confirm the result with "
                "scientific_hypothesis_get_draft. Do not let the parent recreate the "
                "candidate portfolio."
            ),
            "status": "error",
            "additional_kwargs": metadata,
        }
    )


def _passthrough_hypothesis_result(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    """Force the bounded entry node and preserve its completed result verbatim."""

    if not _is_bounded_hypothesis_route(request.state):
        return response
    content = _latest_specialist_result(
        list(request.messages),
        "solar-hypothesis",
    )
    if content is not None:
        return ModelResponse(
            result=[AIMessage(content=content)],
            structured_response=response.structured_response,
        )

    calls, _ = _calls_since_latest_human(list(request.messages))
    attempts = _attempt_count(
        calls,
        "task",
        specialist="solar-hypothesis",
    )
    attempts = max(
        attempts,
        _routed_specialist_attempt_count(
            list(request.messages),
            "solar-hypothesis",
        ),
    )
    if attempts >= 2 or _available_tool(request.tools, ("task",)) is None:
        return response

    # Tool filtering is advisory in several upstream middleware layers: a
    # selector or provider can still return a stale/non-visible tool call.
    # Normalize the actual model result as well, so bounded hypothesis turns
    # reach the specialist in one graph step rather than spending a round on
    # blocked parent-side reads.
    if (
        len(response.result) == 1
        and isinstance(response.result[0], AIMessage)
        and len(response.result[0].tool_calls) == 1
        and response.result[0].tool_calls[0].get("name") == "task"
    ):
        return response

    request_digest = hashlib.sha256(
        _latest_user_request(request.state).encode("utf-8")
    ).hexdigest()[:20]
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "solar-hypothesis",
                            "description": "bounded hypothesis route",
                        },
                        "id": f"call_hypothesis_{request_digest}",
                    }
                ],
            )
        ],
        structured_response=response.structured_response,
    )


class ResearchRouterMiddleware(AgentMiddleware[ResearchRoutingState, Any, Any]):
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
        route = _with_research_obligations(
            self._route_sync(_message_text(latest[1])),
            text=_message_text(latest[1]),
        )
        self._persist_obligations(route)
        return {
            "research_route": route,
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
        route = _with_research_obligations(
            await self._route_async(_message_text(latest[1])),
            text=_message_text(latest[1]),
        )
        await asyncio.to_thread(self._persist_obligations, route)
        return {
            "research_route": route,
            "research_route_turn": key,
        }

    @staticmethod
    def _persist_obligations(route: Mapping[str, Any]) -> None:
        try:
            config = get_config()
            root = workspace_root_from_config(
                config if isinstance(config, Mapping) else None
            )
            record_task_route(root, route)
        except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
            # Direct middleware tests and unthreaded CLI calls have no bound
            # task workspace. The graph state still carries the route.
            return

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        route = request.state.get("research_route")
        if not isinstance(route, Mapping):
            return request
        mode = route.get("mode")
        source_mode = route.get("source_mode")
        needs_computation = route.get("needs_computation") is True
        task_intent = route.get("task_intent", "general")
        required_specialist = route.get("required_specialist", "none")
        requires_dataset_semantics = (
            route.get("requires_dataset_semantics") is True
        )
        requires_computation_receipt = (
            route.get("requires_computation_receipt") is True
        )
        requires_external_evidence = (
            route.get("requires_external_evidence") is True
        )
        required_domain_adapter = route.get("required_domain_adapter", "none")
        deliverable = route.get("deliverable", "chat")
        messages = list(request.messages)
        calls, successful_names = _calls_since_latest_human(messages)
        successful_call_ids = _successful_call_ids(messages)

        directive = [
            "<research_route>",
            f"mode={mode}; source_mode={source_mode}; "
            f"needs_computation={str(needs_computation).lower()}; "
            f"task_intent={task_intent}; "
            f"required_specialist={required_specialist}; "
            f"requires_dataset_semantics={str(requires_dataset_semantics).lower()}; "
            f"requires_computation_receipt={str(requires_computation_receipt).lower()}; "
            f"requires_external_evidence={str(requires_external_evidence).lower()}; "
            f"required_domain_adapter={required_domain_adapter}; "
            f"deliverable={deliverable}",
        ]
        forced_tool: str | None = None
        suppress_tools = False

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
            if required_specialist == "solar-hypothesis":
                completed = _successful_specialists(
                    calls,
                    successful_names,
                    messages,
                )
                attempts = _attempt_count(
                    calls,
                    "task",
                    specialist="solar-hypothesis",
                )
                attempts = max(
                    attempts,
                    _routed_specialist_attempt_count(
                        messages,
                        "solar-hypothesis",
                    ),
                )
                task_available = _available_tool(request.tools, ("task",)) is not None
                if "solar-hypothesis" in completed:
                    suppress_tools = True
                    directive.append(
                        "The required solar-hypothesis delegation completed. Its tool "
                        "result is the complete bounded answer and will be passed "
                        "through verbatim. Do not summarize, translate, reformat, "
                        "correct, shorten, or expand it. Do not append the full-research "
                        "planner or experiment chain, and do not invoke a compensating "
                        "specialist."
                    )
                elif attempts >= 2:
                    directive.append(
                        "solar-hypothesis failed twice. Do not loop or silently "
                        "substitute another specialist; report this exact delegation "
                        "failure as the blocker."
                    )
                    suppress_tools = True
                elif task_available:
                    forced_tool = "task"
                    directive.append(
                        "This bounded hypothesis request must be delegated directly. "
                        "Call task now with subagent_type='solar-hypothesis'. Pass only "
                        "the latest user request and immutable input paths explicitly "
                        "provided by the user. Do not pre-read Wiki, memory, or evidence "
                        "files; the specialist owns discovery and binding. Do not call "
                        "solar-planner first and do not expand this into full_research."
                    )
                else:
                    directive.append(
                        "ROUTING BLOCKER: required specialist solar-hypothesis cannot "
                        "be delegated because the actual tool list does not contain "
                        "'task'. Do not silently continue, answer from memory, or "
                        "substitute another tool/specialist. Report this exact missing-"
                        "tool blocker to the user."
                    )
                    suppress_tools = True
            else:
                local_required = source_mode in {"local", "mixed"}
                external_required = requires_external_evidence
                local_seen = bool(
                    successful_names & {*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS}
                )
                read_seen = bool(successful_names & set(_LOCAL_READ_TOOLS))
                successful_calls = [
                    call for call in calls if call.get("id") in successful_call_ids
                ]
                bound_evidence_claims = {
                    str(call.get("args", {}).get("claim_id"))
                    for call in successful_calls
                    if call.get("name") in _EXTERNAL_EVIDENCE_TOOLS
                    and call.get("args", {}).get("claim_id")
                }
                required_evidence_claims = (
                    {
                        "f107_product_definition",
                        "f107_observatory_history",
                        "f107_1980_discontinuity",
                    }
                    if required_domain_adapter == "f107"
                    and requires_external_evidence
                    else set()
                )
                evidence_count = len(bound_evidence_claims)
                search_count = sum(
                    call.get("name") in _EXTERNAL_SEARCH_TOOLS
                    for call in successful_calls
                )
                fetch_count = sum(
                    call.get("name") in _EXTERNAL_FETCH_TOOLS
                    for call in successful_calls
                )
                search_seen = search_count > evidence_count
                fetch_seen = fetch_count > evidence_count
                external_seen = (
                    required_evidence_claims.issubset(bound_evidence_claims)
                    if required_evidence_claims
                    else evidence_count >= 1
                )
                compute_seen = bool(successful_names & set(_FINALIZE_TOOLS))
                claims_seen = bool(
                    successful_names & set(_CLAIM_VALIDATION_TOOLS)
                )

                if local_required and not local_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        (*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS),
                    )
                elif local_required and not read_seen:
                    forced_tool = _available_tool(request.tools, _LOCAL_READ_TOOLS)
                elif external_required and not search_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        _EXTERNAL_SEARCH_TOOLS,
                    )
                elif external_required and not fetch_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        _EXTERNAL_FETCH_TOOLS,
                    )
                    directive.append(
                        "A search result is metadata, not evidence. Fetch and read "
                        "the selected primary source before making factual claims."
                    )
                elif external_required and not external_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        _EXTERNAL_EVIDENCE_TOOLS,
                    )
                    directive.append(
                        "Call submit_evidence_receipt to submit an exact fetched source "
                        "span as pending evidence. Search metadata alone does not satisfy "
                        "the evidence obligation. The still-required claim ids are: "
                        + (
                            ", ".join(
                                sorted(
                                    required_evidence_claims
                                    - bound_evidence_claims
                                )
                            )
                            or "one claim id matching the report"
                        )
                        + "."
                    )
                elif external_required:
                    completed = _successful_specialists(
                        calls, successful_names, messages
                    )
                    if "solar-evidence" not in completed:
                        forced_tool = _available_tool(request.tools, ("task",))
                        directive.append(
                            "Delegate all pending evidence submissions to "
                            "solar-evidence for independent review. The reviewer "
                            "must return accepted/rejected v2 review receipt refs; "
                            "the submitting agent cannot approve its own evidence."
                        )
                elif requires_dataset_semantics:
                    completed = _successful_specialists(
                        calls, successful_names, messages
                    )
                    if "solar-data" not in completed:
                        forced_tool = _available_tool(request.tools, ("task",))
                        directive.append(
                            "Delegate to solar-data with subagent_type='solar-data'. "
                            "It must return a structured success outcome containing "
                            "a verified DatasetSemanticManifest receipt; prose or a "
                            "work-file path does not complete this stage."
                        )
                if (
                    forced_tool is None
                    and requires_computation_receipt
                ):
                    completed = _successful_specialists(
                        calls, successful_names, messages
                    )
                    if "solar-experiment" not in completed:
                        forced_tool = _available_tool(request.tools, ("task",))
                        directive.append(
                            "Delegate the bounded audited computation to "
                            "solar-experiment. Generic execute may be used only for "
                            "draft exploration and cannot satisfy this obligation. "
                            "The specialist must return a structured success outcome "
                            "with a finalized experiment receipt."
                        )
                    elif not claims_seen:
                        forced_tool = _available_tool(
                            request.tools,
                            _CLAIM_VALIDATION_TOOLS,
                        )
                        directive.append(
                            "Validate every reader-facing quantitative, historical, "
                            "and interpretive claim. Quantitative claims need a "
                            "measurement id; historical claims need claim-matched "
                            "evidence receipts; interpretations need supports and "
                            "limitations."
                        )
                    elif not compute_seen:
                        forced_tool = _available_tool(
                            request.tools,
                            _FINALIZE_TOOLS,
                        )
                        directive.append(
                            "Finalize the parent research task using every dataset, "
                            "evidence, and experiment receipt returned this turn. "
                            "The finalizer will downgrade the task if outputs/report.md "
                            "or a required receipt is missing."
                        )
                elif (
                    forced_tool is None
                    and needs_computation
                    and not requires_computation_receipt
                    and not (
                        successful_names & set(_DRAFT_COMPUTE_TOOLS)
                    )
                ):
                    # Compatibility for callers that construct a legacy route
                    # directly. Routes produced by before_agent always carry
                    # requires_computation_receipt for verified computation.
                    forced_tool = _available_tool(
                        request.tools,
                        _DRAFT_COMPUTE_TOOLS,
                    )

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
        elif suppress_tools:
            prepared = prepared.override(tools=[], tool_choice=None)
        return prepared

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        return _passthrough_hypothesis_result(
            request,
            handler(prepared),
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        return _passthrough_hypothesis_result(
            request,
            await handler(prepared),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        rewritten, blocked = _direct_hypothesis_task_request(request)
        if blocked is not None:
            return blocked
        result = handler(rewritten)
        if rewritten is not request:
            result = _mark_routed_specialist_result(result)
            config = getattr(request.runtime, "config", None)
            result = _require_persisted_hypothesis_draft(result, config)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        rewritten, blocked = _direct_hypothesis_task_request(request)
        if blocked is not None:
            return blocked
        result = await handler(rewritten)
        if rewritten is not request:
            result = _mark_routed_specialist_result(result)
            config = getattr(request.runtime, "config", None)
            result = await asyncio.to_thread(
                _require_persisted_hypothesis_draft,
                result,
                config,
            )
        return result


__all__ = [
    "RequiredSpecialist",
    "ResearchMode",
    "ResearchRouterMiddleware",
    "ResearchRoutingState",
    "SourceMode",
    "TaskIntent",
]

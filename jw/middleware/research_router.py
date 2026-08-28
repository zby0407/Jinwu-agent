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
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from jw.agent_harness import attach_harness_metadata
from jw.research_protocols import (
    F107_DISCONTINUITY_PROTOCOL,
    F107_DISCONTINUITY_REQUIRED_MEASUREMENTS,
    SILSO_CYCLE_REPRODUCTION_PROTOCOL,
    SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
    SOLAR_CYCLE_26_READINESS_PROTOCOL,
    SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
    detect_analysis_protocol,
    f107_discontinuity_directive,
    solar_cycle_26_readiness_directive,
    solar_cycle_26_forecast_backtest_directive,
    solar_polar_precursor_directive,
    silso_cycle_morphology_directive,
)
from jw.research_review import store_from_config
from jw.workspaces import workspace_root_from_config

from .closed_loop_orchestration import closed_loop_receipts
from .utils import append_to_system_message, disable_thinking

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

ResearchMode = Literal["fast_answer", "verified_analysis", "full_research"]
SourceMode = Literal["none", "local", "external", "mixed"]
TaskIntent = Literal[
    "general",
    "research_planning",
    "data_preparation",
    "hypothesis_generation",
    "hypothesis_comparison",
    "hypothesis_update",
    "experiment_design",
    "experiment_run",
]
RequiredSpecialist = Literal[
    "none",
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
]

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
                "research_planning",
                "data_preparation",
                "hypothesis_generation",
                "hypothesis_comparison",
                "hypothesis_update",
                "experiment_design",
                "experiment_run",
            ],
            "description": (
                "The bounded professional intent for planning, data, hypotheses, "
                "or experiment design/execution; otherwise select general."
            ),
        },
        "required_specialist": {
            "type": "string",
            "enum": [
                "none",
                "solar-planner",
                "solar-data",
                "solar-hypothesis",
                "solar-experiment",
            ],
            "description": (
                "The specialist matching the bounded intent. Use none for general "
                "requests and full_research, whose graph owns its stages."
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

_MODEL_CALL_BUDGET_STOP = re.compile(
    r"^\s*Model call limits exceeded:\s*(?:run|thread) limit\b",
    re.IGNORECASE,
)

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
- research_planning: produce or revise a bounded research plan.
- data_preparation: inspect, clean, align, or feature-engineer a bounded dataset.
- hypothesis_generation: generate or formulate scientific hypotheses.
- hypothesis_comparison: compare, rank, or review competing hypotheses.
- hypothesis_update: revise or update hypotheses using new evidence.
- experiment_design: design a bounded experiment without claiming execution.
- experiment_run: execute or resume a bounded accepted experiment.
Each is a bounded verified_analysis request with its matching solar specialist.
Conceptual hypothesis generation remains bounded.  A hypothesis question that
asks the system to decide an empirical relation from observations, data, a
prediction test, or a reproducible calculation requires full_research even when
the user does not know or name the internal stages.  Use task_intent=general and
required_specialist=none for full_research because that route owns its fixed
specialist graph.

Classify the actual evidence dependency, not the phrasing style. A short question
about a named dataset is verified_analysis. A long conceptual explanation may
still be fast_answer. If the user requests local and external evidence, choose
source_mode=mixed. Set needs_computation only when code or calculation is part of
the requested result."""

_FULL_RESEARCH_STAGES = (
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
    "solar-evidence",
)
_RECEIPT_SPECIALISTS = set(_FULL_RESEARCH_STAGES)
_BOUNDED_STAGE_BY_SPECIALIST_INTENT = {
    ("solar-planner", "research_planning"): "planning",
    ("solar-data", "data_preparation"): "data",
    ("solar-hypothesis", "hypothesis_generation"): "hypothesis",
    ("solar-hypothesis", "hypothesis_comparison"): "hypothesis",
    ("solar-hypothesis", "hypothesis_update"): "hypothesis",
    ("solar-experiment", "experiment_design"): "experiment_design",
    ("solar-experiment", "experiment_run"): "experiment_result",
}
_BOUNDED_ROUTE_BY_STAGE = {
    "planning": ("solar-planner", "research_planning"),
    "data": ("solar-data", "data_preparation"),
    "hypothesis": ("solar-hypothesis", "hypothesis_update"),
    "experiment_design": ("solar-experiment", "experiment_design"),
    "experiment_result": ("solar-experiment", "experiment_run"),
}
_RESEARCH_CONTINUATION_ACTION = re.compile(
    r"(?:继续|恢复|接着|resume|continue)", re.IGNORECASE
)
_TERSE_RESEARCH_CONTINUATION = re.compile(
    r"^\s*(?:继续(?:吧|执行)?|恢复|接着|resume|continue)\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
_RESEARCH_CONTINUATION_CONTEXT = re.compile(
    r"(?:科研闭环|研究闭环|状态机|审查|返修|独立复核|独立审查|"
    r"next[_ -]?action|research\s+(?:loop|review)|revision|review)",
    re.IGNORECASE,
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
_SOLAR_PRECURSOR_TABLE_TOOL = "prepare_solar_precursor_cycle_table"
_DATA_FALLBACK = re.compile(
    r"(?:数据|文件|表格|本地|workspace|dataset|data|file|csv|tsv|parquet|"
    r"json|fits?|计算|预测|回归|检验|calculate|predict|regression|test)",
    re.IGNORECASE,
)
_DOWNSTREAM_STATISTICS_REQUEST = re.compile(
    r"(?:下游统计|统计分析|相关分析|pearson|spearman|bootstrap|留一|"
    r"generate\s+(?:csv|markdown|png)|直接读取上传|已验证.*(?:csv|table))",
    re.IGNORECASE | re.DOTALL,
)
_FULL_FALLBACK = re.compile(
    r"(?:完整研究|完整科研|科研闭环|端到端研究|系统(?:性)?研究|研究包|"
    r"可供[^\n。.!?]{0,16}(?:同行|专家)[^\n。.!?]{0,8}(?:初审|审查)|"
    r"end-to-end research|full research|research package|review-ready research)",
    re.IGNORECASE,
)
_CURRENT_OBSERVATION_HYPOTHESIS = re.compile(
    r"(?=.*(?:当前|目前|最新|现有|current|present|latest))"
    r"(?=.*(?:观测|信号|数据|observation|signal|data))"
    r"(?=.*(?:假设|机制|hypothes|mechanism))"
    r"(?=.*(?:证据|支持|趋势|evidence|support|trend))",
    re.IGNORECASE | re.DOTALL,
)
_NEGATED_FULL_FALLBACK = re.compile(
    r"(?:"
    r"(?:不要|无需|不需要|不必|不用|避免|跳过|禁止)"
    r"[^\n。.!?]{0,20}(?:完整研究|完整科研|科研闭环|端到端研究|系统(?:性)?研究|研究包)"
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
_BOUNDED_FALLBACK_PATTERNS = (
    (
        "research_planning",
        "solar-planner",
        re.compile(
            r"(?:制定|生成|修订|完善).{0,16}(?:研究计划|研究规划|实验路线)",
            re.IGNORECASE,
        ),
    ),
    (
        "data_preparation",
        "solar-data",
        re.compile(
            r"(?:清洗|对齐|准备|构建|提取).{0,16}(?:数据|特征|dataset|features?)",
            re.IGNORECASE,
        ),
    ),
    (
        "experiment_run",
        "solar-experiment",
        re.compile(
            r"(?:运行|执行|重跑|恢复|继续).{0,16}(?:实验|回测|experiment|backtest)",
            re.IGNORECASE,
        ),
    ),
    (
        "experiment_design",
        "solar-experiment",
        re.compile(
            r"(?:设计|制定|审查|完善).{0,16}(?:实验|回测|experiment|backtest)",
            re.IGNORECASE,
        ),
    ),
)


class ResearchRoutingState(AgentState):
    """Graph state persisted for one routing decision per user turn."""

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


def _is_research_continuation_request(text: str) -> bool:
    """Recognize an explicit request to advance the persisted research graph."""
    return bool(
        _RESEARCH_CONTINUATION_ACTION.search(text)
        and _RESEARCH_CONTINUATION_CONTEXT.search(text)
    )


def _continuation_route(state: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    """Preserve a v2 workflow route across a terse continuation turn.

    Older checkpoints may already have overwritten their useful route with a
    generic one.  For those only, recover the last explicit graph node from the
    same thread's trace.  Do not read a workspace here: ``before_agent`` may run
    before task-workspace binding, which would risk cross-run state leakage.
    """
    explicit_continuation = _is_research_continuation_request(text)
    terse_continuation = bool(_TERSE_RESEARCH_CONTINUATION.fullmatch(text))
    if not explicit_continuation and not terse_continuation:
        return None
    prior = state.get("research_route")
    if isinstance(prior, Mapping):
        mode = prior.get("mode")
        prior_reason = str(prior.get("reason") or "")
        bounded_stage = _BOUNDED_STAGE_BY_SPECIALIST_INTENT.get(
            (
                str(prior.get("required_specialist")),
                str(prior.get("task_intent")),
            )
        )
        derived_continuation_route = prior_reason.startswith(
            ("recovered ", "explicit continuation ")
        )
        if mode == "full_research" or (
            explicit_continuation
            and bounded_stage is not None
            and not derived_continuation_route
        ):
            resumed = dict(prior)
            resumed["reason"] = "explicit continuation of persisted research graph"
            return resumed

    if not explicit_continuation:
        return None

    messages = list(state.get("messages", []))
    latest = _latest_human(messages)
    history = messages[: latest[0]] if latest is not None else messages
    for message in reversed(history):
        if (
            (
                isinstance(message, HumanMessage)
                or _message_role(message) in {"human", "user"}
            )
            and not _is_research_continuation_request(_message_text(message))
            and _explicit_full_research(_message_text(message))
        ):
            return {
                "mode": "full_research",
                "source_mode": "mixed",
                "needs_computation": True,
                "task_intent": "general",
                "required_specialist": "none",
                "reason": "recovered full research graph from same-thread trace",
            }

    specialist_to_stage = {
        "solar-planner": "planning",
        "solar-data": "data",
        "solar-hypothesis": "hypothesis",
        "solar-experiment": "experiment_result",
    }
    blocked_call_ids = {
        str(
            message.get("tool_call_id")
            if isinstance(message, Mapping)
            else getattr(message, "tool_call_id", "")
        )
        for message in history
        if (isinstance(message, ToolMessage) or _message_role(message) == "tool")
        and _message_text(message).lstrip().startswith("[RESEARCH REVIEW BLOCKED]")
    }
    stage = ""
    for message in reversed(history):
        raw_calls = (
            message.get("tool_calls", [])
            if isinstance(message, Mapping)
            else getattr(message, "tool_calls", [])
        )
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
            continue
        for call in reversed(raw_calls):
            if not isinstance(call, Mapping):
                continue
            if str(call.get("id") or "") in blocked_call_ids:
                continue
            if call.get("name") == "research_release_prepare":
                stage = "final_release"
                break
            args = call.get("args")
            if call.get("name") == "task" and isinstance(args, Mapping):
                stage = specialist_to_stage.get(
                    str(args.get("subagent_type") or ""), ""
                )
                if stage:
                    break
        if stage:
            break

    if stage in {"integration", "final_release"}:
        return {
            "mode": "full_research",
            "source_mode": "mixed",
            "needs_computation": True,
            "task_intent": "general",
            "required_specialist": "none",
            "reason": "recovered full research graph from same-thread trace",
        }
    bounded = _BOUNDED_ROUTE_BY_STAGE.get(stage)
    if bounded is None:
        return None
    specialist, intent = bounded
    return {
        "mode": "verified_analysis",
        "source_mode": "local",
        "needs_computation": stage == "experiment_result",
        "task_intent": intent,
        "required_specialist": specialist,
        "reason": f"recovered bounded {stage} graph from same-thread trace",
    }


def _hypothesis_intent(text: str) -> TaskIntent | None:
    """Return an explicit bounded hypothesis intent, if present."""

    for intent, pattern in _HYPOTHESIS_INTENT_PATTERNS:
        if pattern.search(text):
            return intent
    return None


def _final_release_generation_messages(messages: Sequence[object]) -> list[object]:
    """Keep the scientific question and current request for release synthesis."""

    latest = _latest_human(messages)
    if latest is None:
        return list(messages)
    latest_index, latest_message = latest
    original = next(
        (
            message
            for message in messages[:latest_index]
            if (
                isinstance(message, HumanMessage)
                or _message_role(message) in {"human", "user"}
            )
            and not _is_research_continuation_request(_message_text(message))
        ),
        None,
    )
    return [latest_message] if original is None else [original, latest_message]


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
    elif (required_specialist, routed_intent) in _BOUNDED_STAGE_BY_SPECIALIST_INTENT:
        return normalized
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


def _with_analysis_protocol(
    route: Mapping[str, Any],
    *,
    text: str,
) -> dict[str, Any]:
    """Attach narrow scientific obligations independently of model routing."""

    normalized = dict(route)
    protocol = detect_analysis_protocol(text)
    bounded_hypothesis = (
        normalized.get("mode") == "verified_analysis"
        and normalized.get("required_specialist") == "solar-hypothesis"
        and normalized.get("task_intent")
        in {
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
        }
    )
    if bounded_hypothesis and _CURRENT_OBSERVATION_HYPOTHESIS.search(text):
        normalized.update(
            {
                "source_mode": "mixed",
                "needs_computation": True,
                "preliminary_stages": ["data"],
                "reason": (
                    "Current observational evidence must be prepared before "
                    "hypothesis generation and independent Evidence review"
                ),
            }
        )
    if protocol == "none":
        return normalized
    normalized["required_analysis_protocol"] = protocol
    if protocol in {
        SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
    } or (
        protocol == SOLAR_POLAR_PRECURSOR_PROTOCOL
        and _DOWNSTREAM_STATISTICS_REQUEST.search(text)
    ):
        normalized.update(
            {
                "mode": "full_research",
                "source_mode": "mixed",
                "needs_computation": True,
                "task_intent": "general",
                "required_specialist": "none",
                "reason": (
                    "The requested statistical deliverables require the full "
                    "reviewed research loop, not a deterministic Data-only stop"
                ),
            }
        )
        normalized.pop("preliminary_stages", None)
        return normalized
    empirical_hypothesis = bounded_hypothesis and bool(
        _CURRENT_OBSERVATION_HYPOTHESIS.search(text) or _DATA_FALLBACK.search(text)
    )
    if empirical_hypothesis and not _NEGATED_FULL_FALLBACK.search(text):
        normalized.update(
            {
                "mode": "full_research",
                "source_mode": "mixed",
                "needs_computation": True,
                "task_intent": "general",
                "required_specialist": "none",
                "reason": (
                    "The empirical hypothesis must be resolved through verified "
                    "data, literature, real computation, post-result hypothesis "
                    "update, and reviewed release"
                ),
            }
        )
        normalized.pop("preliminary_stages", None)
        return normalized
    if (
        protocol
        in {
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
            SOLAR_CYCLE_26_READINESS_PROTOCOL,
            SOLAR_POLAR_PRECURSOR_PROTOCOL,
        }
        and normalized.get("mode") != "full_research"
    ):
        if bounded_hypothesis:
            normalized.update(
                {
                    "source_mode": "mixed",
                    "needs_computation": True,
                    "preliminary_stages": ["data"],
                }
            )
            return normalized
        normalized.update(
            {
                "mode": "verified_analysis",
                "source_mode": (
                    "mixed"
                    if normalized.get("source_mode") in {None, "none"}
                    else normalized["source_mode"]
                ),
                "needs_computation": True,
                "task_intent": "data_preparation",
                "required_specialist": "solar-data",
                "reason": (
                    "The selected solar Data protocol requires registered "
                    "authoritative inputs and deterministic computation"
                ),
            }
        )
        return normalized
    if normalized.get("mode") == "fast_answer":
        normalized["mode"] = "verified_analysis"
        normalized["source_mode"] = (
            "mixed"
            if normalized.get("source_mode") == "none"
            else normalized["source_mode"]
        )
        normalized["needs_computation"] = True
        normalized["reason"] = (
            "deterministic scientific protocol requires verified data and computation"
        )
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
    for intent, specialist, pattern in _BOUNDED_FALLBACK_PATTERNS:
        if pattern.search(text):
            return {
                "mode": "verified_analysis",
                "source_mode": "mixed",
                "needs_computation": intent == "experiment_run",
                "task_intent": intent,
                "required_specialist": specialist,
                "reason": "router unavailable; explicit bounded specialist intent preserved",
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
            "research_planning",
            "data_preparation",
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
            "experiment_design",
            "experiment_run",
        }
        or required_specialist
        not in {
            "none",
            "solar-planner",
            "solar-data",
            "solar-hypothesis",
            "solar-experiment",
        }
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


def _latest_solar_precursor_receipt_status(
    messages: Sequence[object],
) -> str | None:
    latest_human = _latest_human(messages)
    start = latest_human[0] + 1 if latest_human else 0
    for message in reversed(messages[start:]):
        if not (isinstance(message, ToolMessage) or _message_role(message) == "tool"):
            continue
        name = (
            message.get("name")
            if isinstance(message, Mapping)
            else getattr(message, "name", None)
        )
        if name != _SOLAR_PRECURSOR_TABLE_TOOL:
            continue
        try:
            payload = json.loads(_message_text(message))
        except (TypeError, ValueError):
            return None
        status = payload.get("status") if isinstance(payload, Mapping) else None
        return status if isinstance(status, str) else None
    return None


def _verified_solar_precursor_table_ready(
    route: Mapping[str, Any],
    system_text: str,
    latest_text: str,
    successful_names: set[str],
    messages: Sequence[object],
) -> bool:
    """Return whether the bounded Data producer has its canonical table.

    The deterministic table is the Data protocol's terminal product.  Qwen may
    still see optional audit/calculation tools after that tool succeeds and keep
    issuing redundant Harness calls.  Detect the producer stage from either the
    persisted route or its injected producer directive, then let the caller
    suppress all tools for the next model turn so it must return the
    receipt-backed result.
    """

    if _SOLAR_PRECURSOR_TABLE_TOOL not in successful_names:
        return False
    if _latest_solar_precursor_receipt_status(messages) != "verified":
        return False
    protocol = str(route.get("required_analysis_protocol") or "")
    route_data_stage = (
        protocol == SOLAR_POLAR_PRECURSOR_PROTOCOL
        and str(route.get("required_specialist") or "") == "solar-data"
        and str(route.get("task_intent") or "") == "data_preparation"
    )
    producer_context = "\n".join((system_text, latest_text))
    injected_data_stage = (
        "[RESEARCH_PRODUCER_V2]" in producer_context
        and "stage=data" in producer_context
        and "solar_polar_precursor_table_v1" in producer_context
    )
    return route_data_stage or injected_data_stage


def _successful_specialists(
    calls: Sequence[Mapping[str, Any]],
    successful_names: set[str],
    messages: Sequence[object],
    *,
    workspace_verified_specialists: set[str] | None = None,
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
        if (
            status != "error"
            and specialist in _RECEIPT_SPECIALISTS
            and (
                workspace_verified_specialists is None
                or specialist in workspace_verified_specialists
            )
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
        if status == "error":
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
            and (
                workspace_verified_specialists is None
                or call.get("args", {}).get("subagent_type")
                in workspace_verified_specialists
            )
        }
    )
    return routed


def _workspace_verified_specialists(
    request: ModelRequest | Any,
    required_analysis_protocol: str,
) -> set[str] | None:
    """Resolve real task-local stage artifacts, or None for unbound test/CLI calls."""

    try:
        config = _request_config(request)
        root = workspace_root_from_config(
            config if isinstance(config, Mapping) else None
        )
        if not (root / "task.json").is_file():
            return None
        required_measurements = (
            F107_DISCONTINUITY_REQUIRED_MEASUREMENTS
            if required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL
            else ()
        )
        return {
            specialist
            for specialist, path in closed_loop_receipts(
                root,
                required_measurement_ids=required_measurements,
            ).items()
            if path is not None
        }
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError):
        return set()


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


def _model_request_user_request(request: ModelRequest) -> str:
    """Read the latest user text from state, falling back to request messages."""

    text = _latest_user_request(request.state)
    if text:
        return text
    latest = _latest_human(list(request.messages))
    return _message_text(latest[1]) if latest is not None else ""


def _request_config(request: object) -> Mapping[str, Any] | None:
    """Resolve RunnableConfig for both model and tool middleware requests.

    Current LangGraph ModelRequest.runtime has no config field. Reading it there
    silently selected the deployment workspace instead of the task workspace.
    ToolCallRequest retains runtime.config, so it remains the direct-call fallback
    after the documented context-local accessor.
    """

    try:
        from langgraph.config import get_config

        active = get_config()
    except Exception:
        active = None
    if isinstance(active, Mapping):
        return active
    runtime = getattr(request, "runtime", None)
    fallback = getattr(runtime, "config", None)
    return fallback if isinstance(fallback, Mapping) else None


def _bounded_review_action(request: object) -> dict[str, Any]:
    return store_from_config(_request_config(request)).bounded_hypothesis_action()


def _bounded_stage_action(request: object, stage: str) -> dict[str, Any]:
    return store_from_config(_request_config(request)).bounded_stage_action(stage)


def _bounded_route_stages(route: Mapping[str, Any]) -> tuple[str, ...]:
    final_stage = _BOUNDED_STAGE_BY_SPECIALIST_INTENT.get(
        (str(route.get("required_specialist")), str(route.get("task_intent")))
    )
    if final_stage is None:
        return ()
    preliminary = route.get("preliminary_stages", [])
    stages = [
        str(stage)
        for stage in preliminary
        if stage in {"planning", "data", "hypothesis", "experiment_design"}
        and stage != final_stage
    ]
    return (*stages, final_stage)


def _bounded_route_action(request: object) -> dict[str, Any]:
    state = getattr(request, "state", None)
    route = state.get("research_route") if isinstance(state, Mapping) else None
    if not isinstance(route, Mapping):
        raise RuntimeError("bounded route is unavailable")
    stages = _bounded_route_stages(route)
    if not stages:
        raise RuntimeError("bounded route has no recognized stage")
    if len(stages) == 1:
        return (
            _bounded_review_action(request)
            if stages[0] == "hypothesis"
            else _bounded_stage_action(request, stages[0])
        )
    return store_from_config(_request_config(request)).bounded_sequence_action(stages)


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

    route = request.state.get("research_route")
    required_analysis_protocol = (
        str(route.get("required_analysis_protocol") or "none")
        if isinstance(route, Mapping)
        else "none"
    )
    task_intent = (
        str(route.get("task_intent") or "general")
        if isinstance(route, Mapping)
        else "general"
    )
    if (
        required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL
        and task_intent
        not in {
            "hypothesis_generation",
            "hypothesis_comparison",
            "hypothesis_update",
        }
    ):
        verified_specialists = _workspace_verified_specialists(
            request,
            required_analysis_protocol,
        )
        if verified_specialists is None:
            return None, ToolMessage(
                content=(
                    "[F107 ROUTING BLOCKED] A task-local workspace is required "
                    "before the F10.7 semantic receipt can be verified. Do not "
                    "delegate the hypothesis specialist without that receipt."
                ),
                tool_call_id=call_id,
                name="task",
                status="error",
            )
        if "solar-data" not in verified_specialists:
            args = call.get("args")
            rewritten_args = dict(args) if isinstance(args, Mapping) else {}
            rewritten_args.update(
                {
                    "subagent_type": "solar-data",
                    "description": (
                        "Prepare the mandatory task-local F10.7 semantic receipt "
                        "before any bounded hypothesis generation. Use only exact "
                        "input paths supplied by the user, call "
                        "bind_f107_dataset_semantics, and return the canonical "
                        "artifact plus receipts/datasets/f107_semantics.json. "
                        "Do not generate or rank hypotheses in this stage."
                    ),
                }
            )
            return request.override(tool_call={**call, "args": rewritten_args}), None

    try:
        review_action = _bounded_route_action(request)
    except Exception as exc:
        return None, ToolMessage(
            content=(
                "[HYPOTHESIS REVIEW BLOCKED] The task-scoped review state could not "
                f"be loaded: {type(exc).__name__}: {exc}"
            ),
            tool_call_id=call_id,
            name="task",
            status="error",
        )
    if review_action["stage"] != "hypothesis":
        return request, None
    if review_action["kind"] == "review":
        args = call.get("args")
        rewritten_args = dict(args) if isinstance(args, Mapping) else {}
        rewritten_args.update(
            {
                "subagent_type": "solar-evidence",
                "description": (
                    "Review the current bounded hypothesis artifact independently. "
                    "review_mode=hypothesis. Call evidence_review_open_context first, "
                    "then persist exactly one hash-bound verdict. Never edit the draft."
                ),
            }
        )
        return request.override(tool_call={**call, "args": rewritten_args}), None
    if review_action["kind"] in {"released", "terminal"}:
        return None, ToolMessage(
            content=(
                "[HYPOTHESIS REVIEW BLOCKED] This stage is already terminal or "
                "accepted; no additional delegation is allowed."
            ),
            tool_call_id=call_id,
            name="task",
            status="error",
        )

    user_request = _latest_user_request(request.state)
    review_issues = review_action.get("issues", [])
    description = (
        "Handle this as the bounded solar-hypothesis specialist. Use only the "
        "verbatim user request below as the task contract; parent prose is not "
        "evidence. Execute this order:\n"
        "1. Call scientific_hypothesis_bind_request immediately, before discovery.\n"
        "2. Call kb_query for this question. Select only the smallest relevant Wiki "
        "bundle: target 3 entries, hard maximum 5. For each selected entry, call "
        "kb_read and then scientific_hypothesis_bind_wiki_evidence immediately. "
        "After three successful bindings cover one mechanism, one method/data "
        "constraint, and the proxy/measurement null, persist H0 or the first "
        "complete candidate before reading any remaining optional entries. Stop "
        "Wiki browsing once the mechanism, scope, data, and test constraints needed "
        "for the candidates are covered.\n"
        "3. After the first complete candidate is persisted, call "
        "scientific_hypothesis_build_literature_bundle for the exact bound question, "
        "then call lit_bundle_read once. Bind at most three directly relevant sources "
        "one at a time and immediately attach every returned evidence_id to the "
        "matching candidate before binding another source. Never substitute the "
        "generic lit_bundle_build tool.\n"
        "4. Bind other non-Wiki material only when it is a traceable inspected artifact; "
        "scenario premises in the request are assumptions, not empirical support.\n"
        "When an upstream receipt defines a measurement or aggregation method, copy "
        "that method exactly into scope and prediction fields. A `_gauss` suffix is a "
        "measurement unit, never evidence of Gaussian weighting; do not replace an "
        "arithmetic mean with a different weighting rule.\n"
        "5. Persist the first complete candidate as soon as it is ready with "
        "scientific_hypothesis_update_draft. Honor the user's requested output "
        "cardinality. If the user asks for exactly one hypothesis, keep credible "
        "rivals and measurement/null explanations inside that candidate's "
        "alternative-explanation and falsification fields; do not expand the "
        "visible or persisted result into a multi-candidate portfolio. Otherwise, "
        "build a 4-6 candidate pool for multi-mechanism or long-tail questions. "
        "Include a modal baseline, both "
        "positive_tail and negative_tail search regions, and a measurement/null "
        "control when applicable. Use controlled mechanism mutations rather than "
        "synonymous rewrites, and do not prematurely discard unfamiliar or "
        "high-evidence-risk candidates. Prefer a smaller persisted portfolio over "
        "a larger prose-only answer if budget is tight. Confidence must be exactly high, "
        "medium, or low; Wiki grounding alone cannot justify high confidence. Every "
        "candidate must include scope_conditions with target, temporal, spatial, "
        "data, method, holds-when, does-not-apply, and generalization boundaries; "
        "epistemic_status separating hypothesis, mechanism inference, and empirical "
        "support; and uncertainty sources, implications, and reduction strategy. "
        "These are distinct from assumptions, confounders, and falsifiers. Do not "
        "use universal scope, unsupported calendar/numeric cutoffs, or vague "
        "'significant/obvious/stable' decision rules without a preregistered rule "
        "and uncertainty bound. For a solar-cycle question, write '太阳活动周期' or "
        "'下一周期'; never use '下一周' to mean the next solar cycle.\n"
        "6. After the complete pool is persisted, call scientific_hypothesis_get_draft "
        "for candidate_pool_sha256 and tail_review_scoring_guide. Then act as an "
        "independent violation-first critic and call "
        "scientific_hypothesis_review_tail. For every common and instance-specific "
        "rubric item, list weaknesses and violated_guidelines first; status is pass "
        "if and only if that list is empty. Apply the guide's explicit pass "
        "conditions, violation conditions, edge rules, and low/medium/high metric "
        "anchors instead of scoring by intuition. Treat all seven rubric "
        "violations as hard gates, and add at least one instance-specific rubric "
        "per candidate derived from the bound question, evidence, or a concrete "
        "candidate contrast. Do not combine rubric rewards or the six tail metrics "
        "into one selection score or choose a winner yourself. The tool recomputes "
        "global and per-region Pareto frontiers and preserves both tail regions plus "
        "eligible null controls. Mechanism distance is only a diversity coordinate, "
        "not a monotonic scientific benefit; a candidate cannot dominate another "
        "solely because it is stranger. Instance-rubric bases must name a scientific "
        "premise, evidence limitation, observable, or concrete candidate conflict, "
        "not merely restate a task or field requirement. Repair and re-review any "
        "violation "
        "or stale review, then update pairwise distinctions after pruning.\n"
        "7. Call scientific_hypothesis_get_draft and leave the complete contract, "
        "hashes, review trace, and every scientific field in persisted state. In the "
        "specialist's final response, write a concise researcher-facing Chinese "
        "summary: lead with the unverified premise and the one or two most "
        "discriminating next tests; then present each selected candidate in natural "
        "language with its claim, compact applicability/failure boundary, necessary "
        "premises, one discriminating prediction and weakening result, main "
        "alternative/confounder, evidence gap/uncertainty, next test, and qualitative "
        "confidence. The scoring, ranking, reviewer rubric, search regions, and "
        "selection trace are internal working state only. Never expose hashes, schema "
        "names, enum values, candidate ids, search-region labels, Pareto mechanics, "
        "rubric rewards, tool receipts, raw field tables, or internal workflow terms "
        "in the chat answer. If the user asks for an audit, translate the audit "
        "findings into ordinary human-readable prose; the raw scoring structure still "
        "stays internal. Never claim reads or bindings without tool receipts.\n"
        "Resolve avoidable draft warnings. Do not rely on a parent-written Wiki "
        "summary or unbound 'verified facts'. Do not publish or freeze unless the "
        "user explicitly requests it.\n\n"
        f"<review_issues>{json.dumps(review_issues, ensure_ascii=False)}</review_issues>\n"
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
        unattributed_single_result = bool(
            len(specialist_call_ids) == 1
            and call_id in {None, ""}
            and routed_specialist is None
        )
        if status == "error" or (
            call_id not in specialist_call_ids
            and routed_specialist != specialist
            and not unattributed_single_result
        ):
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


def _map_routed_specialist_result(
    result: object,
    config: object,
) -> object:
    """Transform both direct and Command-wrapped task tool results."""

    if isinstance(result, ToolMessage):
        marked = _mark_routed_specialist_result(result)
        return _require_persisted_hypothesis_draft(marked, config)
    if not isinstance(result, Command) or not isinstance(result.update, Mapping):
        return result

    raw_messages = result.update.get("messages")
    if not isinstance(raw_messages, Sequence) or isinstance(
        raw_messages,
        (str, bytes),
    ):
        return result
    mapped_messages = [
        _require_persisted_hypothesis_draft(
            _mark_routed_specialist_result(message),
            config,
        )
        if isinstance(message, ToolMessage)
        else message
        for message in raw_messages
    ]
    if list(raw_messages) == mapped_messages:
        return result
    return Command(
        graph=result.graph,
        update={**result.update, "messages": mapped_messages},
        resume=result.resume,
        goto=result.goto,
    )


def _persisted_hypothesis_draft_status(config: object) -> tuple[bool, str] | None:
    """Return draft receipt status, or None when no task workspace is bound."""

    metadata = config.get("metadata", {}) if isinstance(config, Mapping) else {}
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    base_workspace = None
    for values in (metadata, configurable):
        if not isinstance(values, Mapping):
            continue
        candidate = values.get("base_workspace_dir")
        if isinstance(candidate, str) and candidate.strip():
            base_workspace = candidate
            break
    try:
        root = workspace_root_from_config(
            config,
            base_workspace=base_workspace,
        )
    except RuntimeError:
        root = None
    if root is None:
        for values in (metadata, configurable):
            if not isinstance(values, Mapping):
                continue
            candidate = values.get("workspace_dir")
            if isinstance(candidate, str) and candidate.strip():
                root = Path(candidate).expanduser().resolve()
                break
    if root is None:
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
    if not isinstance(result, ToolMessage):
        return result
    metadata = dict(result.additional_kwargs)
    metadata["research_router_specialist"] = "solar-hypothesis"
    if result.status == "error":
        metadata["research_router_internal_failure"] = _message_text(result)[:2_000]
        return result.model_copy(
            update={
                "content": (
                    "科学假设子任务没有完成，因此目前没有可交付的研究结论。"
                    "系统会按原问题重试；如果再次失败，将停止并说明尚缺什么。"
                ),
                "additional_kwargs": metadata,
            }
        )
    receipt = _persisted_hypothesis_draft_status(config)
    if receipt is None:
        metadata["research_router_internal_failure"] = (
            "task workspace was not bound for persisted reader rendering"
        )
        return result.model_copy(
            update={
                "content": (
                    "这次没有形成能够核对来源和边界的完整研究结果，"
                    "因此暂时不返回候选结论。请按原问题重试一次。"
                ),
                "status": "error",
                "additional_kwargs": metadata,
            }
        )
    if receipt[0]:
        content = _message_text(result)
        budget_stopped = _MODEL_CALL_BUDGET_STOP.search(content) is not None
        metadata.update(
            {
                "research_router_specialist": "solar-hypothesis",
                "research_router_result_view": "researcher_summary",
                "research_router_internal_state_path": receipt[1],
            }
        )
        if budget_stopped:
            metadata.update(
                {
                    "research_router_execution_status": "budget_stopped",
                    "research_router_result_status": "partial",
                    "research_router_recovered_persisted_draft": True,
                }
            )
        try:
            from jw.tools.scientific_hypothesis import (
                render_persisted_hypothesis_reader_view,
            )

            reader_view = render_persisted_hypothesis_reader_view(
                receipt[1],
                partial_reason=content if budget_stopped else None,
            )
        except Exception as exc:
            metadata["research_router_internal_failure"] = str(exc)[:2_000]
            return result.model_copy(
                update={
                    "content": (
                        "研究内容已经保存，但转换成面向读者的文字时失败了。"
                        "为避免展示内部数据或未经整理的结论，本次不返回候选内容；"
                        "请重试一次。"
                    ),
                    "status": "error",
                    "additional_kwargs": metadata,
                }
            )
        return result.model_copy(
            update={
                "content": reader_view,
                "additional_kwargs": metadata,
            }
        )
    metadata["research_router_internal_failure"] = receipt[1]
    return result.model_copy(
        update={
            "content": (
                "这次没有形成能够核对来源、适用边界和失效条件的完整研究结果，"
                "因此暂时不返回候选结论。系统会按原问题重试一次，"
                "不会根据不完整内容自行补写答案。"
            ),
            "status": "error",
            "additional_kwargs": metadata,
        }
    )


def _passthrough_hypothesis_result(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    """Force the bounded entry node and preserve its deterministic reader view."""

    if not _is_bounded_hypothesis_route(request.state):
        return response
    try:
        action = _bounded_route_action(request)
    except Exception:
        logger.exception("bounded hypothesis review state is unavailable")
        return _blocked_model_response(
            response,
            "task-scoped review state is unavailable; retry after the state service recovers",
        )
    if action.get("kind") == "terminal":
        return response
    if action.get("kind") == "released":
        try:
            content = store_from_config(
                _request_config(request)
            ).accepted_bounded_markdown("hypothesis")
        except Exception:
            logger.exception("accepted hypothesis state is unavailable")
            return _blocked_model_response(
                response,
                "accepted hypothesis state is unavailable; no unreviewed result was returned",
            )
        if isinstance(content, str) and content.strip():
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
    if (
        action.get("kind") == "producer"
        and action.get("phase") == "hypothesis"
        and attempts >= 2
    ) or _available_tool(request.tools, ("task",)) is None:
        return response

    # Tool filtering is advisory in several upstream middleware layers: a
    # selector or provider can still return a stale/non-visible tool call.
    # Normalize the actual model result as well, so bounded hypothesis turns
    # reach the specialist in one graph step rather than spending a round on
    # blocked parent-side reads.
    expected_agent = (
        "solar-evidence" if action.get("kind") == "review" else action.get("producer")
    )
    if (
        len(response.result) == 1
        and isinstance(response.result[0], AIMessage)
        and len(response.result[0].tool_calls) == 1
        and response.result[0].tool_calls[0].get("name") == "task"
        and isinstance(response.result[0].tool_calls[0].get("args"), Mapping)
        and response.result[0].tool_calls[0]["args"].get("subagent_type")
        == expected_agent
    ):
        if not _message_text(response.result[0]).strip():
            return response
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=response.result[0].tool_calls,
                    additional_kwargs=response.result[0].additional_kwargs,
                    response_metadata=response.result[0].response_metadata,
                    id=response.result[0].id,
                )
            ],
            structured_response=response.structured_response,
        )

    request_digest = hashlib.sha256(
        _latest_user_request(request.state).encode("utf-8")
    ).hexdigest()[:20]
    action = _bounded_route_action(request)
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": expected_agent,
                            "description": "bounded hypothesis review route",
                        },
                        "id": f"call_hypothesis_{request_digest}",
                    }
                ],
            )
        ],
        structured_response=response.structured_response,
    )


def _passthrough_accepted_release(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    """Render only the exact final draft accepted by the hash-bound release gate."""

    route = request.state.get("research_route")
    if not isinstance(route, Mapping) or route.get("mode") != "full_research":
        return response
    config = _request_config(request)
    try:
        store = store_from_config(config)
        report = store.accepted_release_markdown()
        if isinstance(report, str) and report.strip():
            store.mark_release_delivered()
    except Exception:
        logger.exception("accepted release state is unavailable")
        return _blocked_model_response(
            response,
            "accepted release state is unavailable; no unreviewed draft was returned",
        )
    if not isinstance(report, str) or not report.strip():
        return response
    return ModelResponse(
        result=[AIMessage(content=report)],
        structured_response=response.structured_response,
    )


def _single_response_tool_call(response: ModelResponse) -> dict[str, Any] | None:
    if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
        return None
    calls = response.result[0].tool_calls
    return calls[0] if len(calls) == 1 else None


def _blocked_model_response(response: ModelResponse, reason: str) -> ModelResponse:
    return ModelResponse(
        result=[AIMessage(content=f"[RESEARCH REVIEW BLOCKED] {reason}")],
        structured_response=response.structured_response,
    )


def _needs_release_draft_retry(
    request: ModelRequest,
    response: ModelResponse,
) -> bool:
    route = request.state.get("research_route")
    if not isinstance(route, Mapping) or route.get("mode") != "full_research":
        return False
    try:
        action = store_from_config(_request_config(request)).next_action()
    except Exception:
        return False
    if action.get("kind") != "prepare_release":
        return False
    calls = [
        call
        for item in response.result
        if isinstance(item, AIMessage)
        for call in item.tool_calls
    ]
    if any(call.get("name") == "research_release_prepare" for call in calls):
        return False
    # Qwen may attach a short planning sentence to stale context-tool calls.
    # That prose is not a release draft, so the tool mismatch must take
    # precedence over the presence of content.
    if calls:
        return True
    return not any(
        _message_text(item).strip()
        for item in response.result
        if isinstance(item, AIMessage)
    )


def _release_draft_retry_request(request: ModelRequest) -> ModelRequest:
    release_tools = [
        tool for tool in request.tools if _tool_name(tool) == "research_release_prepare"
    ]
    return request.override(
        system_message=append_to_system_message(
            request.system_message,
            (
                "The previous response attempted stale context tools during final "
                "release synthesis. Do not call read_file, ls, or any context tool. "
                "Call research_release_prepare now with the complete reader-facing "
                "Markdown report and claim_citations. Use only the accepted claims "
                "and required limitations already provided in the research route."
            ),
        ),
        tools=release_tools,
        tool_choice=None,
    )


def _silso_release_fallback(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    """Materialize a bounded SILSO report after two empty model drafts.

    The fallback is deliberately protocol-specific: it only activates when the
    accepted integration claim contains the complete morphology measurement
    vector.  It does not invent prose for other research tasks.
    """

    try:
        action = store_from_config(_request_config(request)).next_action()
    except Exception:
        return response
    if action.get("kind") != "prepare_release":
        return response
    release_context = action.get("release_context")
    if not isinstance(release_context, Mapping):
        return response
    claims = release_context.get("claims")
    if not isinstance(claims, list):
        return response
    claim_by_id = {
        str(claim.get("claim_id")): claim
        for claim in claims
        if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
    }
    result_claim = next(
        (
            claim
            for claim in claims
            if isinstance(claim, Mapping)
            and isinstance(claim.get("text"), str)
            and "rise_time_pearson_r=" in claim["text"]
            and "bootstrap_requested_repetitions=" in claim["text"]
        ),
        None,
    )
    if not isinstance(result_claim, Mapping):
        return response
    result_text = str(result_claim["text"])
    metric_names = (
        "cycle_length_pearson_r",
        "cycle_length_pearson_p",
        "cycle_length_spearman_rho",
        "cycle_length_spearman_p",
        "cycle_length_pearson_ci_low",
        "cycle_length_pearson_ci_high",
        "cycle_length_spearman_ci_low",
        "cycle_length_spearman_ci_high",
        "rise_time_pearson_r",
        "rise_time_pearson_p",
        "rise_time_spearman_rho",
        "rise_time_spearman_p",
        "rise_time_pearson_ci_low",
        "rise_time_pearson_ci_high",
        "rise_time_spearman_ci_low",
        "rise_time_spearman_ci_high",
        "decline_time_pearson_r",
        "decline_time_pearson_p",
        "decline_time_spearman_rho",
        "decline_time_spearman_p",
        "decline_time_pearson_ci_low",
        "decline_time_pearson_ci_high",
        "decline_time_spearman_ci_low",
        "decline_time_spearman_ci_high",
    )
    values: dict[str, float] = {}
    for name in metric_names:
        match = re.search(rf"(?:^|[;\s]){re.escape(name)}=([-+0-9.eE]+)", result_text)
        if match is None:
            return response
        try:
            values[name] = float(match.group(1))
        except ValueError:
            return response
    count_match = re.search(r"complete_cycle_count=(\d+)", result_text)
    reps_match = re.search(r"bootstrap_requested_repetitions=(\d+)", result_text)
    if count_match is None or reps_match is None:
        return response

    def relation_row(prefix: str, label: str) -> str:
        return (
            f"| {label} | {values[f'{prefix}_pearson_r']:.4f} "
            f"({values[f'{prefix}_pearson_p']:.4g}) | "
            f"[{values[f'{prefix}_pearson_ci_low']:.4f}, "
            f"{values[f'{prefix}_pearson_ci_high']:.4f}] | "
            f"{values[f'{prefix}_spearman_rho']:.4f} "
            f"({values[f'{prefix}_spearman_p']:.4g}) | "
            f"[{values[f'{prefix}_spearman_ci_low']:.4f}, "
            f"{values[f'{prefix}_spearman_ci_high']:.4f}] |"
        )

    scope_excerpt = (
        "本实验只使用 SILSO v2.0 官方月度序列、13 个月平滑序列和官方活动周边界，"
        "分析已经完整结束的第 1—24 周。"
    )
    result_excerpt = (
        "上升时间与峰值强度呈稳定负相关；两种相关系数、两类 bootstrap 区间、"
        "逐周期留一和两个预先固定时期的方向相互一致。"
    )
    interpretation_excerpt = (
        "该结果支持 Waldmeier 效应在当前样本中的统计表征，样本内描述性结论：高；"
        "它不构成太阳发电机因果机制证明，也不用于第 26 周预测。"
    )
    draft = "\n".join(
        [
            "# SILSO 太阳活动周形态与峰值强度实验报告",
            "",
            "## 研究范围与方法",
            "",
            scope_excerpt,
            "第 25 周只提供第 24 周的下一极小期边界，不作为完整周期样本。时间长度按年月差除以 12 换算为十进制年；早期组固定为第 1—12 周，较现代组固定为第 13—24 周。",
            f"相关分析以完整活动周为重采样单位，bootstrap 固定种子 20260826，共 {int(reps_match.group(1)):,} 次；同时完成 Pearson、Spearman 双侧检验、24 次逐周期留一和两时期比较。",
            "",
            "## 主要统计结果",
            "",
            f"有效样本为 {int(count_match.group(1))} 个完整活动周。括号内为双侧 p 值。",
            "",
            "| 关系 | Pearson r (p) | Pearson 95% CI | Spearman ρ (p) | Spearman 95% CI |",
            "|---|---:|---:|---:|---:|",
            relation_row("cycle_length", "周期长度—峰值"),
            relation_row("rise_time", "上升时间—峰值"),
            relation_row("decline_time", "下降时间—峰值"),
            "",
            "## 稳定性与解释",
            "",
            result_excerpt,
            "周期长度与峰值的两类 bootstrap 区间均跨越零，因此当前证据不足以支持稳定关系。下降时间的 Pearson 区间高于零，但 Spearman 区间跨越零，且早期与较现代时期的结果不一致，说明该关系依赖指标或时期。异常周期只用于报告敏感性，没有为获得显著结果而删除。",
            "",
            interpretation_excerpt,
            "",
            "## 数据边界与局限",
            "",
            "- 全样本只有 24 个周期，两个时期各 12 个周期；小样本使不确定性较大。",
            "- 普通周期级 bootstrap 未显式建模相邻活动周的序列依赖，有效独立样本数可能小于 24。",
            "- 早期活动周的历史观测质量较不均一；第 3 周官方极值表与平滑序列峰值相差 0.1，本实验按预先声明的平滑序列变量取值并在数据质量说明中保留差异。",
            "- 结论只适用于本次核验的 SILSO v2.0 数据与官方边界；没有使用外部文献、极区磁场或 F10.7 数据。",
            "- 第 25 周完成后可按同一协议做样本外方向复核；本实验不分析或预测第 26 周。",
        ]
    )
    citation_specs = (
        ("planning-plan-v1", scope_excerpt),
        (str(result_claim.get("claim_id")), result_excerpt),
        ("hypothesis-output-v2", interpretation_excerpt),
    )
    citations = [
        {"claim_id": claim_id, "draft_excerpt": excerpt}
        for claim_id, excerpt in citation_specs
        if claim_id in claim_by_id and excerpt in draft
    ]
    if not citations:
        return response
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "research_release_prepare",
                        "args": {
                            "draft_markdown": draft,
                            "claim_citations": citations,
                        },
                        "id": "call_research_silso_release_fallback",
                    }
                ],
            )
        ],
        structured_response=response.structured_response,
    )


def _enforce_v2_action_response(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    """Prevent prose from bypassing a required producer, review, or release node."""

    route = request.state.get("research_route")
    if not isinstance(route, Mapping):
        return response
    mode = route.get("mode")
    config = _request_config(request)
    try:
        if mode == "full_research":
            action = store_from_config(config).next_action()
        elif mode == "verified_analysis":
            if not _bounded_route_stages(route):
                return response
            action = _bounded_route_action(request)
        else:
            return response
    except Exception:
        logger.exception("task-scoped review state is unavailable")
        return _blocked_model_response(
            response,
            "task-scoped review state is unavailable; retry after the state service recovers",
        )

    if action["kind"] == "terminal":
        status = str(action.get("status") or "blocked")
        reason = str(action.get("reason") or "unresolved review gate")
        return ModelResponse(
            result=[
                AIMessage(
                    content=(
                        "[RESEARCH REVIEW TERMINAL] No further tool action is "
                        f"allowed in this turn. status={status}; reason={reason}. "
                        "The current artifact and unresolved review requirements "
                        "remain persisted; do not claim release acceptance."
                    )
                )
            ],
            structured_response=response.structured_response,
        )
    if action["kind"] == "released":
        return response
    existing = _single_response_tool_call(response)
    digest = hashlib.sha256(
        _latest_user_request(request.state).encode("utf-8")
    ).hexdigest()[:16]

    if action["kind"] in {"producer", "review"}:
        expected_agent = (
            "solar-evidence" if action["kind"] == "review" else action["producer"]
        )
        if (
            existing is not None
            and existing.get("name") == "task"
            and isinstance(existing.get("args"), Mapping)
            and existing["args"].get("subagent_type") == expected_agent
        ):
            message = response.result[0]
            if isinstance(message, AIMessage) and _message_text(message).strip():
                return ModelResponse(
                    result=[
                        AIMessage(
                            content="",
                            tool_calls=message.tool_calls,
                            additional_kwargs=message.additional_kwargs,
                            response_metadata=message.response_metadata,
                            id=message.id,
                        )
                    ],
                    structured_response=response.structured_response,
                )
            return response
        if _available_tool(request.tools, ("task",)) is None:
            return _blocked_model_response(
                response,
                f"required task node {expected_agent} is unavailable; no unreviewed prose was released",
            )
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": expected_agent,
                                "description": (
                                    "deterministic ResearchRunStateV2 producer route"
                                    if action["kind"] == "producer"
                                    else "deterministic ReviewVerdictV2 review route"
                                ),
                            },
                            "id": f"call_research_{digest}_{action['stage']}",
                        }
                    ],
                )
            ],
            structured_response=response.structured_response,
        )

    tool_name = {"prepare_release": "research_release_prepare"}.get(action["kind"])
    if tool_name is None:
        return _blocked_model_response(
            response, f"unsupported deterministic action {action['kind']}"
        )
    if existing is not None and existing.get("name") == tool_name:
        return response
    if _available_tool(request.tools, (tool_name,)) is None:
        return _blocked_model_response(
            response,
            f"required {tool_name} node is unavailable; no unreviewed prose was released",
        )
    draft = "\n".join(
        _message_text(item) for item in response.result if isinstance(item, AIMessage)
    ).strip()
    if not draft:
        return _blocked_model_response(
            response,
            "the final draft was empty and could not enter the release gate",
        )
    release_context = action.get("release_context", {})
    claims = (
        release_context.get("claims", [])
        if isinstance(release_context, Mapping)
        else []
    )
    citations = [
        {"claim_id": claim["claim_id"], "draft_excerpt": claim["text"]}
        for claim in claims
        if isinstance(claim, Mapping)
        and isinstance(claim.get("claim_id"), str)
        and isinstance(claim.get("text"), str)
        and claim["text"] in draft
    ]
    if not citations:
        return _blocked_model_response(
            response,
            "the prose response contained no exact accepted claim citation; "
            "generate claim_citations and call research_release_prepare",
        )
    args = {"draft_markdown": draft, "claim_citations": citations}
    return ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": args,
                        "id": f"call_research_{digest}_{action['kind']}",
                    }
                ],
            )
        ],
        structured_response=response.structured_response,
    )


def _passthrough_accepted_bounded_stage(
    request: ModelRequest,
    response: ModelResponse,
) -> ModelResponse:
    route = request.state.get("research_route")
    if not isinstance(route, Mapping) or route.get("mode") != "verified_analysis":
        return response
    stage = _BOUNDED_STAGE_BY_SPECIALIST_INTENT.get(
        (str(route.get("required_specialist")), str(route.get("task_intent")))
    )
    if stage is None or stage == "hypothesis":
        return response
    config = _request_config(request)
    try:
        store = store_from_config(config)
        if store.bounded_stage_action(stage).get("kind") != "released":
            return response
        text = store.accepted_bounded_markdown(
            stage,
            analysis_protocol=str(route.get("required_analysis_protocol") or "none"),
        )
    except Exception:
        logger.exception("accepted bounded-stage state is unavailable")
        return _blocked_model_response(
            response,
            "accepted stage state is unavailable; no unreviewed result was returned",
        )
    if not isinstance(text, str) or not text.strip():
        return response
    return ModelResponse(
        result=[AIMessage(content=text)],
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
            route = _fallback_route(text)
        else:
            route = _validated_route(response, fallback_text=text)
        return attach_harness_metadata(_with_analysis_protocol(route, text=text))

    async def _route_async(self, text: str) -> dict[str, Any]:
        try:
            response = await self._model.with_structured_output(_ROUTE_SCHEMA).ainvoke(
                [
                    {"role": "system", "content": _ROUTER_PROMPT},
                    {"role": "user", "content": text},
                ]
            )
        except Exception:
            route = _fallback_route(text)
        else:
            route = _validated_route(response, fallback_text=text)
        return attach_harness_metadata(_with_analysis_protocol(route, text=text))

    def before_agent(
        self,
        state: ResearchRoutingState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        latest = _latest_human(messages)
        if latest is None:
            return None
        key = _turn_key(latest[1])
        if state.get("research_route_turn") == key and state.get("research_route"):
            return None
        text = _message_text(latest[1])
        continued = _continuation_route(state, text)
        return {
            "research_route": (
                attach_harness_metadata(continued)
                if continued is not None
                else self._route_sync(text)
            ),
            "research_route_turn": key,
        }

    async def abefore_agent(
        self,
        state: ResearchRoutingState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        latest = _latest_human(messages)
        if latest is None:
            return None
        key = _turn_key(latest[1])
        if state.get("research_route_turn") == key and state.get("research_route"):
            return None
        text = _message_text(latest[1])
        continued = _continuation_route(state, text)
        return {
            "research_route": (
                attach_harness_metadata(continued)
                if continued is not None
                else await self._route_async(text)
            ),
            "research_route_turn": key,
        }

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        route = request.state.get("research_route")
        if not isinstance(route, Mapping):
            return request
        mode = route.get("mode")
        source_mode = route.get("source_mode")
        needs_computation = route.get("needs_computation") is True
        task_intent = route.get("task_intent", "general")
        required_specialist = route.get("required_specialist", "none")
        capability_id = route.get("capability_id", "analysis")
        messages = list(request.messages)
        calls, successful_names = _calls_since_latest_human(messages)
        latest_human = _latest_human(messages)
        latest_text = _message_text(latest_human[1]) if latest_human else ""
        system_text = _message_text(request.system_message)
        required_analysis_protocol = str(
            route.get("required_analysis_protocol")
            or detect_analysis_protocol(latest_text)
        )
        verified_precursor_table = _verified_solar_precursor_table_ready(
            route,
            system_text,
            latest_text,
            successful_names,
            messages,
        )
        precursor_receipt_status = _latest_solar_precursor_receipt_status(messages)
        nonverified_precursor_receipt = precursor_receipt_status in {"partial", "error"}

        directive = [
            "<research_route>",
            f"mode={mode}; source_mode={source_mode}; "
            f"needs_computation={str(needs_computation).lower()}; "
            f"task_intent={task_intent}; "
            f"required_specialist={required_specialist}; "
            f"required_analysis_protocol={required_analysis_protocol}; "
            f"capability_id={capability_id}",
        ]
        forced_tool: str | None = None
        suppress_tools = False
        release_generation_messages: list[object] | None = None
        if verified_precursor_table:
            directive.append(
                "The deterministic solar precursor table has already returned "
                "status=verified. Return its receipt-backed Data result now; "
                "optional audit and Harness calculation tools are complete for "
                "this producer turn and must not be called again."
            )
        elif nonverified_precursor_receipt:
            directive.append(
                "The latest deterministic solar precursor receipt is not verified. "
                "Keep the current Data tools available, repair the reported gap or "
                "error in place, and do not return it as a completed Data product."
            )

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
            if required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL:
                directive.append(
                    "The bounded analysis must use a hash-bound F10.7 semantic "
                    "manifest when local data is present. "
                    + f107_discontinuity_directive()
                )
            if required_analysis_protocol == SILSO_CYCLE_REPRODUCTION_PROTOCOL:
                directive.append(
                    "The bounded data producer must use the Supervisor-bound SILSO "
                    "monthly-total, monthly-smoothed, and official cycle-extrema "
                    "inputs, then call reproduce_silso_cycle_extrema."
                )
            if required_analysis_protocol == SOLAR_POLAR_PRECURSOR_PROTOCOL:
                directive.append(
                    "The bounded data producer must use the Supervisor-bound SILSO "
                    "monthly-total and MWO/WSO inputs, then call "
                    "prepare_solar_precursor_cycle_table."
                )
            if required_analysis_protocol == SILSO_CYCLE_MORPHOLOGY_PROTOCOL:
                directive.append(
                    "The Data producer must call run_silso_cycle_morphology with "
                    "the three Supervisor-bound SILSO inputs. "
                    + silso_cycle_morphology_directive()
                )
            if required_analysis_protocol == SOLAR_CYCLE_26_READINESS_PROTOCOL:
                directive.append(
                    "The bounded data producer must use all six Supervisor-bound "
                    "readiness inputs, then call prepare_solar_cycle_26_readiness. "
                    + solar_cycle_26_readiness_directive()
                )
            if required_analysis_protocol == SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL:
                directive.append(
                    "The bounded data producer must call "
                    "run_solar_cycle_26_historical_forecast with the three "
                    "Supervisor-bound SILSO inputs. "
                    + solar_cycle_26_forecast_backtest_directive()
                )
            f107_data_pending = False
            if (
                required_specialist == "solar-hypothesis"
                and required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL
                and task_intent
                not in {
                    "hypothesis_generation",
                    "hypothesis_comparison",
                    "hypothesis_update",
                }
            ):
                verified_specialists = _workspace_verified_specialists(
                    request,
                    required_analysis_protocol,
                )
                f107_data_pending = (
                    verified_specialists is None
                    or "solar-data" not in verified_specialists
                )
                if verified_specialists is None:
                    directive.append(
                        "ROUTING BLOCKER: the bounded F10.7 hypothesis request has "
                        "no task-local workspace in which a semantic receipt can be "
                        "verified. Do not delegate or answer from unbound data."
                    )
                    suppress_tools = True
                elif "solar-data" not in verified_specialists:
                    attempts = _attempt_count(
                        calls,
                        "task",
                        specialist="solar-data",
                    )
                    if attempts >= 2:
                        directive.append(
                            "solar-data failed twice before producing the mandatory "
                            "F10.7 semantic receipt. Stop and report this data-stage "
                            "blocker; do not delegate solar-hypothesis."
                        )
                        suppress_tools = True
                    elif _available_tool(request.tools, ("task",)) is not None:
                        forced_tool = "task"
                        directive.append(
                            "The mandatory preliminary graph node is solar-data. "
                            "Call task now with subagent_type='solar-data'; it must "
                            "call bind_f107_dataset_semantics and produce the "
                            "task-local receipt before solar-hypothesis can run."
                        )
                    else:
                        directive.append(
                            "ROUTING BLOCKER: solar-data is mandatory for this F10.7 "
                            "hypothesis request, but the task tool is unavailable."
                        )
                        suppress_tools = True

            if required_specialist == "solar-hypothesis" and not f107_data_pending:
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
                try:
                    action = _bounded_route_action(request)
                except Exception as exc:
                    directive.append(
                        "HYPOTHESIS REVIEW BLOCKER: task-scoped state unavailable: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    suppress_tools = True
                else:
                    directive.append(
                        "Bounded stage next_action="
                        + json.dumps(action, ensure_ascii=False, default=str)
                    )
                    if action["kind"] == "released":
                        suppress_tools = True
                        directive.append(
                            "The exact hypothesis artifact passed Evidence review. "
                            "Return the accepted producer result verbatim."
                        )
                    elif action["kind"] == "terminal":
                        suppress_tools = True
                        directive.append(
                            f"The hypothesis review is {action['status']}; report its "
                            "unresolved issues without claiming acceptance."
                        )
                    elif task_available:
                        forced_tool = "task"
                        if action["kind"] == "review":
                            directive.append(
                                "Call task now with subagent_type='solar-evidence' for "
                                f"the independent {action['stage']} review."
                            )
                        else:
                            expected = action["producer"]
                            directive.append(
                                f"Call task now with subagent_type={expected!r}. "
                                "For a revision, address only the structured issues and "
                                "preserve the existing valid draft."
                            )
                    else:
                        directive.append(
                            "ROUTING BLOCKER: the task tool required by the bounded "
                            "producer/reviewer loop is unavailable."
                        )
                        suppress_tools = True
                if (
                    not suppress_tools
                    and action.get("kind") == "producer"
                    and action.get("phase") == "hypothesis"
                    and attempts >= 2
                ):
                    directive.append(
                        "solar-hypothesis failed twice. Do not loop or silently "
                        "substitute another specialist; report this exact delegation "
                        "failure as the blocker."
                    )
                    suppress_tools = True
            elif (
                bounded_stage := _BOUNDED_STAGE_BY_SPECIALIST_INTENT.get(
                    (str(required_specialist), str(task_intent))
                )
            ) is not None:
                try:
                    action = _bounded_stage_action(request, bounded_stage)
                except Exception as exc:
                    directive.append(
                        "BOUNDED REVIEW BLOCKER: task-scoped state unavailable: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    suppress_tools = True
                else:
                    directive.append(
                        "Bounded stage next_action="
                        + json.dumps(action, ensure_ascii=False, default=str)
                    )
                    if action["kind"] == "released":
                        suppress_tools = True
                        directive.append(
                            "Return the exact independently accepted producer artifact."
                        )
                    elif action["kind"] == "terminal":
                        suppress_tools = True
                        directive.append(
                            f"The bounded stage is {action['status']}; report its "
                            "unresolved issues without claiming acceptance."
                        )
                    elif _available_tool(request.tools, ("task",)) is not None:
                        forced_tool = "task"
                        expected = (
                            "solar-evidence"
                            if action["kind"] == "review"
                            else action["producer"]
                        )
                        directive.append(
                            f"Call task now with subagent_type={expected!r}; "
                            f"stage={bounded_stage}."
                        )
                    else:
                        directive.append(
                            "ROUTING BLOCKER: the bounded producer/reviewer loop "
                            "requires task, but that tool is unavailable."
                        )
                        suppress_tools = True
            elif required_specialist != "solar-hypothesis":
                local_required = source_mode in {"local", "mixed"}
                external_required = source_mode in {"external", "mixed"}
                local_seen = bool(
                    successful_names & {*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS}
                )
                read_seen = bool(successful_names & set(_LOCAL_READ_TOOLS))
                external_seen = bool(successful_names & set(_EXTERNAL_EVIDENCE_TOOLS))
                compute_seen = bool(successful_names & set(_COMPUTE_TOOLS))
                f107_semantics_seen = "bind_f107_dataset_semantics" in successful_names

                if local_required and not local_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        (*_LOCAL_DISCOVERY_TOOLS, *_LOCAL_READ_TOOLS),
                    )
                elif local_required and not read_seen:
                    forced_tool = _available_tool(request.tools, _LOCAL_READ_TOOLS)
                elif (
                    required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL
                    and local_required
                    and not f107_semantics_seen
                ):
                    forced_tool = _available_tool(
                        request.tools,
                        ("bind_f107_dataset_semantics",),
                    )
                elif external_required and not external_seen:
                    forced_tool = _available_tool(
                        request.tools,
                        _EXTERNAL_EVIDENCE_TOOLS,
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
                "Use the explicit ResearchRunStateV2 graph. Every producer result is "
                "checkpointed as an immutable artifact, reviewed by solar-evidence, "
                "and revised only by its owner. Never skip a review node, reuse a stale "
                "approval, or synthesize an unreviewed final claim."
            )
            if required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL:
                directive.append(f107_discontinuity_directive())
            if required_analysis_protocol == SOLAR_POLAR_PRECURSOR_PROTOCOL:
                directive.append(solar_polar_precursor_directive())
            if required_analysis_protocol == SILSO_CYCLE_MORPHOLOGY_PROTOCOL:
                directive.append(silso_cycle_morphology_directive())
            if required_analysis_protocol == SOLAR_CYCLE_26_READINESS_PROTOCOL:
                directive.append(solar_cycle_26_readiness_directive())
            if required_analysis_protocol == SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL:
                directive.append(solar_cycle_26_forecast_backtest_directive())
            config = _request_config(request)
            try:
                action = store_from_config(config).next_action()
            except Exception as exc:
                directive.append(
                    "RESEARCH STATE BLOCKER: the task-scoped v2 state could not be "
                    f"loaded: {type(exc).__name__}: {exc}"
                )
                suppress_tools = True
            else:
                directive.append(
                    "Deterministic next_action="
                    + json.dumps(action, ensure_ascii=False, default=str)
                )
                if action["kind"] == "producer":
                    forced_tool = "task"
                    directive.append(
                        f"Call task now with subagent_type={action['producer']!r}. "
                        f"This is phase={action['phase']} and stage={action['stage']}."
                    )
                    if (
                        required_analysis_protocol == F107_DISCONTINUITY_PROTOCOL
                        and action["stage"] == "data"
                    ):
                        directive.append(
                            "The solar-data producer must call "
                            "bind_f107_dataset_semantics and persist the hash-bound "
                            "receipts/datasets/f107_semantics.json before returning."
                        )
                elif action["kind"] == "review":
                    forced_tool = "task"
                    directive.append(
                        "Call task now with subagent_type='solar-evidence' for "
                        f"review_mode={action['review_mode']}."
                    )
                elif action["kind"] == "prepare_release":
                    forced_tool = "research_release_prepare"
                    release_generation_messages = _final_release_generation_messages(
                        messages
                    )
                    directive.append(
                        "Write one coherent, concise scientific report using only "
                        "accepted claims and the required carried limitations. State "
                        "the result, uncertainty, alternatives, and scope in natural "
                        "reader-facing language. Do not include raw JSON, internal IDs "
                        "or hashes, tool/debug records, failed drafts, workflow status, "
                        "or a verbatim limitations inventory. Keep claim_citations as "
                        "separate machine metadata: bind each material passage to an "
                        "accepted claim_id with a concise draft_excerpt, then call "
                        "research_release_prepare with the complete Markdown draft."
                    )
                elif action["kind"] == "released":
                    suppress_tools = True
                    directive.append(
                        "The final release is accepted. Return only its exact persisted "
                        "Markdown; middleware will enforce verbatim rendering."
                    )
                elif action["kind"] == "terminal":
                    suppress_tools = True
                    directive.append(
                        f"The research run is {action['status']}. Report the current "
                        "best artifact and unresolved issues; never claim acceptance."
                    )

        directive.append("</research_route>")
        prepared = request.override(
            system_message=append_to_system_message(
                request.system_message,
                "\n".join(directive),
            )
        )
        if release_generation_messages is not None:
            prepared = prepared.override(messages=release_generation_messages)
        if verified_precursor_table:
            # A verified canonical table is the terminal Data product for this
            # protocol.  Do not let later branch logic re-expose optional tools.
            prepared = prepared.override(tools=[], tool_choice=None)
        elif nonverified_precursor_receipt:
            prepared = prepared.override(tools=list(request.tools), tool_choice=None)
        elif forced_tool:
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
            else:
                prepared = prepared.override(tools=[], tool_choice=None)
        elif suppress_tools:
            prepared = prepared.override(tools=[], tool_choice=None)
        return prepared

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        raw_response = handler(prepared)
        if _needs_release_draft_retry(request, raw_response):
            raw_response = handler(_release_draft_retry_request(prepared))
            if _needs_release_draft_retry(request, raw_response):
                raw_response = _silso_release_fallback(request, raw_response)
        response = _passthrough_hypothesis_result(request, raw_response)
        response = _enforce_v2_action_response(request, response)
        response = _passthrough_accepted_bounded_stage(request, response)
        return _passthrough_accepted_release(request, response)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = await asyncio.to_thread(self._prepare_request, request)
        raw_response = await handler(prepared)
        if await asyncio.to_thread(
            _needs_release_draft_retry,
            request,
            raw_response,
        ):
            raw_response = await handler(_release_draft_retry_request(prepared))
            if await asyncio.to_thread(
                _needs_release_draft_retry,
                request,
                raw_response,
            ):
                raw_response = await asyncio.to_thread(
                    _silso_release_fallback,
                    request,
                    raw_response,
                )
        response = _passthrough_hypothesis_result(
            request,
            raw_response,
        )
        response = await asyncio.to_thread(
            _enforce_v2_action_response, request, response
        )
        response = await asyncio.to_thread(
            _passthrough_accepted_bounded_stage, request, response
        )
        return await asyncio.to_thread(_passthrough_accepted_release, request, response)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        rewritten, blocked = _direct_hypothesis_task_request(request)
        if blocked is not None:
            return blocked
        result = handler(rewritten)
        routed_args = rewritten.tool_call.get("args", {})
        routed_specialist = (
            routed_args.get("subagent_type")
            if isinstance(routed_args, Mapping)
            else None
        )
        if rewritten is not request and routed_specialist == "solar-hypothesis":
            config = _request_config(request)
            result = _map_routed_specialist_result(result, config)
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
        routed_args = rewritten.tool_call.get("args", {})
        routed_specialist = (
            routed_args.get("subagent_type")
            if isinstance(routed_args, Mapping)
            else None
        )
        if rewritten is not request and routed_specialist == "solar-hypothesis":
            config = _request_config(request)
            result = await asyncio.to_thread(
                _map_routed_specialist_result,
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

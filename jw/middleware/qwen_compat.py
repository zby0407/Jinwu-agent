"""Qwen-specific validation at the final model/tool boundary.

Qwen exposes provider-hosted tools whose names occupy the same namespace as
custom function tools.  A collision is rejected by the provider before the
model gets a turn, which otherwise looks like an agent that declined to use
tools.  This middleware makes that contract explicit and fails locally with an
actionable error before a slow remote request.

The validation runs against ``ModelRequest.tools`` rather than JW's domain tool
registry, so it also covers tools injected by Deep Agents and other middleware.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..llm.models import _dashscope_request_timeout
from .configurable_model import _read_model_override

QWEN_TOOL_USE_PROMPT = """<qwen_tool_contract>
This deployment is optimized for Qwen as an evidence-producing research agent.

- When the user names an external source, dataset, file, table, or version,
  obtain or read that exact material with tools before making substantive claims.
- Never replace an official definition, boundary, formula, or published value
  with an approximation merely to finish the task. If the exact material cannot
  be obtained, report the missing evidence as a blocker.
- For requested calculations, execute code against the retrieved data and keep
  the source rows or derived artifact inspectable. A prose-only calculation is
  not verification.
- When a tool returns an ``artifact_manifest``, use the listed path as the
  downstream program's input. Read or pass that file directly; never copy its
  rows or numeric values from tool output into source code. Reader-facing
  handoffs cite the source path and version without internal integrity fields.
- Treat tool errors as unfinished work. Inspect the failure and use the
  appropriate follow-up operation (for example, edit an existing file rather
  than trying to create it again).
- Treat the user's exclusions, "only" clauses, and requested evidence boundary
  as hard output constraints. Do not add adjacent theories, mechanisms, facts,
  or comparisons just because they are relevant to the broader topic.
- When retrieved products disagree, state exactly what the records establish.
  Do not invent a likely convention or causal explanation for the discrepancy;
  label an unverified reason as unresolved.
- After writing a requested artifact, return a short, self-contained handoff
  that starts with the outcome and names the artifact path. Never use a partial
  suffix or continuation of the artifact itself as the final chat response.
- The final answer must distinguish retrieved facts, computed results, and any
  unresolved discrepancy. Do not claim that a source was checked or code ran
  unless the tool results show it.
</qwen_tool_contract>"""

# Confirmed Qwen/DashScope reserved names for custom function tools.
# ``code_interpreter`` is a provider-hosted built-in; ``search`` is explicitly
# documented by DashScope as disallowed.  Keep this list deliberately narrow:
# unknown future restrictions should surface through the same compatibility
# error once confirmed rather than guessing at every provider tool type.
QWEN_RESERVED_FUNCTION_NAMES: frozenset[str] = frozenset({"code_interpreter", "search"})
_QWEN_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ARTIFACT_MANIFEST_PATTERN = re.compile(
    r"<artifact_manifest>\s*(?P<payload>\{.*?\})\s*</artifact_manifest>",
    re.DOTALL,
)
_ARTIFACT_READBACK_BATCH_SIZE = 8
_RESEARCH_REVIEW_BLOCKED_PREFIXES = (
    "[RESEARCH REVIEW BLOCKED]",
    "[HYPOTHESIS REVIEW BLOCKED]",
    "[HYPOTHESIS ROUTING BLOCKED]",
)
_TOOL_ERROR_PREFIXES = ("[TOOL ERROR]", "[TOOL ERROR CAPSULE]")
_REPEATED_BLOCKED_CALL_STOP = (
    "[RESEARCH REVIEW STOP] The closed-loop state machine rejected the same "
    "tool call twice. This turn stopped without changing research state. Resume "
    "from the deterministic next_action; do not retry the rejected call."
)
_REPEATED_TOOL_ERROR_STOP = (
    "[TOOL RETRY STOP] The identical tool call failed twice in this turn. "
    "Stopped without claiming completion. Preserve the error fingerprint and "
    "resume only after changing the approach or external state."
)
_TOOL_ERROR_COMPACT_THRESHOLD = 1_200
_QWEN_TRANSPORT_RETRY_DELAY_SECONDS = 20.0
_QWEN_TRANSPORT_MAX_RETRIES = 2


async def _sleep_before_qwen_retry(delay_seconds: float) -> None:
    """Sleep between Qwen transport retries behind a testable seam."""

    await asyncio.sleep(delay_seconds)


_SOURCE_RESTRICTED_HYPOTHESIS_DISCOVERY_TOOLS = frozenset(
    {
        "kb_query",
        "kb_read",
        "lit_bundle_build",
        "lit_bundle_read",
        "scientific_hypothesis_bind_wiki_evidence",
        "scientific_hypothesis_build_literature_bundle",
        "scientific_hypothesis_build_novelty_bundle",
        "scientific_hypothesis_bind_literature_evidence",
    }
)
_SOURCE_RESTRICTED_PREBOUND_TOOLS = frozenset(
    {"scientific_hypothesis_bind_request", "scientific_hypothesis_bind_evidence"}
)
_SOURCE_RESTRICTED_HYPOTHESIS_PROTOCOLS = frozenset(
    {
        "silso_cycle_morphology_v1",
        "solar_cycle_26_forecast_backtest_v1",
    }
)
_SOURCE_RESTRICTED_HYPOTHESIS_INSTRUCTION = """
<source_restricted_statistical_task>
This is a source-restricted statistical task. The accepted A2A material contains
hash-matched, Evidence-inspected result excerpts. Treat those excerpts as the only
scientific evidence for this stage. Do not call knowledge discovery tools, generic
filesystem tools, or shell tools. If the host bind receipt reports a prebound seed,
use its evidence_id mapping directly; otherwise bind exact text from the accepted material, then
persist no more than three distinct candidates with
scientific_hypothesis_update_draft. For silso_cycle_morphology_v1 these are cycle
length, rise time, and decline time versus peak strength: persist one candidate per
preregistered relationship. For
solar_cycle_26_forecast_backtest_v1 keep historical backtest skill versus the fixed
baseline, the conditional Cycle 26 forecast, and sensitivity/uncertainty distinct;
preserve a negative skill result and its low forecast confidence. Do not bind the
same accepted excerpt twice, and do not repeatedly rewrite a warning-free candidate.
After the three candidates are complete, read the draft, complete the required tail
review, checkpoint it, and read the final draft before returning prose. Calibrate
confidence at the claim level: A high within-sample descriptive confidence is allowed
when independent sample count, source quality, both correlation measures, bootstrap,
leave-one-out, and fixed-subperiod directions converge. It never upgrades causal or
out-of-sample confidence. When the accepted SILSO excerpt explicitly shows that all
of those convergence conditions hold, assign high to the rise-time within-sample descriptive claim;
retain lower confidence for mechanism, prediction, and the two non-convergent relations.
An unavailable external literature bundle is not a blocker and must not be invented.
</source_restricted_statistical_task>
"""
_SOURCE_RESTRICTED_PREBOUND_INSTRUCTION = """
<source_restricted_prebound_evidence>
The host has already validated and prebound the exact evidence rows for all three
registered SILSO relationships. Do not call a bind tool again and do not reconstruct
an excerpt from memory. Use the evidence_id mapping in the previous bind receipt,
write one candidate per relationship, and proceed to draft review/checkpoint.
</source_restricted_prebound_evidence>
"""
_FINAL_RELEASE_GATE_INSTRUCTION = """
<final_release_gate>
The only remaining action is the final release gate. Call
research_release_prepare once with the complete reader-facing Markdown draft and
its claim_citations. Do not call read_file, write_todos, ls, shell, memory, or
any other context or workflow tool. The accepted claims and carried limitations
in the research route are the complete evidence boundary for this synthesis.
</final_release_gate>
"""


def _is_structured_tool_error_message(message: BaseMessage) -> bool:
    """Recognize bounded-tool failures that should count toward retry limits."""

    text = QwenToolCompatibilityMiddleware._message_text(message).lstrip()
    if text.startswith(_TOOL_ERROR_PREFIXES) or text.startswith(
        "[CONTRACT TOOL BLOCKED]"
    ):
        return True
    if not text.startswith("{"):
        return False
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, Mapping) and payload.get("status") == "error"


def _source_restricted_host_seed_bound(messages: Sequence[BaseMessage]) -> bool:
    """Detect a successful host evidence-seed receipt in the current turn."""

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) not in {
            None,
            "scientific_hypothesis_bind_request",
        }:
            continue
        try:
            payload = json.loads(QwenToolCompatibilityMiddleware._message_text(message))
        except (TypeError, ValueError):
            continue
        candidates: list[Mapping[str, Any]] = []
        if isinstance(payload, Mapping):
            candidates.append(payload)
            nested = payload.get("result")
            if isinstance(nested, Mapping):
                candidates.append(nested)
        for candidate in candidates:
            count = candidate.get("prebound_evidence_count")
            mapping = candidate.get("prebound_evidence_ids_by_relationship")
            if (
                isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 3
                and isinstance(mapping, Mapping)
                and {
                    "cycle_length_peak",
                    "rise_time_peak",
                    "decline_time_peak",
                }.issubset(mapping)
            ):
                return True
    return False


_PLANNER_SERIAL_TOOL_NAMES = frozenset(
    {
        "research_planner_create_empirical_plan",
        "research_planner_get_section",
        "research_planner_stage_revision_section",
        "research_planner_commit_revision_candidate",
    }
)
# No-deliberation transitions that carry no scientific arguments. Forcing Qwen
# to "decide" one of these via an object ``tool_choice`` costs a remote call
# and re-opens the DashScope thinking-mode rejection.  The middleware emits the
# corresponding tool call locally so planner sub-agents can also take this path;
# the Supervisor's post-task close-out is only reached after a sub-agent returns.
_PLANNER_NO_DELIBERATION_TOOLS = frozenset(
    {
        "research_planner_validate_draft",
        "research_planner_freeze_plan",
    }
)
_PLANNER_DETERMINISTIC_ACTION_TO_TOOL = {
    "commit_revision_candidate": "research_planner_commit_revision_candidate",
    "validate_draft": "research_planner_validate_draft",
    "freeze_plan": "research_planner_freeze_plan",
}
# ``commit_revision_candidate`` has the same property.  Keep all three edges in
# one set so a complete draft cannot burn its remaining model-call budget by
# repeatedly asking the model to re-emit validate/freeze calls.
_PLANNER_LOCAL_NO_DELIBERATION_TOOLS = frozenset(
    {
        *_PLANNER_NO_DELIBERATION_TOOLS,
        "research_planner_commit_revision_candidate",
    }
)
_DATA_DETERMINISTIC_TOOLS = frozenset(
    {
        "solar_data_open_context",
        "prepare_solar_cycle_26_readiness",
        "run_solar_cycle_26_historical_forecast",
        "prepare_solar_precursor_cycle_table",
        "reproduce_silso_cycle_extrema",
    }
)
_EVIDENCE_OPEN_TOOL = "evidence_review_open_context"
_EVIDENCE_READ_TOOL = "evidence_review_read_source"
_EVIDENCE_SUBMIT_TOOL = "evidence_review_submit_round"
_SOLAR_PRECURSOR_DATASET_IDS = frozenset(
    {"silso-monthly-total-v2", "mwo-wso-polar-field-v2"}
)
_SILSO_REPRODUCTION_PROTOCOL = "silso_cycle_reproduction_v1"
_SILSO_EXTREMA_DATA_PRODUCT = "silso_cycle_extrema_v1"
_SOLAR_PRECURSOR_DATA_PRODUCT = "solar_polar_precursor_table_v1"
_SOLAR_CYCLE_26_READINESS_PROTOCOL = "solar_cycle_26_readiness_v1"
_SOLAR_CYCLE_26_READINESS_DATA_PRODUCT = "solar_cycle_26_readiness_inventory_v1"
_SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL = "solar_cycle_26_forecast_backtest_v1"
_SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT = "solar_cycle_26_forecast_backtest_v1"
_SOLAR_CYCLE_26_READINESS_DATASET_IDS = frozenset(
    {
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
        "noaa-swpc-monthly-f107-v1",
        "mwo-wso-polar-field-v2",
        "wso-current-polar-field-v1",
    }
)
_SILSO_REPRODUCTION_DATASET_IDS = frozenset(
    {
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
    }
)

_logger = logging.getLogger(__name__)

_RETRYABLE_QWEN_TRANSPORT_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutError",
    }
)


def _is_retryable_qwen_transport_error(exc: BaseException) -> bool:
    """Recognize one-request transport failures without retrying API rejections."""

    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            return False
        if type(current).__name__ in _RETRYABLE_QWEN_TRANSPORT_ERRORS:
            return True
        current = current.__cause__ or current.__context__
    return False


def _safe_host(base_url: Any) -> str:
    """Return only the scheme://host of a base URL, never its path/query."""
    text = str(base_url or "")
    if not text:
        return ""
    match = re.match(r"^(https?://[^/?#]+)", text)
    return match.group(1) if match else "(non-url)"


def _safe_tool_choice_label(tool_choice: Any) -> str:
    """Render a tool_choice as a structural label without tool args/payloads."""
    if isinstance(tool_choice, Mapping):
        function = tool_choice.get("function")
        name = function.get("name") if isinstance(function, Mapping) else None
        return f"object:{tool_choice.get('type')}:{name or '<auto>'}"
    return repr(tool_choice)


class QwenToolSchemaError(ValueError):
    """Raised when the final custom-tool schema is invalid for Qwen."""


def is_qwen_model(model_name: str | None) -> bool:
    """Return whether *model_name* identifies a Qwen-family model."""
    if not model_name:
        return False
    normalized = model_name.casefold().rsplit("/", 1)[-1]
    return normalized.startswith(("qwen", "qwq"))


def _tool_name(tool: BaseTool | dict[str, Any]) -> str | None:
    if isinstance(tool, BaseTool):
        return tool.name or None
    name = tool.get("name")
    if not name and isinstance(tool.get("function"), dict):
        name = tool["function"].get("name")
    return name if isinstance(name, str) and name else None


def validate_qwen_tool_schema(
    tools: list[BaseTool | dict[str, Any]],
) -> tuple[str, ...]:
    """Validate model-facing custom function names against Qwen constraints."""
    names = tuple(name for tool in tools if (name := _tool_name(tool)))
    collisions = sorted(set(names) & QWEN_RESERVED_FUNCTION_NAMES)
    if collisions:
        joined = ", ".join(collisions)
        raise QwenToolSchemaError(
            "Qwen rejected the assembled custom-tool schema because these names "
            f"are provider-reserved: {joined}. Rename the local tools before "
            "sending the request; do not enable a provider built-in as a "
            "substitute for a JW-local capability."
        )

    invalid = sorted(
        {
            name
            for name in names
            if len(name) > 64 or not _QWEN_TOOL_NAME_PATTERN.fullmatch(name)
        }
    )
    if invalid:
        joined = ", ".join(invalid)
        raise QwenToolSchemaError(
            "Qwen custom function names must contain only letters, numbers, "
            f"underscores, or hyphens and be at most 64 characters: {joined}"
        )

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise QwenToolSchemaError(
            "Qwen custom function names must be unique after all middleware is "
            f"assembled: {', '.join(duplicates)}"
        )
    return names


class QwenToolCompatibilityMiddleware(AgentMiddleware):
    """Apply Qwen's tool contract and validate its final custom-tool schema."""

    name = "qwen_tool_compatibility"

    def __init__(self, *, default_model: str | None = None) -> None:
        super().__init__()
        self._default_model = default_model

    def _active_model_is_qwen(self, request: ModelRequest | None = None) -> bool:
        if request is not None:
            request_model = str(
                getattr(request.model, "model_name", None)
                or getattr(request.model, "model", "")
            ).strip()
            if request_model:
                return is_qwen_model(request_model)
        override_model, _ = _read_model_override()
        return is_qwen_model(override_model or self._default_model)

    @staticmethod
    def _thinking_budget(request: ModelRequest) -> int:
        context = str(getattr(request.system_message, "content", ""))
        context += "\n" + "\n".join(
            str(getattr(message, "content", ""))
            for message in list(request.messages)[-4:]
        )
        if "Evidence Reviewer" in context or "[EVIDENCE_REVIEW_V2]" in context:
            name, default = "JW_QWEN_REVIEW_THINKING_BUDGET", 8192
        elif "[RESEARCH_PRODUCER_V2]" in context and "stage=planning" in context:
            # Planner's contract is large but mostly schema filling. Extra
            # hidden reasoning makes Qwen more likely to keep revising the same
            # JSON until the upstream connection is dropped; reserve tokens for
            # the visible, validator-bound plan instead.
            name, default = "JW_QWEN_PLANNING_THINKING_BUDGET", 1536
        elif "[RESEARCH_PRODUCER_V2]" in context and "stage=data" in context:
            name, default = "JW_QWEN_DATA_THINKING_BUDGET", 4096
        elif "[RESEARCH_PRODUCER_V2]" in context:
            name, default = "JW_QWEN_PRODUCER_THINKING_BUDGET", 6144
        elif "ResearchRunStateV2" in context or "full_research" in context:
            # The Supervisor chooses the next typed action; it does not author
            # the stage's scientific payload. Giving every routing/delegation
            # edge the same multi-thousand-token budget as a producer caused
            # several minutes of idle frontend latency per edge on real Qwen
            # Max streams. Keep scientific reasoning in the producer while
            # bounding the protocol decision itself.
            name, default = "JW_QWEN_SUPERVISOR_THINKING_BUDGET", 1024
        else:
            name, default = "JW_QWEN_THINKING_BUDGET", 4096
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer in [256, 65536]") from exc
        if not 256 <= value <= 65536:
            raise ValueError(f"{name} must be an integer in [256, 65536]")
        return value

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        if not self._active_model_is_qwen(request):
            context = "\n".join(
                [self._message_text(request.system_message)]
                + [self._message_text(message) for message in request.messages]
            )
            deterministic_tool = self._deterministic_evidence_submit_tool(
                list(request.messages),
                list(request.tools),
                enabled="[EVIDENCE_REVIEW_V2]" in context,
                review_mode=self._evidence_review_mode(context),
            )
            if deterministic_tool is None or not type(
                request.model
            ).__module__.startswith("langchain_anthropic"):
                return request
            from deepagents.middleware._utils import append_to_system_message

            submit_tool = next(
                tool for tool in request.tools if _tool_name(tool) == deterministic_tool
            )
            schema = getattr(submit_tool, "args_schema", None)
            if schema is None:
                return request
            model_name = str(
                getattr(request.model, "model_name", None)
                or getattr(request.model, "model", "")
            ).casefold()
            if model_name != "kimi-for-coding":
                return request.override(
                    tools=[submit_tool],
                    system_message=append_to_system_message(
                        request.system_message,
                        self._atomic_evidence_submit_instruction(deterministic_tool),
                    ),
                    tool_choice=None,
                )
            return request.override(
                tools=[],
                system_message=append_to_system_message(
                    request.system_message,
                    self._atomic_evidence_submit_instruction(deterministic_tool),
                ),
                tool_choice=None,
                response_format=ProviderStrategy(schema),
            )
        request_tools = list(request.tools)
        validate_qwen_tool_schema(request_tools)
        from deepagents.middleware._utils import append_to_system_message

        from .utils import configure_qwen_thinking, disable_thinking

        system_message = append_to_system_message(
            request.system_message,
            QWEN_TOOL_USE_PROMPT,
        )
        projected_messages = self._compact_repeated_tool_rounds(
            self._compact_tool_error_messages(request.messages)
        )
        request_context = "\n".join(
            [self._message_text(system_message)]
            + [self._message_text(message) for message in projected_messages]
        )
        overrides: dict[str, Any] = {
            "system_message": system_message,
            "messages": projected_messages,
        }
        planning_revision_context = (
            "[RESEARCH_PRODUCER_V2]" in request_context
            and "stage=planning" in request_context
        )
        experiment_design_context = (
            "[RESEARCH_PRODUCER_V2]" in request_context
            and "stage=experiment_design" in request_context
        )
        experiment_design_protocol = next(
            (
                protocol
                for protocol in (
                    "silso_cycle_morphology_v1",
                    "solar_cycle_26_forecast_backtest_v1",
                )
                if protocol in request_context
            ),
            None,
        )
        data_stage_context = (
            "[RESEARCH_PRODUCER_V2]" in request_context
            and "stage=data" in request_context
        )
        evidence_review_context = "[EVIDENCE_REVIEW_V2]" in request_context
        source_restricted_hypothesis = (
            "[RESEARCH_PRODUCER_V2]" in request_context
            and "stage=hypothesis" in request_context
            and any(
                protocol in request_context
                for protocol in _SOURCE_RESTRICTED_HYPOTHESIS_PROTOCOLS
            )
        )
        final_release_context = (
            "<research_route>" in request_context
            and "Deterministic next_action=" in request_context
            and '"prepare_release"' in request_context
            and '"final_release"' in request_context
        )
        closed_loop_context = any(
            marker in request_context
            for marker in ("[RESEARCH_PRODUCER_V2]", "[EVIDENCE_REVIEW_V2]")
        )
        if closed_loop_context:
            # Qwen already has a bounded native reasoning channel. An explicit
            # think_tool adds another model/tool edge, persists private scratch
            # text, and in live runs encouraged reflection-without-state-change.
            # Keep it available outside the typed research loop.
            request_tools = [
                tool for tool in request_tools if _tool_name(tool) != "think_tool"
            ]
            overrides["tools"] = request_tools
        if source_restricted_hypothesis:
            request_tools = [
                tool
                for tool in request_tools
                if _tool_name(tool).startswith("scientific_hypothesis_")
                and _tool_name(tool)
                not in _SOURCE_RESTRICTED_HYPOTHESIS_DISCOVERY_TOOLS
            ]
            overrides["tools"] = request_tools
            system_message = append_to_system_message(
                system_message,
                _SOURCE_RESTRICTED_HYPOTHESIS_INSTRUCTION,
            )
            overrides["system_message"] = system_message
            model_settings = dict(getattr(request, "model_settings", None) or {})
            model_settings["parallel_tool_calls"] = False
            overrides["model_settings"] = model_settings
            if _source_restricted_host_seed_bound(projected_messages):
                request_tools = [
                    tool
                    for tool in request_tools
                    if _tool_name(tool) not in _SOURCE_RESTRICTED_PREBOUND_TOOLS
                ]
                overrides["tools"] = request_tools
                system_message = append_to_system_message(
                    system_message,
                    _SOURCE_RESTRICTED_PREBOUND_INSTRUCTION,
                )
                overrides["system_message"] = system_message
        if final_release_context:
            # ResearchRouter is intentionally earlier in the middleware stack,
            # but generic filesystem/todo middleware can add tools afterwards.
            # Re-assert the release boundary at the final provider edge so a
            # stale context action cannot consume the only synthesis turn.
            request_tools = [
                tool
                for tool in request_tools
                if _tool_name(tool) == "research_release_prepare"
            ]
            overrides["tools"] = request_tools
            system_message = append_to_system_message(
                system_message,
                _FINAL_RELEASE_GATE_INSTRUCTION,
            )
            overrides["system_message"] = system_message
            model_settings = dict(getattr(request, "model_settings", None) or {})
            model_settings["parallel_tool_calls"] = False
            overrides["model_settings"] = model_settings
        if planning_revision_context or evidence_review_context:
            # Qwen frequently emits every planned JSON replacement in one
            # parallel tool-call response. Those responses are both too large
            # for a reliable upstream connection and prone to schema drift.
            # Force one bounded read/write per model turn; the shadow-candidate
            # tools preserve progress between turns.
            model_settings = dict(getattr(request, "model_settings", None) or {})
            model_settings["parallel_tool_calls"] = False
            overrides["model_settings"] = model_settings
        if experiment_design_context:
            # Registered experiment protocols are a strict bind -> inspect ->
            # design state machine.  A provider-side parallel response can
            # otherwise fan one forced transition out into many duplicate,
            # state-mutating tool calls.
            model_settings = dict(getattr(request, "model_settings", None) or {})
            model_settings["parallel_tool_calls"] = False
            overrides["model_settings"] = model_settings
            attempted = self._experiment_design_attempted_tools(
                projected_messages,
                protocol=experiment_design_protocol,
            )
            if attempted:
                request_tools = [
                    tool for tool in request_tools if _tool_name(tool) not in attempted
                ]
                overrides["tools"] = request_tools
        verified_data_terminal = (
            data_stage_context
            and self._latest_data_terminal_receipt_is_verified(projected_messages)
        )
        if verified_data_terminal:
            request_tools = []
            overrides["tools"] = []
        deterministic_tool = self._deterministic_planner_tool(
            projected_messages,
            request_tools,
            enabled=planning_revision_context,
        )
        if deterministic_tool is None:
            deterministic_tool = self._deterministic_data_tool(
                projected_messages,
                request_tools,
                enabled=data_stage_context,
                preopened_context=self._preopened_data_context(
                    str(system_message.content)
                ),
            )
        if deterministic_tool is None:
            deterministic_tool = self._deterministic_experiment_design_tool(
                projected_messages,
                request_tools,
                enabled=experiment_design_context,
                protocol=experiment_design_protocol,
            )
        if deterministic_tool is None:
            deterministic_tool = self._deterministic_evidence_submit_tool(
                projected_messages,
                request_tools,
                enabled=evidence_review_context,
                review_mode=self._evidence_review_mode(request_context),
            )
        tool_choice = None if verified_data_terminal else request.tool_choice
        if verified_data_terminal:
            overrides["tool_choice"] = None
        if deterministic_tool is not None:
            # ChatAnthropic (used by Kimi for Coding) accepts a tool name or
            # Anthropic ``{type: tool, name: ...}``, not the OpenAI
            # ``{type: function, function: ...}`` shape used by ChatOpenAI.
            # A plain name is normalized by LangChain for the provider while
            # preserving the same required-tool semantics.
            model_class = type(request.model)
            if deterministic_tool == "research_planner_create_empirical_plan":
                # The Qwen business-space route rejects object/required
                # tool_choice even when the request explicitly disables
                # thinking. Expose only the compact plan tool and retain auto
                # selection, so the model still supplies every scientific
                # argument without reopening the full planner tool surface.
                request_tools = [
                    tool
                    for tool in request_tools
                    if _tool_name(tool) == deterministic_tool
                ]
                overrides["tools"] = request_tools
                system_message = append_to_system_message(
                    system_message,
                    self._atomic_empirical_plan_instruction(deterministic_tool),
                )
                overrides["system_message"] = system_message
                tool_choice = None
            elif (
                deterministic_tool == _EVIDENCE_SUBMIT_TOOL
                and self._active_model_is_qwen(request)
            ):
                # Qwen3.8-Max can remain provider-side thinking-enabled even
                # after ``disable_thinking`` adds ``enable_thinking=false``.
                # DashScope then rejects the OpenAI object/required
                # ``tool_choice`` used by a forced atomic review. Expose only
                # the already-resolved submit tool, keep selection on auto,
                # and make the one allowed action explicit in the prompt. The
                # model still supplies the complete review arguments while
                # the request remains valid in thinking mode.
                request_tools = [
                    tool
                    for tool in request_tools
                    if _tool_name(tool) == deterministic_tool
                ]
                overrides["tools"] = request_tools
                system_message = append_to_system_message(
                    system_message,
                    self._atomic_evidence_submit_instruction(deterministic_tool),
                )
                overrides["system_message"] = system_message
                tool_choice = None
            elif model_class.__module__.startswith("langchain_anthropic"):
                # Kimi for Coding is always-thinking.  Its Anthropic endpoint
                # rejects *all* forced tool choices while thinking is active.
                # At the scientific-decision edge, expose only the atomic
                # submit tool and leave selection on auto; the model still
                # supplies every assessment/verdict argument.
                request_tools = [
                    tool
                    for tool in request_tools
                    if _tool_name(tool) == deterministic_tool
                ]
                overrides["tools"] = request_tools
                system_message = append_to_system_message(
                    system_message,
                    self._atomic_evidence_submit_instruction(deterministic_tool),
                )
                overrides["system_message"] = system_message
                tool_choice = None
            else:
                tool_choice = {
                    "type": "function",
                    "function": {"name": deterministic_tool},
                }
            overrides["tool_choice"] = tool_choice
        explicitly_disabled = (
            dict(getattr(request.model, "extra_body", None) or {}).get(
                "enable_thinking"
            )
            is False
        )
        forced_tool_choice = tool_choice not in (None, False, "auto", "none")
        if explicitly_disabled or forced_tool_choice:
            # DashScope rejects required/object tool_choice in thinking mode.
            # Keep the forced choice (needed by structured output) and disable
            # thinking only for this request instead of weakening the contract
            # to tool_choice="auto".  Also preserve an explicit non-thinking
            # model supplied by a structured worker; do not re-enable thinking
            # merely because its forced choice is attached by a later strategy.
            #
            # Replaying ANY ``reasoning_content`` history can still make the
            # provider classify the whole conversation as thinking mode even
            # when this request itself sets ``enable_thinking=false``. A forced
            # no-deliberation transition therefore strips reasoning from every
            # prior AI message, not just the older rounds.
            overrides["model"] = disable_thinking(request.model)
            overrides["messages"] = self._strip_all_reasoning(projected_messages)
            if forced_tool_choice:
                self._log_forced_request_safely(
                    tool_choice=tool_choice,
                    model=overrides["model"],
                    messages=overrides["messages"],
                )
        else:
            # Qwen3's own API exposes a hard thinking budget.  A bounded
            # request is more reliable than asking a reasoning model to decide
            # for itself when to stop, and review receives a larger allowance
            # than routine orchestration.
            overrides["model"] = configure_qwen_thinking(
                request.model,
                thinking_budget=self._thinking_budget(request),
                preserve_thinking=True,
            )
            overrides["messages"] = self._bound_reasoning_history(projected_messages)
        return request.override(**overrides)

    @staticmethod
    def _atomic_evidence_submit_instruction(tool_name: str) -> str:
        return (
            "All declared evidence sources have now been inspected. The only "
            f"remaining action is to call {tool_name} once with the complete "
            "scientific assessment and routing verdict. Do not return prose. "
            "In assessment_claims, provide exactly one row for each artifact "
            "claim_id and reuse only the exact ids returned by "
            "evidence_review_open_context; never append a component suffix or "
            "invent a new id. In scientific_quality_claims, provide one "
            "scientific_quality_claims row per artifact claim by default, using "
            "its load-bearing component; add another component row only when the "
            "artifact states a distinct numeric result or prediction that needs "
            "a separate decision. Keep each prose field to one short sentence, "
            "and do not repeat source quotations outside locator. "
            "When sources span instrument, proxy, or processing regimes, set "
            "independent_sample_count to the scope-matched complete independent units "
            "for the reviewed component. A broader table row count may be stated in "
            "notes but must not raise the conclusion cap; mark mixed-regime evidence "
            "as partial unless a documented harmonization supports pooling. An accept or "
            "accept_with_limits data verdict must also compare the produced fields and "
            "derivable inputs with every load-bearing observable requested by the task. "
            "If a requested core variable and the inputs needed to calculate it are absent, "
            "treat that absence as a major gap and use revise or block; the existence of a "
            "related historical table is not sufficient. An accept or "
            "accept_with_limits verdict must enumerate "
            "accepted_claims using the exact reviewed claim ids. An "
            "evidence_matrix row whose evidence_role is gap must omit source_ref "
            "or set it to null, because a gap is not a source. An accept or "
            "accept_with_limits verdict may carry only minor or informational "
            "issues. Put retained scientific limitations in carry_forward_limits; "
            "do not also encode them as issue rows unless a producer revision is "
            "actually required. An explicitly labeled exploratory hypothesis may be "
            "accepted with limits when it claims no empirical confirmation, records "
            "its evidence gaps, and supplies falsifiable predictions plus a bounded "
            "next test; absence of a completed test is not itself a defect in a "
            "hypothesis proposal. Before reporting a missing portfolio or novelty "
            "field, inspect the candidate fields in the artifact. Explicit alternatives, "
            "confounders, and falsification conditions satisfy the portfolio structure, "
            "and novelty_not_assessed with coverage gaps is valid when no priority or "
            "novelty claim is made. If any unresolved critical or major issue remains, "
            "use revise or block. novelty_assessment.search_cutoff must be null or "
            "a complete ISO-8601 timestamp including time and timezone, not a bare "
            "calendar date. For revise, next_owner must be a producer at or "
            "before the reviewed stage; for data use solar-data or solar-planner, "
            "never solar-hypothesis."
        )

    @staticmethod
    def _atomic_empirical_plan_instruction(tool_name: str) -> str:
        return (
            f"Call {tool_name} exactly once now; it is the only available tool. "
            "Return no prose. compact_plan_json must be a JSON string encoding "
            "exactly these fields: scope with objective, population_or_period, "
            "boundaries, non_goals; subquestions with question, purpose, "
            "completion_evidence; evidence_gaps as strings; datasets with name, "
            "purpose, required_variables, time_coverage_needed, cadence_needed, "
            "quality_requirements; stage_methods with exactly the five keys data, "
            "hypothesis_generation, experiment_design, experiment_result, "
            "hypothesis_update and one method string per key; evaluation_focus as "
            "strings. Preserve the exact bound research question. "
            "Do not add route transitions, lifecycle fields, stage identifiers, "
            "artifact identifiers, or a claimed result; the host constructs those "
            "generic fields and later agents perform the research."
        )

    @staticmethod
    def _evidence_review_mode(content: str) -> str | None:
        explicit = list(
            re.finditer(
                r"(?m)^review_mode=(planning|data|hypothesis|experiment_design|"
                r"experiment_result|integration|final_release)\s*$",
                content,
            )
        )
        if explicit:
            return explicit[-1].group(1)
        delegated = list(
            re.finditer(
                r"(?i)\b(planning|data|hypothesis|experiment[ _-]design|"
                r"experiment[ _-]result|integration|final[ _-]release)"
                r"[ _-](?:stage[ _-])?review\b",
                content,
            )
        )
        if delegated:
            return re.sub(r"[ -]", "_", delegated[-1].group(1).lower())
        return None

    @classmethod
    def _evidence_navigation_state(
        cls,
        messages: Sequence[BaseMessage],
        review_mode: str | None,
    ) -> tuple[dict[str, Any] | None, list[str], set[str], int, bool]:
        """Return opened context, ordered sources, reads, and submit state."""

        if review_mode is None:
            return None, [], set(), 0, False
        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index
        calls: dict[str, tuple[str, Mapping[str, Any]]] = {}
        opened: dict[str, Any] | None = None
        read_refs: set[str] = set()
        submit_attempts = 0
        submit_succeeded = False
        for message in messages[latest_human_index + 1 :]:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    name = call.get("name")
                    args = call.get("args")
                    call_id = call.get("id")
                    if (
                        isinstance(name, str)
                        and isinstance(args, Mapping)
                        and isinstance(call_id, str)
                    ):
                        calls[call_id] = (name, args)
                        if name == _EVIDENCE_SUBMIT_TOOL:
                            submit_attempts += 1
                continue
            if not isinstance(message, ToolMessage):
                continue
            call = calls.get(str(message.tool_call_id))
            if call is None:
                continue
            name, args = call
            if name == _EVIDENCE_OPEN_TOOL:
                try:
                    payload = json.loads(cls._message_text(message))
                except (TypeError, ValueError):
                    continue
                result = payload.get("result") if isinstance(payload, Mapping) else None
                if payload.get("ok") is True and isinstance(result, Mapping):
                    opened = dict(result)
            elif name == _EVIDENCE_READ_TOOL:
                source_ref = args.get("source_ref")
                if isinstance(source_ref, str) and source_ref:
                    try:
                        payload = json.loads(cls._message_text(message))
                    except (TypeError, ValueError):
                        continue
                    # A structured read error is an inspected gap, not a reason
                    # to reopen the same immutable source until the tool budget
                    # is exhausted.  Preserve the error ToolMessage for the
                    # reviewer and advance to the atomic verdict submission.
                    if isinstance(payload, Mapping) and isinstance(
                        payload.get("ok"), bool
                    ):
                        read_refs.add(source_ref)
            elif name == _EVIDENCE_SUBMIT_TOOL:
                try:
                    payload = json.loads(cls._message_text(message))
                except (TypeError, ValueError):
                    continue
                submit_succeeded = (
                    isinstance(payload, Mapping) and payload.get("ok") is True
                )

        ordered_refs: list[str] = []
        if opened is not None:
            for artifact in opened.get("artifacts", []):
                if not isinstance(artifact, Mapping):
                    continue
                candidates = list(artifact.get("evidence_refs", []))
                for claim in artifact.get("claims", []):
                    if not isinstance(claim, Mapping):
                        continue
                    candidates.extend(claim.get("supporting_evidence", []))
                    candidates.extend(claim.get("opposing_evidence", []))
                    candidates.extend(claim.get("limiting_evidence", []))
                for source_ref in candidates:
                    if (
                        isinstance(source_ref, str)
                        and source_ref
                        and source_ref not in ordered_refs
                    ):
                        ordered_refs.append(source_ref)
        return opened, ordered_refs, read_refs, submit_attempts, submit_succeeded

    @classmethod
    def _deterministic_evidence_submit_tool(
        cls,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
        *,
        enabled: bool,
        review_mode: str | None,
    ) -> str | None:
        """Force the one scientific decision call after deterministic inspection."""

        if not enabled:
            return None
        available = {name for tool in tools if (name := _tool_name(tool)) is not None}
        (
            opened,
            ordered_refs,
            read_refs,
            submit_attempts,
            submit_succeeded,
        ) = cls._evidence_navigation_state(messages, review_mode)
        if (
            opened is not None
            and all(source_ref in read_refs for source_ref in ordered_refs)
            and not submit_succeeded
            and submit_attempts < 2
            and _EVIDENCE_SUBMIT_TOOL in available
        ):
            return _EVIDENCE_SUBMIT_TOOL
        return None

    @classmethod
    def _latest_data_terminal_receipt_is_verified(
        cls,
        messages: Sequence[BaseMessage],
    ) -> bool:
        """Accept only the latest explicit terminal Data receipt."""

        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index
        terminal_tools = {
            "prepare_solar_cycle_26_readiness",
            "run_solar_cycle_26_historical_forecast",
            "prepare_solar_precursor_cycle_table",
            "reproduce_silso_cycle_extrema",
        }
        terminal_receipts = {
            "receipts/datasets/solar_cycle_26_readiness_inventory.json",
            "receipts/datasets/solar_precursor_cycle_table.json",
            "receipts/datasets/silso_cycle_extrema.json",
            "receipts/datasets/solar_cycle_26_forecast_backtest.json",
        }
        for message in reversed(messages[latest_human_index + 1 :]):
            if not isinstance(message, ToolMessage):
                continue
            try:
                payload = json.loads(cls._message_text(message))
            except (TypeError, ValueError):
                if message.name in terminal_tools:
                    return False
                continue
            if not isinstance(payload, Mapping):
                if message.name in terminal_tools:
                    return False
                continue
            receipt_refs = payload.get("receipt_refs")
            typed_terminal_payload = (
                isinstance(receipt_refs, Sequence)
                and not isinstance(receipt_refs, (str, bytes))
                and any(
                    isinstance(ref, str) and ref in terminal_receipts
                    for ref in receipt_refs
                )
            )
            if message.name not in terminal_tools and not typed_terminal_payload:
                continue
            return payload.get("status") == "verified"
        return False

    @classmethod
    def _deterministic_data_tool(
        cls,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
        *,
        enabled: bool,
        preopened_context: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Force the bounded Data-stage discovery and curated adapter route."""

        if not enabled:
            return None
        available = set(validate_qwen_tool_schema(tools))
        open_tool = "solar_data_open_context"
        readiness_tool = "prepare_solar_cycle_26_readiness"
        forecast_tool = "run_solar_cycle_26_historical_forecast"
        prepare_tool = "prepare_solar_precursor_cycle_table"
        reproduce_tool = "reproduce_silso_cycle_extrema"

        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index

        relevant = [
            message
            for message in messages[latest_human_index + 1 :]
            if isinstance(message, ToolMessage)
            and message.name
            in {open_tool, readiness_tool, forecast_tool, prepare_tool, reproduce_tool}
        ]
        if relevant and relevant[-1].name in {
            readiness_tool,
            forecast_tool,
            prepare_tool,
            reproduce_tool,
        }:
            return None
        if preopened_context is None:
            if not relevant:
                return open_tool if open_tool in available else None
            latest = relevant[-1]
            if latest.name != open_tool:
                return None
            try:
                payload = json.loads(cls._message_text(latest))
            except (TypeError, ValueError):
                return None
            if not isinstance(payload, Mapping):
                return None
        else:
            payload = preopened_context
        if (
            payload.get("must_stop") is True
            or payload.get("status") != "inputs_available"
        ):
            return None
        eligible = payload.get("eligible_inputs")
        if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
            return None
        dataset_ids = {
            str(item.get("dataset_id"))
            for item in eligible
            if isinstance(item, Mapping)
        }
        if (
            payload.get("analysis_protocol") == _SOLAR_CYCLE_26_READINESS_PROTOCOL
            and payload.get("required_data_product")
            == _SOLAR_CYCLE_26_READINESS_DATA_PRODUCT
            and _SOLAR_CYCLE_26_READINESS_DATASET_IDS <= dataset_ids
            and readiness_tool in available
        ):
            return readiness_tool
        if (
            payload.get("analysis_protocol")
            == _SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL
            and payload.get("required_data_product")
            == _SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT
            and _SILSO_REPRODUCTION_DATASET_IDS <= dataset_ids
            and forecast_tool in available
        ):
            return forecast_tool
        if (
            payload.get("analysis_protocol") == _SILSO_REPRODUCTION_PROTOCOL
            and payload.get("required_data_product") == _SILSO_EXTREMA_DATA_PRODUCT
            and _SILSO_REPRODUCTION_DATASET_IDS <= dataset_ids
            and reproduce_tool in available
        ):
            return reproduce_tool
        if (
            payload.get("required_data_product") == _SOLAR_PRECURSOR_DATA_PRODUCT
            and _SOLAR_PRECURSOR_DATASET_IDS <= dataset_ids
            and prepare_tool in available
        ):
            return prepare_tool
        return None

    @classmethod
    def _deterministic_experiment_design_tool(
        cls,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
        *,
        enabled: bool,
        protocol: str | None,
    ) -> str | None:
        """Route registered protocols to their host-owned design adapter.

        The generic single-stage schema is intentionally flexible, but it is
        the wrong boundary for the two registered solar protocols: their
        design (and worker contract) is pre-registered and deterministic.
        Qwen can otherwise spend its whole design budget repairing a free-form
        schema even though the host already has a valid design builder.
        """
        if not enabled or protocol not in {
            "silso_cycle_morphology_v1",
            "solar_cycle_26_forecast_backtest_v1",
        }:
            return None
        specialized = {
            "silso_cycle_morphology_v1": "automatic_experiment_create_silso_morphology_design",
            "solar_cycle_26_forecast_backtest_v1": "automatic_experiment_create_sc26_forecast_design",
        }[protocol]
        bind_tool = "automatic_experiment_bind_request"
        inspect_tool = "automatic_experiment_inspect_inputs"
        available = {name for tool in tools if (name := _tool_name(tool)) is not None}

        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index
        bound_run_id: str | None = None
        inspected_run_id: str | None = None
        for message in messages[latest_human_index + 1 :]:
            if not isinstance(message, ToolMessage):
                continue
            try:
                payload = json.loads(cls._message_text(message))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            nested = payload.get("result")
            receipt = nested if isinstance(nested, Mapping) else payload
            run_id = receipt.get("run_id")
            if message.name == bind_tool and isinstance(run_id, str) and run_id:
                if (
                    receipt.get("status") == "request_bound"
                    or receipt.get("error_code")
                    == "RESEARCH_EXPERIMENT_SCOPE_ALREADY_BOUND"
                ):
                    bound_run_id = run_id
                    inspected_run_id = None
            elif (
                message.name == inspect_tool
                and isinstance(run_id, str)
                and run_id == bound_run_id
                and receipt.get("status")
                in {"inputs_snapshotted", "already_snapshotted"}
            ):
                if receipt.get("phase") == "design_validated":
                    # Resuming an already completed design requires no builder.
                    return None
                inspected_run_id = run_id
            elif message.name == specialized:
                # The protocol permits exactly one builder call.  Flat tool
                # receipts are canonical, while nested receipts remain accepted
                # for compatibility.  Success and failure both end forcing so
                # a malformed call cannot become an unbounded retry loop.
                return None

        attempted = cls._experiment_design_attempted_tools(
            messages,
            protocol=protocol,
        )
        if bind_tool not in attempted:
            return bind_tool if bind_tool in available else None
        if bound_run_id is None:
            return None
        if inspect_tool not in attempted:
            return inspect_tool if inspect_tool in available else None
        if inspected_run_id != bound_run_id:
            return None
        return specialized if specialized in available else None

    @classmethod
    def _experiment_design_attempted_tools(
        cls,
        messages: Sequence[BaseMessage],
        *,
        protocol: str | None,
    ) -> set[str]:
        """Return protocol lifecycle tools already attempted in this turn."""

        if protocol not in {
            "silso_cycle_morphology_v1",
            "solar_cycle_26_forecast_backtest_v1",
        }:
            return set()
        specialized = {
            "silso_cycle_morphology_v1": "automatic_experiment_create_silso_morphology_design",
            "solar_cycle_26_forecast_backtest_v1": "automatic_experiment_create_sc26_forecast_design",
        }[protocol]
        lifecycle_tools = {
            "automatic_experiment_bind_request",
            "automatic_experiment_inspect_inputs",
            specialized,
        }
        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index
        return {
            str(message.name)
            for message in messages[latest_human_index + 1 :]
            if isinstance(message, ToolMessage) and message.name in lifecycle_tools
        }

    @staticmethod
    def _preopened_data_context(content: str) -> Mapping[str, Any] | None:
        """Extract the Supervisor-injected context without parsing later prose."""

        marker = "deterministic_data_context="
        start = content.rfind(marker)
        if start < 0:
            return None
        candidate = content[start + len(marker) :].lstrip()
        try:
            payload, _end = json.JSONDecoder().raw_decode(candidate)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, Mapping) else None

    @classmethod
    def _deterministic_planner_tool(
        cls,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
        *,
        enabled: bool,
    ) -> str | None:
        """Resolve a no-deliberation planner transition from the latest receipt."""

        if not enabled:
            return None
        available = set(validate_qwen_tool_schema(tools))
        for message in reversed(messages):
            if not isinstance(message, ToolMessage):
                continue
            if message.name not in {
                "research_planner_get_brief",
                "research_planner_get_draft_status",
                "research_planner_create_empirical_plan",
                "research_planner_update_draft",
                "research_planner_validate_draft",
                "research_planner_apply_revision_patch",
                "research_planner_stage_revision_section",
                "research_planner_commit_revision_candidate",
            }:
                return None
            try:
                payload = json.loads(cls._message_text(message))
            except (TypeError, ValueError):
                return None
            if not isinstance(payload, Mapping):
                return None
            recommended_tool = payload.get("recommended_next_tool")
            if (
                recommended_tool == "research_planner_create_empirical_plan"
                and recommended_tool in available
            ):
                return recommended_tool
            checkpoint = payload.get("draft_checkpoint")
            next_action = (
                checkpoint.get("next_action")
                if isinstance(checkpoint, Mapping)
                else payload.get("next_action")
            )
            if payload.get("status") == "plan_ready":
                next_action = "freeze_plan"
            tool_name = _PLANNER_DETERMINISTIC_ACTION_TO_TOOL.get(
                str(next_action or "")
            )
            return tool_name if tool_name in available else None
        return None

    @staticmethod
    def _bound_reasoning_history(
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """Keep only the immediately actionable Qwen tool-loop reasoning.

        DashScope can reuse ``reasoning_content`` when ``preserve_thinking`` is
        enabled, but replaying every old trace compounds input cost and makes a
        long-running Qwen agent anchor on abandoned plans. Research artifacts
        and verdict capsules are the durable memory; model-visible raw reasoning
        is retained only for the newest tool-call round in the current user turn.
        """

        latest_human_index = -1
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index

        keep_index: int | None = None
        for index in range(len(messages) - 1, latest_human_index, -1):
            message = messages[index]
            if isinstance(message, AIMessage) and message.tool_calls:
                keep_index = index
                break

        bounded: list[BaseMessage] = []
        for index, message in enumerate(messages):
            if not isinstance(message, AIMessage) or index == keep_index:
                bounded.append(message)
                continue
            additional = dict(message.additional_kwargs)
            if "reasoning_content" not in additional:
                bounded.append(message)
                continue
            additional.pop("reasoning_content", None)
            bounded.append(message.model_copy(update={"additional_kwargs": additional}))
        return bounded

    @staticmethod
    def _strip_all_reasoning(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Drop ``reasoning_content`` from every AI message, not just old rounds.

        Unlike :meth:`_bound_reasoning_history`, which keeps the newest tool-call
        round's reasoning for continuity, a forced ``tool_choice`` transition is
        a no-deliberation edge: replaying any reasoning channel risks the
        provider reclassifying the request as thinking mode and rejecting it.
        """

        stripped: list[BaseMessage] = []
        for message in messages:
            if not isinstance(message, AIMessage):
                stripped.append(message)
                continue
            additional = dict(message.additional_kwargs)
            if "reasoning_content" not in additional:
                stripped.append(message)
                continue
            additional.pop("reasoning_content", None)
            stripped.append(
                message.model_copy(update={"additional_kwargs": additional})
            )
        return stripped

    @staticmethod
    def _log_forced_request_safely(
        *, tool_choice: Any, model: Any, messages: Sequence[BaseMessage]
    ) -> None:
        """Record provider-safe diagnostics for a forced ``tool_choice`` request.

        Only structural fields are logged. Message text, headers, and any
        credential material are deliberately excluded so this can stay enabled
        in the shared langgraph dev log.
        """

        extra_body = dict(getattr(model, "extra_body", None) or {})
        reasoning_history = any(
            isinstance(message, AIMessage)
            and "reasoning_content" in dict(message.additional_kwargs)
            for message in messages
        )
        _logger.info(
            "[jw.middleware.qwen_compat] forced tool_choice request: "
            "model_class=%s model_name=%s base_url_host=%s tool_choice=%s "
            "enable_thinking=%s thinking_budget_present=%s "
            "preserve_thinking_present=%s reasoning_in_history=%s",
            type(model).__name__,
            str(getattr(model, "model_name", None) or getattr(model, "model", "")),
            _safe_host(
                getattr(model, "openai_api_base", None)
                or getattr(model, "base_url", None)
            ),
            _safe_tool_choice_label(tool_choice),
            extra_body.get("enable_thinking"),
            "thinking_budget" in extra_body,
            "preserve_thinking" in extra_body,
            reasoning_history,
        )

    @classmethod
    def _compact_tool_error_messages(
        cls, messages: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        """Replace oversized tracebacks with stable, decision-useful capsules."""
        compacted: list[BaseMessage] = []
        exception_line = re.compile(
            r"^(?:[A-Za-z_][\w.]*)(?:Error|Exception|Timeout):\s*.+$"
        )
        for message in messages:
            if not isinstance(message, ToolMessage):
                compacted.append(message)
                continue
            rendered = cls._message_text(message)
            if (
                not rendered.lstrip().startswith("[TOOL ERROR]")
                or len(rendered) <= _TOOL_ERROR_COMPACT_THRESHOLD
            ):
                compacted.append(message)
                continue
            summary = "tool execution failed"
            for line in reversed(rendered.splitlines()):
                stripped = line.strip()
                if exception_line.fullmatch(stripped):
                    summary = stripped[:600]
                    break
            fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            content = (
                "[TOOL ERROR CAPSULE]\n"
                f"fingerprint={fingerprint}\n"
                f"tool={message.name or 'unknown'}\n"
                f"error={summary}\n"
                "retry_policy=one identical graph-level retry is allowed; "
                "after two identical failures stop"
            )
            compacted.append(message.model_copy(update={"content": content}))
        return compacted

    @classmethod
    def _compact_repeated_tool_rounds(
        cls, messages: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        """Keep one of consecutive identical tool-call/result rounds.

        DashScope rejects a request before inference when conversation history
        contains the same function name and arguments across several consecutive
        rounds.  ResearchRunStateV2 retains the complete audit history; this
        projection only removes redundant model-visible rounds and always keeps
        a protocol-valid AI/tool pair.
        """

        output: list[BaseMessage] = []
        index = 0
        pending_signature: tuple[tuple[str, str], ...] | None = None
        pending_round: list[BaseMessage] = []

        def flush_pending() -> None:
            nonlocal pending_signature, pending_round
            if pending_round:
                output.extend(pending_round)
            pending_signature = None
            pending_round = []

        while index < len(messages):
            message = messages[index]
            if not isinstance(message, AIMessage):
                flush_pending()
                output.append(message)
                index += 1
                continue
            signature = cls._tool_call_signature(message)
            call_ids = {
                str(call.get("id"))
                for call in message.tool_calls
                if isinstance(call.get("id"), str)
            }
            if not signature or not call_ids:
                flush_pending()
                output.append(message)
                index += 1
                continue
            end = index + 1
            results: list[BaseMessage] = []
            seen: set[str] = set()
            while end < len(messages) and isinstance(messages[end], ToolMessage):
                tool_message = messages[end]
                tool_call_id = str(tool_message.tool_call_id)
                if tool_call_id not in call_ids:
                    break
                results.append(tool_message)
                seen.add(tool_call_id)
                end += 1
            if seen != call_ids:
                flush_pending()
                output.append(message)
                index += 1
                continue
            round_messages = [message, *results]
            if signature == pending_signature:
                # Retain the newest result because it carries the latest stop or
                # state-machine instruction while avoiding a provider rejection.
                pending_round = round_messages
            else:
                flush_pending()
                pending_signature = signature
                pending_round = round_messages
            index = end

        flush_pending()
        return output

    @staticmethod
    def _recover_reasoning_tool_call(
        response: ModelResponse,
        tools: list[BaseTool | dict[str, Any]],
    ) -> ModelResponse:
        """Recover a strict Qwen/QwQ tool JSON misplaced in reasoning content."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        if message.tool_calls or message.invalid_tool_calls or message.text.strip():
            return response
        reasoning = message.additional_kwargs.get("reasoning_content")
        if not isinstance(reasoning, str):
            return response
        try:
            candidate = json.loads(reasoning)
        except (TypeError, ValueError):
            return response
        if not isinstance(candidate, dict):
            return response
        name = candidate.get("name")
        arguments = candidate.get("arguments")
        if not isinstance(name, str) or name not in validate_qwen_tool_schema(tools):
            return response
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                return response
        if not isinstance(arguments, dict):
            return response

        digest = hashlib.sha256(reasoning.encode("utf-8")).hexdigest()[:24]
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        recovered = message.model_copy(
            update={
                "tool_calls": [
                    {
                        "name": name,
                        "args": arguments,
                        "id": f"call_qwen_recovered_{digest}",
                        "type": "tool_call",
                    }
                ],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[recovered],
            structured_response=response.structured_response,
        )

    @staticmethod
    def _serialize_planner_revision_calls(
        response: ModelResponse,
        *,
        enabled: bool,
    ) -> ModelResponse:
        """Keep one bounded planner revision call if a provider ignores the flag."""
        if not enabled or not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        if len(message.tool_calls) <= 1 or not any(
            call.get("name") in _PLANNER_SERIAL_TOOL_NAMES
            for call in message.tool_calls
        ):
            return response
        first = message.tool_calls[0]
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        metadata["jw_deferred_parallel_tool_calls"] = len(message.tool_calls) - 1
        serialized = message.model_copy(
            update={
                "content": "",
                "tool_calls": [first],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[serialized],
            structured_response=response.structured_response,
        )

    @staticmethod
    def _serialize_experiment_design_calls(
        response: ModelResponse,
        *,
        enabled: bool,
    ) -> ModelResponse:
        """Keep exactly one lifecycle transition in an experiment-design turn."""

        if not enabled or not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        lifecycle_names = {
            "automatic_experiment_bind_request",
            "automatic_experiment_inspect_inputs",
            "automatic_experiment_create_silso_morphology_design",
            "automatic_experiment_create_sc26_forecast_design",
        }
        if len(message.tool_calls) <= 1 or not any(
            call.get("name") in lifecycle_names for call in message.tool_calls
        ):
            return response
        first = next(
            call for call in message.tool_calls if call.get("name") in lifecycle_names
        )
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        metadata["jw_deferred_parallel_tool_calls"] = len(message.tool_calls) - 1
        serialized = message.model_copy(
            update={
                "content": "",
                "tool_calls": [first],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[serialized],
            structured_response=response.structured_response,
        )

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        return "\n".join(
            str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
            for block in content
        )

    @classmethod
    def _latest_artifact_manifest(
        cls,
        messages: Sequence[BaseMessage],
    ) -> tuple[int, tuple[str, ...]] | None:
        latest: tuple[int, tuple[str, ...]] | None = None
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            matches = list(
                _ARTIFACT_MANIFEST_PATTERN.finditer(cls._message_text(message))
            )
            if not matches:
                continue
            paths: list[str] = []
            for match in matches:
                try:
                    payload = json.loads(match.group("payload"))
                except (TypeError, ValueError):
                    continue
                for item in payload.get("files", []):
                    if not isinstance(item, Mapping):
                        continue
                    path = item.get("path")
                    if isinstance(path, str) and path.startswith("/"):
                        paths.append(path)
            if paths:
                latest = (index, tuple(dict.fromkeys(paths)))
        return latest

    @staticmethod
    def _attempted_readback_paths(
        messages: Sequence[BaseMessage],
        *,
        after_index: int,
    ) -> set[str]:
        attempted: set[str] = set()
        for message in messages[after_index + 1 :]:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls:
                if call.get("name") != "read_file":
                    continue
                args = call.get("args")
                if not isinstance(args, Mapping):
                    continue
                path = args.get("file_path") or args.get("path")
                if isinstance(path, str):
                    attempted.add(path)
        return attempted

    @classmethod
    def _enforce_artifact_readback(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
    ) -> ModelResponse:
        """Turn a premature final answer into deterministic read_file calls."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        if (
            message.tool_calls
            or message.invalid_tool_calls
            or not message.text.strip()
            or "read_file" not in validate_qwen_tool_schema(tools)
        ):
            return response

        manifest = cls._latest_artifact_manifest(messages)
        if manifest is None:
            return response
        manifest_index, artifact_paths = manifest
        attempted = cls._attempted_readback_paths(
            messages,
            after_index=manifest_index,
        )
        missing = [path for path in artifact_paths if path not in attempted]
        if not missing:
            return response

        tool_calls = []
        for path in missing[:_ARTIFACT_READBACK_BATCH_SIZE]:
            digest = hashlib.sha256(
                f"artifact-readback:{manifest_index}:{path}".encode()
            ).hexdigest()[:24]
            tool_calls.append(
                {
                    "name": "read_file",
                    "args": {"file_path": path},
                    "id": f"call_qwen_readback_{digest}",
                    "type": "tool_call",
                }
            )
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        enforced = message.model_copy(
            update={
                "content": "",
                "tool_calls": tool_calls,
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[enforced],
            structured_response=response.structured_response,
        )

    @staticmethod
    def _completed_tool_events(
        messages: Sequence[BaseMessage],
    ) -> list[tuple[str, int, int, ToolMessage]]:
        """Return completed tool calls with their call and result positions."""
        calls: dict[str, tuple[str, int]] = {}
        events: list[tuple[str, int, int, ToolMessage]] = []
        for index, message in enumerate(messages):
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    call_id = call.get("id")
                    name = call.get("name")
                    if isinstance(call_id, str) and isinstance(name, str):
                        calls[call_id] = (name, index)
                continue
            if not isinstance(message, ToolMessage):
                continue
            completed = calls.get(str(message.tool_call_id))
            if completed is None:
                continue
            name, call_index = completed
            events.append((name, call_index, index, message))
        return events

    @classmethod
    def _enforce_hypothesis_readback_after_ready_update(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
    ) -> ModelResponse:
        """Follow a warning-free draft update with its required readback."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        if "scientific_hypothesis_get_draft" not in validate_qwen_tool_schema(tools):
            return response

        events = cls._completed_tool_events(messages)
        latest_update: tuple[int, ToolMessage] | None = None
        last_read = -1
        for name, _, result_index, tool_message in events:
            if name == "scientific_hypothesis_update_draft":
                latest_update = (result_index, tool_message)
            elif name == "scientific_hypothesis_get_draft":
                last_read = result_index
        if latest_update is None or last_read > latest_update[0]:
            return response

        try:
            update_result = json.loads(cls._message_text(latest_update[1]))
        except (TypeError, ValueError):
            return response
        next_action = update_result.get("next_required_action")
        if not (
            update_result.get("status") == "draft"
            and update_result.get("soft_warning_count") == 0
            and update_result.get("return_gate") == "get_draft_required"
            and isinstance(next_action, dict)
            and next_action.get("tool") == "scientific_hypothesis_get_draft"
        ):
            return response

        message = response.result[0]
        if (
            len(message.tool_calls) == 1
            and message.tool_calls[0].get("name") == "scientific_hypothesis_get_draft"
        ):
            return response
        digest = hashlib.sha256(
            f"hypothesis-ready-readback:{len(messages)}:{latest_update[0]}".encode()
        ).hexdigest()[:24]
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        refreshed = message.model_copy(
            update={
                "content": "",
                "tool_calls": [
                    {
                        "name": "scientific_hypothesis_get_draft",
                        "args": {},
                        "id": f"call_qwen_hypothesis_ready_readback_{digest}",
                        "type": "tool_call",
                    }
                ],
                "invalid_tool_calls": [],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[refreshed],
            structured_response=response.structured_response,
        )

    @classmethod
    def _enforce_current_hypothesis_draft_before_tail_review(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
    ) -> ModelResponse:
        """Make Qwen read the current candidate-pool hash before tail review."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        if not any(
            call.get("name") == "scientific_hypothesis_review_tail"
            for call in message.tool_calls
        ):
            return response
        if "scientific_hypothesis_get_draft" not in validate_qwen_tool_schema(tools):
            return response

        events = cls._completed_tool_events(messages)
        last_update = max(
            (
                result_index
                for name, _, result_index, _ in events
                if name == "scientific_hypothesis_update_draft"
            ),
            default=-1,
        )
        last_read = max(
            (
                result_index
                for name, _, result_index, _ in events
                if name == "scientific_hypothesis_get_draft"
            ),
            default=-1,
        )
        if last_read > last_update:
            return response

        digest = hashlib.sha256(
            f"hypothesis-refresh:{len(messages)}:{last_update}".encode()
        ).hexdigest()[:24]
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        refreshed = message.model_copy(
            update={
                "content": "",
                "tool_calls": [
                    {
                        "name": "scientific_hypothesis_get_draft",
                        "args": {},
                        "id": f"call_qwen_hypothesis_refresh_{digest}",
                        "type": "tool_call",
                    }
                ],
                "invalid_tool_calls": [],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[refreshed],
            structured_response=response.structured_response,
        )

    @classmethod
    def _enforce_hypothesis_checkpoint_after_tail_review(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
        tools: list[BaseTool | dict[str, Any]],
    ) -> ModelResponse:
        """Read the selected pool, rank it, then checkpoint a current ranking."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        message = response.result[0]
        if message.tool_calls or message.invalid_tool_calls:
            return response
        if "scientific_hypothesis_checkpoint_draft" not in validate_qwen_tool_schema(
            tools
        ):
            return response

        events = cls._completed_tool_events(messages)
        latest_review: tuple[int, ToolMessage] | None = None
        latest_ranking: tuple[int, ToolMessage] | None = None
        last_draft_change = -1
        last_draft_read = -1
        last_checkpoint = -1
        for name, _, result_index, tool_message in events:
            if name == "scientific_hypothesis_update_draft":
                last_draft_change = result_index
            elif name == "scientific_hypothesis_get_draft":
                last_draft_read = result_index
            elif name == "scientific_hypothesis_review_tail":
                latest_review = (result_index, tool_message)
            elif name == "scientific_hypothesis_rank_portfolio":
                latest_ranking = (result_index, tool_message)
            elif name == "scientific_hypothesis_checkpoint_draft":
                last_checkpoint = result_index
        if latest_review is None:
            return response
        review_index, review_message = latest_review
        if review_index <= max(last_draft_change, last_checkpoint):
            return response
        try:
            review_result = json.loads(cls._message_text(review_message))
        except (TypeError, ValueError):
            return response
        if (
            not isinstance(review_result, Mapping)
            or review_result.get("status") != "tail_reviewed"
        ):
            return response

        tool_names = validate_qwen_tool_schema(tools)
        ranking_required = "scientific_hypothesis_rank_portfolio" in tool_names
        if ranking_required and (
            latest_ranking is None or latest_ranking[0] <= review_index
        ):
            if (
                "scientific_hypothesis_get_draft" not in tool_names
                or last_draft_read > review_index
            ):
                return response
            digest = hashlib.sha256(
                f"hypothesis-ranking-read:{len(messages)}:{review_index}".encode()
            ).hexdigest()[:24]
            metadata = dict(message.response_metadata)
            metadata["finish_reason"] = "tool_calls"
            ranking_read = message.model_copy(
                update={
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "scientific_hypothesis_get_draft",
                            "args": {},
                            "id": f"call_qwen_hypothesis_ranking_read_{digest}",
                            "type": "tool_call",
                        }
                    ],
                    "invalid_tool_calls": [],
                    "response_metadata": metadata,
                }
            )
            return ModelResponse(
                result=[ranking_read],
                structured_response=response.structured_response,
            )
        if ranking_required and latest_ranking is not None:
            ranking_index, ranking_message = latest_ranking
            if ranking_index <= max(review_index, last_draft_change, last_checkpoint):
                return response
            try:
                ranking_result = json.loads(cls._message_text(ranking_message))
            except (TypeError, ValueError):
                return response
            if (
                not isinstance(ranking_result, Mapping)
                or ranking_result.get("status") != "portfolio_ranked"
            ):
                return response

        digest = hashlib.sha256(
            f"hypothesis-checkpoint:{len(messages)}:{review_index}".encode()
        ).hexdigest()[:24]
        metadata = dict(message.response_metadata)
        metadata["finish_reason"] = "tool_calls"
        checkpointed = message.model_copy(
            update={
                "content": "",
                "tool_calls": [
                    {
                        "name": "scientific_hypothesis_checkpoint_draft",
                        "args": {},
                        "id": f"call_qwen_hypothesis_checkpoint_{digest}",
                        "type": "tool_call",
                    }
                ],
                "invalid_tool_calls": [],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[checkpointed],
            structured_response=response.structured_response,
        )

    @staticmethod
    def _tool_call_signature(message: AIMessage) -> tuple[tuple[str, str], ...]:
        """Return a stable signature that ignores provider-generated call IDs."""
        signature: list[tuple[str, str]] = []
        for call in message.tool_calls:
            name = call.get("name")
            args = call.get("args")
            if not isinstance(name, str) or not isinstance(args, Mapping):
                return ()
            signature.append(
                (
                    name,
                    json.dumps(
                        dict(args),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                )
            )
        return tuple(signature)

    @classmethod
    def _stop_repeated_blocked_tool_call(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
    ) -> ModelResponse:
        """Stop only an exact retry after the research state machine blocked it."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        current = response.result[0]
        current_signature = cls._tool_call_signature(current)
        if not current_signature or not messages:
            return response

        blocked_index: int | None = None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, ToolMessage):
                if (
                    cls._message_text(message)
                    .lstrip()
                    .startswith(_RESEARCH_REVIEW_BLOCKED_PREFIXES)
                ):
                    blocked_index = index
                break
        if blocked_index is None:
            return response

        previous_call: AIMessage | None = None
        for message in reversed(messages[:blocked_index]):
            if isinstance(message, AIMessage):
                previous_call = message
                break
        if previous_call is None:
            return response
        if cls._tool_call_signature(previous_call) != current_signature:
            return response

        metadata = dict(current.response_metadata)
        metadata["finish_reason"] = "stop"
        stopped = current.model_copy(
            update={
                "content": _REPEATED_BLOCKED_CALL_STOP,
                "tool_calls": [],
                "invalid_tool_calls": [],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[stopped],
            structured_response=response.structured_response,
        )

    @classmethod
    def _stop_after_two_identical_tool_errors(
        cls,
        response: ModelResponse,
        messages: Sequence[BaseMessage],
    ) -> ModelResponse:
        """Allow one explicit retry, then stop a third identical tool attempt."""
        if not isinstance(response, ModelResponse):
            return response
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return response
        current = response.result[0]
        signature = cls._tool_call_signature(current)
        if not signature:
            return response

        latest_human_index = -1
        call_signatures: dict[str, tuple[tuple[str, str], ...]] = {}
        for index, message in enumerate(messages):
            if getattr(message, "type", "") in {"human", "user"}:
                latest_human_index = index
                call_signatures.clear()
                continue
            if index <= latest_human_index:
                continue
            if isinstance(message, AIMessage):
                message_signature = cls._tool_call_signature(message)
                if not message_signature:
                    continue
                for call in message.tool_calls:
                    call_id = call.get("id")
                    if isinstance(call_id, str):
                        call_signatures[call_id] = message_signature

        failures = 0
        for message in messages[latest_human_index + 1 :]:
            if not isinstance(message, ToolMessage):
                continue
            if not _is_structured_tool_error_message(message):
                continue
            if call_signatures.get(str(message.tool_call_id)) == signature:
                failures += 1
        if failures < 2:
            return response

        metadata = dict(current.response_metadata)
        metadata["finish_reason"] = "stop"
        stopped = current.model_copy(
            update={
                "content": _REPEATED_TOOL_ERROR_STOP,
                "tool_calls": [],
                "invalid_tool_calls": [],
                "response_metadata": metadata,
            }
        )
        return ModelResponse(
            result=[stopped],
            structured_response=response.structured_response,
        )

    def _synthesize_planner_no_deliberation_response(
        self,
        request: ModelRequest,
        prepared: ModelRequest,
    ) -> ModelResponse | None:
        """Short-circuit a forced planner transition with no scientific args.

        Returns ``None`` unless the deterministic planner resolution pinned
        validate, freeze, or shadow-candidate commit. Otherwise the graph's
        tool node executes the exact transition in-process with runtime config.
        """

        if not self._active_model_is_qwen(request):
            return None
        system = self._message_text(prepared.system_message)
        if "[RESEARCH_PRODUCER_V2]" not in system or "stage=planning" not in system:
            return None
        forced = getattr(prepared, "tool_choice", None)
        if not isinstance(forced, Mapping):
            return None
        function = forced.get("function")
        if not isinstance(function, Mapping):
            return None
        tool_name = function.get("name")
        if tool_name not in _PLANNER_LOCAL_NO_DELIBERATION_TOOLS:
            return None
        if not any(_tool_name(tool) == tool_name for tool in list(prepared.tools)):
            return None
        _logger.info(
            "[jw.middleware.qwen_compat] synthesizing local planner %s "
            "tool_call (no remote call); deterministic no-deliberation edge",
            tool_name,
        )
        tool_call = {
            "name": tool_name,
            "args": {"request_sha256": ""},
            "id": f"local_{str(tool_name).removeprefix('research_planner_')}",
            "type": "tool_call",
        }
        message = AIMessage(
            content="",
            tool_calls=[tool_call],
            response_metadata={"finish_reason": "tool_calls"},
        )
        return ModelResponse(result=[message])

    @classmethod
    def _synthesize_data_transition_response(
        cls,
        prepared: ModelRequest,
    ) -> ModelResponse | None:
        """Emit a deterministic Data transition after a provider rejection.

        Some Qwen business-space routes remain always-thinking even when the
        OpenAI-compatible request explicitly sets ``enable_thinking=false``.
        Those routes reject a required/object ``tool_choice`` before the model
        can emit the already-resolved Data action.  The compatibility fallback
        therefore reconstructs only the same bounded transition selected by
        :meth:`_deterministic_data_tool`; it never invents dataset paths or
        scientific content.
        """

        context_text = "\n".join(
            [cls._message_text(prepared.system_message)]
            + [cls._message_text(message) for message in prepared.messages]
        )
        if (
            "[RESEARCH_PRODUCER_V2]" not in context_text
            or "stage=data" not in context_text
        ):
            return None
        forced = getattr(prepared, "tool_choice", None)
        if not isinstance(forced, Mapping):
            return None
        function = forced.get("function")
        if not isinstance(function, Mapping):
            return None
        name = function.get("name")
        if name not in _DATA_DETERMINISTIC_TOOLS or not any(
            _tool_name(tool) == name for tool in list(prepared.tools)
        ):
            return None

        data_context = cls._preopened_data_context(
            cls._message_text(prepared.system_message)
        )
        if data_context is None:
            for message in reversed(prepared.messages):
                if (
                    not isinstance(message, ToolMessage)
                    or message.name != "solar_data_open_context"
                ):
                    continue
                try:
                    candidate = json.loads(cls._message_text(message))
                except (TypeError, ValueError):
                    continue
                if isinstance(candidate, Mapping):
                    data_context = candidate
                    break

        args: dict[str, Any]
        if name == "solar_data_open_context":
            protocol = None
            patterns = (
                r"required_analysis_protocol\s*[=:]\s*[\"']?([A-Za-z0-9_.-]+)",
                r"[\"']analysis_protocol[\"']\s*:\s*[\"']([A-Za-z0-9_.-]+)",
                r"analysis_protocol\s*=\s*([A-Za-z0-9_.-]+)",
            )
            for pattern in patterns:
                match = re.search(pattern, context_text)
                if match:
                    protocol = match.group(1)
                    break
            if not protocol:
                return None
            args = {"analysis_protocol": protocol}
        else:
            if not isinstance(data_context, Mapping):
                return None
            eligible = data_context.get("eligible_inputs")
            if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
                return None
            paths = {
                str(item.get("dataset_id")): str(item.get("path"))
                for item in eligible
                if isinstance(item, Mapping)
                and isinstance(item.get("dataset_id"), str)
                and isinstance(item.get("path"), str)
                and item.get("path")
            }
            if name == "prepare_solar_precursor_cycle_table":
                required = {
                    "sunspot_path": paths.get("silso-monthly-total-v2"),
                    "polar_field_path": paths.get("mwo-wso-polar-field-v2"),
                }
            elif name == "prepare_solar_cycle_26_readiness":
                required = {
                    "monthly_total_path": paths.get("silso-monthly-total-v2"),
                    "smoothed_path": paths.get("silso-monthly-smoothed-v2"),
                    "official_extrema_path": paths.get("silso-cycle-extrema-v2"),
                    "f107_path": paths.get("noaa-swpc-monthly-f107-v1"),
                    "historical_polar_path": paths.get("mwo-wso-polar-field-v2"),
                    "current_polar_path": paths.get("wso-current-polar-field-v1"),
                    "cutoff_date": "2026-06-30",
                }
            elif name == "run_solar_cycle_26_historical_forecast":
                required = {
                    "monthly_total_path": paths.get("silso-monthly-total-v2"),
                    "smoothed_path": paths.get("silso-monthly-smoothed-v2"),
                    "official_extrema_path": paths.get("silso-cycle-extrema-v2"),
                }
            else:
                required = {
                    "monthly_total_path": paths.get("silso-monthly-total-v2"),
                    "smoothed_path": paths.get("silso-monthly-smoothed-v2"),
                    "official_extrema_path": paths.get("silso-cycle-extrema-v2"),
                    "cycles": "21-24",
                }
            if any(value is None for value in required.values()):
                return None
            args = required

        _logger.info(
            "[jw.middleware.qwen_compat] synthesizing local Data transition "
            "tool_call after thinking/tool_choice provider rejection: tool=%s",
            name,
        )
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": args,
                            "id": f"local_data_{name}",
                            "type": "tool_call",
                        }
                    ],
                    response_metadata={"finish_reason": "tool_calls"},
                )
            ]
        )

    @staticmethod
    def _is_thinking_tool_choice_rejection(exc: Exception) -> bool:
        rendered = str(exc).casefold()
        return (
            "tool_choice parameter does not support" in rendered
            and "thinking mode" in rendered
        )

    @classmethod
    def _safe_retry_without_forced_tool_choice(
        cls,
        prepared: ModelRequest,
    ) -> ModelRequest | None:
        """Build one provider-safe retry for an otherwise valid Qwen tool call.

        Token Plan routes can remain in thinking mode even when the request
        carries ``enable_thinking=false``.  In that state DashScope rejects an
        OpenAI ``required``/object ``tool_choice`` before the model gets a turn.
        This occurs in nested Deep Agents too, where the request has no research
        stage marker and therefore cannot use the domain-specific local adapter.
        If the forced choice identifies one tool, expose only that tool and let
        the provider use automatic selection; the prompt preserves the original
        one-tool intent while making the retry valid for thinking mode.
        """

        forced = getattr(prepared, "tool_choice", None)
        implicit_binding = forced in (None, False, "auto", "none")
        selected_name: str | None = None
        if not implicit_binding and isinstance(forced, Mapping):
            function = forced.get("function")
            if isinstance(function, Mapping):
                candidate = function.get("name")
                if isinstance(candidate, str) and candidate.strip():
                    selected_name = candidate.strip()
            if selected_name is None:
                candidate = forced.get("name")
                if isinstance(candidate, str) and candidate.strip():
                    selected_name = candidate.strip()
        elif not implicit_binding and isinstance(forced, str) and forced != "required":
            selected_name = forced.strip() or None

        tools = list(getattr(prepared, "tools", ()) or ())
        named_tools = {
            name: item for item in tools if (name := _tool_name(item)) is not None
        }
        if selected_name is None and forced == "required" and len(named_tools) == 1:
            selected_name = next(iter(named_tools))
        if not named_tools:
            return None
        if selected_name is not None:
            selected_tool = named_tools.get(selected_name)
            if selected_tool is None:
                return None
            retry_tools = [selected_tool]
            action_text = f"call {selected_name} exactly once with complete arguments"
        else:
            # The agent factory can bind a forced choice downstream of this
            # middleware, leaving request.tool_choice as None. Keep all declared
            # tools in that case so the model can still select the action implied
            # by the current conversation.
            retry_tools = list(named_tools.values())
            action_text = "continue with the appropriate tool call exactly once"

        from deepagents.middleware._utils import append_to_system_message

        instruction = (
            "The provider rejected the previous forced tool_choice because this "
            "Qwen route is in thinking mode. This retry uses automatic selection "
            f"and asks the model to {action_text}, then return the tool result. "
            "Do not answer in prose before calling it."
        )
        return prepared.override(
            tools=retry_tools,
            tool_choice=None,
            messages=cls._strip_all_reasoning(list(prepared.messages)),
            system_message=append_to_system_message(
                prepared.system_message,
                instruction,
            ),
        )

    @classmethod
    def _synthesize_evidence_navigation_response(
        cls,
        prepared: ModelRequest,
    ) -> ModelResponse | None:
        """Execute Evidence context opening and declared-source reads as fixed edges."""

        context = "\n".join(
            [cls._message_text(prepared.system_message)]
            + [cls._message_text(message) for message in prepared.messages]
        )
        if "[EVIDENCE_REVIEW_V2]" not in context:
            return None
        review_mode = cls._evidence_review_mode(context)
        if review_mode is None:
            return None
        available = set(validate_qwen_tool_schema(list(prepared.tools)))
        (
            opened,
            ordered_refs,
            read_refs,
            submit_attempts,
            submit_succeeded,
        ) = cls._evidence_navigation_state(list(prepared.messages), review_mode)
        if submit_succeeded:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="Evidence review round persisted.",
                        response_metadata={"finish_reason": "stop"},
                    )
                ]
            )
        if submit_attempts >= 2:
            return ModelResponse(
                result=[
                    AIMessage(
                        content=(
                            "Evidence review round did not persist after two attempts."
                        ),
                        response_metadata={"finish_reason": "stop"},
                    )
                ]
            )
        if opened is None and _EVIDENCE_OPEN_TOOL in available:
            tool_calls = [
                {
                    "name": _EVIDENCE_OPEN_TOOL,
                    "args": {"review_mode": review_mode},
                    "id": "local_evidence_open",
                    "type": "tool_call",
                }
            ]
        else:
            unread = [ref for ref in ordered_refs if ref not in read_refs]
            if not unread or _EVIDENCE_READ_TOOL not in available:
                return None
            tool_calls = [
                {
                    "name": _EVIDENCE_READ_TOOL,
                    "args": {"review_mode": review_mode, "source_ref": source_ref},
                    "id": (
                        "local_evidence_"
                        + hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:20]
                    ),
                    "type": "tool_call",
                }
                for source_ref in unread
            ]
        message = AIMessage(
            content="",
            tool_calls=tool_calls,
            response_metadata={"finish_reason": "tool_calls"},
        )
        return ModelResponse(result=[message])

    @staticmethod
    def _is_kimi_evidence_structured_submit(prepared: ModelRequest) -> bool:
        response_format = getattr(prepared, "response_format", None)
        if not isinstance(response_format, ProviderStrategy):
            return False
        model_name = str(
            getattr(prepared.model, "model_name", None)
            or getattr(prepared.model, "model", "")
        ).casefold()
        return (
            model_name == "kimi-for-coding"
            and response_format.schema_spec.name == _EVIDENCE_SUBMIT_TOOL
        )

    @staticmethod
    def _normalize_kimi_evidence_submission(
        submission: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(submission)
        for field in (
            "assessment_claims",
            "scientific_quality_claims",
            "issues",
            "accepted_claims",
            "blocked_claims",
            "carry_forward_limits",
        ):
            raw_value = normalized.get(field)
            if not isinstance(raw_value, str):
                continue
            try:
                parsed_value = json.loads(raw_value)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed_value, list):
                # Qwen sometimes JSON-encodes an array inside a tool argument
                # even though the provider schema also permits a native array.
                # Parse that one boundary representation before iterating it;
                # otherwise a valid matrix becomes one list entry per character.
                normalized[field] = parsed_value
        assessment_mode = (
            os.environ.get("JW_EVIDENCE_REVIEW_MODE", "two_pass").strip().lower()
        )
        if assessment_mode not in {"closed", "two_pass"}:
            assessment_mode = "two_pass"
        # This value describes the configured review procedure, not the
        # artifact stage in ``review_mode``. Keep it deterministic at the
        # model/tool boundary so a stage name cannot invalidate a complete
        # atomic review submission.
        normalized["assessment_review_mode"] = assessment_mode
        issues = [
            issue
            for issue in normalized.get("issues", [])
            if isinstance(issue, Mapping)
        ]
        has_major_issue = any(
            issue.get("severity") in {"critical", "major"} for issue in issues
        )
        if (
            normalized.get("decision") in {"accept", "accept_with_limits"}
            and has_major_issue
        ):
            normalized["decision"] = "revise"
        if normalized.get("decision") == "revise" and not normalized.get("next_owner"):
            owners = [issue.get("owner") for issue in issues if issue.get("owner")]
            if owners:
                normalized["next_owner"] = owners[0]

        if normalized.get("decision") in {
            "accept",
            "accept_with_limits",
        } and not normalized.get("accepted_claims"):
            blocked_claims = {
                claim_id
                for claim_id in normalized.get("blocked_claims", [])
                if isinstance(claim_id, str) and claim_id
            }
            accepted_claims: list[str] = []
            for row in normalized.get("assessment_claims", []):
                if not isinstance(row, Mapping):
                    continue
                claim_id = row.get("claim_id")
                if (
                    isinstance(claim_id, str)
                    and claim_id
                    and claim_id not in blocked_claims
                    and row.get("disposition") in {"supported", "limited_support"}
                    and claim_id not in accepted_claims
                ):
                    accepted_claims.append(claim_id)
            if accepted_claims:
                # This is a provider-boundary repair, not a new scientific
                # judgment: the reviewer already marked these exact artifact
                # claims supported in ReviewAssessmentV1 and selected an
                # accepting verdict. Leave undecided/opposed claims absent so
                # the server-side contract still rejects an incoherent round.
                normalized["accepted_claims"] = accepted_claims

        quality_claims: list[Any] = []
        for raw_claim in normalized.get("scientific_quality_claims", []):
            if not isinstance(raw_claim, Mapping):
                quality_claims.append(raw_claim)
                continue
            claim = dict(raw_claim)
            evidence_matrix = claim.get("evidence_matrix", [])
            unresolved_scope = any(
                isinstance(row, Mapping)
                and (
                    row.get("scope_match") in {"mismatch", "not_assessable"}
                    or row.get("entailment") in {"not_entailed", "not_assessable"}
                )
                for row in evidence_matrix
            )
            if claim.get("quality_status") == "release_candidate" and unresolved_scope:
                claim["quality_status"] = "evidence_constrained"
                if claim.get("conclusion_cap") == "release_candidate":
                    claim["conclusion_cap"] = "evidence_constrained"
            quality_claims.append(claim)
        normalized["scientific_quality_claims"] = quality_claims
        return normalized

    @classmethod
    def _normalize_qwen_evidence_submission_response(
        cls,
        response: ModelResponse,
    ) -> ModelResponse:
        """Repair host-owned fields in Qwen's atomic Evidence tool call.

        ``assessment_review_mode`` is runtime configuration rather than a
        reviewer decision. Likewise, an accepting verdict can recover omitted
        claim ids only from the same call's explicit supported/limited-support
        assessment rows. All evidence content and scientific judgments remain
        model-authored and are still validated by the persistence tool.
        """

        if not isinstance(response, ModelResponse):
            return response

        changed = False
        messages: list[BaseMessage] = []
        for message in response.result:
            if not isinstance(message, AIMessage):
                messages.append(message)
                continue
            tool_calls: list[dict[str, Any]] = []
            message_changed = False
            for call in message.tool_calls:
                if call.get("name") == _EVIDENCE_SUBMIT_TOOL and isinstance(
                    call.get("args"), Mapping
                ):
                    normalized_args = cls._normalize_kimi_evidence_submission(
                        call["args"]
                    )
                    tool_calls.append({**call, "args": normalized_args})
                    message_changed = message_changed or normalized_args != call["args"]
                else:
                    tool_calls.append(call)
            if message_changed:
                messages.append(message.model_copy(update={"tool_calls": tool_calls}))
                changed = True
            else:
                messages.append(message)
        if not changed:
            return response
        return ModelResponse(
            result=messages,
            structured_response=response.structured_response,
        )

    @classmethod
    def _kimi_evidence_structured_failure(
        cls,
        *,
        error_type: str,
        parsed_present: bool,
        raw_message_present: bool,
    ) -> ModelResponse:
        """Return a bounded diagnostic when Kimi cannot form the atomic review.

        The structured-output wrapper may retain a raw provider response and an
        exception containing model text. Neither belongs in the agent history or
        failure receipt. Keep only stable structural facts so the orchestrator can
        distinguish repeated provider/schema failures without turning them into a
        synthetic evidence submission.
        """

        lines = [
            "[KIMI EVIDENCE STRUCTURED SUBMIT FAILED]",
            "event=kimi_evidence_structured_submit_failed",
            f"error_type={error_type}",
            f"parsed_present={str(parsed_present).lower()}",
            f"raw_message_present={str(raw_message_present).lower()}",
        ]
        fingerprint = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        return ModelResponse(
            result=[
                AIMessage(
                    content="\n".join([*lines, f"fingerprint={fingerprint}"]),
                    response_metadata={"finish_reason": "stop"},
                )
            ]
        )

    @classmethod
    def _evidence_structured_result(
        cls,
        result: Mapping[str, Any],
    ) -> ModelResponse:
        parsing_error = result.get("parsing_error")
        parsed = result.get("parsed")
        raw = result.get("raw")
        raw_message_present = isinstance(raw, AIMessage)
        if parsing_error is not None:
            return cls._kimi_evidence_structured_failure(
                error_type=type(parsing_error).__name__,
                parsed_present=parsed is not None,
                raw_message_present=raw_message_present,
            )
        if parsed is None:
            return cls._kimi_evidence_structured_failure(
                error_type="parsed_missing",
                parsed_present=False,
                raw_message_present=raw_message_present,
            )
        if not raw_message_present:
            return cls._kimi_evidence_structured_failure(
                error_type="raw_not_ai_message",
                parsed_present=True,
                raw_message_present=False,
            )
        if hasattr(parsed, "model_dump"):
            submission = parsed.model_dump()
        elif isinstance(parsed, Mapping):
            submission = dict(parsed)
        else:
            raise TypeError("Kimi Evidence structured output returned no parsed value")
        submission = cls._normalize_kimi_evidence_submission(submission)
        message = raw.model_copy(
            update={
                "content": "",
                "tool_calls": [
                    {
                        "name": _EVIDENCE_SUBMIT_TOOL,
                        "args": dict(submission),
                        "id": "local_evidence_submit",
                        "type": "tool_call",
                    }
                ],
                "response_metadata": {
                    **raw.response_metadata,
                    "finish_reason": "tool_calls",
                },
            }
        )
        return ModelResponse(result=[message], structured_response=None)

    @staticmethod
    def _kimi_evidence_output_model(model: Any) -> Any:
        # ChatAnthropic otherwise sends max_tokens=4096, which is too small for
        # one schema-bound assessment, quality matrix, and verdict transaction.
        return model.model_copy(update={"max_tokens": 32768})

    @classmethod
    def _invoke_kimi_evidence_structured(
        cls,
        prepared: ModelRequest,
    ) -> ModelResponse:
        response_format = prepared.response_format
        assert isinstance(response_format, ProviderStrategy)
        review_model = cls._kimi_evidence_output_model(prepared.model)
        structured = review_model.with_structured_output(
            response_format.schema,
            method="json_schema",
            include_raw=True,
        )
        messages = list(prepared.messages)
        if prepared.system_message is not None:
            messages.insert(0, prepared.system_message)
        result = structured.invoke(messages)
        return cls._evidence_structured_result(result)

    @classmethod
    async def _ainvoke_kimi_evidence_structured(
        cls,
        prepared: ModelRequest,
    ) -> ModelResponse:
        response_format = prepared.response_format
        assert isinstance(response_format, ProviderStrategy)
        review_model = cls._kimi_evidence_output_model(prepared.model)
        structured = review_model.with_structured_output(
            response_format.schema,
            method="json_schema",
            include_raw=True,
        )
        messages = list(prepared.messages)
        if prepared.system_message is not None:
            messages.insert(0, prepared.system_message)
        result = await structured.ainvoke(messages)
        return cls._evidence_structured_result(result)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        local_transition = self._synthesize_planner_no_deliberation_response(
            request, prepared
        )
        if local_transition is not None:
            return local_transition
        local_evidence = self._synthesize_evidence_navigation_response(prepared)
        if local_evidence is not None:
            return local_evidence
        if self._is_kimi_evidence_structured_submit(prepared):
            return self._invoke_kimi_evidence_structured(prepared)
        try:
            response = handler(prepared)
        except Exception as exc:
            if self._is_thinking_tool_choice_rejection(exc):
                local_data = self._synthesize_data_transition_response(prepared)
                if local_data is not None:
                    return local_data
                safe_retry = self._safe_retry_without_forced_tool_choice(prepared)
                if safe_retry is not None:
                    _logger.warning(
                        "[jw.middleware.qwen_compat] Qwen rejected forced "
                        "tool_choice; retrying once with a single auto-selected tool"
                    )
                    response = handler(safe_retry)
                else:
                    raise
            elif self._active_model_is_qwen(
                request
            ) and _is_retryable_qwen_transport_error(exc):
                _logger.warning(
                    "[jw.middleware.qwen_compat] transient Qwen transport failure; "
                    "retrying the same model request once (%s)",
                    type(exc).__name__,
                )
                response = handler(prepared)
            else:
                raise
        _logger.info(
            "[jw.middleware.qwen_compat] model result tool_calls=%s",
            [
                call.get("name")
                for message in getattr(response, "result", [])
                if isinstance(message, AIMessage)
                for call in message.tool_calls
            ],
        )
        if not self._active_model_is_qwen(request):
            return response
        tools = list(prepared.tools)
        audit_messages = list(request.messages)
        evidence_normalized = self._normalize_qwen_evidence_submission_response(
            response
        )
        recovered = self._recover_reasoning_tool_call(evidence_normalized, tools)
        serialized = self._serialize_planner_revision_calls(
            recovered,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=planning"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        serialized = self._serialize_experiment_design_calls(
            serialized,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=experiment_design"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        hypothesis_ready_readback = (
            self._enforce_hypothesis_readback_after_ready_update(
                serialized,
                audit_messages,
                tools,
            )
        )
        hypothesis_refreshed = (
            self._enforce_current_hypothesis_draft_before_tail_review(
                hypothesis_ready_readback,
                audit_messages,
                tools,
            )
        )
        readback_checked = self._enforce_artifact_readback(
            hypothesis_refreshed,
            audit_messages,
            tools,
        )
        hypothesis_checkpointed = self._enforce_hypothesis_checkpoint_after_tail_review(
            readback_checked,
            audit_messages,
            tools,
        )
        blocked_checked = self._stop_repeated_blocked_tool_call(
            hypothesis_checkpointed,
            audit_messages,
        )
        return self._stop_after_two_identical_tool_errors(
            blocked_checked,
            audit_messages,
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        _logger.info(
            "[jw.middleware.qwen_compat] model call: model=%s stage_planning=%s "
            "producer_v2=%s active_qwen=%s",
            str(
                getattr(request.model, "model_name", None)
                or getattr(request.model, "model", "")
            ),
            "stage=planning" in str(getattr(prepared.system_message, "content", "")),
            "[RESEARCH_PRODUCER_V2]"
            in str(getattr(prepared.system_message, "content", "")),
            self._active_model_is_qwen(request),
        )
        local_transition = self._synthesize_planner_no_deliberation_response(
            request, prepared
        )
        if local_transition is not None:
            return local_transition
        local_evidence = self._synthesize_evidence_navigation_response(prepared)
        if local_evidence is not None:
            return local_evidence
        if self._is_kimi_evidence_structured_submit(prepared):
            return await self._ainvoke_kimi_evidence_structured(prepared)
        try:
            response = await self._await_handler_with_qwen_wall_timeout(
                request, prepared, handler
            )
        except Exception as exc:
            if self._is_thinking_tool_choice_rejection(exc):
                local_data = self._synthesize_data_transition_response(prepared)
                if local_data is not None:
                    return local_data
                safe_retry = self._safe_retry_without_forced_tool_choice(prepared)
                if safe_retry is not None:
                    _logger.warning(
                        "[jw.middleware.qwen_compat] Qwen rejected forced "
                        "tool_choice; retrying once with a single auto-selected tool"
                    )
                    response = await self._await_handler_with_qwen_wall_timeout(
                        request, safe_retry, handler
                    )
                else:
                    raise
            elif self._active_model_is_qwen(
                request
            ) and _is_retryable_qwen_transport_error(exc):
                current = exc
                for retry_index in range(_QWEN_TRANSPORT_MAX_RETRIES):
                    _logger.warning(
                        "[jw.middleware.qwen_compat] transient Qwen transport "
                        "failure; retry %d/%d after %.0f seconds (%s)",
                        retry_index + 1,
                        _QWEN_TRANSPORT_MAX_RETRIES,
                        _QWEN_TRANSPORT_RETRY_DELAY_SECONDS,
                        type(current).__name__,
                    )
                    await _sleep_before_qwen_retry(_QWEN_TRANSPORT_RETRY_DELAY_SECONDS)
                    try:
                        response = await self._await_handler_with_qwen_wall_timeout(
                            request, prepared, handler
                        )
                        break
                    except Exception as retry_exc:
                        current = retry_exc
                        if (
                            not self._active_model_is_qwen(request)
                            or not _is_retryable_qwen_transport_error(retry_exc)
                            or retry_index + 1 >= _QWEN_TRANSPORT_MAX_RETRIES
                        ):
                            raise
            else:
                raise
        _logger.info(
            "[jw.middleware.qwen_compat] model result tool_calls=%s",
            [
                call.get("name")
                for message in getattr(response, "result", [])
                if isinstance(message, AIMessage)
                for call in message.tool_calls
            ],
        )
        if not self._active_model_is_qwen(request):
            return response
        tools = list(prepared.tools)
        audit_messages = list(request.messages)
        evidence_normalized = self._normalize_qwen_evidence_submission_response(
            response
        )
        recovered = self._recover_reasoning_tool_call(evidence_normalized, tools)
        serialized = self._serialize_planner_revision_calls(
            recovered,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=planning"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        serialized = self._serialize_experiment_design_calls(
            serialized,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=experiment_design"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        hypothesis_ready_readback = (
            self._enforce_hypothesis_readback_after_ready_update(
                serialized,
                audit_messages,
                tools,
            )
        )
        hypothesis_refreshed = (
            self._enforce_current_hypothesis_draft_before_tail_review(
                hypothesis_ready_readback,
                audit_messages,
                tools,
            )
        )
        readback_checked = self._enforce_artifact_readback(
            hypothesis_refreshed,
            audit_messages,
            tools,
        )
        hypothesis_checkpointed = self._enforce_hypothesis_checkpoint_after_tail_review(
            readback_checked,
            audit_messages,
            tools,
        )
        blocked_checked = self._stop_repeated_blocked_tool_call(
            hypothesis_checkpointed,
            audit_messages,
        )
        return self._stop_after_two_identical_tool_errors(
            blocked_checked,
            audit_messages,
        )

    async def _await_handler_with_qwen_wall_timeout(
        self,
        request: ModelRequest,
        prepared: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Bound one complete Qwen request, including a live response stream."""

        if not self._active_model_is_qwen(request):
            return await handler(prepared)
        timeout_s = _dashscope_request_timeout()
        try:
            return await asyncio.wait_for(handler(prepared), timeout=timeout_s)
        except TimeoutError:
            _logger.warning(
                "[jw.middleware.qwen_compat] Qwen model request exceeded the "
                "%.0f-second total wall-clock timeout",
                timeout_s,
            )
            raise


__all__ = [
    "QWEN_RESERVED_FUNCTION_NAMES",
    "QWEN_TOOL_USE_PROMPT",
    "QwenToolCompatibilityMiddleware",
    "QwenToolSchemaError",
    "is_qwen_model",
    "validate_qwen_tool_schema",
]

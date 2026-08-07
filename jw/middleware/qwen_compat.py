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
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

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
  rows or numeric values from tool output into source code. Preserve the
  reported SHA-256 in the final evidence trail when one is available.
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
_PLANNER_SERIAL_TOOL_NAMES = frozenset(
    {
        "research_planner_get_section",
        "research_planner_stage_revision_section",
        "research_planner_commit_revision_candidate",
    }
)
# No-deliberation transitions that carry no scientific arguments. Forcing Qwen
# to "decide" one of these via an object ``tool_choice`` costs a remote call
# and re-opens the DashScope thinking-mode rejection. The Supervisor executes
# them deterministically instead of asking the model.
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
_SOLAR_PRECURSOR_DATASET_IDS = frozenset(
    {"silso-monthly-total-v2", "mwo-wso-polar-field-v2"}
)
_SILSO_REPRODUCTION_PROTOCOL = "silso_cycle_reproduction_v1"
_SILSO_EXTREMA_DATA_PRODUCT = "silso_cycle_extrema_v1"
_SOLAR_PRECURSOR_DATA_PRODUCT = "solar_polar_precursor_table_v1"
_SILSO_REPRODUCTION_DATASET_IDS = frozenset(
    {
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
    }
)

_logger = logging.getLogger(__name__)


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

    def _active_model_is_qwen(self) -> bool:
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
            name, default = "JW_QWEN_PLANNING_THINKING_BUDGET", 3072
        elif "[RESEARCH_PRODUCER_V2]" in context and "stage=data" in context:
            name, default = "JW_QWEN_DATA_THINKING_BUDGET", 4096
        elif "[RESEARCH_PRODUCER_V2]" in context:
            name, default = "JW_QWEN_PRODUCER_THINKING_BUDGET", 6144
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
        if not self._active_model_is_qwen():
            return request
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
        overrides: dict[str, Any] = {
            "system_message": system_message,
            "messages": projected_messages,
        }
        planning_revision_context = "[RESEARCH_PRODUCER_V2]" in str(
            system_message.content
        ) and "stage=planning" in str(system_message.content)
        data_stage_context = "[RESEARCH_PRODUCER_V2]" in str(
            system_message.content
        ) and "stage=data" in str(system_message.content)
        closed_loop_context = any(
            marker in str(system_message.content)
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
        if planning_revision_context:
            # Qwen frequently emits every planned JSON replacement in one
            # parallel tool-call response. Those responses are both too large
            # for a reliable upstream connection and prone to schema drift.
            # Force one bounded read/write per model turn; the shadow-candidate
            # tools preserve progress between turns.
            model_settings = dict(getattr(request, "model_settings", None) or {})
            model_settings["parallel_tool_calls"] = False
            overrides["model_settings"] = model_settings
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
        tool_choice = request.tool_choice
        if deterministic_tool is not None:
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
            and message.name in {open_tool, prepare_tool, reproduce_tool}
        ]
        if relevant and relevant[-1].name in {prepare_tool, reproduce_tool}:
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
            # ``validate_draft`` / ``freeze_plan`` carry no scientific content:
            # the Supervisor executes them in-process instead of asking the
            # model to re-emit them, so they must not pin a forced tool_choice
            # here. Returning None leaves the edge to the deterministic
            # orchestration close-out.
            if tool_name in _PLANNER_NO_DELIBERATION_TOOLS:
                return None
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
            if not cls._message_text(message).lstrip().startswith(_TOOL_ERROR_PREFIXES):
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

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        prepared = self._prepare_request(request)
        response = handler(prepared)
        if not self._active_model_is_qwen():
            return response
        tools = list(prepared.tools)
        audit_messages = list(request.messages)
        recovered = self._recover_reasoning_tool_call(response, tools)
        serialized = self._serialize_planner_revision_calls(
            recovered,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=planning"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        readback_checked = self._enforce_artifact_readback(
            serialized,
            audit_messages,
            tools,
        )
        blocked_checked = self._stop_repeated_blocked_tool_call(
            readback_checked,
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
        response = await handler(prepared)
        if not self._active_model_is_qwen():
            return response
        tools = list(prepared.tools)
        audit_messages = list(request.messages)
        recovered = self._recover_reasoning_tool_call(response, tools)
        serialized = self._serialize_planner_revision_calls(
            recovered,
            enabled=(
                "[RESEARCH_PRODUCER_V2]"
                in str(getattr(prepared.system_message, "content", ""))
                and "stage=planning"
                in str(getattr(prepared.system_message, "content", ""))
            ),
        )
        readback_checked = self._enforce_artifact_readback(
            serialized,
            audit_messages,
            tools,
        )
        blocked_checked = self._stop_repeated_blocked_tool_call(
            readback_checked,
            audit_messages,
        )
        return self._stop_after_two_identical_tool_errors(
            blocked_checked,
            audit_messages,
        )


__all__ = [
    "QWEN_RESERVED_FUNCTION_NAMES",
    "QWEN_TOOL_USE_PROMPT",
    "QwenToolCompatibilityMiddleware",
    "QwenToolSchemaError",
    "is_qwen_model",
    "validate_qwen_tool_schema",
]

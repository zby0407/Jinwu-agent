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

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        if not self._active_model_is_qwen():
            return request
        validate_qwen_tool_schema(list(request.tools))
        from deepagents.middleware._utils import append_to_system_message

        from .utils import disable_thinking

        system_message = append_to_system_message(
            request.system_message,
            QWEN_TOOL_USE_PROMPT,
        )
        overrides: dict[str, Any] = {"system_message": system_message}
        tool_choice = request.tool_choice
        if tool_choice not in (None, False, "auto", "none"):
            # DashScope rejects required/object tool_choice in thinking mode.
            # Keep the forced choice (needed by structured output) and disable
            # thinking only for this request instead of weakening the contract
            # to tool_choice="auto".
            overrides["model"] = disable_thinking(request.model)
        return request.override(**overrides)

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
        recovered = self._recover_reasoning_tool_call(response, tools)
        return self._enforce_artifact_readback(
            recovered,
            list(prepared.messages),
            tools,
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
        recovered = self._recover_reasoning_tool_call(response, tools)
        return self._enforce_artifact_readback(
            recovered,
            list(prepared.messages),
            tools,
        )


__all__ = [
    "QWEN_RESERVED_FUNCTION_NAMES",
    "QWEN_TOOL_USE_PROMPT",
    "QwenToolCompatibilityMiddleware",
    "QwenToolSchemaError",
    "is_qwen_model",
    "validate_qwen_tool_schema",
]

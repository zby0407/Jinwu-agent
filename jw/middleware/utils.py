"""Shared utilities for JW middleware.

Functions here are used by multiple middleware modules (memory, tool_selector)
and should not depend on any specific middleware class.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage


def disable_thinking(model: BaseChatModel) -> BaseChatModel:
    """Return a copy of the model with thinking/reasoning disabled.

    Anthropic's API does not allow extended thinking when ``tool_choice``
    forces tool use (as ``with_structured_output`` does).  Similarly,
    OpenAI reasoning can conflict.  Strip these settings so structured
    output calls work reliably.

    Uses ``model_copy()`` to produce a real new instance — ``bind()`` only
    wraps the model in a ``RunnableBinding`` whose kwargs do NOT override
    first-class Pydantic fields like ``thinking`` on ``ChatAnthropic``.
    """
    updates: dict[str, Any] = {}
    model_kwargs = getattr(model, "model_kwargs", {}) or {}

    if getattr(model, "thinking", None) or "thinking" in model_kwargs:
        updates["thinking"] = None
    if getattr(model, "reasoning", None) or "reasoning" in model_kwargs:
        updates["reasoning"] = None

    # DashScope's OpenAI-compatible endpoint can enable thinking server-side,
    # so there may be no local ``thinking``/``reasoning`` field to clear. This
    # applies not only to Qwen names: DashScope-hosted auxiliary families can
    # reject schema-forced tool choice with the same provider error. Detect the
    # provider endpoint as well as the Qwen family and explicitly disable it on
    # the copied request model, preserving any other extra_body options.
    model_name = (
        str(getattr(model, "model_name", None) or getattr(model, "model", None) or "")
        .casefold()
        .rsplit("/", 1)[-1]
    )
    base_url = str(
        getattr(model, "openai_api_base", None)
        or getattr(model, "base_url", None)
        or ""
    ).casefold()
    dashscope_hosted = any(
        host in base_url for host in ("dashscope.aliyuncs.com", "maas.aliyuncs.com")
    )
    if model_name.startswith(("qwen", "qwq")) or dashscope_hosted:
        extra_body = dict(getattr(model, "extra_body", None) or {})
        # Do not leave mutually inconsistent thinking controls attached to a
        # forced-tool request. Some DashScope model routes still classify the
        # request as thinking-enabled when preserve_thinking/thinking_budget
        # survive beside enable_thinking=false.
        extra_body.pop("thinking_budget", None)
        extra_body.pop("preserve_thinking", None)
        extra_body["enable_thinking"] = False
        updates["extra_body"] = extra_body
        if any(
            key in model_kwargs
            for key in ("enable_thinking", "thinking_budget", "preserve_thinking")
        ):
            cleaned_model_kwargs = dict(model_kwargs)
            cleaned_model_kwargs.pop("enable_thinking", None)
            cleaned_model_kwargs.pop("thinking_budget", None)
            cleaned_model_kwargs.pop("preserve_thinking", None)
            updates["model_kwargs"] = cleaned_model_kwargs

    if not updates:
        return model

    # Prefer Pydantic model_copy (creates a true new instance with the
    # field cleared) over bind() which only adds invocation kwargs.
    try:
        return model.model_copy(update=updates)
    except Exception:
        # Fallback for non-Pydantic or unusual model classes
        # Note: bind() may not effectively override first-class Pydantic fields
        return model.bind(**updates)


def configure_qwen_thinking(
    model: BaseChatModel,
    *,
    thinking_budget: int,
    preserve_thinking: bool = True,
) -> BaseChatModel:
    """Return a Qwen request model with an explicit bounded thinking policy."""

    model_name = (
        str(getattr(model, "model_name", None) or getattr(model, "model", None) or "")
        .casefold()
        .rsplit("/", 1)[-1]
    )
    if not model_name.startswith("qwen3"):
        return model
    extra_body = dict(getattr(model, "extra_body", None) or {})
    extra_body.update(
        {
            "enable_thinking": True,
            "thinking_budget": thinking_budget,
            "preserve_thinking": preserve_thinking,
        }
    )
    try:
        return model.model_copy(update={"extra_body": extra_body})
    except Exception:
        return model.bind(extra_body=extra_body)


def append_to_system_message(
    system_message: SystemMessage | None, text: str
) -> SystemMessage:
    """Append a text block to a system message, preserving its metadata.

    Used by the memory and scheduler middleware. Unlike building a fresh
    ``SystemMessage``, ``model_copy`` keeps ``additional_kwargs`` (e.g.
    ``cache_control`` prompt-cache breakpoints), ``id``, ``name`` and
    ``response_metadata`` from the original message.
    """
    existing_blocks = list(system_message.content_blocks) if system_message else []
    new_blocks = [*existing_blocks, {"type": "text", "text": text}]
    if system_message is None:
        return SystemMessage(content=new_blocks)
    return system_message.model_copy(update={"content": new_blocks})

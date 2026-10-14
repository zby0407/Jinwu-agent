"""Recognize explicit current 24-hour forecast requests, without running anything."""

import re


def bounded_tool_model(model):
    """A request-local non-retrying model; no global provider settings change."""
    from jw.middleware.utils import disable_thinking

    model = disable_thinking(model)
    name = str(getattr(model, "model_name", "")).casefold()
    base = str(getattr(model, "openai_api_base", "")).casefold()
    updates = {"max_retries": 0, "timeout": 150}
    if name.startswith("deepseek") or "api.deepseek.com" in base:
        body = dict(getattr(model, "extra_body", None) or {})
        body["thinking"] = {"type": "disabled"}
        updates.update(extra_body=body, reasoning_effort=None)
    # Pydantic field copies alone do not change the already-created SDK clients.
    for root_name, client_name in (
        ("root_client", "client"),
        ("root_async_client", "async_client"),
    ):
        client = getattr(model, root_name, None)
        if client is not None and hasattr(client, "with_options"):
            limited = client.with_options(max_retries=0, timeout=150)
            updates[root_name] = limited
            updates[client_name] = limited.chat.completions
    safe = model.model_copy(update=updates)
    qwen = (
        name.startswith(("qwen", "qwq"))
        or "dashscope.aliyuncs.com" in base
        or "maas.aliyuncs.com" in base
    )
    deepseek = name.startswith("deepseek") or "api.deepseek.com" in base
    if (qwen or deepseek) and hasattr(type(safe), "_get_request_payload"):
        # JW passback wrappers close over their instance. Rebind on the request
        # copy so thinking and retry settings cannot read the original fields.
        object.__setattr__(
            safe, "_get_request_payload", type(safe)._get_request_payload.__get__(safe)
        )
        from jw.llm.patches import (
            _patch_deepseek_reasoning_passback,
            _patch_qwen_reasoning_passback,
        )

        if deepseek:
            _patch_deepseek_reasoning_passback(safe)
        else:
            _patch_qwen_reasoning_passback(safe)
    return safe


def is_live_request(text: str) -> bool:
    text = text.strip().casefold()
    if re.search(
        r"能不能做到|是否支持|前端输入|提示词|接入|实现功能|这个功能|模型能否|评估模型",
        text,
    ):
        return False
    text = re.sub(r'“[^”]*”|「[^」]*」|`[^`]*`|"[^"\n]*"', "", text)
    if not re.search(r"耀斑|solar\s+flare", text):
        return False
    if re.search(
        r"(?:不要|暂不|先不|取消|停止|别)[^，。；,\n]{0,12}(?:预测|预报|发报)|如何|怎么实现|原理|解释|举例|假设|如果|测试代码|修复|修改代码|(?:don't|do not).{0,12}(?:predict|forecast)|how to",
        text,
    ):
        return False
    horizon = re.search(
        r"(?:未来|接下来|往后|今后|现在起).{0,12}(?:24\s*(?:h|小时|小時)|二十四小时|一天)|next\s+24\s*(?:h|hours)",
        text,
    )
    action = re.search(r"预测|预报|发报|forecast|predict", text)
    return bool(horizon and action)


def latest_request(messages):
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            content = message.content
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            return str(content), message
    return "", None

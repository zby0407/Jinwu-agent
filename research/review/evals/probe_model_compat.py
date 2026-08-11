#!/usr/bin/env python3
"""Run bounded real compatibility probes without persisting model text or keys."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from jw.config import apply_config_to_env, get_effective_config
from jw.llm import get_chat_model

STRUCTURED_SCHEMA = {
    "title": "CompatibilityProbeRecord",
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "value": {"type": "integer"},
    },
    "required": ["status", "value"],
    "additionalProperties": False,
}


@tool
def probe_add(left: int, right: int) -> int:
    """Add two integers for a compatibility probe."""

    return left + right


def _nonempty_content(message: Any) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(isinstance(item, dict) and item.get("text") for item in content)
    return False


def _thinking_observed(message: Any) -> bool:
    additional = getattr(message, "additional_kwargs", {})
    if isinstance(additional, dict) and additional.get("reasoning_content"):
        return True
    content = getattr(message, "content", None)
    return bool(
        isinstance(content, list)
        and any(
            isinstance(item, dict)
            and item.get("type") in {"thinking", "reasoning", "reasoning_content"}
            for item in content
        )
    )


def _tool_call(message: Any) -> dict[str, Any] | None:
    calls = getattr(message, "tool_calls", None)
    if not isinstance(calls, list) or len(calls) != 1:
        return None
    call = calls[0]
    return call if isinstance(call, dict) else None


def _structured_ok(value: Any) -> bool:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return (
        isinstance(value, dict)
        and value.get("status") == "ok"
        and isinstance(value.get("value"), int)
    )


def _failure(exc: BaseException) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    result = {
        "ok": False,
        "error_type": type(exc).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
    }
    body = getattr(exc, "body", None)
    if body is not None:
        text = json.dumps(body, ensure_ascii=False, default=str)
        text = re.sub(
            r"(?i)(api[_ -]?key|authorization|token|secret|password)\s*[:=]\s*[^,}\s]+",
            r"\1=<redacted>",
            text,
        )
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "<redacted-key>", text)
        result["provider_error"] = text[:2000]
    return result


def probe_one(label: str, model_name: str, provider: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "label": label,
        "model": model_name,
        "provider": provider,
        "started_at": datetime.now(UTC).isoformat(),
        "probes": {},
    }

    try:
        model = get_chat_model(
            model=model_name,
            provider=provider,
            max_tokens=2048,
            max_retries=0,
        )
        answer = model.invoke("Reply with the single word OK.")
        record["probes"]["ordinary_answer"] = {
            "ok": _nonempty_content(answer),
            "thinking_observed": _thinking_observed(answer),
        }
    except Exception as exc:  # pragma: no cover - live provider boundary
        record["probes"]["ordinary_answer"] = _failure(exc)

    try:
        model = get_chat_model(
            model=model_name,
            provider=provider,
            max_tokens=2048,
            max_retries=0,
        ).bind_tools([probe_add])
        answer = model.invoke(
            "Call probe_add exactly once with left=2 and right=3. Do not answer directly."
        )
        call = _tool_call(answer)
        record["probes"]["single_tool"] = {
            "ok": bool(call and call.get("name") == "probe_add"),
            "thinking_observed": _thinking_observed(answer),
        }
    except Exception as exc:  # pragma: no cover - live provider boundary
        record["probes"]["single_tool"] = _failure(exc)

    try:
        model = get_chat_model(
            model=model_name,
            provider=provider,
            max_tokens=2048,
            max_retries=0,
        )
        structured_method = None
        structured_prompt = (
            "Return status='ok' and value=7 using the required structure."
        )
        if provider == "kimi-coding":
            structured_method = "json_schema"
        elif provider == "deepseek":
            structured_method = "json_mode"
            structured_prompt = (
                "Return only a JSON object with status='ok' and value=7 using "
                "the required structure."
            )
        structured = (
            model.with_structured_output(
                STRUCTURED_SCHEMA,
                method=structured_method,
            )
            if structured_method
            else model.with_structured_output(STRUCTURED_SCHEMA)
        )
        answer = structured.invoke(structured_prompt)
        record["probes"]["structured_output"] = {
            "ok": _structured_ok(answer),
            "method": structured_method or "provider_default",
        }
    except Exception as exc:  # pragma: no cover - live provider boundary
        record["probes"]["structured_output"] = _failure(exc)

    try:
        model = get_chat_model(
            model=model_name,
            provider=provider,
            max_tokens=2048,
            max_retries=0,
        ).bind_tools([probe_add])
        human_1 = HumanMessage(
            "Call probe_add exactly once with left=4 and right=5. Do not answer directly."
        )
        answer_1 = model.invoke([human_1])
        call_1 = _tool_call(answer_1)
        if not call_1:
            raise RuntimeError("first round returned no unique tool call")
        tool_result_1 = ToolMessage(
            content="9",
            tool_call_id=str(call_1.get("id") or "probe-call-1"),
            name="probe_add",
        )
        human_2 = HumanMessage(
            "Now call probe_add exactly once with left=6 and right=7. Do not answer directly."
        )
        answer_2 = model.invoke([human_1, answer_1, tool_result_1, human_2])
        call_2 = _tool_call(answer_2)
        record["probes"]["multi_turn_tool"] = {
            "ok": bool(call_2 and call_2.get("name") == "probe_add"),
            "round_1_thinking_observed": _thinking_observed(answer_1),
            "round_2_thinking_observed": _thinking_observed(answer_2),
            "reasoning_content_passback_source_present": bool(
                isinstance(answer_1, AIMessage)
                and answer_1.additional_kwargs.get("reasoning_content")
            ),
        }
    except Exception as exc:  # pragma: no cover - live provider boundary
        record["probes"]["multi_turn_tool"] = _failure(exc)

    record["passed"] = all(
        isinstance(result, dict) and result.get("ok") is True
        for result in record["probes"].values()
    )
    record["ended_at"] = datetime.now(UTC).isoformat()
    return record


def _parse_spec(value: str) -> tuple[str, str, str]:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise argparse.ArgumentTypeError("spec must be label:model:provider")
    return tuple(part.strip() for part in parts)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", action="append", type=_parse_spec, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    apply_config_to_env(get_effective_config())
    payload = {
        "schema_version": "model-compatibility-probes-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "results": [probe_one(*spec) for spec in args.spec],
    }
    payload["passed"] = all(result["passed"] for result in payload["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "passed": payload["passed"],
                "models": [
                    {"label": row["label"], "passed": row["passed"]}
                    for row in payload["results"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

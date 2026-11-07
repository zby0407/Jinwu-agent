"""Immutable, task-local storage of genuine scientific specialist replies.

This transports model-produced text; it does not author or validate science.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .contracts import utc_now
from .discovery_support import write_receipt

MAX_REPLY_BYTES = 1_000_000


def reply_failure(content: str, *, status: str = "success") -> str | None:
    """Recognize framework failures, without judging a partial science report."""
    text = content.lstrip()
    if status == "error":
        return "tool_error"
    if text.startswith(
        ("[TASK CANCELLED]", "[TOOL ERROR", "[CONTRACT TOOL BLOCKED]", "Tool call ")
    ):
        return "tool_failure"
    if re.match(r"^(?:Model|Tool) call limits exceeded\b", text, re.IGNORECASE):
        return "internal_call_limit"
    if text.startswith(
        ("[JW_PROVIDER:", "GraphRecursionError:", "TrialLimitExceeded:")
    ):
        return "runtime_failure"
    return None


def persist_reply(workspace: Path, call_id: str, result: Any) -> Any:
    """Persist before downstream context editing/offload can erase a reply."""

    def persist(message):
        if not isinstance(message, ToolMessage):
            return message
        content = message.content
        if not isinstance(content, str):
            return message
        raw = content.encode("utf-8")
        if len(raw) > MAX_REPLY_BYTES:
            raise ValueError("scientific handoff exceeds 1 MB; return a bounded report")
        digest = hashlib.sha256(raw).hexdigest()
        name = re.sub(r"[^A-Za-z0-9_-]", "_", call_id)[:100] or "reply"
        folder = workspace.resolve() / "scientific_data/handoffs"
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("handoff directory escaped task workspace")
        path = folder / f"{name}__{digest[:20]}.txt"
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("handoff file escaped task workspace")
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError("stored specialist reply changed") from None
        failure = reply_failure(content, status=message.status)
        receipt = path.with_suffix(".json")
        payload = {
            "kind": "scientific_data_handoff",
            "status": "error" if failure else "returned",
            "failure_reason": failure,
            "tool_call_id": call_id,
            "message_id": message.id,
            "report_path": str(path.relative_to(workspace.resolve())),
            "receipt_path": str(receipt.relative_to(workspace.resolve())),
            "sha256": digest,
            "bytes": len(raw),
            "characters": len(content),
            "created_at": utc_now(),
            "excerpt": content[:2500],
            "scientific_acceptance": "not_independently_reviewed",
            "read_more": "sci_data_runs(kind='handoff', receipt_path=..., content_offset=..., content_limit=...)",
        }
        if not receipt.exists():
            write_receipt(receipt, payload)
        else:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        return message.model_copy(
            update={"content": json.dumps(payload, ensure_ascii=False)}
        )

    if isinstance(result, ToolMessage):
        return persist(result)
    if isinstance(result, Command) and isinstance(result.update, dict):
        update = dict(result.update)
        update["messages"] = [persist(m) for m in update.get("messages", [])]
        return replace(result, update=update)
    return result


def read_reply(workspace: Path, receipt_path: str) -> tuple[dict, str]:
    root = workspace.resolve()
    folder = root / "scientific_data/handoffs"
    receipt = Path(receipt_path)
    receipt = (receipt if receipt.is_absolute() else root / receipt).resolve()
    if not receipt.is_relative_to(folder) or receipt.stat().st_size > 100_000:
        raise ValueError("handoff receipt escaped its task-local directory")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if payload.get("kind") != "scientific_data_handoff":
        raise ValueError("not a specialist handoff receipt")
    path = (root / payload["report_path"]).resolve()
    if not path.is_relative_to(folder) or path.stat().st_size > MAX_REPLY_BYTES:
        raise ValueError("handoff report escaped its bounded directory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != payload["sha256"]:
        raise ValueError("specialist reply hash changed")
    text = raw.decode("utf-8")
    failure = reply_failure(text)
    if failure and payload.get("status") != "error":
        # Earlier receipts are immutable; reject their invalid success label
        # in memory rather than silently rewriting historical evidence.
        payload = {
            **payload,
            "recorded_status": payload.get("status"),
            "status": "error",
            "failure_reason": failure,
        }
    return payload, text


def turn_messages(request) -> list:
    """Use durable turn identity, not a context-edited model projection."""
    saved = request.state.get("messages") if request.state else None
    return list(saved) if saved else list(request.messages)


def delegation_state(messages) -> tuple[set[str], Any]:
    def value(message, key, default=None):
        return (
            message.get(key, default)
            if isinstance(message, Mapping)
            else getattr(message, key, default)
        )

    start = 0
    for index, message in enumerate(messages):
        if value(message, "type", value(message, "role")) in {"human", "user"}:
            start = index + 1
    ids = {
        call.get("id")
        for message in messages[start:]
        for call in value(message, "tool_calls", []) or []
        if call.get("name") == "task"
        and call.get("args", {}).get("subagent_type") == "scientific-data"
    }
    reply = next(
        (
            m
            for m in reversed(messages[start:])
            if value(m, "type", value(m, "role")) == "tool"
            and value(m, "tool_call_id") in ids
        ),
        None,
    )
    return ids, reply


def requested_report_path(text: str) -> str | None:
    match = re.search(
        r"(?:保存|另存|写入|save|write|persist).{0,100}?(/?outputs/[\w./-]+\.(?:md|txt))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    path = match.group(1).lstrip("/")
    if ".." in Path(path).parts or len(path) > 240:
        raise ValueError("unsafe requested report path")
    return path

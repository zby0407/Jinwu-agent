"""Translate pi RPC events into EvoScientist stream events."""

from __future__ import annotations

from typing import Any

from ..stream.emitter import StreamEventEmitter


class PiEventTranslator:
    """Convert raw pi events into StreamEventEmitter event dicts."""

    def __init__(self, emitter: StreamEventEmitter | None = None) -> None:
        self.emitter = emitter or StreamEventEmitter()
        self._emitted_tool_calls: set[str] = set()
        self._full_response = ""

    def translate(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        etype = event.get("type")
        if etype == "message_update":
            return self._translate_message_update(event)
        if etype == "tool_execution_end":
            return self._translate_tool_execution_end(event)
        if etype == "message_end":
            return self._translate_message_end(event)
        if etype == "agent_end":
            return []
        return []

    def _translate_message_update(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        ame = event.get("assistantMessageEvent") or {}
        ame_type = ame.get("type")
        if ame_type == "text_delta":
            text = ame.get("delta") or ""
            if text:
                self._full_response += text
                return [self.emitter.text(text).data]
            return []
        if ame_type == "toolcall_end":
            tool_call = ame.get("toolCall") or {}
            tc_id = tool_call.get("id") or ""
            name = tool_call.get("name") or ""
            args = tool_call.get("arguments") or {}
            if not tc_id or tc_id in self._emitted_tool_calls:
                return []
            self._emitted_tool_calls.add(tc_id)
            return [self.emitter.tool_call(name, dict(args), str(tc_id)).data]
        return []

    def _translate_tool_execution_end(
        self, event: dict[str, Any]
    ) -> list[dict[str, Any]]:
        tc_id = event.get("toolCallId") or ""
        name = event.get("toolName") or "unknown"
        result = event.get("result") or {}
        is_error = bool(event.get("isError"))
        content = self._extract_result_text(result)
        success = not is_error
        return [self.emitter.tool_result(name, content, success, str(tc_id)).data]

    def _translate_message_end(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        inp = int(usage.get("input") or 0)
        out_toks = int(usage.get("output") or 0)
        if inp or out_toks:
            out.append(self.emitter.usage_stats(inp, out_toks).data)

        if message.get("stopReason") == "error" and message.get("errorMessage"):
            out.append(self.emitter.error(str(message["errorMessage"])).data)
        return out

    @staticmethod
    def _extract_result_text(result: Any) -> str:
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                text = "".join(parts)
                if text:
                    return text
            if isinstance(content, str):
                return content
        return str(result)

    @property
    def full_response(self) -> str:
        return self._full_response

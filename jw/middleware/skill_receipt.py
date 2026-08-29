"""Model-visible receipt for the exact project Skills assigned to an agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest


class SkillReceiptMiddleware(AgentMiddleware):
    """Append a compact, deterministic Skill-loading receipt to each request.

    ``SkillsMiddleware`` exposes the full instructions on demand.  This
    middleware records which sources were actually resolved from the JW
    registry, so a run can be audited without trusting model prose.
    """

    def __init__(self, receipt: Mapping[str, Any]) -> None:
        self.receipt = dict(receipt)

    def _text(self) -> str:
        skills = self.receipt.get("skills", [])
        missing = self.receipt.get("missing", [])
        lines = [
            "<skill_runtime_receipt>",
            f"schema_version: {self.receipt.get('schema_version', 'jw-skill-receipt-v1')}",
            f"agent: {self.receipt.get('agent', '')}",
            f"status: {self.receipt.get('status', 'unknown')}",
            f"skill_count: {self.receipt.get('skill_count', len(skills))}",
            "skills: " + (", ".join(str(item) for item in skills) or "(none)"),
        ]
        if missing:
            lines.append("missing: " + ", ".join(str(item) for item in missing))
        lines.extend(
            [
                "Use the listed Skills when relevant; do not claim a Skill was loaded if its source is missing.",
                "</skill_runtime_receipt>",
            ]
        )
        return "\n".join(lines)

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        from deepagents.middleware._utils import append_to_system_message

        marker = "<skill_runtime_receipt>"
        current = str(request.system_message.content) if request.system_message else ""
        if marker in current:
            return request
        return request.override(
            system_message=append_to_system_message(
                request.system_message,
                self._text(),
            )
        )

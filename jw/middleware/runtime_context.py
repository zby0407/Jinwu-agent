"""Runtime context middleware for JW."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langgraph.config import get_config

RUNTIME_CONTEXT_TEMPLATE = """<runtime_context>
Current date: {date}
Local timezone: {timezone}

Use this context to resolve relative time references like today, yesterday, and
next week.
{project_inputs}
</runtime_context>"""


def _format_timezone(now: datetime) -> str:
    """Return a compact local timezone label for prompt injection."""
    offset = now.utcoffset()
    if offset is None:
        return "local"

    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    offset_text = f"UTC{sign}{hours:02d}:{minutes:02d}"

    name = now.tzname()
    if name and name != offset_text:
        return f"{name} ({offset_text})"
    return offset_text


class RuntimeContextMiddleware(AgentMiddleware):
    """Inject per-turn runtime context into model calls."""

    def __init__(
        self,
        *,
        now_fn: Callable[[], datetime] | None = None,
        workspace_dir: str | Path | None = None,
    ) -> None:
        self._now_fn = now_fn or (lambda: datetime.now().astimezone())
        if workspace_dir is None:
            from .. import paths

            workspace_dir = paths.WORKSPACE_ROOT
        self._resolved_base_workspace = str(Path(workspace_dir).expanduser().resolve())

    def _project_inputs(self) -> str:
        from ..workspaces import cached_project_inputs_for_config

        try:
            current = get_config()
        except RuntimeError:
            return ""
        config = current if isinstance(current, dict) else None
        inputs = cached_project_inputs_for_config(
            config,
            self._resolved_base_workspace,
        )
        if not inputs:
            return ""
        limit = 50
        lines = [
            "",
            "Task-bound project inputs are available read-only. Inspect relevant "
            "files before downloading substitutes:",
            *(
                f"- {item['path']} [{item.get('role', 'primary_data')}]"
                for item in inputs[:limit]
            ),
        ]
        if len(inputs) > limit:
            lines.append(f"- … {len(inputs) - limit} more in input_manifest.json")
        lines.extend(
            [
                "",
                "Data lineage requirements:",
                "- When the user names a dataset or version, derive requested "
                "measurements from the matching declared raw/tabular input or "
                "an authoritative source.",
                "- Existing scripts, reports, and derived artifacts are secondary "
                "references. Do not copy their embedded numeric constants and "
                "claim they came from the requested dataset unless the user "
                "explicitly asks to reuse that artifact.",
                "- Dataset semantics are also evidence: entity identifiers, time "
                "boundaries, units, smoothing, and other transformations must be "
                "derived by an explicit reproducible rule or supported by "
                "dataset-provided or authoritative metadata. Never guess "
                "approximate boundaries and relabel them as requested entities.",
                "- Before model fitting, inspect or save the derived feature table "
                "and sanity-check its identifiers, time coverage, units, row "
                "counts, and chronological training boundary against the request.",
                "- Treat prior memories and answers as hypotheses, not source "
                "evidence. Recompute from the declared input when their provenance "
                "is missing, secondary, or conflicts with the requested version.",
                "- Record the source virtual path and the transformation used. "
                "For shell or Python analysis, first copy selected read-only "
                "project inputs into /inputs/.",
            ]
        )
        return "\n".join(lines)

    def _runtime_context(self) -> str:
        now = self._now_fn()
        return RUNTIME_CONTEXT_TEMPLATE.format(
            date=now.strftime("%Y-%m-%d"),
            timezone=_format_timezone(now),
            project_inputs=self._project_inputs(),
        )

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        """Append runtime context to the system prompt."""
        from deepagents.middleware._utils import append_to_system_message

        new_system = append_to_system_message(
            request.system_message,
            self._runtime_context(),
        )
        return request.override(system_message=new_system)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject runtime context before the sync model handler."""
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject runtime context before the async model handler."""
        return await handler(self.modify_request(request))


def create_runtime_context_middleware(
    *,
    now_fn: Callable[[], datetime] | None = None,
    workspace_dir: str | Path | None = None,
) -> RuntimeContextMiddleware:
    """Build runtime-context middleware."""
    return RuntimeContextMiddleware(
        now_fn=now_fn,
        workspace_dir=workspace_dir,
    )

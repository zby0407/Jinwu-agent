"""Host-enforced, stage-aware Wiki context injection + throttled growth.

Research stages should not have to remember to query the Wiki.  This
middleware detects the specialist identity and, for the five research stages,
injects one frozen ``knowledge-context-v1`` packet into the model's system
prompt.  The packet is built from the bound task question, selects canonical
entries ahead of candidates, and records a usage receipt for every entry.

The middleware is provider-agnostic and sits before the Qwen compatibility
layer.  It never injects into the main agent or the read-only knowledge agent.

On the main agent it also owns the *growth trigger*: when a turn ends the
middleware may start one throttled background maintenance cycle
(``knowledge_base.growth.run_growth_cycle``).  Growth therefore happens
without the agent deciding to call ``kb_growth``, which is what makes the Wiki
self-maintaining.  The cycle is date-bucketed and idempotent, so repeated
triggers cannot double-apply work, and it never writes knowledge that a
deterministic gate has not approved.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langgraph.config import get_config

logger = logging.getLogger(__name__)

_STAGE_AGENT_MARKERS = {
    "solar-planner": "planning",
    "solar-data": "data",
    "solar-hypothesis": "hypothesis",
    "solar-experiment": "experiment_design",
    "solar-evidence": "evidence",
}
_EXPERIMENT_STAGE_RE = re.compile(
    r"\bstage\s*=\s*(experiment_design|experiment_result)\b"
)
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_CACHE_LIMIT = 128


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content or "")


def _message_text(message: Any) -> str:
    return _content_text(getattr(message, "content", message))


def _system_text(request: ModelRequest) -> str:
    return _content_text(getattr(request, "system_message", None))


def _specialist_identity(system_text: str) -> str:
    if "You are the independent Evidence Reviewer" in system_text:
        return "solar-evidence"
    for name in _STAGE_AGENT_MARKERS:
        if f"{name} sub-agent" in system_text:
            return name
    return ""


def detect_stage(request: ModelRequest) -> str:
    """Return the research stage for this model call, or ``\"\"`` to skip."""

    system_text = _system_text(request)
    specialist = _specialist_identity(system_text)
    if not specialist:
        return ""
    if specialist == "solar-evidence":
        return "evidence"
    if specialist != "solar-experiment":
        return _STAGE_AGENT_MARKERS[specialist]
    # The experiment prompt mentions both phases in prose, so only trust the
    # task-description marker, never the static system prompt.
    messages = "\n".join(_message_text(item) for item in (request.messages or [])[-8:])
    match = _EXPERIMENT_STAGE_RE.search(messages)
    return match.group(1) if match else "experiment_design"


def _task_workspace() -> Path | None:
    try:
        config = get_config()
    except RuntimeError:
        return None
    try:
        from jw.workspaces import workspace_root_from_config

        return workspace_root_from_config(config)
    except Exception:
        return None


def _task_question(request: ModelRequest) -> str:
    workspace = _task_workspace()
    if workspace is not None:
        try:
            import json

            task = json.loads(
                (Path(workspace) / "task.json").read_text(encoding="utf-8")
            )
            question = str(task.get("research_question") or "").strip()
            if question:
                return question[:1200]
        except (OSError, TypeError, ValueError):
            pass
    for message in reversed(list(request.messages or [])):
        if getattr(message, "type", "") == "human":
            text = " ".join(_TAG_RE.sub(" ", _message_text(message)).split())
            if text:
                return text[:800]
    return ""


def _cache_key(request: ModelRequest, stage: str) -> str:
    try:
        config = get_config()
    except RuntimeError:
        config = {}
    configurable = config.get("configurable") if isinstance(config, dict) else {}
    thread_id = str((configurable or {}).get("thread_id") or "")
    if thread_id:
        return f"{thread_id}|{stage}"
    question = _task_question(request)
    fingerprint = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    return f"{stage}|{fingerprint}"


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _enabled() -> bool:
    return _flag("JW_KNOWLEDGE_CONTEXT")


def _growth_enabled() -> bool:
    return _flag("JW_KB_AUTO_GROWTH")


def _growth_interval_seconds() -> float:
    raw = os.getenv("JW_KB_AUTO_GROWTH_INTERVAL", "3600").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 3600.0


# Process-wide throttle.  A turn ends constantly, but a maintenance sweep only
# needs to happen on the order of once per interval; the growth jobs are also
# date-bucketed, so this only saves work, it never loses it.
_GROWTH_LOCK = threading.Lock()
_GROWTH_LAST_STARTED = 0.0
_GROWTH_INFLIGHT = False
_GROWTH_LAST_RESULT: dict[str, Any] = {}


def _run_growth_cycle_once() -> None:
    """Background body: one idempotent maintenance sweep."""

    global _GROWTH_INFLIGHT, _GROWTH_LAST_RESULT
    try:
        from jw.tools.knowledge_base import _get_store
        from knowledge_base.growth import run_growth_cycle

        _GROWTH_LAST_RESULT = dict(run_growth_cycle(_get_store()))
        logger.debug("knowledge growth cycle: %s", _GROWTH_LAST_RESULT)
    except Exception as exc:
        _GROWTH_LAST_RESULT = {"status": "error", "error": str(exc)}
        logger.warning("knowledge growth cycle failed", exc_info=True)
    finally:
        with _GROWTH_LOCK:
            _GROWTH_INFLIGHT = False


def trigger_growth(*, force: bool = False) -> dict[str, Any]:
    """Start one throttled background growth cycle.

    Returns ``{"status": "started"|"throttled"|"inflight"|"disabled"}``.  The
    call never blocks and never raises; the actual sweep runs on a daemon
    thread so a turn's completion is not delayed by maintenance work.
    """

    global _GROWTH_LAST_STARTED, _GROWTH_INFLIGHT
    if not force and not _growth_enabled():
        return {"status": "disabled"}
    now = time.monotonic()
    with _GROWTH_LOCK:
        if _GROWTH_INFLIGHT:
            return {"status": "inflight"}
        interval = _growth_interval_seconds()
        if not force and _GROWTH_LAST_STARTED and now - _GROWTH_LAST_STARTED < interval:
            return {"status": "throttled", "interval_seconds": interval}
        _GROWTH_LAST_STARTED = now
        _GROWTH_INFLIGHT = True
    thread = threading.Thread(
        target=_run_growth_cycle_once,
        name="kb-growth-cycle",
        daemon=True,
    )
    thread.start()
    return {"status": "started"}


def growth_state() -> dict[str, Any]:
    """Expose throttle state (diagnostics and tests)."""

    with _GROWTH_LOCK:
        return {
            "enabled": _growth_enabled(),
            "interval_seconds": _growth_interval_seconds(),
            "inflight": _GROWTH_INFLIGHT,
            "last_result": dict(_GROWTH_LAST_RESULT),
        }


def _reset_growth_state() -> None:
    """Test hook: clear the process-wide throttle."""

    global _GROWTH_LAST_STARTED, _GROWTH_INFLIGHT, _GROWTH_LAST_RESULT
    with _GROWTH_LOCK:
        _GROWTH_LAST_STARTED = 0.0
        _GROWTH_INFLIGHT = False
        _GROWTH_LAST_RESULT = {}


class KnowledgeContextMiddleware(AgentMiddleware):
    """Inject one frozen, stage-aware Wiki context packet per specialist task."""

    def __init__(self, *, growth: bool = False) -> None:
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._growth = growth

    def _packet(self, request: ModelRequest, stage: str) -> str:
        key = _cache_key(request, stage)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        query = _task_question(request)
        try:
            from jw.tools.knowledge_base import _get_store
            from knowledge_base.context import (
                STAGE_AGENT,
                build_knowledge_context,
                render_knowledge_context,
                unavailable_context,
            )

            agent = STAGE_AGENT.get(stage, "solar-knowledge")
            run_id = ""
            try:
                config = get_config()
            except RuntimeError:
                config = {}
            configurable = (
                config.get("configurable") if isinstance(config, dict) else {}
            )
            run_id = str((configurable or {}).get("thread_id") or "")
            packet = build_knowledge_context(
                _get_store(),
                query,
                stage=stage,
                limit=6,
                agent=agent,
                run_id=run_id,
                record_usage=True,
            )
            rendered = render_knowledge_context(packet)
        except Exception as exc:
            try:
                from knowledge_base.context import (
                    render_knowledge_context,
                    unavailable_context,
                )

                rendered = render_knowledge_context(
                    unavailable_context(stage, query, str(exc))
                )
            except Exception:
                return ""

        self._cache[key] = rendered
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return rendered

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        if not _enabled():
            return request
        if "<knowledge_context" in _system_text(request):
            return request
        stage = detect_stage(request)
        if not stage:
            return request
        rendered = self._packet(request, stage)
        if not rendered:
            return request
        from deepagents.middleware._utils import append_to_system_message

        return request.override(
            system_message=append_to_system_message(
                request.system_message,
                rendered,
            )
        )

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        return await handler(self.modify_request(request))

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._growth:
            trigger_growth()
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._growth:
            trigger_growth()
        return None


def create_knowledge_context_middleware(
    *, growth: bool = False
) -> KnowledgeContextMiddleware:
    """Build the host knowledge-context middleware.

    ``growth=True`` additionally makes this instance the host's growth trigger:
    every main-agent turn end may schedule one throttled maintenance sweep.
    Specialist instances keep it off, so a sub-agent can never start a sweep.
    """

    return KnowledgeContextMiddleware(growth=growth)


__all__ = [
    "KnowledgeContextMiddleware",
    "create_knowledge_context_middleware",
    "detect_stage",
    "growth_state",
    "trigger_growth",
]

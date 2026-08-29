"""JW Agent graph construction.

This module defines the agent graph and its factory functions.  All heavy
initialization (deepagents, backends, LLM, middleware) is deferred to first
use so that importing this module is fast and non-agent CLI commands
(``jw config list``, ``jw onboard``) never pay the cost.

Usage:
    from jw import jw_agent
    from jw.stream.events import stream_agent_events

    # Notebook / programmatic usage
    async for event in stream_agent_events(
        jw_agent, "your question", thread_id="1"
    ):
        ...
"""

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)

from . import paths as _paths_mod
from .config import (
    DEFAULT_AGENT_MODEL_CALL_LIMIT,
    DEFAULT_AGENT_TOOL_CALL_LIMIT,
    DEFAULT_SUBAGENT_MODEL_CALL_LIMIT,
    DEFAULT_SUBAGENT_TOOL_CALL_LIMIT,
    MemoryControls,
    MemoryObservationTarget,
    apply_config_to_env,
    get_effective_config,
)
from .memory import MemorySourceType
from .paths import set_active_workspace, set_workspace_root
from .prompts import get_system_prompt
from .subagents.skill_registry import skills_for_agent as _skills_for_agent

# Suppress noisy warnings from deepagents skill loader
# (non-string frontmatter fields, etc.).
logging.getLogger("deepagents.middleware.skills").setLevel(logging.ERROR)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# =============================================================================
# Constants
# =============================================================================

SUBAGENTS_CONFIG = Path(__file__).parent / "subagents"
SKILLS_DIR = str(Path(__file__).parent / "subagents")

# Keep the root Agent's on-demand catalog bounded to orchestration/release
# capabilities. Role-specific skills are injected only into the six Solar
# specialist graphs via ``_inject_subagent_middleware``.
DEFAULT_SKILL_SOURCES = tuple(_skills_for_agent("JW"))
HYPOTHESIS_SUBAGENT_MODEL_CALL_LIMIT_FLOOR = 32


def _main_skill_sources() -> list[str]:
    """Return the registry-governed Skill sources for the JW main agent."""
    from .subagents.skill_registry import skills_for_agent

    return skills_for_agent("JW")


def _positive_call_limit(value: object, fallback: int | None) -> int | None:
    """Normalize a configured call budget; zero disables the limit."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value if value > 0 else None
    return fallback


def _call_limit_middleware(
    cfg,
    *,
    subagent: bool,
    model_limit_override: int | None = None,
    model_limit_floor: int | None = None,
) -> list[AgentMiddleware]:
    """Build LangChain's stateful loop guards from JW configuration."""
    if subagent:
        model_limit = _positive_call_limit(
            (
                model_limit_override
                if model_limit_override is not None
                else getattr(cfg, "subagent_model_call_limit", None)
            ),
            DEFAULT_SUBAGENT_MODEL_CALL_LIMIT,
        )
        hard_model_limit = _positive_call_limit(
            getattr(cfg, "subagent_model_call_hard_limit", None),
            None,
        )
        if model_limit is not None and hard_model_limit is not None:
            model_limit = min(model_limit, hard_model_limit)
        tool_limit = _positive_call_limit(
            getattr(cfg, "subagent_tool_call_limit", None),
            DEFAULT_SUBAGENT_TOOL_CALL_LIMIT,
        )
    else:
        model_limit = _positive_call_limit(
            getattr(cfg, "agent_model_call_limit", None),
            DEFAULT_AGENT_MODEL_CALL_LIMIT,
        )
        tool_limit = _positive_call_limit(
            getattr(cfg, "agent_tool_call_limit", None),
            DEFAULT_AGENT_TOOL_CALL_LIMIT,
        )
    if model_limit is not None and model_limit_floor is not None:
        model_limit = max(model_limit, model_limit_floor)

    middleware: list[AgentMiddleware] = []
    if tool_limit is not None:
        # Let the model see a blocked-tool result and produce a truthful partial
        # handoff instead of ending on an opaque framework error.
        middleware.append(
            ToolCallLimitMiddleware(
                run_limit=tool_limit,
                exit_behavior="continue",
            )
        )
    if model_limit is not None:
        # Hard termination fallback if the model ignores repeated blocked-tool
        # results and continues asking for tools.
        middleware.append(
            ModelCallLimitMiddleware(
                run_limit=model_limit,
                exit_behavior="end",
            )
        )
    return middleware


def _resolve_subagent_dirs(
    bundles: list[str] | None = None,
) -> list[Path]:
    """Return the sub-agent config directories to load, honouring bundles.

    Thin wrapper around :func:`JW.subagents._registry.resolve_bundle_dirs`
    so the rest of this module keeps a single call-site. ``bundles=None``
    enables every discovered bundle (the historical default behaviour);
    pass an explicit list (e.g. ``["core"]``) to restrict which domain
    bundles are active for a deployment.

    Falls back to ``[SUBAGENTS_CONFIG]`` if the registry is unavailable so
    the agent still boots against a legacy flat ``subagents/`` layout.
    """
    try:
        from .subagents._registry import resolve_bundle_dirs

        dirs = resolve_bundle_dirs(SUBAGENTS_CONFIG, bundles=bundles)
        if dirs:
            return dirs
    except Exception:  # pragma: no cover - defensive fallback
        pass
    return [SUBAGENTS_CONFIG]


# =============================================================================
# Lazy state — initialized on first use, not at import time
# =============================================================================

_config = None
_chat_model = None
# Track the (model, provider) binding of _chat_model so cache invalidates
# when config.model/provider change (e.g. via /model). Without this,
# _ensure_chat_model() returns the stale cached instance even after
# _ensure_config(new_cfg) has overwritten the active config — causing
# /model switch to lag one step (see issue #179).
_chat_model_key: tuple[str | None, str | None] | None = None

# Auxiliary model for background/helper LLM calls (memory workers + main-agent
# tool selector). Cached separately from the main model; falls back to the main
# instance when the auxiliary_* config fields are empty (see
# _ensure_auxiliary_chat_model).
_auxiliary_chat_model = None
_auxiliary_chat_model_key: tuple[str | None, str | None] | None = None

# Cache MCP tools by the effective config signature to avoid reconnecting
# to MCP servers on every `/new` when config is unchanged.
_MCP_TOOLS_CACHE_KEY: str | None = None
_MCP_TOOLS_CACHE_VALUE: dict[str, list] | None = None

# Default agent (no checkpointer) — used by langgraph dev / LangSmith / notebooks.
# Lazily constructed on first access so MCP tools are included without
# spawning subprocesses at import time.
_jw_agent = None


# =============================================================================
# Lazy initialization helpers
# =============================================================================


def set_active_config(cfg) -> None:
    """Commit *cfg* as the active module config.

    Public commit path for callers (e.g. ``/model``) that built an agent on
    the pure ``create_cli_agent(config=..., chat_model=...)`` path and now
    want it to become the session-wide active config.  This is the write half
    of ``_ensure_config(cfg)`` extracted so the pure path can defer the commit
    until the agent has been built successfully.
    """
    global _config
    _config = cfg
    apply_config_to_env(cfg)


def _apply_env_from_config(cfg) -> None:
    """Apply *cfg*'s API-key env vars without caching it as ``_config``.

    ``apply_config_to_env`` is set-if-unset (guards on ``not
    os.environ.get(...)``), so this is idempotent and safe to call on the pure
    path, where no module globals may be written.
    """
    apply_config_to_env(cfg)


def _ensure_config(config=None):
    """Return cached config.  If *config* is passed, cache and use it."""
    if config is not None:
        set_active_config(config)
    if _config is None:
        set_active_config(get_effective_config())
    return _config


def _build_chat_model(cfg):
    """Build a chat model from *cfg* without writing any module globals.

    Pure-construction counterpart to ``_ensure_chat_model``: used by ``/model``
    to verify a switch before committing, and threaded into
    ``create_cli_agent(chat_model=...)`` so the new agent binds the requested
    model without touching the cached ``_chat_model``.
    """
    from .llm import get_chat_model

    return get_chat_model(model=cfg.model, provider=cfg.provider)


def _replace_chat_model(instance, key: tuple[str | None, str | None]) -> None:
    """Install a new chat model and propagate the related invariants.

    Single write point for ``_chat_model`` / ``_chat_model_key`` /
    ``_jw_agent``: both ``_ensure_chat_model`` (cache-miss
    rebuild) and ``set_chat_model`` (explicit switch via ``/model``)
    funnel through here so the three globals can never drift.
    """
    global _chat_model, _chat_model_key, _jw_agent
    _chat_model = instance
    _chat_model_key = key
    # The lazy default agent captured a reference to the previous
    # ``_chat_model`` at build time, so it must be rebuilt on next access.
    _jw_agent = None


def _ensure_chat_model():
    """Return cached chat model, rebuilding if cfg.model/provider changed.

    The cache key is the current config's ``(model, provider)``. If it
    differs from the key that built ``_chat_model``, rebuild — this makes
    ``create_cli_agent(config=temp_cfg)`` bind the freshly requested model
    into the new agent without requiring callers to interleave
    ``set_chat_model()`` calls in any particular order.
    """
    cfg = _ensure_config()
    key = (cfg.model, cfg.provider)
    if _chat_model is None or _chat_model_key != key:
        _replace_chat_model(_build_chat_model(cfg), key)
    return _chat_model


def _ensure_auxiliary_chat_model():
    """Return the auxiliary chat model for background/helper LLM calls.

    Resolves ``(cfg.auxiliary_model or cfg.model, cfg.auxiliary_provider or
    cfg.provider)``. When the auxiliary fields are empty — or resolve to the same
    ``(model, provider)`` pair as the main model — returns the main
    ``_ensure_chat_model()`` instance directly, so no second client is built.
    Otherwise it is cached separately under its own key. Onboard sets the
    provider alongside the model, so the ``or cfg.provider`` fallback only
    matters for a model set without an explicit auxiliary provider.
    """
    global _auxiliary_chat_model, _auxiliary_chat_model_key
    from .llm import get_chat_model

    cfg = _ensure_config()
    aux_model = cfg.auxiliary_model or cfg.model
    aux_provider = cfg.auxiliary_provider or cfg.provider
    if (aux_model, aux_provider) == (cfg.model, cfg.provider):
        return _ensure_chat_model()
    key = (aux_model, aux_provider)
    if _auxiliary_chat_model is None or _auxiliary_chat_model_key != key:
        _auxiliary_chat_model = get_chat_model(model=aux_model, provider=aux_provider)
        _auxiliary_chat_model_key = key
    return _auxiliary_chat_model


def set_chat_model(model: str, provider: str | None = None):
    """Replace the cached chat model with a new one.

    Called by ``/model`` to switch the LLM mid-session.  No-op when the
    cache already holds the requested ``(model, provider)`` — avoids
    spawning a second ``get_chat_model`` instance (and its HTTP client)
    under the ``/model`` flow where ``_ensure_chat_model`` has already
    rebuilt ``_chat_model`` during the preceding ``_load_agent`` call.
    Returns the current chat model instance.
    """
    from .llm import get_chat_model

    # Invalidate the auxiliary cache too: when auxiliary_* is empty it mirrors
    # the main model, so a /model switch must let it re-resolve to the new main.
    global _auxiliary_chat_model, _auxiliary_chat_model_key
    _auxiliary_chat_model = None
    _auxiliary_chat_model_key = None

    key = (model, provider)
    if _chat_model is None or _chat_model_key != key:
        _replace_chat_model(get_chat_model(model=model, provider=provider), key)
    return _chat_model


def set_chat_model_instance(instance, key: tuple[str | None, str | None]) -> None:
    """Commit an already-built chat model *instance* as the active model.

    Companion to ``set_active_config`` for the pure path: installs a model that
    ``_build_chat_model`` already constructed (e.g. during a ``/model`` verify)
    without rebuilding it, keeping ``_chat_model`` / ``_chat_model_key`` /
    ``_jw_agent`` in sync via ``_replace_chat_model``.  Unlike
    ``set_chat_model``, the caller owns the ``(model, provider)`` *key*.
    """
    _replace_chat_model(instance, key)


# =============================================================================
# MCP caching
# =============================================================================


def _load_mcp_config_once() -> tuple[str, dict]:
    """Load MCP config and return ``(signature, config)``."""
    from .mcp.client import load_mcp_config

    cfg = load_mcp_config()
    if not cfg:
        return "", {}
    try:
        sig = json.dumps(cfg, sort_keys=True, ensure_ascii=True)
    except TypeError:
        sig = repr(cfg)
    return sig, cfg


def _load_mcp_tools_cached(on_progress=None) -> dict[str, list]:
    """Load MCP tools with config-aware caching.

    Args:
        on_progress: Optional per-server progress callback forwarded to
            :func:`JW.mcp.load_mcp_tools`.  Only invoked on a
            cache miss — cached replays don't re-emit progress events.
    """
    global _MCP_TOOLS_CACHE_KEY, _MCP_TOOLS_CACHE_VALUE

    from .mcp import load_mcp_tools

    cfg_key, cfg = _load_mcp_config_once()
    if not cfg_key:
        _MCP_TOOLS_CACHE_KEY = ""
        _MCP_TOOLS_CACHE_VALUE = {}
        return {}

    if _MCP_TOOLS_CACHE_KEY == cfg_key and _MCP_TOOLS_CACHE_VALUE is not None:
        return {k: list(v) for k, v in _MCP_TOOLS_CACHE_VALUE.items()}

    loaded = load_mcp_tools(config=cfg, on_progress=on_progress)
    _MCP_TOOLS_CACHE_KEY = cfg_key
    _MCP_TOOLS_CACHE_VALUE = {k: list(v) for k, v in loaded.items()}
    return {k: list(v) for k, v in loaded.items()}


# =============================================================================
# Agent construction helpers
# =============================================================================


def _configured_system_prompt(cfg) -> str:
    # In dangerous mode the agent works on the real filesystem; give it the real
    # cwd so it can use absolute paths instead of the virtual `/` workspace root.
    real_cwd = str(_paths_mod.resolve_virtual_path("/")) if cfg.dangerous_mode else None
    return get_system_prompt(
        dangerous=cfg.dangerous_mode,
        cwd=real_cwd,
    )


def _inject_subagent_middleware(
    subs: list[dict],
    *,
    workspace_dir: str | Path | None = None,
    cfg=None,
    chat_model=None,
) -> None:
    """Ensure every subagent gets error handling and context management middleware.

    Without this, subagent tool errors are caught by LangGraph's default
    ToolNode handler which produces terse messages without tracebacks or
    retry guidance — reducing the subagent's ability to self-recover.

    *chat_model*, when provided, is forwarded to the subagents'
    ``create_context_editing_middleware`` so the pure ``create_cli_agent``
    path doesn't fall back to the global-writing ``_ensure_chat_model()``.
    """
    from .middleware import (
        AutomaticExperimentTerminalGuardMiddleware,
        ContextOverflowMapperMiddleware,
        ContractToolAllowlistMiddleware,
        QwenToolCompatibilityMiddleware,
        TaskCancellationMiddleware,
        ToolErrorHandlerMiddleware,
        create_context_editing_middleware,
        create_memory_lifecycle_middleware,
        create_memory_middleware,
        create_runtime_context_middleware,
        default_memory_scheduler,
    )

    cfg = cfg if cfg is not None else _ensure_config()
    memory_controls = MemoryControls.from_config(cfg)
    memory_dir = str(_paths_mod.MEMORIES_DIR)
    memory_scheduler = default_memory_scheduler()
    for sa in subs:
        name = str(sa.get("name") or "sub-agent")
        model_call_limit = sa.pop("_model_call_limit", None)
        source_type = MemorySourceType.SUBAGENT
        memory_middleware = create_memory_middleware(
            memory_dir,
            workspace_dir=workspace_dir,
            source_type=source_type,
            source_agent=name,
            enable_profile_memory=memory_controls.profile_enabled,
            enable_observation_memory=memory_controls.observations_enabled,
            enable_observation_tool=memory_controls.observation_tool_enabled(
                MemoryObservationTarget.AGENT
            ),
            memory_scheduler=memory_scheduler,
        )
        middleware = [
            TaskCancellationMiddleware(),
            *(
                [AutomaticExperimentTerminalGuardMiddleware()]
                if name == "solar-experiment"
                else []
            ),
            *_call_limit_middleware(
                cfg,
                subagent=True,
                model_limit_override=model_call_limit,
                model_limit_floor=(
                    HYPOTHESIS_SUBAGENT_MODEL_CALL_LIMIT_FLOOR
                    if name == "solar-hypothesis"
                    else None
                ),
            ),
            # Subagents share the main agent's model: use the threaded
            # ``chat_model`` on the pure path, else defer to the factory's
            # ``_ensure_chat_model()`` fallback (when ``chat_model=None``).
            create_context_editing_middleware(chat_model),
            create_runtime_context_middleware(workspace_dir=workspace_dir),
            ToolErrorHandlerMiddleware(),
            ContextOverflowMapperMiddleware(),
        ]
        if memory_controls.memory_enabled:
            middleware.append(memory_middleware)
        if memory_controls.worker_needed(MemoryObservationTarget.SUBAGENT_WORKER):
            middleware.append(
                create_memory_lifecycle_middleware(
                    memory_dir,
                    workspace_dir=workspace_dir,
                    project_id=memory_middleware.project_id,
                    source_type=MemorySourceType.SUBAGENT,
                    source_agent=name,
                    memory_scheduler=memory_scheduler,
                )
            )
        restrict_tools = bool(sa.pop("_restrict_tools", False))
        if restrict_tools:
            allowed_tools = frozenset(
                tool_name
                for tool in sa.get("tools", [])
                if isinstance((tool_name := getattr(tool, "name", None)), str)
            )
            # Keep this last: DeepAgents prepends filesystem middleware and
            # other middleware may inject tools.  The closed-contract filter
            # must see and remove all of them before the model call.
            middleware.append(ContractToolAllowlistMiddleware(allowed_tools))
        # Keep this last so Qwen validates the exact specialist tool set after
        # capability filtering and can disable thinking for forced tool calls.
        middleware.append(QwenToolCompatibilityMiddleware(default_model=cfg.model))
        sa.setdefault("middleware", []).extend(middleware)


def _ensure_general_purpose_subagent(subs: list[dict]) -> None:
    """Materialize DeepAgents' default subagent so our middleware wraps it."""
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    name = GENERAL_PURPOSE_SUBAGENT["name"]
    if any(sa.get("name") == name for sa in subs):
        return

    subs.insert(
        0,
        {
            **GENERAL_PURPOSE_SUBAGENT,
            "skills": list(DEFAULT_SKILL_SOURCES),
        },
    )


def _maybe_swap_async_subagents(
    subs: list, middleware: list | None = None, *, cfg=None
) -> list:
    """Replace ``_async``-flagged sub-agents with ``AsyncSubAgent`` specs when enabled.

    Reads the ``_async`` field carried through by ``utils.load_subagents._build_one``
    (sourced from each yaml's ``async: true`` flag). When
    ``config.enable_async_subagents`` is also set, those sub-agents are
    swapped from synchronous in-process dicts to ``AsyncSubAgent`` references
    pointing at the langgraph dev graph of the same name.

    The deployed graphs live in ``JW.langgraph_dev.graphs`` and
    are registered in ``jw/langgraph_dev/langgraph.json``.

    Adding a new async sub-agent requires no change here — flip
    ``async: true`` in its yaml and create the matching deployment graph.

    All return paths strip the internal ``_async`` field from sub-agent dicts
    before handoff, since deepagents may schema-validate the kwarg.

    When async subagents are actually swapped in and ``middleware`` is provided,
    appends ``AsyncWatcherMiddleware`` so launches spawn an
    ``async_notifier`` watcher.
    """
    cfg = cfg if cfg is not None else _ensure_config()
    if not getattr(cfg, "enable_async_subagents", False):
        # Async fully disabled — strip the internal flag before handoff.
        for s in subs:
            s.pop("_async", None)
        return subs

    # Guard: if the langgraph dev subprocess never came up (port conflict,
    # binary missing, etc.), routing sub-agents to a dead URL produces hangs
    # and confusing tool errors. Fall back to in-process sync delegation.
    from .langgraph_dev.manager import is_async_subagents_available

    if not is_async_subagents_available():
        logging.getLogger(__name__).warning(
            "enable_async_subagents=true but langgraph dev is not reachable; "
            "falling back to in-process sync delegation for all sub-agents."
        )
        # Strip the internal ``_async`` flag (carried from ``load_subagents``)
        # before sub-agents reach deepagents — it's never a deepagents key.
        for s in subs:
            s.pop("_async", None)
        return subs

    # The ``_async`` flag was set by ``utils.load_subagents._build_one`` from
    # each yaml's ``async:`` field. No need to re-parse the yaml files here.
    async_specs: dict[str, str] = {
        s["name"]: s.get("description", "") for s in subs if s.get("_async")
    }

    if not async_specs:
        for s in subs:
            s.pop("_async", None)
        return subs

    from deepagents import AsyncSubAgent

    port = int(getattr(cfg, "langgraph_dev_port", 6174))
    out = []
    agent_specs: dict[str, AsyncSubAgent] = {}
    # MCP tools routed to async sub-agents (via ``expose_to: <name>`` in
    # mcp.yaml) ARE delivered — the deployed factory
    # ``subagents/_factory.py:build_async_subagent_graph`` loads its own MCP
    # connection per server (cost: one extra MCP server subprocess per
    # exposed server, since stdio transports can't share across processes).
    for s in subs:
        name = s.get("name")
        if name in async_specs:
            spec = AsyncSubAgent(
                name=name,
                description=async_specs[name],
                graph_id=name,
                url=f"http://localhost:{port}",
            )
            agent_specs[name] = spec
            out.append(spec)
        else:
            # Strip the internal flag before handoff to deepagents.
            s.pop("_async", None)
            out.append(s)

    if agent_specs and middleware is not None:
        from .middleware.async_watcher import AsyncWatcherMiddleware

        middleware.append(AsyncWatcherMiddleware(agent_specs))

    # Forward the CLI's live (model, provider) into deepagents'
    # start/update_async_task tool calls so the deployed graph can
    # re-resolve its chat model per run via ConfigurableModelMiddleware.
    # Idempotent — safe to call on every CLI startup.
    if agent_specs:
        from .llm.patches import _patch_deepagents_model_passthrough

        _patch_deepagents_model_passthrough()

    return out


def _apply_agent_model_overrides(subs: list, *, cfg=None) -> list:
    """Pin a per-agent chat model on sync (in-process) sub-agent specs.

    Async sub-agents resolve their model remotely per run (see
    ``_maybe_swap_async_subagents`` + ``ConfigurableModelMiddleware``); sync
    ones compile a graph in-process whose model falls back to the main
    agent's when the spec carries no ``model``. When
    ``cfg.agent_model_overrides`` names a sync sub-agent, build its chat
    model via jw's own routing (``get_chat_model``) and set it on the spec so
    deepagents compiles that sub-agent against the pinned model.

    Mutates ``subs`` in place and returns it. Only fires on an override hit
    whose model differs from the global — an empty/unmatched override string
    leaves every spec untouched, so behaviour is identical to single-model.
    """
    cfg = cfg if cfg is not None else _ensure_config()
    global_model = getattr(cfg, "model", None)
    for s in subs:
        if not isinstance(s, dict) or "graph_id" in s or "model" in s:
            continue  # async / compiled / already pinned
        from .llm.patches import _resolve_agent_model

        model, provider = _resolve_agent_model(cfg, s.get("name"))
        if not model or model == global_model:
            continue
        from .llm import get_chat_model

        s["model"] = get_chat_model(model=model, provider=provider)
    return subs


def _validate_agent_harness(
    subs: list[dict],
    *,
    tool_bundles,
    main_tools,
    middleware,
) -> None:
    """Fail startup when the product manifest drifts from runtime ownership."""

    from .agent_harness import DEEP_AGENT_CORE_TOOLS, validate_capability_manifest

    runtime_tool_names = set(DEEP_AGENT_CORE_TOOLS)
    for tool in main_tools:
        name = getattr(tool, "name", None)
        if isinstance(name, str):
            runtime_tool_names.add(name)
    for item in middleware:
        for tool in getattr(item, "tools", ()):
            name = getattr(tool, "name", None)
            if isinstance(name, str):
                runtime_tool_names.add(name)

    missing = validate_capability_manifest(
        tool_bundle_names=tool_bundles,
        specialist_names=(
            str(spec.get("name"))
            for spec in subs
            if isinstance(spec, dict) and spec.get("name")
        ),
        runtime_tool_names=runtime_tool_names,
    )
    if missing:
        raise RuntimeError(
            "agent capability manifest has unresolved runtime owners: "
            + ", ".join(missing)
        )


def _build_base_kwargs(
    base_backend, base_middleware, *, cfg=None, chat_model=None, workspace_dir=None
):
    """Build agent kwargs *without* MCP (fast, no subprocess spawning)."""
    from .tools import (
        get_builtin_tool_registry,
        get_main_agent_tools,
        get_tool_bundles,
    )
    from .utils import load_subagents

    cfg = cfg if cfg is not None else _ensure_config()
    tool_registry = get_builtin_tool_registry()
    base_tools = get_main_agent_tools()

    subs = load_subagents(
        _resolve_subagent_dirs(),
        tool_registry=tool_registry,
        tool_bundles=get_tool_bundles(),
    )
    _ensure_general_purpose_subagent(subs)
    _validate_agent_harness(
        subs,
        tool_bundles=get_tool_bundles(),
        main_tools=base_tools,
        middleware=base_middleware,
    )
    _inject_subagent_middleware(
        subs, workspace_dir=workspace_dir, cfg=cfg, chat_model=chat_model
    )
    subs = _maybe_swap_async_subagents(subs, base_middleware, cfg=cfg)
    subs = _apply_agent_model_overrides(subs, cfg=cfg)
    return {
        "name": "JW",
        "model": chat_model if chat_model is not None else _ensure_chat_model(),
        "tools": list(base_tools),
        "backend": base_backend,
        "subagents": subs,
        "middleware": base_middleware,
        "system_prompt": _configured_system_prompt(cfg),
        "skills": _main_skill_sources(),
    }


def load_mcp_and_build_kwargs(
    base_backend,
    base_middleware,
    *,
    on_mcp_progress=None,
    cfg=None,
    chat_model=None,
    workspace_dir=None,
):
    """Load MCP tools (cached by config) and build agent kwargs.

    Re-connects to MCP servers only when the effective MCP config changes.
    Falls back to base kwargs if no MCP configured.

    Args:
        on_mcp_progress: Optional per-server progress callback.  Forwarded
            to the MCP loader so UIs can render live status.
        cfg: Explicit config to thread through instead of reading the cached
            ``_config``.  Used by the pure ``create_cli_agent`` path.
        chat_model: Explicit chat model to bind instead of
            ``_ensure_chat_model()`` (which would write module globals).
    """
    from .tools import (
        get_builtin_tool_registry,
        get_main_agent_tools,
        get_tool_bundles,
    )
    from .utils import load_subagents

    cfg = cfg if cfg is not None else _ensure_config()
    mcp_by_agent = _load_mcp_tools_cached(on_progress=on_mcp_progress)
    if not mcp_by_agent:
        return _build_base_kwargs(
            base_backend,
            base_middleware,
            cfg=cfg,
            chat_model=chat_model,
            workspace_dir=workspace_dir,
        )

    tool_registry = get_builtin_tool_registry()
    base_tools = get_main_agent_tools()

    # Fresh tool registry — start from base tools + MCP tools
    registry = dict(tool_registry)
    for tools in mcp_by_agent.values():
        for t in tools:
            registry[t.name] = t

    mcp_main = mcp_by_agent.pop("main", [])

    subs = load_subagents(
        _resolve_subagent_dirs(),
        tool_registry=registry,
        tool_bundles=get_tool_bundles(),
    )

    _ensure_general_purpose_subagent(subs)
    _validate_agent_harness(
        subs,
        tool_bundles=get_tool_bundles(),
        main_tools=[*base_tools, *mcp_main],
        middleware=base_middleware,
    )
    _inject_subagent_middleware(
        subs, workspace_dir=workspace_dir, cfg=cfg, chat_model=chat_model
    )

    # Inject MCP tools into subagents by name
    for sa in subs:
        if sa_tools := mcp_by_agent.get(sa["name"], []):
            sa.setdefault("tools", []).extend(sa_tools)

    # Swap selected sub-agents to AsyncSubAgent (must happen AFTER MCP injection
    # since async sub-agents are remote graphs that load their own tools).
    subs = _maybe_swap_async_subagents(subs, base_middleware, cfg=cfg)
    subs = _apply_agent_model_overrides(subs, cfg=cfg)

    return {
        "name": "JW",
        "model": chat_model if chat_model is not None else _ensure_chat_model(),
        "tools": base_tools + mcp_main,
        "backend": base_backend,
        "subagents": subs,
        "middleware": base_middleware,
        "system_prompt": _configured_system_prompt(cfg),
        "skills": _main_skill_sources(),
    }


# =============================================================================
# Default agent (langgraph dev / notebooks)
# =============================================================================


def _get_default_backend():
    """Build the default composite backend from current paths."""
    workspace_dir = str(_paths_mod.WORKSPACE_ROOT)
    set_active_workspace(workspace_dir)
    return _build_default_composite_backend(workspace_dir)


def _build_default_composite_backend(
    workspace_dir: str,
    *,
    project_shared_dir: str | None = None,
    root_precreated: bool = False,
    skills_backend=None,
    memory_backend=None,
):
    """Build one concrete backend for a resolved task workspace.

    ``project_shared_dir`` is mounted explicitly at ``/project/``.  Previous
    task runs are deliberately not mounted, so continuity is opt-in through
    stable project assets rather than accidental visibility of old scratch
    files.  ``skills_backend`` and ``memory_backend`` may be shared across
    task-scoped composites: they are stateless, point at deployment-wide
    roots, and constructing them in a synchronous DeepAgents backend callback
    can otherwise perform symlink resolution on the ASGI event loop.
    """
    from deepagents.backends import CompositeBackend

    from .backends import (
        _BUILTIN_SKILL_ROOTS,
        CustomSandboxBackend,
        MemoryFilesystemBackend,
        MergedSkillsBackend,
        ReadOnlyFilesystemBackend,
    )

    cfg = _ensure_config()
    memory_dir = str(_paths_mod.MEMORIES_DIR)
    user_skills_dir = str(_paths_mod.USER_SKILLS_DIR)
    global_skills_dir = str(_paths_mod.GLOBAL_SKILLS_DIR)

    # Dangerous mode opens the workspace (`/`) route to the real filesystem;
    # the /skills/ and /memories/ routes stay confined (virtual_mode=True).
    ws_backend = CustomSandboxBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        timeout=cfg.sandbox_execute_timeout,
        dangerous=cfg.dangerous_mode,
        ensure_root=not root_precreated,
        read_only_mounts=(
            {"/project": project_shared_dir} if project_shared_dir else None
        ),
    )
    sk_backend = (
        skills_backend
        if skills_backend is not None
        else MergedSkillsBackend(
            primary_dir=user_skills_dir,
            global_dir=global_skills_dir,
            secondary_dir=SKILLS_DIR,
            secondary_roots=tuple(str(path) for path in _BUILTIN_SKILL_ROOTS),
        )
    )
    mem_backend = (
        memory_backend
        if memory_backend is not None
        else MemoryFilesystemBackend(
            root_dir=memory_dir,
            virtual_mode=True,
        )
    )
    routes = {
        "/skills/": sk_backend,
        "/memories/": mem_backend,
    }
    if project_shared_dir:
        routes["/project/"] = ReadOnlyFilesystemBackend(
            root_dir=project_shared_dir,
            virtual_mode=True,
        )
    return CompositeBackend(
        default=ws_backend,
        routes=routes,
    )


def _get_scoped_backend_factory():
    """Return a ToolRuntime backend factory scoped by project/run/thread.

    Existing checkpoint threads are bound to the legacy base workspace once at
    deployment migration time.  Every subsequently-created thread receives an
    isolated run directory.  The returned concrete backends are cached by
    resolved workspace, so resolving the factory for every file tool remains
    cheap and thread-safe.
    """
    import logging
    import threading

    from .workspaces import (
        bootstrap_legacy_bindings,
        cached_bindings_for_resolved_base,
        get_cached_binding_for_resolved_base,
        preload_bindings,
        scope_thread_id,
    )

    base_workspace = str(_paths_mod.WORKSPACE_ROOT.resolve())
    try:
        bootstrap_legacy_bindings(
            base_workspace,
            _paths_mod.DATA_DIR / "sessions.db",
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Legacy workspace binding bootstrap failed; continuing with isolated "
            "bindings for new threads.",
            exc_info=True,
        )
    preload_bindings(base_workspace)

    # DeepAgents resolves backend factories from its async ``before_agent``
    # node, but the factory protocol itself is synchronous.  Construct the
    # deployment-wide routes now, while the graph is loading outside that
    # event loop.  FilesystemBackend.__init__ calls Path.resolve(); a symlinked
    # workspace or skill root would otherwise trigger Blockbuster's os.readlink
    # guard before Qwen ever receives the user turn.
    from .backends import (
        _BUILTIN_SKILL_ROOTS,
        MemoryFilesystemBackend,
        MergedSkillsBackend,
    )

    shared_skills_backend = MergedSkillsBackend(
        primary_dir=str(_paths_mod.USER_SKILLS_DIR),
        global_dir=str(_paths_mod.GLOBAL_SKILLS_DIR),
        secondary_dir=SKILLS_DIR,
        secondary_roots=tuple(str(path) for path in _BUILTIN_SKILL_ROOTS),
    )
    shared_memory_backend = MemoryFilesystemBackend(
        root_dir=str(_paths_mod.MEMORIES_DIR),
        virtual_mode=True,
    )

    cache: dict[tuple[str, str | None], object] = {}
    lock = threading.RLock()

    def _binding_key(binding) -> tuple[str, str | None]:
        if binding.legacy:
            return (binding.workspace, None)
        return (binding.workspace, binding.project_shared)

    # A resumed checkpoint can enter directly at ``tools`` and therefore skip
    # TaskWorkspaceMiddleware.before_agent.  Prewarm every persisted binding at
    # graph construction so SkillsMiddleware never has to canonicalize paths on
    # the ASGI event loop after a service restart or page refresh.
    for persisted_binding in cached_bindings_for_resolved_base(base_workspace):
        key = _binding_key(persisted_binding)
        if key in cache:
            continue
        try:
            cache[key] = _build_default_composite_backend(
                key[0],
                project_shared_dir=key[1],
                root_precreated=True,
                skills_backend=shared_skills_backend,
                memory_backend=shared_memory_backend,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to prewarm persisted task backend for thread %s.",
                persisted_binding.thread_id,
                exc_info=True,
            )

    def _factory(runtime):
        config = getattr(runtime, "config", None)
        thread_id = scope_thread_id(config if isinstance(config, dict) else None)
        if not thread_id:
            # LangGraph's ToolRuntime view may not carry configurable values in
            # before_agent nodes even though the context config already does.
            # Use the same canonical fallback as TaskWorkspaceMiddleware so
            # prewarming and later SkillsMiddleware resolution select one key.
            from langgraph.config import get_config

            try:
                current = get_config()
            except RuntimeError:
                current = None
            if isinstance(current, dict):
                context_thread_id = scope_thread_id(current)
                if context_thread_id:
                    config = current
                    thread_id = context_thread_id
        binding = (
            get_cached_binding_for_resolved_base(thread_id, base_workspace)
            if thread_id
            else None
        )
        if thread_id and binding is None:
            raise RuntimeError(
                "Task workspace binding was not initialized before backend "
                f"resolution (thread_id={thread_id})."
            )
        key = (base_workspace, None) if binding is None else _binding_key(binding)
        with lock:
            backend = cache.get(key)
        if backend is None:
            try:
                import asyncio

                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "Task backend was not prepared before async SkillsMiddleware "
                    f"resolution (thread_id={thread_id or '<none>'})."
                )
            backend = _build_default_composite_backend(
                key[0],
                project_shared_dir=key[1],
                root_precreated=True,
                skills_backend=shared_skills_backend,
                memory_backend=shared_memory_backend,
            )
            with lock:
                backend = cache.setdefault(key, backend)
        return backend

    return _factory


def _get_default_middleware(
    *,
    for_async_subagent: bool = False,
    workspace_dir: str | Path | None = None,
    cfg=None,
    chat_model=None,
    memory_source_agent: str = "JW",
    allowed_tools: frozenset[str] | None = None,
    backend_factory=None,
    skills_backend=None,
    skill_sources: list[str] | None = None,
    model_call_limit_override: int | None = None,
):
    """Build the default middleware list.

    Args:
        for_async_subagent: When True, omit middleware that would deadlock a
            deployed async sub-agent. Specifically: ``AskUserMiddleware`` uses
            ``interrupt()`` to pause the graph waiting for a user reply, but
            async sub-agents run in the ``langgraph dev`` subprocess where
            the parent only holds a ``task_id`` and has no UI path to surface
            (or resume) an interrupt — the sub-agent would hang forever the
            first time it called ``ask_user``. This mirrors the same reason
            ``subagents/_factory.py`` deliberately skips ``interrupt_on=`` on
            the deepagents level. Defaults to False (full middleware list)
            for the CLI's in-process agent.
        cfg: Explicit config to use instead of the cached ``_config``.
        chat_model: Explicit model to bind instead of ``_ensure_chat_model()``
            (avoids writing module globals on the pure path).
        memory_source_agent: Attribution name for profile/observation writes.
            Async sub-agent factories pass their deployed agent name here.
        allowed_tools: Optional exact tool boundary for a deployed specialist.
            The async factory derives this from the same resolved YAML
            capability bundles used by the synchronous sub-agent.
    """
    from .middleware import (
        AutomaticExperimentTerminalGuardMiddleware,
        ClosedLoopOrchestrationGuardMiddleware,
        ConfigurableModelMiddleware,
        ContextOverflowMapperMiddleware,
        ContractToolAllowlistMiddleware,
        ModelFallbackMiddleware,
        QwenToolCompatibilityMiddleware,
        ResearchReviewOrchestrationMiddleware,
        ResearchRouterMiddleware,
        TaskCancellationMiddleware,
        TaskWorkspaceMiddleware,
        ToolErrorHandlerMiddleware,
        VirtualPathCodeGuardMiddleware,
        create_code_interpreter_middleware,
        create_context_editing_middleware,
        create_memory_lifecycle_middleware,
        create_memory_middleware,
        create_runtime_context_middleware,
        create_scheduler_middleware,
        create_tool_selector_middleware,
        default_memory_scheduler,
        load_fallback_chain,
        SkillReceiptMiddleware,
    )

    cfg = cfg if cfg is not None else _ensure_config()
    if cfg.model_fallbacks:
        load_fallback_chain(cfg.model_fallbacks)
    model = chat_model if chat_model is not None else _ensure_chat_model()
    memory_dir = str(_paths_mod.MEMORIES_DIR)
    source_type = (
        MemorySourceType.SUBAGENT if for_async_subagent else MemorySourceType.TURN
    )
    memory_controls = MemoryControls.from_config(cfg)
    memory_scheduler = default_memory_scheduler()
    worker_target = (
        MemoryObservationTarget.SUBAGENT_WORKER
        if for_async_subagent
        else MemoryObservationTarget.TURN_WORKER
    )
    # ``ConfigurableModelMiddleware`` is placed first so it wraps
    # ``ModelFallbackMiddleware``: a configurable.model override sets the
    # PRIMARY model only, leaving the fallback chain free to try its own
    # alternatives instead of re-overriding every retry to the same model.
    memory_middleware = create_memory_middleware(
        memory_dir,
        workspace_dir=workspace_dir,
        source_type=source_type,
        source_agent=memory_source_agent,
        enable_profile_memory=memory_controls.profile_enabled,
        enable_observation_memory=memory_controls.observations_enabled,
        enable_observation_tool=memory_controls.observation_tool_enabled(
            MemoryObservationTarget.AGENT
        ),
        memory_scheduler=memory_scheduler,
    )
    # Main-agent tool selection may use the auxiliary model; async sub-agents
    # keep the main model (they do real work, not a one-off helper call).
    # context_editing stays on the main model — its model only sizes the
    # context-window trigger for the main agent's own history.
    if for_async_subagent:
        tool_selector_model = model
    elif chat_model is None:
        tool_selector_model = _ensure_auxiliary_chat_model()
    else:
        aux_model = cfg.auxiliary_model or cfg.model
        aux_provider = cfg.auxiliary_provider or cfg.provider
        if (aux_model, aux_provider) == (cfg.model, cfg.provider):
            tool_selector_model = model
        else:
            from .llm import get_chat_model

            tool_selector_model = get_chat_model(model=aux_model, provider=aux_provider)
    mw = [
        TaskWorkspaceMiddleware(workspace_dir, backend_factory=backend_factory),
        *_call_limit_middleware(
            cfg,
            subagent=for_async_subagent,
            model_limit_override=model_call_limit_override,
        ),
    ]
    if skills_backend is not None and skill_sources:
        # DeepAgents normally prepends SkillsMiddleware ahead of every custom
        # middleware.  For a task-scoped synchronous backend factory that is
        # too early: the workspace/backend has not yet been prepared off the
        # ASGI event loop.  Suppress the built-in instance at graph creation
        # and place the equivalent middleware immediately after preparation.
        from deepagents.middleware.skills import SkillsMiddleware
        from .subagents.skill_registry import skill_receipt_for_sources

        # Keep a model-visible and machine-readable receipt next to the
        # on-demand Skills middleware. This proves which role-scoped sources
        # were resolved, instead of relying on the model to self-report them.
        mw.append(
            SkillReceiptMiddleware(
                skill_receipt_for_sources(memory_source_agent, skill_sources)
            )
        )
        mw.append(SkillsMiddleware(backend=skills_backend, sources=skill_sources))
    mw.extend(
        [
            *([TaskCancellationMiddleware()] if for_async_subagent else []),
            *(
                [AutomaticExperimentTerminalGuardMiddleware()]
                if for_async_subagent and memory_source_agent == "solar-experiment"
                else []
            ),
            VirtualPathCodeGuardMiddleware(),
            *(
                [ResearchRouterMiddleware(model=model)]
                if not for_async_subagent
                else []
            ),
            *(
                [ResearchReviewOrchestrationMiddleware()]
                if not for_async_subagent
                else []
            ),
            *(
                [ClosedLoopOrchestrationGuardMiddleware()]
                if not for_async_subagent
                else []
            ),
            ConfigurableModelMiddleware(),
            create_context_editing_middleware(model),
            ModelFallbackMiddleware(),
            ContextOverflowMapperMiddleware(),
            ToolErrorHandlerMiddleware(),
            *create_tool_selector_middleware(
                model=tool_selector_model,
                track_stream_selection=not for_async_subagent,
            ),
            # Interpreter prompt must land before runtime/memory context, so this
            # middleware sits ahead of runtime_context in the stack.
            create_code_interpreter_middleware(
                timeout=cfg.code_interpreter_timeout,
                max_result_chars=cfg.code_interpreter_max_result_chars,
            ),
        ]
    )
    if cfg.enable_scheduler and not for_async_subagent:
        mw.append(create_scheduler_middleware())
    mw.append(create_runtime_context_middleware(workspace_dir=workspace_dir))
    if memory_controls.memory_enabled:
        mw.append(memory_middleware)
    if memory_controls.worker_needed(worker_target):
        mw.append(
            create_memory_lifecycle_middleware(
                memory_dir,
                workspace_dir=workspace_dir,
                project_id=memory_middleware.project_id,
                source_type=source_type,
                source_agent=memory_source_agent,
                memory_scheduler=memory_scheduler,
            )
        )

    if cfg.enable_ask_user and not cfg.auto_mode and not for_async_subagent:
        from .middleware.ask_user import AskUserMiddleware

        mw.insert(0, AskUserMiddleware())

    # Background-process tools (run_in_background / check_process / stop_process /
    # list_processes) — main agent only. Async sub-agents run on langgraph-dev and
    # must not spawn local OS processes.
    if not for_async_subagent:
        from .middleware.background import BackgroundExecutionMiddleware

        mw.append(BackgroundExecutionMiddleware())

    if allowed_tools is not None:
        # Async specialists are root graphs, so apply the same hard boundary
        # that inline specialist specs receive in _inject_subagent_middleware.
        mw.append(ContractToolAllowlistMiddleware(allowed_tools))

    # Keep this last: Qwen must validate the exact tool set that survives all
    # registries, middleware injection, selection, and specialist allowlists.
    mw.append(QwenToolCompatibilityMiddleware(default_model=cfg.model))

    return mw


def _get_default_agent():
    """Build the default agent (no checkpointer) on first access.

    MCP loading depends on which subprocess mode (if any) this agent is
    being built in. ``langgraph_dev.manager.start_langgraph_dev`` injects
    ``JW_DEPLOY_MODE`` into the subprocess with one of two values:

    - ``JW_DEPLOY_MODE=full`` — set by ``jw deploy``. The
      subprocess is the *primary* programmatic entry point (Python scripts,
      Jupyter, integration tests via ``langgraph_sdk``), so it needs the full
      configuration: **load MCP**, and ``_ASYNC_SUBAGENTS_AVAILABLE`` flips on
      at module load so async sub-agents self-loop through this same
      langgraph dev server.

    - ``JW_DEPLOY_MODE=stripped`` — set by ``jw`` / ``jw
      serve``. The CLI's in-process main agent already loaded MCP; this
      subprocess only services async sub-agent self-loops, so **skip MCP**
      to avoid running a second copy of the same servers.

    Plain ``from jw import jw_agent`` (env var unset)
    loads MCP. Async sub-agents stay disabled in that case because there is
    no langgraph dev server to self-loop into.
    """
    global _jw_agent
    if _jw_agent is None:
        from deepagents import create_deep_agent

        cfg = _ensure_config()
        be = _get_scoped_backend_factory()
        mw = _get_default_middleware(
            backend_factory=be,
            skills_backend=be,
            skill_sources=_main_skill_sources(),
        )

        # HITL on main agent only (mirrors create_cli_agent). Use middleware,
        # not interrupt_on= kwarg — the kwarg propagates to every subagent and
        # breaks parallel execute calls (multi-pending-interrupt LangGraph
        # error). See PR #202.
        if not cfg.auto_approve:
            mw.append(
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "execute": True,
                        "run_in_background": True,
                        "schedule_task": True,
                    }
                )
            )

        if os.environ.get("JW_DEPLOY_MODE", "").lower() == "stripped":
            kwargs = _build_base_kwargs(
                be,
                mw,
                workspace_dir=str(_paths_mod.WORKSPACE_ROOT),
            )
        else:
            kwargs = load_mcp_and_build_kwargs(
                be,
                mw,
                workspace_dir=str(_paths_mod.WORKSPACE_ROOT),
            )

        # SkillsMiddleware is deliberately placed after TaskWorkspaceMiddleware
        # above.  Passing this through would make DeepAgents prepend a duplicate
        # instance before task backend preparation.
        kwargs["skills"] = None
        _jw_agent = create_deep_agent(
            **kwargs,
        ).with_config({"recursion_limit": cfg.recursion_limit})
    return _jw_agent


def __getattr__(name: str):
    if name == "jw_agent":
        return _get_default_agent()
    # Backward compat for module-level names
    if name == "chat_model":
        return _ensure_chat_model()
    if name == "SYSTEM_PROMPT":
        return _configured_system_prompt(_ensure_config())
    if name == "backend":
        return _get_default_backend()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# CLI agent factory
# =============================================================================


def create_cli_agent(
    workspace_dir: str | None = None,
    checkpointer=None,
    config=None,
    chat_model=None,
    *,
    on_mcp_progress=None,
) -> "CompiledStateGraph":
    """Create agent with checkpointer for CLI multi-turn support.

    A fresh backend is constructed on every call using the current
    ``paths.WORKSPACE_ROOT`` (or the explicit *workspace_dir*), so
    runtime ``set_workspace_root()`` changes are always respected.

    **Pure path:** when *both* ``config`` and ``chat_model`` are explicit, this
    writes none of the cached config/model module globals (``_config``,
    ``_chat_model``, ``_chat_model_key``, ``_jw_agent``) — the agent
    is built purely from the passed-in locals.  The caller commits the switch
    on success via ``set_active_config`` / ``set_chat_model_instance`` (see
    ``/model``).  Otherwise the existing module-global path runs (langgraph
    dev, notebooks, and CLI startup, which pass ``config=`` only).

    Args:
        workspace_dir: Per-session workspace directory. If ``None``,
            defaults to the current ``paths.WORKSPACE_ROOT``.
        checkpointer: Optional LangGraph checkpointer. If ``None``,
            falls back to ``InMemorySaver`` (non-persistent).
        config: Optional pre-loaded ``JWConfig``.  If ``None``,
            loads from file/env/defaults.  Passing this avoids double
            loading when the CLI has already loaded config.
        chat_model: Optional pre-built chat model.  Only triggers the pure
            path when ``config`` is also explicit; otherwise it is ignored in
            favor of the ``_ensure_chat_model()`` fallback.
    """
    import os as _os

    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend

    from . import paths as _paths
    from .backends import (
        _BUILTIN_SKILL_ROOTS,
        CustomSandboxBackend,
        MemoryFilesystemBackend,
        MergedSkillsBackend,
    )

    # Pure path only when BOTH config and chat_model are explicit: build from
    # locals and write no module globals. Otherwise keep the legacy
    # global-writing behavior — callers that pass config= only (CLI startup,
    # langgraph dev) rely on it to seat the active config/model.
    if config is not None and chat_model is not None:
        cfg = config
        _apply_env_from_config(cfg)
    else:
        cfg = _ensure_config(config)
        chat_model = None

    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()

    # When no explicit workspace_dir is provided, apply config.default_workdir
    # as a fallback.  This covers direct callers (notebooks, iMessage server)
    # that never call set_workspace_root() themselves.  CLI callers always
    # pass workspace_dir explicitly, so their --workdir is never overwritten.
    if workspace_dir is None:
        if cfg.default_workdir:
            set_workspace_root(
                _os.path.abspath(_os.path.expanduser(cfg.default_workdir))
            )
        workspace_dir = str(_paths.WORKSPACE_ROOT)

    # Read paths dynamically so runtime set_workspace_root() changes are picked up
    _mem_dir = str(_paths.MEMORIES_DIR)
    _usr_skills_dir = str(_paths.USER_SKILLS_DIR)
    _global_skills_dir = str(_paths.GLOBAL_SKILLS_DIR)

    # Always construct fresh backends from current paths (avoids stale
    # module-level backend when workspace root changed at runtime).
    set_active_workspace(workspace_dir)
    ws_backend = CustomSandboxBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        timeout=cfg.sandbox_execute_timeout,
        dangerous=cfg.dangerous_mode,
    )
    sk_backend = MergedSkillsBackend(
        primary_dir=_usr_skills_dir,
        global_dir=_global_skills_dir,
        secondary_dir=SKILLS_DIR,
        secondary_roots=tuple(str(path) for path in _BUILTIN_SKILL_ROOTS),
    )
    mem_backend = MemoryFilesystemBackend(
        root_dir=_mem_dir,
        virtual_mode=True,
    )
    be = CompositeBackend(
        default=ws_backend,
        routes={
            "/skills/": sk_backend,
            "/memories/": mem_backend,
        },
    )

    # Delegate middleware construction to the single source of truth so the
    # CLI agent never drifts from the default chain. Anything CLI-specific
    # (e.g. ``HumanInTheLoopMiddleware``) is appended below.
    mw: list[AgentMiddleware] = _get_default_middleware(
        workspace_dir=workspace_dir, cfg=cfg, chat_model=chat_model
    )

    # HITL on main agent only — passing `interrupt_on=` to create_deep_agent
    # would propagate it to every subagent, breaking parallel execute calls
    # (multi-pending-interrupt LangGraph error).
    if not cfg.auto_approve:
        mw.append(
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "execute": True,
                    "run_in_background": True,
                    "schedule_task": True,
                    # Knowledge-base decision gates (mirrors the deploy path):
                    # promotion to canonical, plan freeze, hypothesis freeze.
                    "kb_promote": True,
                    "research_planner_freeze_plan": True,
                    "scientific_hypothesis_freeze": True,
                }
            )
        )

    # Re-load MCP tools from current config (picks up /mcp add changes)
    kwargs = load_mcp_and_build_kwargs(
        be,
        mw,
        on_mcp_progress=on_mcp_progress,
        cfg=cfg,
        chat_model=chat_model,
        workspace_dir=workspace_dir,
    )

    return create_deep_agent(
        **kwargs,
        checkpointer=checkpointer,
    ).with_config({"recursion_limit": cfg.recursion_limit})

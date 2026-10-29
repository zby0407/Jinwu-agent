"""Custom HTTP routes mounted alongside the langgraph dev server.

The langgraph-api host supports a top-level ``http`` key in
``langgraph.json`` that names an ASGI app to mount on the same
process as the graph. We use it to surface the registry the WebUI's
``/model`` picker needs.

Why this lives here and not as a separate sidecar: the WebUI talks to
``jw deploy``'s langgraph endpoint anyway, so one origin keeps the
WebUI's fetch logic simple — no CORS dance, no extra port to configure.

Why Starlette and not FastAPI: ``langgraph_api`` already depends on
Starlette; adding FastAPI would pull in pydantic v1-vs-v2 reconciliation
the deploy doesn't need. The one route here has no input model, just a
JSON body, so the lower-level surface is sufficient.

Lightweight by design — module-level imports stick to ``config``,
``llm.models`` (registry only; no chat-model construction), and
Starlette itself. Nothing on this surface should pull the agent into
memory.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware

from jw.config import get_effective_config
from jw.langgraph_dev.data_sources_api import DATA_SOURCE_ROUTES
from jw.langgraph_dev.knowledge_api import KB_ROUTES
from jw.langgraph_dev.reproduction_api import REPRODUCTION_ROUTES
from jw.langgraph_dev.runtime import runtime_status
from jw.langgraph_dev.maintenance import (
    DesktopMaintenanceMiddleware,
    desktop_maintenance,
)


async def list_model_picker_entries(*args, **kwargs):
    from jw.llm.models import list_model_picker_entries as entries

    return await entries(*args, **kwargs)


async def get_runtime_status(_request: Request) -> JSONResponse:
    return JSONResponse(runtime_status(), headers={"Cache-Control": "no-store"})


@asynccontextmanager
async def lifespan(_app):
    async def warm_main():
        from .main_graph import jw_agent

        try:
            await asyncio.to_thread(jw_agent)
        except Exception:
            # Failure is reported by runtime-status; configuration remains usable.
            pass

    task = asyncio.create_task(warm_main())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def get_models(_request: Request) -> JSONResponse:
    """Return the model registry as ``{entries, default}``.

    ``entries`` preserves the registry order so the WebUI picker can
    rank providers per short name the same way the backend would.
    Mirrors the TUI ``/model`` picker by appending locally-pulled
    Ollama models when ``ollama_base_url`` is configured — same
    ``discover_ollama_models()`` call, same 1.5-s timeout, same
    fail-soft semantics (the probe returns ``[]`` on any error, never
    raises). The TUI's "Custom Ollama model…" sentinel is intentionally
    omitted: that's a widget-specific input affordance, not part of
    the registry surface.

    ``default`` reflects the deployment's currently-configured fallback
    (``config.yaml``'s ``model`` / ``provider`` — what ``/model reset``
    would land on). Returned even when the configured pair isn't in
    the registry, so the picker can still label it.

    Uses ``get_effective_config()`` (not ``load_config()``) so env-var
    overrides like ``OLLAMA_BASE_URL`` from ``_ENV_MAPPINGS`` are
    honored — matching the deploy's actual model-building behavior.
    Offloaded to a thread because ``get_effective_config()`` calls
    ``find_dotenv(usecwd=True)`` which invokes ``os.getcwd()`` — a
    blocking syscall that langgraph-dev's ``blockbuster`` middleware
    refuses to allow on the async event loop (would surface as a 500).
    """
    cfg = await asyncio.to_thread(get_effective_config)
    entries = [
        {"name": name, "model_id": model_id, "provider": provider}
        for name, model_id, provider in await list_model_picker_entries(
            getattr(cfg, "ollama_base_url", None),
            include_custom_ollama=False,
        )
    ]
    return JSONResponse(
        {
            "entries": entries,
            "default": {"name": cfg.model, "provider": cfg.provider},
        }
    )


app = Starlette(
    lifespan=lifespan,
    middleware=[Middleware(DesktopMaintenanceMiddleware)],
    routes=[
        Route("/api/runtime-status", get_runtime_status, methods=["GET"]),
        Route("/api/desktop-maintenance", desktop_maintenance, methods=["POST"]),
        Route("/api/models", get_models, methods=["GET"]),
        *REPRODUCTION_ROUTES,
        # Read-only knowledge-base surface (see knowledge_api.py).
        *KB_ROUTES,
        # Observational data-source status and toggles (see data_sources_api.py).
        *DATA_SOURCE_ROUTES,
    ],
)

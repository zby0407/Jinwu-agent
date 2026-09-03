"""Local-only HTTP endpoint for launching the fixed H1/H2 suite."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jw import paths
from jw.config import get_effective_config
from jw.reproduction.service import launch_solar_h1_h2
from jw.reproduction.suite import SUITE_ID

INTENT_HEADER = "X-JW-Reproduction-Intent"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _trusted_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in _LOCAL_HOSTS


async def launch_reproduction(request: Request) -> JSONResponse:
    if not _trusted_origin(request.headers.get("origin")):
        return JSONResponse(
            {"error": "reproduction requests must originate locally"}, status_code=403
        )
    if request.headers.get(INTENT_HEADER) != SUITE_ID:
        return JSONResponse(
            {"error": f"{INTENT_HEADER} must equal {SUITE_ID}"}, status_code=400
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "request body must be valid JSON"}, status_code=400
        )
    if (
        not isinstance(body, dict)
        or set(body) != {"trigger"}
        or body.get("trigger") not in {"webui", "cli"}
    ):
        return JSONResponse(
            {"error": 'body must be exactly {"trigger": "webui" | "cli"}'},
            status_code=400,
        )

    cfg = await asyncio.to_thread(get_effective_config)
    if cfg.dangerous_mode:
        return JSONResponse(
            {"error": "reproduction is disabled while dangerous_mode is enabled"},
            status_code=403,
        )
    if not str(cfg.dashscope_api_key or "").strip():
        return JSONResponse(
            {"error": "DASHSCOPE_API_KEY is required for solar-h1-h2-v1"},
            status_code=503,
        )

    workspace = Path(paths.WORKSPACE_ROOT).resolve()
    try:
        result = await launch_solar_h1_h2(
            trigger=str(body["trigger"]),
            base_workspace=workspace,
        )
    except Exception as exc:
        return JSONResponse(
            {"error": f"reproduction launch failed: {exc}"}, status_code=500
        )
    status_code = (
        201
        if result["status"] == "submitted"
        else 207
        if result["status"] == "partial"
        else 500
    )
    return JSONResponse(result, status_code=status_code)


REPRODUCTION_ROUTES = [
    Route("/api/reproductions/solar-h1-h2", launch_reproduction, methods=["POST"]),
]

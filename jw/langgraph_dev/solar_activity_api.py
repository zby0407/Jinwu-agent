"""Read-only REST surface for the first solar-activity table."""

from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jw.solar_activity_overview import build_solar_activity_overview

_NO_STORE = {"Cache-Control": "no-store"}


def _json(data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=_NO_STORE)


def _positive_int(raw: str | None, default: int, maximum: int) -> int:
    try:
        value = int(raw or default)
    except ValueError:
        return default
    return min(max(value, 1), maximum)


async def solar_activity_overview(request: Request) -> JSONResponse:
    params = request.query_params
    months = _positive_int(params.get("months"), 24, 240)
    cycles = _positive_int(params.get("cycles"), 6, 25)
    refresh = params.get("refresh") in {"1", "true", "yes"}
    try:
        result = await asyncio.to_thread(
            build_solar_activity_overview,
            months=months,
            cycle_count=cycles,
            refresh=refresh,
        )
    except RuntimeError as exc:
        return _json(
            {"status": "error", "error": str(exc), "code": "upstream_unavailable"},
            status_code=502,
        )
    return _json(result)


SOLAR_ACTIVITY_ROUTES = [
    Route(
        "/api/solar-activity/overview",
        solar_activity_overview,
        methods=["GET"],
    ),
]

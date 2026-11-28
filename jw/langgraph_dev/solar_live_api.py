"""Live solar dashboard routes backed by ``jw.solar_live``."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from jw.solar_live import (
    HISTORY_CHANNELS,
    LIVE_IMAGES,
    build_live_overview,
    history_image,
    history_listing,
    live_image,
)


async def solar_live_overview(_request: Request) -> JSONResponse:
    result = await asyncio.to_thread(build_live_overview)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


async def solar_live_image(request: Request) -> Response:
    key = request.path_params["key"]
    if key not in LIVE_IMAGES:
        return JSONResponse({"error": f"unknown live image: {key}"}, status_code=404)
    try:
        image = await asyncio.to_thread(live_image, key)
    except (OSError, ValueError) as exc:
        return JSONResponse(
            {"error": str(exc), "code": "upstream_error"}, status_code=502
        )
    return Response(
        image["bytes"],
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def solar_live_history(request: Request) -> JSONResponse:
    date = request.path_params["date"]
    try:
        result = await asyncio.to_thread(history_listing, date)
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "code": "invalid_date"}, status_code=404)
    return JSONResponse(result, headers={"Cache-Control": "private, max-age=300"})


async def solar_live_history_image(request: Request) -> Response:
    channel = request.path_params["channel"]
    stamp = request.path_params["stamp"]
    if channel not in HISTORY_CHANNELS:
        return JSONResponse({"error": f"unknown history channel: {channel}"}, status_code=404)
    try:
        image = await asyncio.to_thread(history_image, channel, stamp)
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "code": "invalid_stamp"}, status_code=404)
    except OSError as exc:
        return JSONResponse(
            {"error": str(exc), "code": "upstream_error"}, status_code=502
        )
    return Response(
        image["bytes"],
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


SOLAR_LIVE_ROUTES = [
    Route("/api/solar-live/overview", solar_live_overview, methods=["GET"]),
    Route("/api/solar-live/image/{key}", solar_live_image, methods=["GET"]),
    Route("/api/solar-live/history/{date}", solar_live_history, methods=["GET"]),
    Route("/api/solar-live/history/image/{channel}/{stamp}", solar_live_history_image, methods=["GET"]),
]

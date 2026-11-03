"""REST surface for scientific-data parsing interfaces.

The WebUI can show which PDF/chart/discovery interfaces are configured and let
the operator save endpoints and keys on this machine.  Stored secrets are
never returned: GET only sends a mask plus the effective source.

Writes are loopback-only by default because this surface sits on the same
unauthenticated origin as the agent.  ``JW_SCIENTIFIC_DATA_ALLOW_REMOTE=1``
opts a deployment into remote writes; values are still never echoed.
"""

from __future__ import annotations

import asyncio
import json
import os

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jw.scientific_data.interfaces import interface_status, update_interfaces

_NO_STORE = {"Cache-Control": "no-store"}


def _json(data: object, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=_NO_STORE)


def _is_loopback(request: Request) -> bool:
    client = request.client
    host = client.host if client is not None else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"} or host.startswith(
        "127."
    )


def _writes_allowed(request: Request) -> bool:
    if os.environ.get("JW_SCIENTIFIC_DATA_ALLOW_REMOTE", "").strip() in {
        "1",
        "true",
        "yes",
    }:
        return True
    return _is_loopback(request)


async def list_scientific_data_interfaces(_request: Request) -> JSONResponse:
    status = await asyncio.to_thread(interface_status)
    return _json(status)


async def save_scientific_data_interfaces(request: Request) -> JSONResponse:
    if not _writes_allowed(request):
        return _json(
            {
                "status": "error",
                "error": "接口设置只能从本机写入。请在运行后端的机器上打开设置。",
            },
            status_code=403,
        )
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _json({"status": "error", "error": "请求体必须是 JSON"}, status_code=400)
    if not isinstance(body, dict):
        return _json(
            {"status": "error", "error": "请求体必须是 JSON 对象"}, status_code=400
        )
    updates = body.get("settings")
    if not isinstance(updates, dict):
        return _json(
            {"status": "error", "error": "缺少 settings 对象"}, status_code=400
        )
    result = await asyncio.to_thread(update_interfaces, updates)
    return _json(result, status_code=200 if result.get("status") == "ok" else 400)


SCIENTIFIC_DATA_INTERFACE_ROUTES = [
    Route(
        "/api/scientific-data/interfaces",
        list_scientific_data_interfaces,
        methods=["GET"],
    ),
    Route(
        "/api/scientific-data/interfaces",
        save_scientific_data_interfaces,
        methods=["PUT"],
    ),
]

"""REST surface for literature-source credentials.

The WebUI can show *which* sources are configured and let the operator save a
key on this machine. It never receives a stored secret back: GET returns only a
mask and the effective source (environment/config).

Writes are loopback-only by default because the custom Starlette surface sits
on the same unauthenticated origin as the agent. A deployment that really wants
remote writes can opt in with ``JW_CREDENTIALS_ALLOW_REMOTE=1``; even then the
endpoint never echoes values.
"""

from __future__ import annotations

import asyncio
import json
import os

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jw.literature_credentials import credential_status, update_credentials

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
    if os.environ.get("JW_CREDENTIALS_ALLOW_REMOTE", "").strip() in {
        "1",
        "true",
        "yes",
    }:
        return True
    return _is_loopback(request)


async def list_literature_credentials(_request: Request) -> JSONResponse:
    items = await asyncio.to_thread(credential_status)
    return _json({"status": "ok", "items": items})


async def save_literature_credentials(request: Request) -> JSONResponse:
    if not _writes_allowed(request):
        return _json(
            {
                "status": "error",
                "error": "凭据只能从本机写入。请在运行后端的机器上打开设置。",
            },
            status_code=403,
        )
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _json(
            {"status": "error", "error": "请求体必须是 JSON"}, status_code=400
        )
    if not isinstance(body, dict):
        return _json(
            {"status": "error", "error": "请求体必须是 JSON 对象"}, status_code=400
        )
    updates = body.get("credentials")
    if not isinstance(updates, dict):
        return _json(
            {"status": "error", "error": "缺少 credentials 对象"}, status_code=400
        )
    result = await asyncio.to_thread(update_credentials, updates)
    return _json(result, status_code=200 if result.get("status") == "ok" else 400)


LITERATURE_CREDENTIAL_ROUTES = [
    Route(
        "/api/literature/credentials",
        list_literature_credentials,
        methods=["GET"],
    ),
    Route(
        "/api/literature/credentials",
        save_literature_credentials,
        methods=["PUT"],
    ),
]

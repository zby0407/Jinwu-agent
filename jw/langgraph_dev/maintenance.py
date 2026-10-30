"""Drain new run submissions before the desktop applies a configuration change."""

import os
import re
import secrets

from starlette.responses import JSONResponse

from . import runtime

_CREATES_RUN = re.compile(r"^/(?:threads/[^/]+/)?runs(?:/(?:stream|wait|batch))?/?$")


class DesktopMaintenanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        creates_run = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and _CREATES_RUN.fullmatch(scope["path"])
        )
        if creates_run and runtime.maintenance:
            await JSONResponse(
                {"detail": "连接设置正在应用，请稍后发送。"}, status_code=503
            )(scope, receive, send)
            return
        if creates_run:
            runtime.run_requests += 1
        try:
            await self.app(scope, receive, send)
        finally:
            if creates_run:
                runtime.run_requests -= 1


async def desktop_maintenance(request):
    token = os.getenv("JW_DESKTOP_CONTROL_TOKEN", "")
    if not token or not secrets.compare_digest(
        request.headers.get("x-jw-desktop-token", ""), token
    ):
        return JSONResponse({"ok": False}, status_code=403)
    payload = await request.json()
    runtime.maintenance = payload.get("enabled") is True
    return JSONResponse({"ok": True, "inFlight": runtime.run_requests})

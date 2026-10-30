"""Offline transport double: no test in this package touches the network."""

from __future__ import annotations

import json
from collections.abc import Mapping

from jw.research_influence.transport import HttpResponse


class FakeTransport:
    """A transport whose answers are scripted per URL fragment."""

    def __init__(self, routes: Mapping[str, object] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str]] = []

    def add(
        self,
        fragment: str,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ):
        """Register one canned answer, matched by substring."""
        self.routes[fragment] = (status, payload, content_type)
        return self

    def _respond(self, url: str) -> HttpResponse:
        for fragment, value in self.routes.items():
            if fragment not in url:
                continue
            if isinstance(value, HttpResponse):
                return HttpResponse(
                    status=value.status,
                    body=value.body,
                    content_type=value.content_type,
                    url=url,
                    error=value.error,
                    truncated=value.truncated,
                )
            status, payload, content_type = value  # type: ignore[misc]
            if isinstance(payload, bytes):
                body = payload
            elif isinstance(payload, str):
                body = payload.encode("utf-8")
            elif payload is None:
                body = b""
            else:
                body = json.dumps(payload).encode("utf-8")
            return HttpResponse(
                status=status, body=body, content_type=content_type, url=url
            )
        return HttpResponse(status=404, url=url, error="http_404")

    def get(self, url, *, headers=None, timeout=20.0, max_bytes=0):
        """Record the call and return the scripted answer."""
        self.calls.append(("GET", url))
        return self._respond(url)

    def post_json(self, url, *, payload, headers=None, timeout=20.0, max_bytes=0):
        """Record the call and return the scripted answer."""
        self.calls.append(("POST", url))
        return self._respond(url)

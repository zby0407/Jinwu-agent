"""HTTP transport used by every provider, with an injectable seam for tests.

Providers never touch ``urllib`` directly: they receive a :class:`Transport`.
Tests pass a fixtures transport, production passes :class:`UrllibTransport`,
and no test ever needs the network to prove a provider parses a payload.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

USER_AGENT = "jw-research-influence/1.0"


def _read_capped(response: object, max_bytes: int) -> tuple[bytes, bool]:
    """Read a response body, reporting whether the cap cut it short.

    ``read(n)`` returns at most ``n`` bytes and says nothing about what was
    left behind, so reading exactly the cap makes a truncated body look like a
    complete one: the JSON parser then fails and the caller blames the provider
    for an "invalid payload".  Reading one byte past the cap turns the cut into
    a fact we can name.
    """
    if max_bytes <= 0:
        return response.read(), False
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        return body[:max_bytes], True
    return body, False


@dataclass(slots=True)
class HttpResponse:
    """One transport answer: status, bytes and content type."""

    status: int
    body: bytes = b""
    content_type: str = ""
    url: str = ""
    error: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """Whether the response carries a success status."""
        return 200 <= self.status < 300 and not self.error

    def json(self) -> Mapping[str, object] | None:
        """Decode the body as a JSON mapping, or ``None`` when unparsable."""
        try:
            payload = json.loads(self.body.decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None


class Transport(Protocol):
    """Minimal HTTP surface a provider needs."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """Fetch ``url`` and return the response without raising."""
        ...

    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """POST a JSON body and return the response without raising."""
        ...


@dataclass(slots=True)
class UrllibTransport:
    """Default transport: stdlib, no retries, no credential handling."""

    user_agent: str = USER_AGENT

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """Fetch ``url``, converting every failure into a response object."""
        merged = {"User-Agent": self.user_agent}
        merged.update(dict(headers or {}))
        request = urllib.request.Request(url, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body, truncated = _read_capped(response, max_bytes)
                return HttpResponse(
                    status=int(getattr(response, "status", 200)),
                    body=body,
                    content_type=str(response.headers.get("content-type", "")),
                    url=url,
                    truncated=truncated,
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=int(exc.code), url=url, error=f"http_{exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HttpResponse(status=0, url=url, error=f"{type(exc).__name__}: {exc}")

    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """POST ``payload`` as JSON, converting every failure into a response."""
        merged = {"User-Agent": self.user_agent, "Content-Type": "application/json"}
        merged.update(dict(headers or {}))
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=merged, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data, truncated = _read_capped(response, max_bytes)
                return HttpResponse(
                    status=int(getattr(response, "status", 200)),
                    body=data,
                    content_type=str(response.headers.get("content-type", "")),
                    url=url,
                    truncated=truncated,
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=int(exc.code), url=url, error=f"http_{exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HttpResponse(status=0, url=url, error=f"{type(exc).__name__}: {exc}")


@dataclass(slots=True)
class PoliteTransport:
    """Wraps a transport with a per-host minimum interval.

    Publishers and arXiv ask programmatic clients to slow down; this keeps the
    pipeline inside those expectations instead of hammering a host.
    """

    inner: Transport
    min_interval: float = 1.0
    overrides: Mapping[str, float] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """Sleep when the host was contacted too recently, then delegate."""
        host = _host_of(url)
        interval = float(self.overrides.get(host, self.min_interval))
        last = self._last.get(host)
        if last is not None and interval > 0:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        response = self.inner.get(
            url, headers=headers, timeout=timeout, max_bytes=max_bytes
        )
        self._last[host] = time.monotonic()
        return response

    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_bytes: int = 0,
    ) -> HttpResponse:
        """Apply the same per-host politeness to a JSON POST."""
        host = _host_of(url)
        interval = float(self.overrides.get(host, self.min_interval))
        last = self._last.get(host)
        if last is not None and interval > 0:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        response = self.inner.post_json(
            url,
            payload=payload,
            headers=headers,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        self._last[host] = time.monotonic()
        return response


def _host_of(url: str) -> str:
    """Host portion of ``url`` without scheme or path."""
    text = str(url or "")
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.split("/", 1)[0].casefold()


__all__ = [
    "USER_AGENT",
    "HttpResponse",
    "PoliteTransport",
    "Transport",
    "UrllibTransport",
]

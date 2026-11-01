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


#: Failures that are worth retrying: the request was well formed, the server
#: was busy or transiently broken.  A 4xx other than 429 is a caller problem.
#: Status 0 covers connection resets and TLS EOFs, which OpenAlex and some
#: publishers produce under load even for a well-formed read.
_RETRYABLE_STATUSES = frozenset({0, 429, 500, 502, 503, 504})


def _retry_after_seconds(headers: object) -> float:
    """Parse a numeric Retry-After header, or return zero when absent."""
    try:
        value = headers.get("Retry-After")  # type: ignore[union-attr]
    except AttributeError:
        return 0.0
    if value is None:
        return 0.0
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        return 0.0


def _sleep_before_retry(response: HttpResponse, attempt: int, base: float) -> None:
    """Back off before the next attempt, honouring Retry-After when present."""
    delay = response.retry_after or base * (2**attempt)
    if delay > 0:
        time.sleep(delay)


@dataclass(slots=True)
class HttpResponse:
    """One transport answer: status, bytes and content type."""

    status: int
    body: bytes = b""
    content_type: str = ""
    url: str = ""
    error: str = ""
    truncated: bool = False
    retry_after: float = 0.0

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
    """Default transport: stdlib, bounded retries, no credential handling."""

    user_agent: str = USER_AGENT
    max_retries: int = 2
    backoff_seconds: float = 1.0

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
        response = HttpResponse(status=0, url=url)
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, headers=merged)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as raw:
                    body, truncated = _read_capped(raw, max_bytes)
                    return HttpResponse(
                        status=int(getattr(raw, "status", 200)),
                        body=body,
                        content_type=str(raw.headers.get("content-type", "")),
                        url=url,
                        truncated=truncated,
                    )
            except urllib.error.HTTPError as exc:
                response = HttpResponse(
                    status=int(exc.code),
                    url=url,
                    error=f"http_{exc.code}",
                    retry_after=_retry_after_seconds(exc.headers),
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                response = HttpResponse(
                    status=0, url=url, error=f"{type(exc).__name__}: {exc}"
                )
            if (
                attempt >= self.max_retries
                or response.status not in _RETRYABLE_STATUSES
            ):
                return response
            _sleep_before_retry(response, attempt, self.backoff_seconds)
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
        """POST ``payload`` as JSON, converting every failure into a response."""
        merged = {"User-Agent": self.user_agent, "Content-Type": "application/json"}
        merged.update(dict(headers or {}))
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        response = HttpResponse(status=0, url=url)
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url, data=body, headers=merged, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as raw:
                    data, truncated = _read_capped(raw, max_bytes)
                    return HttpResponse(
                        status=int(getattr(raw, "status", 200)),
                        body=data,
                        content_type=str(raw.headers.get("content-type", "")),
                        url=url,
                        truncated=truncated,
                    )
            except urllib.error.HTTPError as exc:
                response = HttpResponse(
                    status=int(exc.code),
                    url=url,
                    error=f"http_{exc.code}",
                    retry_after=_retry_after_seconds(exc.headers),
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                response = HttpResponse(
                    status=0, url=url, error=f"{type(exc).__name__}: {exc}"
                )
            if (
                attempt >= self.max_retries
                or response.status not in _RETRYABLE_STATUSES
            ):
                return response
            _sleep_before_retry(response, attempt, self.backoff_seconds)
        return response


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

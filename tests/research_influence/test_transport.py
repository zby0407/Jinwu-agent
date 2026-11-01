"""The transport must say when the byte cap cut a body short.

A truncated body is still valid UTF-8 and still starts like JSON; only the
parser notices, and it reports a broken payload.  These tests pin the flag that
lets the caller blame the cap instead of the provider.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from jw.research_influence.contracts import ProviderStatus
from jw.research_influence.providers import openalex
from jw.research_influence.providers.base import ProviderPolicy, json_result
from jw.research_influence.transport import (
    HttpResponse,
    UrllibTransport,
    _read_capped,
)

from .conftest import FakeTransport


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers = {"content-type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body if size is None or size < 0 else self._body[:size]


class _CappedResponse:
    """A file-like response that honours ``read(n)`` and counts the calls."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._body[self.offset :]
        else:
            chunk = self._body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_reading_one_byte_past_the_cap_detects_the_cut():
    """Exactly-capped reads look complete; one extra byte tells the truth."""
    response = _CappedResponse(b"0123456789")
    body, truncated = _read_capped(response, 5)
    assert body == b"01234"
    assert truncated is True


def test_a_body_that_fits_is_not_marked_truncated():
    """A body shorter than the cap is complete, not suspicious."""
    response = _CappedResponse(b"{}")
    body, truncated = _read_capped(response, 5)
    assert body == b"{}"
    assert truncated is False


def test_an_unlimited_read_never_marks_truncation():
    """``max_bytes=0`` means no cap, so nothing can be cut."""
    response = _CappedResponse(b"0123456789")
    body, truncated = _read_capped(response, 0)
    assert body == b"0123456789"
    assert truncated is False


def test_a_truncated_payload_is_named_not_blamed_on_the_provider():
    """A cut body must not surface as ``invalid_payload``."""
    response = HttpResponse(status=200, body=b'{"meta": {"count": 20', truncated=True)
    result = json_result("openalex", "https://example.test", response)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.note.startswith("response_too_large:")


def test_openalex_reports_the_size_cap_through_the_transport_seam():
    """The provider path keeps the distinction: cap, not parser."""
    transport = FakeTransport(
        {"/works": HttpResponse(status=200, body=b'{"results": [', truncated=True)}
    )
    result = openalex.fetch_work("10.1/x", transport=transport, policy=ProviderPolicy())
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "response_too_large" in result.note


def test_transport_retries_a_429_then_returns_the_success(monkeypatch):
    """A transient 429 is retried instead of becoming a permanent empty result."""
    calls: list[str] = []
    responses = [
        urllib.error.HTTPError(
            "https://example.test", 429, "Too Many Requests", {}, None
        ),
        _FakeResponse(b'{"ok": true}'),
    ]

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        "jw.research_influence.transport.time.sleep", lambda _seconds: None
    )
    result = UrllibTransport(max_retries=1, backoff_seconds=0).get(
        "https://example.test"
    )
    assert result.status == 200
    assert result.json() == {"ok": True}
    assert len(calls) == 2


def test_transport_retries_a_connection_reset_then_returns_the_success(monkeypatch):
    """A TLS EOF on a read is transient, not evidence the source is unavailable."""
    calls: list[str] = []
    responses = [
        urllib.error.URLError(
            "SSL: UNEXPECTED_EOF_WHILE_READING EOF occurred in violation of protocol"
        ),
        _FakeResponse(b'{"ok": true}'),
    ]

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        "jw.research_influence.transport.time.sleep", lambda _seconds: None
    )
    result = UrllibTransport(max_retries=1, backoff_seconds=0).get(
        "https://example.test"
    )
    assert result.status == 200
    assert result.json() == {"ok": True}
    assert len(calls) == 2


def test_transport_keeps_the_last_429_after_retries_are_exhausted(monkeypatch):
    """A persistent 429 stays a rate-limit response, not a fake success."""
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            "https://example.test", 429, "Too Many Requests", {}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        "jw.research_influence.transport.time.sleep", lambda _seconds: None
    )
    result = UrllibTransport(max_retries=1, backoff_seconds=0).get(
        "https://example.test"
    )
    assert result.status == 429
    assert result.error == "http_429"
    assert len(calls) == 2

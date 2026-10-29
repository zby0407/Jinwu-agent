"""Task-scoped transport failures must not become literature evidence."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime
from email.message import Message
from unittest.mock import Mock

import pytest

from jw.tools import research_planner as _planner_tools  # noqa: F401
from research_planner import knowledge


@pytest.fixture(autouse=True)
def isolated_cooldowns(monkeypatch):
    knowledge._SEARCH_RATE_LIMITS.clear()
    clock = [100.0]
    monkeypatch.setattr(knowledge, "_cooldown_now", lambda: clock[0])
    yield clock
    knowledge._SEARCH_RATE_LIMITS.clear()


def _http_error(status=429, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        knowledge.OPENALEX_WORKS_URL,
        status,
        "synthetic transport failure",
        headers,
        None,
    )


def _empty_response(*_args, **_kwargs):
    return io.BytesIO(json.dumps({"results": []}).encode())


def test_same_task_different_query_is_short_circuited_without_faking_a_new_429(
    monkeypatch,
):
    network = Mock(side_effect=_http_error())
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)

    first = knowledge.search_scholarly_literature(
        "synthetic query a", rate_limit_scope="task-a"
    )
    second = knowledge.search_scholarly_literature(
        "synthetic query b", rate_limit_scope="task-a"
    )

    assert network.call_count == 1
    assert first["failure_kind"] == "rate_limited"
    assert first["http_status"] == 429
    assert first["network_request_performed"] is True
    assert first["full_text_retrieved"] is False
    assert second["failure_kind"] == "rate_limit_cooldown"
    assert second["http_status"] is None
    assert second["upstream_http_status"] == 429
    assert second["network_request_performed"] is False
    assert second["query"] == "synthetic query b"
    assert second["retry_after_seconds"] == 60
    assert second["results"] == []
    assert "another query" in second["safe_next_action"]


def test_scopes_and_legacy_callers_do_not_inherit_another_tasks_cooldown(monkeypatch):
    network = Mock(side_effect=_http_error())
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)
    knowledge.search_scholarly_literature("synthetic query", rate_limit_scope="task-a")
    knowledge.search_scholarly_literature("synthetic query", rate_limit_scope="task-b")
    knowledge.search_scholarly_literature("synthetic query")
    knowledge.search_scholarly_literature("synthetic query")
    assert network.call_count == 4


def test_cooldown_expiry_permits_an_explicit_request_and_drops_expired_state(
    monkeypatch, isolated_cooldowns
):
    network = Mock(side_effect=[_http_error(retry_after="2"), _empty_response()])
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)
    knowledge.search_scholarly_literature("synthetic first", rate_limit_scope="task-a")
    isolated_cooldowns[0] += 2

    result = knowledge.search_scholarly_literature(
        "synthetic second", rate_limit_scope="task-a"
    )

    assert network.call_count == 2
    assert result["status"] == "ok"
    assert result["results"] == []
    assert knowledge._SEARCH_RATE_LIMITS == {}


@pytest.mark.parametrize(
    ("header", "expected", "source"),
    [
        ("120", 120, "header"),
        ("0", 1, "header"),
        ("not a delay", 60, "default_cooldown"),
        (None, 60, "default_cooldown"),
    ],
)
def test_retry_after_controls_cooldown_without_automatic_network_retry(
    monkeypatch, header, expected, source
):
    network = Mock(side_effect=_http_error(retry_after=header))
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)
    result = knowledge.search_scholarly_literature(
        "synthetic query", rate_limit_scope="task-a"
    )
    assert network.call_count == 1
    assert result["retry_after_seconds"] == expected
    assert result["retry_after_source"] == source


def test_retry_after_http_date_and_bad_header_are_bounded_to_valid_delays():
    now = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    assert (
        knowledge._retry_after_seconds("Sat, 24 Oct 2026 15:02:00 GMT", now_utc=now)
        == 120
    )
    assert (
        knowledge._retry_after_seconds("Sat, 24 Oct 2026 14:59:00 GMT", now_utc=now)
        == 0
    )
    assert knowledge._retry_after_seconds("-10", now_utc=now) is None
    assert knowledge._retry_after_seconds("inf", now_utc=now) is None


def test_other_http_failures_do_not_install_a_rate_limit(monkeypatch):
    network = Mock(side_effect=_http_error(status=503))
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)
    for _ in range(2):
        result = knowledge.search_scholarly_literature(
            "synthetic query", rate_limit_scope="task-a"
        )
        assert result["http_status"] == 503
        assert result["failure_kind"] == "http_error"
        assert result["network_request_performed"] is True
    assert network.call_count == 2
    assert knowledge._SEARCH_RATE_LIMITS == {}


def test_success_preserves_discovery_metadata_and_never_adds_scientific_support(
    monkeypatch,
):
    row = {
        "id": "synthetic-id",
        "title": "Synthetic fixture",
        "publication_year": 2020,
        "authorships": [],
    }
    monkeypatch.setattr(
        knowledge.urllib.request,
        "urlopen",
        lambda *_a, **_k: io.BytesIO(json.dumps({"results": [row]}).encode()),
    )
    result = knowledge.search_scholarly_literature(
        "synthetic query", rate_limit_scope="task-a"
    )
    assert result["status"] == "ok"
    assert result["results"][0]["title"] == "Synthetic fixture"
    assert "not verified full-text support" in result["notice"]
    assert knowledge._SEARCH_RATE_LIMITS == {}


def test_invalid_scope_cannot_create_shared_cooldown_state(monkeypatch):
    network = Mock(side_effect=AssertionError("unexpected network request"))
    monkeypatch.setattr(knowledge.urllib.request, "urlopen", network)
    with pytest.raises(knowledge.ContractError, match="rate_limit_scope"):
        knowledge.search_scholarly_literature("synthetic query", rate_limit_scope="")
    assert network.call_count == 0

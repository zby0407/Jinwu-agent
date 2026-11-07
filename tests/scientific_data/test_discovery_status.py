import asyncio
import hashlib
import json
from unittest.mock import MagicMock

import pytest

from jw.scientific_data.workflow import contract_from_request
from jw.scientific_data.discovery import discover_sources
from jw.tools import scientific_data as tools


def test_unknown_provider_query_metadata_is_not_filled_with_an_empty_list(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tools, "_workspace", lambda c: tmp_path)
    monkeypatch.setattr(
        tools,
        "literature_searcher",
        lambda *a, **k: {"status": "unavailable", "results": [], "source": "openalex"},
    )
    reply = json.loads(
        tools.sci_data_discover.invoke(
            {
                "request_json": json.dumps(
                    {"research_goal": "published signal", "domain": "generic"}
                ),
                "allow_network": True,
            }
        )
    )
    provider = reply["searches"][0]["discovery"]["provider_status"]
    assert provider["source"] == "openalex"
    assert "providers_queried" not in provider


def test_local_deadline_capsule_does_not_invite_automatic_task_retry():
    from types import SimpleNamespace
    from jw.middleware.tool_error_handler import _build_error_message
    from jw.provider_diagnostics import LocalModelTimeoutError

    try:
        raise LocalModelTimeoutError("operator deadline reached")
    except LocalModelTimeoutError:
        message = _build_error_message(
            SimpleNamespace(tool_call={"name": "task", "id": "local-limit"})
        )
    assert message.status == "error"
    assert "do not automatically retry" in message.content


@pytest.mark.parametrize(
    "provider_status,expected",
    [("ok", "no_results"), ("unavailable", "unavailable"), ("partial", "partial")],
)
def test_provider_failure_is_not_a_successful_empty_search(provider_status, expected):
    contract = contract_from_request(
        {"research_goal": "published signal", "domain": "generic"}
    )
    report = discover_sources(
        contract,
        allow_network=True,
        searcher=lambda q, n: {
            "status": provider_status,
            "results": [],
            "provider_diagnostics": ["provider did not complete"],
        },
    )
    assert report.status == expected
    if provider_status != "ok":
        assert all(i.subject.get("status") != "no_results" for i in report.issues)


def test_failed_discovery_delivery_and_historical_recovery_are_truthful(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tools, "_workspace", lambda c: tmp_path)
    search = MagicMock(
        return_value={
            "status": "unavailable",
            "results": [],
            "source": "openalex",
            "providers_queried": ["openalex"],
            "provider_diagnostics": ["provider unavailable"],
        }
    )
    monkeypatch.setattr(tools, "literature_searcher", search)
    reply = json.loads(
        tools.sci_data_discover.invoke(
            {
                "request_json": json.dumps(
                    {"research_goal": "published signal", "domain": "generic"}
                ),
                "allow_network": True,
                "queries": ["one bounded query"],
            }
        )
    )
    assert reply["search_status"] == "unavailable"
    assert reply["searches"][0]["discovery"]["status"] == "unavailable"
    assert reply["provider_scope"]["repositories"] == []
    receipt = tmp_path / reply["receipt_path"]
    saved = json.loads(receipt.read_text(encoding="utf8"))
    saved["search_status"] = "no_usable_candidates"
    saved["searches"][0]["discovery"]["status"] = "no_results"
    receipt.write_text(json.dumps(saved), encoding="utf8")
    before = hashlib.sha256(receipt.read_bytes()).hexdigest()
    recovered = json.loads(
        tools.sci_data_runs.invoke({"kind": "discovery", "receipt_path": str(receipt)})
    )["discovery_receipts"][0]
    assert recovered["search_status"] == "unavailable"
    assert recovered["recorded_search_status"] == "no_usable_candidates"
    assert recovered["searches"][0]["discovery"]["recorded_status"] == "no_results"
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == before
    assert search.call_count == 1


@pytest.mark.asyncio
async def test_only_own_expired_deadline_is_a_local_nonretryable_timeout(monkeypatch):
    from jw.middleware import qwen_compat as qwen
    from jw.provider_diagnostics import LocalModelTimeoutError, diagnose_provider_error

    middleware = qwen.QwenToolCompatibilityMiddleware()
    monkeypatch.setattr(middleware, "_active_model_is_qwen", lambda r: True)
    monkeypatch.setattr(qwen, "_dashscope_request_timeout", lambda: 0.01)

    async def slow(r):
        await asyncio.sleep(60)

    with pytest.raises(LocalModelTimeoutError) as caught:
        await middleware._await_handler_with_qwen_wall_timeout(None, None, slow)
    assert diagnose_provider_error(caught.value)["code"] == "LOCAL_TIMEOUT"
    assert not qwen._is_retryable_qwen_transport_error(caught.value)

    async def underlying_timeout(r):
        raise TimeoutError("underlying transport timeout")

    with pytest.raises(TimeoutError) as underlying:
        await middleware._await_handler_with_qwen_wall_timeout(
            None, None, underlying_timeout
        )
    assert type(underlying.value) is TimeoutError
    assert diagnose_provider_error(underlying.value)["code"] == "TIMEOUT"

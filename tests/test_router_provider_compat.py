"""Provider routing requests exercised through offline HTTP and production patches."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
import pytest
from langchain_openai import ChatOpenAI

from jw.llm.patches import (
    _patch_deepseek_reasoning_passback,
    _patch_openai_compat_content,
)
from jw.middleware.research_router import ResearchRouterMiddleware


_MODEL = "deepseek-v4-pro"
_CLASSIFIED_ROUTE = {
    "mode": "fast_answer",
    "source_mode": "none",
    "needs_computation": False,
    "task_intent": "general",
    "required_specialist": "none",
    "reason": "offline classifier response",
}


@pytest.fixture(autouse=True)
def _disable_remote_tracing(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def _completion(content: str) -> dict:
    return {
        "id": "offline-router-completion",
        "object": "chat.completion",
        "created": 0,
        "model": _MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


@asynccontextmanager
async def _offline_router(*, base_url="https://api.deepseek.com", failure=None):
    requests = []
    original_extra = {
        "thinking": {"type": "enabled"},
        "offline_preserved_option": {"value": "keep-me"},
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if failure == "http400":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "private-response-detail-must-not-be-logged",
                        "type": "invalid_request_error",
                    }
                },
            )
        if failure == "malformed":
            content = "not valid JSON: private-response-detail-must-not-be-logged"
        elif failure == "invalid_enum":
            content = json.dumps({**_CLASSIFIED_ROUTE, "mode": "invented_route"})
        else:
            content = json.dumps(_CLASSIFIED_ROUTE)
        return httpx.Response(200, json=_completion(content))

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport, trust_env=False) as sync_client:
        async with httpx.AsyncClient(
            transport=transport, trust_env=False
        ) as async_client:
            model = ChatOpenAI(
                model=_MODEL,
                base_url=base_url,
                api_key="offline-placeholder-not-a-real-key",
                http_client=sync_client,
                http_async_client=async_client,
                max_retries=0,
                extra_body=deepcopy(original_extra),
            )
            # Match get_chat_model's production order. These instance patches
            # capture the original model, so a model_copy-only override can
            # appear correct locally while sending the old settings on the wire.
            _patch_openai_compat_content(model)
            _patch_deepseek_reasoning_passback(model)
            yield ResearchRouterMiddleware(model=model), model, requests, original_extra


async def _classify(router, invocation, text):
    if invocation == "sync":
        return router._route_sync(text)
    return await router._route_async(text)


@pytest.mark.parametrize("invocation", ["sync", "async"])
@pytest.mark.parametrize(
    "base_url", ["https://api.deepseek.com", "https://API.DeepSeek.com/v1"]
)
async def test_official_deepseek_classifier_uses_json_mode_on_the_wire(
    invocation, base_url
):
    async with _offline_router(base_url=base_url) as fixture:
        router, model, requests, original_extra = fixture
        text = "Explain the concept of solar rotation."
        route = await _classify(router, invocation, text)

        assert {key: route[key] for key in _CLASSIFIED_ROUTE} == _CLASSIFIED_ROUTE
        assert len(requests) == 1
        payload = requests[0]
        assert payload["response_format"] == {"type": "json_object"}
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["offline_preserved_option"] == {"value": "keep-me"}
        system = payload["messages"][0]["content"]
        assert "Return exactly one JSON object" in system
        schema = json.loads(system.split("this JSON schema:\n", 1)[1])
        assert schema["properties"]["mode"]["enum"] == [
            "fast_answer",
            "verified_analysis",
            "full_research",
        ]
        assert set(schema["required"]) == set(_CLASSIFIED_ROUTE)
        assert payload["messages"][1]["content"] == text
        assert model.extra_body == original_extra
        assert model._get_request_payload([])["extra_body"] == original_extra


@pytest.mark.parametrize("invocation", ["sync", "async"])
@pytest.mark.parametrize("failure", ["http400", "malformed", "invalid_enum"])
async def test_classifier_failure_preserves_local_scope_without_success_claim(
    invocation, failure, caplog
):
    caplog.set_level(logging.WARNING, logger="jw.middleware.research_router")
    async with _offline_router(failure=failure) as fixture:
        router, model, requests, original_extra = fixture
        route = await _classify(router, invocation, "Inspect the local CSV data.")

        assert len(requests) == 1
        assert route["mode"] == "verified_analysis"
        assert route["source_mode"] == "local"
        assert route["needs_computation"] is False
        assert route["reason"].startswith("router unavailable;")
        assert route["reason"] != _CLASSIFIED_ROUTE["reason"]
        assert model.extra_body == original_extra
        if failure != "invalid_enum":
            expected_type = (
                "BadRequestError" if failure == "http400" else "OutputParserException"
            )
            assert f"classifier failed ({expected_type})" in caplog.text
        assert "private-response-detail-must-not-be-logged" not in caplog.text
        assert "offline-placeholder-not-a-real-key" not in caplog.text


@pytest.mark.parametrize("invocation", ["sync", "async"])
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://api.deepseek.com.example.invalid/v1",
        "https://proxy.example.invalid/api.deepseek.com/v1",
    ],
)
async def test_other_endpoints_keep_default_structured_output(invocation, base_url):
    async with _offline_router(base_url=base_url) as fixture:
        router, model, requests, original_extra = fixture
        route = await _classify(
            router, invocation, "Explain the concept of solar rotation."
        )

        assert route["reason"] == _CLASSIFIED_ROUTE["reason"]
        assert len(requests) == 1
        payload = requests[0]
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["offline_preserved_option"] == {"value": "keep-me"}
        assert "Return exactly one JSON object" not in payload["messages"][0]["content"]
        assert model.extra_body == original_extra


@pytest.mark.parametrize("invocation", ["sync", "async"])
async def test_explicit_literature_scope_does_not_call_classifier(invocation):
    async with _offline_router(failure="http400") as fixture:
        router, _, requests, _ = fixture
        route = await _classify(
            router,
            invocation,
            "Literature review only. Do not perform data analysis or experiments.",
        )
        assert requests == []
        assert route["literature_only"] is True
        assert route["mode"] == "verified_analysis"
        assert route["needs_computation"] is False
        assert route["required_specialist"] == "none"

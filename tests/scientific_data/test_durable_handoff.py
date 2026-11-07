import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import (
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from jw.middleware.research_router import ResearchRouterMiddleware
from jw.scientific_data.handoff import persist_reply, read_reply


def test_saved_inspection_recovery_filters_and_pages_without_reopening_original(
    tmp_path, monkeypatch
):
    from jw.tools import scientific_data as tools

    monkeypatch.setattr(tools, "_workspace", lambda c: tmp_path)
    folder = tmp_path / "scientific_data/runs/previous/inspections"
    folder.mkdir(parents=True)
    for i, preview in enumerate(["footnote " + "abc" * 5000, "unrelated"]):
        (folder / f"inspection-{i}.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "source_id": "source",
                    "path": "no-original-present.pdf",
                    "sha256": "sha256:original",
                    "preview": preview,
                    "selection": {"pdf_start_page": 4},
                }
            ),
            encoding="utf8",
        )
    result = json.loads(
        tools.sci_data_runs.invoke(
            {
                "kind": "inspection",
                "inspection_query": "FOOTNOTE",
                "content_offset": 20,
                "content_limit": 512,
            }
        )
    )
    assert result["status"] == "ok" and result["pagination"]["total"] == 1
    item = result["inspection_receipts"][0]
    assert len(item["preview"]) == 512 and item["next_content_offset"] == 532
    assert item["evidence_kind"] == "saved_inspection_not_a_new_original_check"
    recovered = json.loads(
        tools.sci_data_runs.invoke(
            {
                "kind": "inspection",
                "receipt_path": item["receipt_path"],
                "content_offset": 532,
                "content_limit": 512,
            }
        )
    )
    assert recovered["inspection_receipts"][0]["content_offset"] == 532
    assert not (tmp_path / "no-original-present.pdf").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "Model call limits exceeded: run limit (24/24)",
        "Tool call limits exceeded: run limit (48/48)",
        "[JW_PROVIDER:LOCAL_LIMIT] request refused",
    ],
)
def test_framework_failure_never_becomes_a_saved_report(tmp_path, monkeypatch, raw):
    monkeypatch.setattr("jw.middleware.research_router.disable_thinking", lambda m: m)
    monkeypatch.setattr(
        "jw.middleware.research_router.workspace_root_from_config", lambda c: tmp_path
    )
    user = HumanMessage("保存报告到 outputs/report.md", id="limit-test")
    call = {
        "name": "task",
        "args": {"subagent_type": "scientific-data"},
        "id": "limited",
    }
    reply = persist_reply(tmp_path, "limited", ToolMessage(raw, tool_call_id="limited"))
    compact = json.loads(reply.content)
    assert compact["status"] == "error"
    path = tmp_path / compact["receipt_path"]
    # Also reject the historical erroneous success label, without editing it.
    legacy = json.loads(path.read_text(encoding="utf8"))
    legacy["status"] = "returned"
    path.write_text(json.dumps(legacy), encoding="utf8")
    before = path.read_bytes()
    assert read_reply(tmp_path, compact["receipt_path"])[0]["status"] == "error"
    assert path.read_bytes() == before

    def write_file(file_path: str, content: str):
        return file_path

    request = ModelRequest(
        model=MagicMock(),
        tools=[StructuredTool.from_function(write_file, description="Save")],
        messages=[user],
        state={
            "messages": [user, AIMessage("", tool_calls=[call]), reply],
            "research_route": {
                "task_intent": "data_integration",
                "required_specialist": "scientific-data",
            },
        },
    )
    handler = MagicMock(
        side_effect=AssertionError("do not infer or save failed reports")
    )
    response = ResearchRouterMiddleware(model=MagicMock()).wrap_model_call(
        request, handler
    )
    assert not response.result[0].tool_calls
    assert "未成功完成" in response.result[0].content
    handler.assert_not_called()


def test_reply_survives_projection_retry_and_report_is_saved_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("jw.middleware.research_router.disable_thinking", lambda m: m)
    monkeypatch.setattr(
        "jw.middleware.research_router.workspace_root_from_config", lambda c: tmp_path
    )
    middleware = ResearchRouterMiddleware(model=MagicMock())
    user = HumanMessage(
        "查找公开科学资料，保存报告到 outputs/source_report.md", id="user-1"
    )
    call = {
        "name": "task",
        "args": {"subagent_type": "scientific-data", "description": "collect"},
        "id": "dispatch-1",
        "type": "tool_call",
    }
    messages = [user, AIMessage("", tool_calls=[call])]
    request = ToolCallRequest(
        tool_call=call,
        tool=None,
        state={"messages": messages},
        runtime=SimpleNamespace(config={}),
    )
    raw = "# Native report\n\npartial: one source acquired; second observation unverified."
    reply = middleware.wrap_tool_call(
        request, lambda _: ToolMessage(raw, tool_call_id="dispatch-1")
    )
    compact = json.loads(reply.content)
    assert compact["kind"] == "scientific_data_handoff"
    assert read_reply(tmp_path, compact["receipt_path"])[1] == raw
    # A retry after an outer error must reuse the genuine reply, not rerun tools.
    retry_handler = MagicMock(side_effect=AssertionError("must not rerun specialist"))
    assert (
        json.loads(middleware.wrap_tool_call(request, retry_handler).content)["sha256"]
        == compact["sha256"]
    )
    retry_handler.assert_not_called()
    persist_reply(
        tmp_path,
        "dispatch-1",
        ToolMessage(
            "[TOOL ERROR] outer transport failed",
            tool_call_id="dispatch-1",
            status="error",
        ),
    )
    assert len(list((tmp_path / "scientific_data/handoffs").glob("*.json"))) == 2

    def task(description: str, subagent_type: str):
        return description

    def write_file(file_path: str, content: str):
        return file_path

    tools = [
        StructuredTool.from_function(task, description="Delegate"),
        StructuredTool.from_function(write_file, description="Save"),
    ]
    durable = [*messages, reply]
    model_request = ModelRequest(
        model=MagicMock(),
        tools=tools,
        messages=[user],
        state={
            "messages": durable,
            "research_route": {
                "task_intent": "data_integration",
                "required_specialist": "scientific-data",
            },
        },
    )
    handler = MagicMock(
        return_value=ModelResponse(
            result=[AIMessage(content="报告已保存，可继续完成剩余交付。")]
        )
    )
    response = middleware.wrap_model_call(model_request, handler)
    save_call = response.result[0].tool_calls[0]
    assert save_call["name"] == "write_file" and save_call["args"]["content"] == raw
    assert save_call["args"]["file_path"] == "/outputs/source_report.md"
    saved = [
        *durable,
        response.result[0],
        ToolMessage("File written", tool_call_id=save_call["id"]),
    ]
    final = middleware.wrap_model_call(
        model_request.override(state={**model_request.state, "messages": saved}),
        handler,
    )
    assert not final.result[0].tool_calls
    handler.assert_called_once()
    assert "剩余交付" in final.result[0].content
    # An unsolicited second task call is blocked at execution, not just hidden.
    duplicate = {**call, "id": "dispatch-2"}
    blocked = middleware.wrap_tool_call(
        ToolCallRequest(
            tool_call=duplicate,
            tool=None,
            state={"messages": saved},
            runtime=SimpleNamespace(config={}),
        ),
        retry_handler,
    )
    assert blocked.status == "error" and "ALREADY DISPATCHED" in blocked.content


def test_handoff_hash_and_workspace_boundaries(tmp_path):
    result = persist_reply(
        tmp_path, "id/unsafe", ToolMessage("native", tool_call_id="id/unsafe")
    )
    saved = json.loads(result.content)
    (tmp_path / saved["report_path"]).write_text("tampered")
    with pytest.raises(ValueError, match="hash changed"):
        read_reply(tmp_path, saved["receipt_path"])
    with pytest.raises(ValueError, match="escaped"):
        read_reply(tmp_path, "../outside.json")


def test_stopped_generation_cannot_resume_with_older_or_missing_token(
    tmp_path, monkeypatch
):
    from jw.task_cancellation import task_is_cancelled

    marker = tmp_path / "receipts/task_cancelled.json"
    marker.parent.mkdir()
    marker.write_text('{"cancelled_at_epoch_ms":1000}')
    monkeypatch.setattr(
        "jw.task_cancellation.workspace_root_from_config", lambda c: tmp_path
    )
    monkeypatch.setattr("jw.task_cancellation.time.time", lambda: 2)
    assert task_is_cancelled({})
    assert task_is_cancelled({"configurable": {"task_turn_started_at_ms": 900}})
    assert not task_is_cancelled({"configurable": {"task_turn_started_at_ms": 1500}})
    assert task_is_cancelled({"configurable": {"task_turn_started_at_ms": 90000}})
    # Marker stays immutable: resuming one turn does not revive old children.
    assert marker.exists()
    assert task_is_cancelled({"configurable": {"task_turn_started_at_ms": 900}})


def test_discovery_returns_compact_summary_and_one_explicit_literature_source(
    tmp_path, monkeypatch
):
    from jw.tools import scientific_data as tools

    monkeypatch.setattr(tools, "_workspace", lambda c: tmp_path)
    calls = []

    def search(query, limit, *, source):
        calls.append((query, source))
        return {
            "status": "ok",
            "results": [
                {
                    "url": f"https://data.example/{i}",
                    "title": "Evidence " + str(i),
                    "source_id": str(i),
                }
                for i in range(30)
            ],
        }

    monkeypatch.setattr(tools, "literature_searcher", search)
    result = tools.sci_data_discover.invoke(
        {
            "request_json": '{"research_goal":"collect generic observations"}',
            "queries": ["narrow query"],
            "allow_network": True,
        }
    )
    data = json.loads(result)
    assert calls == [("narrow query", "openalex")]
    assert len(result) < 15000 and data["candidate_total"] == 30
    assert data["candidates_truncated"]
    saved = json.loads(
        __import__("pathlib").Path(data["receipt_path"]).read_text(encoding="utf-8")
    )
    assert len(saved["candidates"]) == 30
    recovered = json.loads(
        tools.sci_data_runs.invoke(
            {
                "kind": "discovery",
                "receipt_path": data["receipt_path"],
                "item_offset": 25,
                "item_limit": 5,
            }
        )
    )
    assert len(recovered["discovery_receipts"][0]["candidates"]) == 5
    assert len(calls) == 1
    assert (
        json.loads(
            tools.sci_data_discover.invoke(
                {"request_json": "{}", "literature_source": "all"}
            )
        )["status"]
        == "error"
    )


def test_dataverse_fanout_preserves_deferred_candidates_without_listing_them():
    from jw.scientific_data.data_providers import dataverse_search, DataProviderSettings

    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if "/api/search?" in request.full_url:
            return json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "name": str(i),
                                "global_id": f"doi:10/test{i}",
                                "url": f"https://data.example/{i}",
                            }
                            for i in range(5)
                        ]
                    }
                }
            ).encode()
        return b'{"data":{"latestVersion":{"files":[]}}}'

    result = dataverse_search(
        "generic observations", 5, opener=opener, settings=DataProviderSettings()
    )
    assert len(calls) == 3  # one search, two dataset listings
    assert len(result.hits) == 5
    assert result.status == "partial"
    assert sum("file_listing_deferred_budget" in h.notes for h in result.hits) == 3


def test_offline_discovery_cannot_invoke_real_searcher(tmp_path, monkeypatch):
    from jw.tools import scientific_data as tools

    monkeypatch.setattr(tools, "_workspace", lambda c: tmp_path)
    blocked = MagicMock(side_effect=AssertionError("offline must never reach provider"))
    monkeypatch.setattr(tools, "literature_searcher", blocked)
    result = json.loads(
        tools.sci_data_discover.invoke(
            {
                "request_json": '{"research_goal":"inspect existing evidence"}',
                "extra_urls_json": '["https://candidate.example/data"]',
                "allow_network": False,
            }
        )
    )
    blocked.assert_not_called()
    assert result["search_status"] == "policy_blocked"
    assert result["candidates"][0]["url"] == "https://candidate.example/data"

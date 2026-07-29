from __future__ import annotations

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

from jw.middleware.research_router import (
    ResearchRouterMiddleware,
    _successful_specialists,
)


def _tool(name: str):
    def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        run,
        name=name,
        description=f"{name} test tool",
    )


def _middleware(monkeypatch) -> ResearchRouterMiddleware:
    model = MagicMock()
    monkeypatch.setattr(
        "jw.middleware.research_router.disable_thinking",
        lambda value: value,
    )
    return ResearchRouterMiddleware(model=model)


def _request(
    *,
    route: dict[str, object],
    messages=None,
    tools=None,
) -> ModelRequest:
    return ModelRequest(
        model=MagicMock(),
        messages=messages or [HumanMessage("test", id="human-1")],
        tools=tools or [],
        state={"research_route": route},
    )


def _prepared(middleware: ResearchRouterMiddleware, request: ModelRequest):
    captured: list[ModelRequest] = []

    def handler(inner: ModelRequest):
        captured.append(inner)
        return MagicMock()

    middleware.wrap_model_call(request, handler)
    return captured[0]


def _route(
    mode: str,
    *,
    source_mode: str = "none",
    needs_computation: bool = False,
    task_intent: str = "general",
    required_specialist: str = "none",
) -> dict[str, object]:
    return {
        "mode": mode,
        "source_mode": source_mode,
        "needs_computation": needs_computation,
        "task_intent": task_intent,
        "required_specialist": required_specialist,
        "reason": "test",
    }


def test_specialist_success_requires_workspace_verified_receipt() -> None:
    calls = [
        {
            "name": "task",
            "id": "task-data",
            "args": {"subagent_type": "solar-data"},
        }
    ]
    messages = [
        ToolMessage(
            json.dumps(
                {
                    "status": "success",
                    "receipt_refs": ["receipts/datasets/f107_semantics.json"],
                }
            ),
            tool_call_id="task-data",
            name="task",
        )
    ]

    assert (
        _successful_specialists(
            calls,
            {"task"},
            messages,
            workspace_verified_receipts=set(),
        )
        == set()
    )
    assert _successful_specialists(
        calls,
        {"task"},
        messages,
        workspace_verified_receipts={"receipts/datasets/f107_semantics.json"},
    ) == {"solar-data"}


def _obligations(
    route: dict[str, object],
    *,
    adapter: str = "none",
) -> dict[str, object]:
    computation = route["needs_computation"] is True
    verified = route["mode"] in {"verified_analysis", "full_research"}
    local = route["source_mode"] in {"local", "mixed"} and computation
    return {
        **route,
        "requires_dataset_semantics": local,
        "requires_computation_receipt": verified and computation,
        "requires_external_evidence": route["source_mode"] in {"external", "mixed"},
        "external_evidence_reasons": (
            ["source_mode"] if route["source_mode"] in {"external", "mixed"} else []
        ),
        "required_evidence_claims": [],
        "required_domain_adapter": adapter,
        "deliverable": (
            "audited_report"
            if verified and computation
            else ("draft" if route["mode"] == "verified_analysis" else "chat")
        ),
    }


def test_router_runs_once_per_human_turn_and_persists_decision(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route("verified_analysis", source_mode="local")
    middleware._model.with_structured_output.return_value = structured
    state = {"messages": [HumanMessage("读取本地数据", id="turn-1")]}

    update = middleware.before_agent(state, runtime=None)

    assert update == {
        "research_route": _obligations(
            _route("verified_analysis", source_mode="local")
        ),
        "research_route_turn": "turn-1",
    }
    persisted = {**state, **update}
    assert middleware.before_agent(persisted, runtime=None) is None
    structured.invoke.assert_called_once()
    schema = middleware._model.with_structured_output.call_args.args[0]
    assert schema["properties"]["task_intent"]["enum"] == [
        "general",
        "hypothesis_generation",
        "hypothesis_comparison",
        "hypothesis_update",
    ]
    assert schema["properties"]["required_specialist"]["enum"] == [
        "none",
        "solar-hypothesis",
    ]


def test_f107_computation_gets_audited_route_obligations(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route(
        "verified_analysis",
        source_mode="mixed",
        needs_computation=True,
    )
    middleware._model.with_structured_output.return_value = structured

    update = middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    "分析 F10.7 与 SILSO 太阳黑子数的跨时段漂移",
                    id="turn-f107",
                )
            ]
        },
        runtime=None,
    )

    route = update["research_route"]
    assert route["requires_dataset_semantics"] is True
    assert route["requires_computation_receipt"] is True
    assert route["requires_external_evidence"] is True
    assert route["required_domain_adapter"] == "f107"
    assert route["required_analysis_protocol"] == "f107_discontinuity_v1"
    assert route["deliverable"] == "audited_report"


def test_f107_causal_comparison_cannot_become_bounded_hypothesis_route(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route(
        "verified_analysis",
        source_mode="mixed",
        needs_computation=True,
        task_intent="hypothesis_comparison",
        required_specialist="solar-hypothesis",
    )
    middleware._model.with_structured_output.return_value = structured

    update = middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    "比较 F10.7 漂移的数据不连续与物理变化归因假说，"
                    "查阅原始研究并完成正式回归检验",
                    id="turn-f107-causal",
                )
            ]
        },
        runtime=None,
    )

    route = update["research_route"]
    assert route["mode"] == "verified_analysis"
    assert route["task_intent"] == "general"
    assert route["required_specialist"] == "none"
    assert route["deliverable"] == "audited_report"
    assert route["requires_external_evidence"] is True
    assert route["requires_computation_receipt"] is True
    assert route["required_domain_adapter"] == "f107"
    assert {
        "competing_hypotheses",
        "domain_mandatory_claim",
    }.issubset(route["external_evidence_reasons"])


def test_local_route_with_explicit_literature_request_forces_external_stage(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route(
        "verified_analysis",
        source_mode="local",
        needs_computation=True,
    )
    middleware._model.with_structured_output.return_value = structured

    update = middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    "读取本地数据并查阅原始研究，比较仪器变化与物理变化",
                    id="turn-local-literature",
                )
            ]
        },
        runtime=None,
    )

    route = update["research_route"]
    assert route["source_mode"] == "local"
    assert route["requires_external_evidence"] is True
    assert "explicit_literature_request" in route["external_evidence_reasons"]


def test_computational_hypothesis_comparison_keeps_audited_parent_pipeline(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route(
        "full_research",
        source_mode="mixed",
        needs_computation=True,
    )
    middleware._model.with_structured_output.return_value = structured

    update = middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    "请为太阳周期异常生成并比较可证伪的科学假设",
                    id="turn-hypothesis",
                )
            ]
        },
        runtime=None,
    )

    assert update is not None
    route = update["research_route"]
    assert route["mode"] == "verified_analysis"
    assert route["task_intent"] == "general"
    assert route["required_specialist"] == "none"
    assert route["deliverable"] == "audited_report"
    assert route["requires_external_evidence"] is True
    assert route["requires_computation_receipt"] is True
    assert "competing_hypotheses" in route["external_evidence_reasons"]


@pytest.mark.parametrize(
    ("text", "expected_intent"),
    [
        ("请提出三个可证伪的太阳活动科学假设", "hypothesis_generation"),
        (
            "Update our hypothesis using the new polar-field evidence.",
            "hypothesis_update",
        ),
        (
            "Do not run full research; just generate testable hypotheses.",
            "hypothesis_generation",
        ),
    ],
)
def test_router_fallback_recognizes_bilingual_hypothesis_intent(
    monkeypatch,
    text: str,
    expected_intent: str,
) -> None:
    middleware = _middleware(monkeypatch)
    middleware._model.with_structured_output.side_effect = RuntimeError(
        "router unavailable"
    )

    update = middleware.before_agent(
        {"messages": [HumanMessage(text, id=f"turn-{expected_intent}")]},
        runtime=None,
    )

    assert update is not None
    expected = _obligations(
        {
            "mode": "verified_analysis",
            "source_mode": "mixed",
            "needs_computation": False,
            "task_intent": expected_intent,
            "required_specialist": "solar-hypothesis",
            "reason": "router unavailable; explicit hypothesis intent kept specialized",
        }
    )
    route = update["research_route"]
    for key, value in expected.items():
        if key == "external_evidence_reasons":
            continue
        assert route[key] == value
    assert set(route["external_evidence_reasons"]) >= set(
        expected["external_evidence_reasons"]
    )


def test_router_fallback_competing_hypotheses_uses_evidence_pipeline(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    middleware._model.with_structured_output.side_effect = RuntimeError(
        "router unavailable"
    )

    update = middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    "Compare the competing solar-cycle hypotheses.",
                    id="turn-competing-hypotheses",
                )
            ]
        },
        runtime=None,
    )

    route = update["research_route"]
    assert route["mode"] == "verified_analysis"
    assert route["task_intent"] == "general"
    assert route["required_specialist"] == "none"
    assert route["requires_external_evidence"] is True
    assert "competing_hypotheses" in route["external_evidence_reasons"]


def test_verified_local_route_forces_discovery_then_read_then_compute(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="local",
        needs_computation=True,
    )
    tools = [_tool("ls"), _tool("read_file"), _tool("execute")]
    human = HumanMessage("读取并计算", id="turn-1")

    first = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=tools),
    )
    assert first.tool_choice is None
    assert [tool.name for tool in first.tools] == ["ls"]

    after_ls = [
        human,
        AIMessage(
            "",
            tool_calls=[{"name": "ls", "args": {"path": "/project"}, "id": "ls-1"}],
        ),
        ToolMessage("a.csv", tool_call_id="ls-1", name="ls"),
    ]
    second = _prepared(
        middleware,
        _request(route=route, messages=after_ls, tools=tools),
    )
    assert second.tool_choice is None
    assert [tool.name for tool in second.tools] == ["read_file"]

    after_read = [
        *after_ls,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/project/a.csv"},
                    "id": "read-1",
                }
            ],
        ),
        ToolMessage("x,y", tool_call_id="read-1", name="read_file"),
    ]
    third = _prepared(
        middleware,
        _request(route=route, messages=after_read, tools=tools),
    )
    assert third.tool_choice is None
    assert [tool.name for tool in third.tools] == ["execute"]


def test_f107_pipeline_binds_dataset_semantics_before_external_search(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = {
        **_obligations(
            _route(
                "verified_analysis",
                source_mode="mixed",
                needs_computation=True,
            ),
            adapter="f107",
        ),
        "requires_external_evidence": True,
        "external_evidence_reasons": [
            "source_mode",
            "domain_mandatory_claim",
        ],
        "required_evidence_claims": [
            {"claim_id": "f107_product_definition"},
            {"claim_id": "f107_observatory_history"},
            {"claim_id": "f107_1980_discontinuity"},
        ],
    }
    human = HumanMessage("分析 F10.7 归因", id="turn-f107-order")
    read_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"file_path": "/project/f107.csv"},
                "id": "read-f107",
            }
        ],
    )
    after_read = [
        human,
        read_call,
        ToolMessage("date,f107", tool_call_id="read-f107", name="read_file"),
    ]

    data_stage = _prepared(
        middleware,
        _request(
            route=route,
            messages=after_read,
            tools=[_tool("task"), _tool("tavily_search")],
        ),
    )
    assert [tool.name for tool in data_stage.tools] == ["task"]
    assert "subagent_type='solar-data' before external evidence" in (
        data_stage.system_message.text
    )

    data_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {
                    "subagent_type": "solar-data",
                    "description": "bind semantics",
                },
                "id": "task-solar-data",
            }
        ],
    )
    after_data = [
        *after_read,
        data_call,
        ToolMessage(
            json.dumps(
                {
                    "status": "success",
                    "summary": "dataset semantics verified",
                    "receipt_refs": ["receipts/data/manifest.json"],
                }
            ),
            tool_call_id="task-solar-data",
            name="task",
        ),
    ]
    evidence_stage = _prepared(
        middleware,
        _request(
            route=route,
            messages=after_data,
            tools=[_tool("task"), _tool("tavily_search")],
        ),
    )
    assert [tool.name for tool in evidence_stage.tools] == ["tavily_search"]


def test_hypothesis_route_forces_direct_task_delegation(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    tools = [_tool("read_file"), _tool("task"), _tool("execute")]

    prepared = _prepared(
        middleware,
        _request(route=route, tools=tools),
    )

    assert prepared.tool_choice is None
    assert [tool.name for tool in prepared.tools] == ["task"]
    assert "subagent_type='solar-hypothesis'" in prepared.system_message.text
    assert "Do not call solar-planner first" in prepared.system_message.text
    assert (
        "mandatory next graph node is solar-planner" not in prepared.system_message.text
    )


def test_hypothesis_model_result_replaces_parent_preread_with_task(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    request = _request(
        route=route,
        tools=[_tool("read_file"), _tool("task")],
    )

    response = middleware.wrap_model_call(
        request,
        lambda _inner: ModelResponse(
            result=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/memories/MEMORY.md"},
                            "id": "stale-read",
                        }
                    ],
                )
            ]
        ),
    )

    assert len(response.result) == 1
    assert len(response.result[0].tool_calls) == 1
    call = response.result[0].tool_calls[0]
    assert call["name"] == "task"
    assert call["args"]["subagent_type"] == "solar-hypothesis"
    assert call["id"].startswith("call_hypothesis_")


def test_hypothesis_task_execution_rewrites_generic_delegation_to_specialist(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, "/task/work/scientific_hypothesis_state.json"),
    )
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("请形成并维护候选草稿", id="turn-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {
                    "subagent_type": "general-purpose",
                    "description": "Read the Wiki tree.",
                },
                "id": "task-generic",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )
    captured: list[ToolCallRequest] = []

    def handler(inner: ToolCallRequest) -> ToolMessage:
        captured.append(inner)
        return ToolMessage(
            "persisted specialist draft",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        )

    result = middleware.wrap_tool_call(request, handler)

    assert len(captured) == 1
    args = captured[0].tool_call["args"]
    assert args["subagent_type"] == "solar-hypothesis"
    assert "请形成并维护候选草稿" in args["description"]
    assert "Read the Wiki tree." not in args["description"]
    assert result.additional_kwargs["research_router_specialist"] == "solar-hypothesis"
    followup = _prepared(
        middleware,
        _request(
            route=route,
            messages=[human, model_call, result],
            tools=[_tool("task"), _tool("read_file")],
        ),
    )
    assert followup.tools == []
    assert "will be passed through verbatim" in followup.system_message.text


def test_hypothesis_route_blocks_parent_preread_at_tool_execution(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("请提出太阳活动周竞争假设", id="turn-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"file_path": "/project/memory.md"},
                "id": "read-before-task",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )
    called = False

    def handler(_inner: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        raise AssertionError("parent pre-read must not execute")

    result = middleware.wrap_tool_call(request, handler)

    assert called is False
    assert result.status == "error"
    assert result.name == "read_file"
    assert "must enter through one direct solar-hypothesis delegation" in str(
        result.content
    )
    assert "pre-read Wiki or memory files" in str(result.content)


def test_hypothesis_route_blocks_parallel_compensating_tasks(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("提出竞争假设", id="turn-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "general-purpose", "description": "read A"},
                "id": "task-first",
            },
            {
                "name": "task",
                "args": {"subagent_type": "general-purpose", "description": "read B"},
                "id": "task-second",
            },
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[1],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )
    called = False

    def handler(_inner: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        raise AssertionError("parallel compensating task must not execute")

    result = middleware.wrap_tool_call(request, handler)

    assert called is False
    assert result.status == "error"
    assert "exactly one direct task delegation" in str(result.content)


def test_hypothesis_task_requires_persisted_draft_receipt(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (False, "latest_draft is missing"),
    )
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("提出并维护竞争假设草稿", id="turn-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis", "description": "draft"},
                "id": "task-hypothesis",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={"configurable": {}}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda inner: ToolMessage(
            "A prose-only DRAFT",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        ),
    )

    assert result.status == "error"
    assert "HYPOTHESIS DRAFT INCOMPLETE" in str(result.content)
    assert result.additional_kwargs["research_router_specialist"] == "solar-hypothesis"
    retry = _prepared(
        middleware,
        _request(
            route=route,
            messages=[human, model_call, result],
            tools=[_tool("task"), _tool("read_file")],
        ),
    )
    assert [tool.name for tool in retry.tools] == ["task"]


def test_hypothesis_model_budget_exhaustion_preserves_partial_without_retry(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, "/task/work/scientific_hypothesis_state.json"),
    )
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("提出探索性假设草稿", id="turn-hypothesis-budget")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis", "description": "draft"},
                "id": "task-hypothesis-budget",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={"configurable": {}}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda inner: ToolMessage(
            "Model call limits exceeded: run limit (24/24)",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        ),
    )

    payload = json.loads(str(result.content))
    assert payload["status"] == "partial"
    assert payload["error_code"] == "model_call_budget_exhausted"
    assert payload["retryable"] is False
    assert payload["artifact_refs"] == ["/task/work/scientific_hypothesis_state.json"]
    assert result.additional_kwargs["research_router_outcome_status"] == "partial"
    assert not result.additional_kwargs.get("receipt_refs")

    followup = _prepared(
        middleware,
        _request(
            route=route,
            messages=[human, model_call, result],
            tools=[_tool("task"), _tool("read_file")],
        ),
    )
    assert followup.tools == []
    assert "model-call budget was exhausted" in followup.system_message.text


def test_hypothesis_prose_without_receipt_is_not_completed(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage("提出竞争假设", id="turn-hypothesis")
    call = {
        "name": "task",
        "args": {"subagent_type": "solar-hypothesis", "description": "exact request"},
        "id": "hypothesis-task",
    }
    messages = [
        human,
        AIMessage("", tool_calls=[call]),
        ToolMessage(
            "# DRAFT\n\nExact specialist body.",
            tool_call_id="hypothesis-task",
            name="task",
        ),
    ]
    request = _request(
        route=route,
        messages=messages,
        tools=[_tool("task"), _tool("read_file")],
    )
    captured: list[ModelRequest] = []

    def handler(inner: ModelRequest) -> ModelResponse:
        captured.append(inner)
        return ModelResponse(result=[AIMessage("Parent rewrite")])

    response = middleware.wrap_model_call(request, handler)

    assert [tool.name for tool in captured[0].tools] == ["task"]
    assert response.result[0].content == ""
    assert response.result[0].tool_calls[0]["name"] == "task"
    assert (
        response.result[0].tool_calls[0]["args"]["subagent_type"] == "solar-hypothesis"
    )


def test_hypothesis_route_reports_missing_task_tool_without_substitution(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_update",
        required_specialist="solar-hypothesis",
    )

    prepared = _prepared(
        middleware,
        _request(
            route=route,
            tools=[_tool("read_file"), _tool("execute")],
        ),
    )

    assert prepared.tool_choice is None
    assert prepared.tools == []
    assert "ROUTING BLOCKER" in prepared.system_message.text
    assert "actual tool list does not contain 'task'" in prepared.system_message.text
    assert "Do not silently continue" in prepared.system_message.text


def test_full_research_route_advances_explicit_specialist_graph(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    task = _tool("task")
    human = HumanMessage("完成端到端研究", id="turn-1")

    first = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[task]),
    )
    assert first.tool_choice is None
    assert [tool.name for tool in first.tools] == ["task"]
    assert "mandatory next graph node is solar-planner" in first.system_message.text

    after_planner = [
        human,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "solar-planner"},
                    "id": "planner-1",
                }
            ],
        ),
        ToolMessage(
            json.dumps(
                {
                    "status": "success",
                    "receipt_refs": ["receipts/planner.json"],
                }
            ),
            tool_call_id="planner-1",
            name="task",
        ),
    ]
    second = _prepared(
        middleware,
        _request(route=route, messages=after_planner, tools=[task]),
    )
    assert second.tool_choice is None
    assert [tool.name for tool in second.tools] == ["task"]
    assert "mandatory next graph node is solar-hypothesis" in second.system_message.text


def test_failed_required_stage_stops_after_two_attempts(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    human = HumanMessage("完成端到端研究", id="turn-1")
    messages = [human]
    for index in (1, 2):
        call_id = f"planner-{index}"
        messages.extend(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-planner"},
                            "id": call_id,
                        }
                    ],
                ),
                ToolMessage(
                    "failed",
                    tool_call_id=call_id,
                    name="task",
                    status="error",
                ),
            ]
        )

    prepared = _prepared(
        middleware,
        _request(route=route, messages=messages, tools=[_tool("task")]),
    )

    assert prepared.tool_choice is None
    assert "failed twice. Do not loop" in prepared.system_message.text


def test_fast_answer_does_not_force_tool_use(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)

    prepared = _prepared(
        middleware,
        _request(
            route=_route("fast_answer"),
            tools=[_tool("read_file"), _tool("execute")],
        ),
    )

    assert prepared.tool_choice is None
    assert "direct-answer path" in prepared.system_message.text

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
from langgraph.types import Command

from jw.middleware.research_router import (
    ResearchRouterMiddleware,
    _fallback_route,
    _latest_specialist_result,
    _passthrough_accepted_bounded_stage,
    _passthrough_accepted_release,
    _passthrough_hypothesis_result,
    _successful_specialists,
    _with_analysis_protocol,
)
from jw.research_protocols import (
    SOLAR_CYCLE_26_READINESS_PROTOCOL,
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
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


def test_f107_discontinuity_overrides_fast_answer_route() -> None:
    route = _with_analysis_protocol(
        _route("fast_answer"),
        text="分析 F10.7 在 1980-1981 年的不连续性",
    )

    assert route["mode"] == "verified_analysis"
    assert route["needs_computation"] is True
    assert route["source_mode"] == "mixed"
    assert route["required_analysis_protocol"] == "f107_discontinuity_v1"


def test_silso_cycle_reproduction_routes_to_bounded_data_specialist() -> None:
    route = _with_analysis_protocol(
        _route("fast_answer"),
        text=(
            "Use WDC-SILSO Version 2.0 to reproduce the official minima, maxima, "
            "and rise time for solar cycles 21-24."
        ),
    )

    assert route["mode"] == "verified_analysis"
    assert route["task_intent"] == "data_preparation"
    assert route["required_specialist"] == "solar-data"
    assert route["needs_computation"] is True
    assert route["required_analysis_protocol"] == "silso_cycle_reproduction_v1"


def test_current_observation_hypothesis_adds_bounded_data_stage() -> None:
    route = _with_analysis_protocol(
        _route(
            "verified_analysis",
            source_mode="mixed",
            task_intent="hypothesis_generation",
            required_specialist="solar-hypothesis",
        ),
        text=(
            "当前第25太阳活动周的观测信号，能否为第26太阳活动周的强度趋势和"
            "物理机制提供证据？请提出一个最值得检验的科学假设，并说明现有证据。"
        ),
    )

    assert route["required_specialist"] == "solar-hypothesis"
    assert route["task_intent"] == "hypothesis_generation"
    assert route["preliminary_stages"] == ["data"]
    assert route["needs_computation"] is True


def test_quantitative_observational_hypothesis_enters_full_research_without_stage_hints() -> (
    None
):
    prompt = (
        "在太阳活动周15至24的逐周期观测中，上一活动周较长是否会削弱极小期"
        "极区场强对下一活动周振幅的预测关系？请提出一个最值得检验、可证伪的"
        "交互作用假设，并说明现有证据与最强零假设。"
    )

    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)

    assert route["mode"] == "full_research"
    assert route["source_mode"] == "mixed"
    assert route["needs_computation"] is True
    assert route["task_intent"] == "general"
    assert route["required_specialist"] == "none"
    assert route["required_analysis_protocol"] == "solar_polar_precursor_v1"


def test_current_observation_hypothesis_starts_with_data_producer(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = {
        **_route(
            "verified_analysis",
            source_mode="mixed",
            needs_computation=True,
            task_intent="hypothesis_generation",
            required_specialist="solar-hypothesis",
        ),
        "preliminary_stages": ["data"],
    }
    fake_store = MagicMock()
    fake_store.bounded_sequence_action.return_value = {
        "kind": "producer",
        "stage": "data",
        "producer": "solar-data",
        "phase": "bounded_data",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("task")]),
        lambda _inner: ModelResponse(result=[AIMessage("准备直接回答")]),
    )

    call = response.result[0].tool_calls[0]
    assert response.result[0].content == ""
    assert call["args"]["subagent_type"] == "solar-data"
    fake_store.bounded_sequence_action.assert_called_with(("data", "hypothesis"))


def test_data_producer_suppresses_optional_tools_after_verified_precursor_table(
    monkeypatch,
) -> None:
    """A verified deterministic table ends the Data tool loop for this protocol."""

    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._bounded_stage_action",
        lambda _request, _stage: {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
        },
    )
    route = {
        **_route(
            "verified_analysis",
            source_mode="local",
            needs_computation=True,
            task_intent="data_preparation",
            required_specialist="solar-data",
        ),
        "required_analysis_protocol": SOLAR_POLAR_PRECURSOR_PROTOCOL,
    }
    messages = [
        HumanMessage("[RESEARCH_PRODUCER_V2] stage=data", id="data-turn"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_solar_precursor_cycle_table",
                    "args": {},
                    "id": "prepare-1",
                }
            ],
        ),
        ToolMessage(
            content='{"status":"verified","output_ref":"work/solar_data/table.csv"}',
            tool_call_id="prepare-1",
            name="prepare_solar_precursor_cycle_table",
        ),
    ]

    prepared = _prepared(
        middleware,
        _request(
            route=route,
            messages=messages,
            tools=[
                _tool("task"),
                _tool("prepare_solar_precursor_cycle_table"),
                _tool("solar_research_analysis"),
                _tool("dataset_statistics"),
            ],
        ),
    )

    assert prepared.tools == []


@pytest.mark.parametrize("receipt_status", ["partial", "error"])
def test_data_producer_keeps_tools_after_nonverified_precursor_receipt(
    monkeypatch, receipt_status: str
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._bounded_stage_action",
        lambda _request, _stage: {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
        },
    )
    route = {
        **_route(
            "verified_analysis",
            source_mode="local",
            needs_computation=True,
            task_intent="data_preparation",
            required_specialist="solar-data",
        ),
        "required_analysis_protocol": SOLAR_POLAR_PRECURSOR_PROTOCOL,
    }
    messages = [
        HumanMessage("[RESEARCH_PRODUCER_V2] stage=data", id="data-turn"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_solar_precursor_cycle_table",
                    "args": {},
                    "id": "prepare-1",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({"status": receipt_status}),
            tool_call_id="prepare-1",
            name="prepare_solar_precursor_cycle_table",
        ),
    ]
    tools = [
        _tool("task"),
        _tool("prepare_solar_precursor_cycle_table"),
        _tool("solar_research_analysis"),
        _tool("dataset_statistics"),
    ]

    prepared = _prepared(
        middleware,
        _request(route=route, messages=messages, tools=tools),
    )

    assert [tool.name for tool in prepared.tools] == [tool.name for tool in tools]


def test_full_research_data_message_suppresses_tools_after_verified_precursor_table(
    monkeypatch,
) -> None:
    """The full-research task description identifies its nested Data producer."""

    middleware = _middleware(monkeypatch)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "data",
        "producer": "solar-data",
        "phase": "data",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )
    route = {
        **_route(
            "full_research",
            source_mode="mixed",
            needs_computation=True,
        ),
        "required_analysis_protocol": SOLAR_POLAR_PRECURSOR_PROTOCOL,
    }
    messages = [
        HumanMessage(
            "\n".join(
                [
                    "[RESEARCH_PRODUCER_V2]",
                    "phase=data",
                    "stage=data",
                    'deterministic_data_context={"required_data_product":'
                    '"solar_polar_precursor_table_v1"}',
                ]
            ),
            id="nested-data-turn",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_solar_precursor_cycle_table",
                    "args": {},
                    "id": "prepare-full-1",
                }
            ],
        ),
        ToolMessage(
            content='{"status":"verified","output_ref":"work/solar_data/table.csv"}',
            tool_call_id="prepare-full-1",
            name="prepare_solar_precursor_cycle_table",
        ),
    ]

    prepared = middleware._prepare_request(
        _request(
            route=route,
            messages=messages,
            tools=[
                _tool("task"),
                _tool("prepare_solar_precursor_cycle_table"),
                _tool("solar_research_analysis"),
                _tool("dataset_statistics"),
            ],
        )
    )

    assert prepared.tools == []


def test_fallback_preserves_explicit_bounded_planning_route() -> None:
    result = _fallback_route("请制定一份极区磁场研究计划")

    assert result["task_intent"] == "research_planning"
    assert result["required_specialist"] == "solar-planner"


def test_fallback_recognizes_natural_reviewable_research_package() -> None:
    result = _fallback_route(
        "请系统研究第26太阳活动周预测是否可以启动，形成可供同行初审的研究包。"
    )

    assert result["mode"] == "full_research"
    assert result["source_mode"] == "mixed"
    assert result["required_specialist"] == "none"


def test_main_cycle_26_launch_gate_routes_to_readiness_not_precursor() -> None:
    prompt = (
        "请把资料截止在 2026 年 6 月 30 日，系统研究第 26 太阳活动周强度预测"
        "现在是否可以启动。重点核查 SILSO、F10.7 和 WSO 极区磁场，最终明确"
        "回答可以启动或暂不启动。"
    )

    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)

    assert route["mode"] == "full_research"
    assert route["required_analysis_protocol"] == SOLAR_CYCLE_26_READINESS_PROTOCOL
    assert route["required_analysis_protocol"] != SOLAR_POLAR_PRECURSOR_PROTOCOL


def test_cycle_26_probability_forecast_routes_to_full_research_readiness() -> None:
    prompt = (
        "请系统研究并正式发布第 26 太阳活动周的初步概率预测，"
        "给出点预测、80% 和 95% 预测区间、峰值时间和更新规则，"
        "并形成可供同行初审的研究包。"
    )

    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)

    assert route["mode"] == "full_research"
    assert route["required_analysis_protocol"] == SOLAR_CYCLE_26_READINESS_PROTOCOL
    assert route["required_analysis_protocol"] != SOLAR_POLAR_PRECURSOR_PROTOCOL


def test_specialist_success_requires_workspace_verified_artifact() -> None:
    calls = [
        {
            "name": "task",
            "id": "planner-1",
            "args": {"subagent_type": "solar-planner"},
        }
    ]
    messages = [
        ToolMessage("frozen", tool_call_id="planner-1", name="task"),
    ]

    assert _successful_specialists(calls, {"task"}, messages) == {"solar-planner"}
    assert (
        _successful_specialists(
            calls,
            {"task"},
            messages,
            workspace_verified_specialists=set(),
        )
        == set()
    )
    assert _successful_specialists(
        calls,
        {"task"},
        messages,
        workspace_verified_specialists={"solar-planner"},
    ) == {"solar-planner"}


def test_router_runs_once_per_human_turn_and_persists_decision(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    structured = MagicMock()
    structured.invoke.return_value = _route("verified_analysis", source_mode="local")
    middleware._model.with_structured_output.return_value = structured
    state = {"messages": [HumanMessage("读取本地数据", id="turn-1")]}

    update = middleware.before_agent(state, runtime=None)

    assert update == {
        "research_route": {
            **_route("verified_analysis", source_mode="local"),
            "harness_version": "agent-runtime-harness-v1",
            "capability_id": "analysis",
        },
        "research_route_turn": "turn-1",
    }
    persisted = {**state, **update}
    assert middleware.before_agent(persisted, runtime=None) is None
    structured.invoke.assert_called_once()
    schema = middleware._model.with_structured_output.call_args.args[0]
    assert schema["properties"]["task_intent"]["enum"] == [
        "general",
        "research_planning",
        "data_preparation",
        "hypothesis_generation",
        "hypothesis_comparison",
        "hypothesis_update",
        "experiment_design",
        "experiment_run",
    ]
    assert schema["properties"]["required_specialist"]["enum"] == [
        "none",
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
    ]


def test_router_preserves_bounded_graph_on_explicit_continuation(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    prior = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    state = {
        "messages": [
            HumanMessage(
                "继续当前科研闭环，按 next_action 完成证据审查",
                id="turn-continue",
            )
        ],
        "research_route": prior,
        "research_route_turn": "older-turn",
    }

    update = middleware.before_agent(state, runtime=SimpleNamespace(config={}))

    assert update["research_route"]["mode"] == "verified_analysis"
    assert update["research_route"]["task_intent"] == "hypothesis_generation"
    assert update["research_route"]["required_specialist"] == "solar-hypothesis"
    assert update["research_route_turn"] == "turn-continue"
    middleware._model.with_structured_output.assert_not_called()


def test_router_preserves_full_graph_on_terse_continuation(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    prior = _route("full_research", source_mode="mixed", needs_computation=True)
    state = {
        "messages": [HumanMessage("继续。", id="turn-terse-continue")],
        "research_route": prior,
        "research_route_turn": "older-turn",
    }

    update = middleware.before_agent(state, runtime=SimpleNamespace(config={}))

    assert update["research_route"]["mode"] == "full_research"
    assert update["research_route"]["reason"] == (
        "explicit continuation of persisted research graph"
    )
    assert update["research_route_turn"] == "turn-terse-continue"
    middleware._model.with_structured_output.assert_not_called()


def test_router_recovers_legacy_bounded_stage_on_explicit_continuation(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    prior_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis"},
                "id": "prior-hypothesis",
            }
        ],
    )
    wrong_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-planner"},
                "id": "blocked-wrong-planner",
            }
        ],
    )
    wrong_result = ToolMessage(
        "[RESEARCH REVIEW BLOCKED] research run is blocked",
        tool_call_id="blocked-wrong-planner",
        name="task",
    )
    state = {
        "messages": [
            HumanMessage("提出太阳活动周竞争假设", id="original-turn"),
            prior_call,
            ToolMessage(
                "persisted hypothesis artifact",
                tool_call_id="prior-hypothesis",
                name="task",
            ),
            HumanMessage(
                "继续当前科研闭环，执行 next_action",
                id="older-continuation",
            ),
            wrong_call,
            wrong_result,
            HumanMessage("恢复研究审查状态机", id="turn-legacy-continue"),
        ],
        "research_route": {
            **_route(
                "verified_analysis",
                source_mode="local",
                task_intent="research_planning",
                required_specialist="solar-planner",
            ),
            "reason": "explicit continuation of persisted research graph",
        },
        "research_route_turn": "generic-turn",
    }

    update = middleware.before_agent(state, runtime=SimpleNamespace(config={}))

    route = update["research_route"]
    assert route["mode"] == "verified_analysis"
    assert route["task_intent"] == "hypothesis_update"
    assert route["required_specialist"] == "solar-hypothesis"
    assert route["reason"] == (
        "recovered bounded hypothesis graph from same-thread trace"
    )
    middleware._model.with_structured_output.assert_not_called()


def test_router_recovers_full_graph_after_release_prepare_attempt(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    state = {
        "messages": [
            HumanMessage("提出太阳活动周竞争假设", id="original-turn"),
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"subagent_type": "solar-hypothesis"},
                        "id": "prior-hypothesis",
                    }
                ],
            ),
            ToolMessage(
                "persisted hypothesis update",
                tool_call_id="prior-hypothesis",
                name="task",
            ),
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "research_release_prepare",
                        "args": {
                            "draft_markdown": "# Draft",
                            "claim_citations": [],
                        },
                        "id": "release-attempt",
                    }
                ],
            ),
            ToolMessage(
                '{"ok": false, "status": "error"}',
                tool_call_id="release-attempt",
                name="research_release_prepare",
            ),
            HumanMessage(
                "继续完成上述完整科研闭环。",
                id="turn-release-continue",
            ),
        ],
        "research_route": {
            **_route(
                "verified_analysis",
                source_mode="local",
                task_intent="hypothesis_update",
                required_specialist="solar-hypothesis",
            ),
            "reason": "recovered bounded hypothesis graph from same-thread trace",
        },
        "research_route_turn": "older-turn",
    }

    update = middleware.before_agent(state, runtime=SimpleNamespace(config={}))

    route = update["research_route"]
    assert route["mode"] == "full_research"
    assert route["task_intent"] == "general"
    assert route["required_specialist"] == "none"
    assert route["reason"] == "recovered full research graph from same-thread trace"
    middleware._model.with_structured_output.assert_not_called()


def test_explicit_hypothesis_intent_overrides_full_research_misroute(
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
    assert route["task_intent"] == "hypothesis_comparison"
    assert route["required_specialist"] == "solar-hypothesis"


@pytest.mark.parametrize(
    ("text", "expected_intent"),
    [
        ("请提出三个可证伪的太阳活动科学假设", "hypothesis_generation"),
        ("Compare the competing solar-cycle hypotheses.", "hypothesis_comparison"),
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
    assert update["research_route"] == {
        "mode": "verified_analysis",
        "source_mode": "mixed",
        "needs_computation": False,
        "task_intent": expected_intent,
        "required_specialist": "solar-hypothesis",
        "reason": "router unavailable; explicit hypothesis intent kept specialized",
        "harness_version": "agent-runtime-harness-v1",
        "capability_id": "scientific_hypothesis",
    }


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
    assert "Call task now with subagent_type='solar-hypothesis'" in (
        prepared.system_message.text
    )
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


def test_hypothesis_model_result_removes_parent_authored_expected_answer(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    user_request = "为什么太阳活动周期上升期会出现双峰？"
    request = _request(
        route=route,
        messages=[HumanMessage(user_request)],
        tools=[_tool("task")],
    )

    response = middleware.wrap_model_call(
        request,
        lambda _inner: ModelResponse(
            result=[
                AIMessage(
                    "我去找专家。",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "solar-hypothesis",
                                "description": (
                                    "只生成三个候选，并分别使用发电机、通量输运、"
                                    "活动经度机制。"
                                ),
                            },
                            "id": "task-contaminated",
                        }
                    ],
                )
            ]
        ),
    )

    call = response.result[0].tool_calls[0]
    assert call["args"]["subagent_type"] == "solar-hypothesis"
    assert response.result[0].content == ""


def test_hypothesis_task_execution_rewrites_generic_delegation_to_specialist(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, "/task/work/scientific_hypothesis_state.json"),
    )
    monkeypatch.setattr(
        "jw.tools.scientific_hypothesis.render_persisted_hypothesis_reader_view",
        lambda _path, *, partial_reason=None: "# 科学假设组合\n\n研究者摘要",
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
    assert "tail_review_scoring_guide" in args["description"]
    assert "violated_guidelines" in args["description"]
    assert "pass if and only if that list is empty" in args["description"]
    assert "A `_gauss` suffix is a measurement unit" in args["description"]
    assert result.content == "# 科学假设组合\n\n研究者摘要"
    assert result.additional_kwargs["research_router_specialist"] == "solar-hypothesis"
    assert (
        result.additional_kwargs["research_router_result_view"] == "researcher_summary"
    )
    # ResearchReviewOrchestrationMiddleware checkpoints the producer result
    # and advances the persisted state to the independent reviewer. This
    # router-only unit test deliberately does not fake that second middleware.


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


def test_single_hypothesis_request_keeps_rivals_inside_one_candidate(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    human = HumanMessage(
        "请提出一个最值得检验的科学假设，并说明现有证据。",
        id="turn-single-hypothesis",
    )
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis"},
                "id": "task-single-hypothesis",
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

    middleware.wrap_tool_call(request, handler)

    description = captured[0].tool_call["args"]["description"]
    assert "Honor the user's requested output cardinality" in description
    assert "do not expand the visible or persisted result" in description
    assert "请提出一个最值得检验的科学假设" in description


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


def test_hypothesis_review_error_is_not_relabelled_as_producer_failure(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    monkeypatch.setattr(
        "jw.middleware.research_router._bounded_review_action",
        lambda _request: {
            "kind": "review",
            "stage": "hypothesis",
            "review_mode": "hypothesis",
        },
    )
    human = HumanMessage("提出竞争假设", id="turn-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-evidence"},
                "id": "task-evidence",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )
    failure = (
        "[RESEARCH REVIEW BLOCKED] solar-evidence returned without persisting "
        "a hash-bound ReviewVerdictV2"
    )

    result = middleware.wrap_tool_call(
        request,
        lambda inner: ToolMessage(
            failure,
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
            status="error",
        ),
    )

    assert result.status == "error"
    assert result.content == failure
    assert result.additional_kwargs.get("research_router_specialist") != (
        "solar-hypothesis"
    )


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
    assert "暂时不返回候选结论" in str(result.content)
    assert "latest_draft is missing" not in str(result.content)
    assert (
        result.additional_kwargs["research_router_internal_failure"]
        == "latest_draft is missing"
    )
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


def test_hypothesis_task_without_workspace_never_exposes_raw_result(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: None,
    )
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
                "args": {"subagent_type": "solar-hypothesis"},
                "id": "task-hypothesis",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda inner: ToolMessage(
            "rubric_reward=1; Pareto=[H1]; draft_sha256=secret",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        ),
    )

    assert result.status == "error"
    assert "暂时不返回候选结论" in str(result.content)
    assert "rubric_reward" not in str(result.content)
    assert "Pareto" not in str(result.content)
    assert "draft_sha256" not in str(result.content)


def test_hypothesis_task_error_is_humanized(monkeypatch) -> None:
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
                "args": {"subagent_type": "solar-hypothesis"},
                "id": "task-hypothesis",
            }
        ],
    )
    request = ToolCallRequest(
        tool_call=model_call.tool_calls[0],
        tool=None,
        state={"research_route": route, "messages": [human, model_call]},
        runtime=SimpleNamespace(config={}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda inner: ToolMessage(
            "InternalError: schema enum mismatch at candidate_pool_sha256",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
            status="error",
        ),
    )

    assert result.status == "error"
    assert "科学假设子任务没有完成" in str(result.content)
    assert "InternalError" not in str(result.content)
    assert "candidate_pool_sha256" not in str(result.content)
    assert (
        "InternalError" in result.additional_kwargs["research_router_internal_failure"]
    )


def test_hypothesis_receipt_uses_runtime_base_workspace(
    monkeypatch,
    tmp_path,
) -> None:
    from jw.middleware.research_router import _persisted_hypothesis_draft_status

    task_root = tmp_path / "projects" / "default" / "runs" / "run-1"
    state_path = task_root / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        ('{"latest_draft_sha256":"abc","latest_draft":{"candidates":[{"id":"H0"}]}}'),
        encoding="utf-8",
    )
    calls = []

    def resolve(config, base_workspace=None):
        calls.append((config, base_workspace))
        return task_root

    monkeypatch.setattr(
        "jw.middleware.research_router.workspace_root_from_config",
        resolve,
    )
    config = {
        "configurable": {"thread_id": "thread-1"},
        "metadata": {"base_workspace_dir": str(tmp_path)},
    }

    assert _persisted_hypothesis_draft_status(config) == (True, str(state_path))
    assert calls == [(config, str(tmp_path))]


def test_hypothesis_budget_stop_recovers_persisted_draft(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    state_path = "/task/work/scientific_hypothesis_state.json"
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, state_path),
    )
    reader_view = "# 科学假设组合\n\n本次生成提前停止，下面展示的是已经保存的草稿。"
    monkeypatch.setattr(
        "jw.tools.scientific_hypothesis.render_persisted_hypothesis_reader_view",
        lambda path, *, partial_reason=None: reader_view,
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
            "Model call limits exceeded: run limit (32/32)",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        ),
    )

    assert result.content == reader_view
    assert result.status == "success"
    assert (
        result.additional_kwargs["research_router_execution_status"] == "budget_stopped"
    )
    assert result.additional_kwargs["research_router_result_status"] == "partial"
    assert result.additional_kwargs["research_router_recovered_persisted_draft"] is True

    # With the bounded review action returning a fresh tool call, the passthrough
    # response may differ when state hasn't advanced. The key invariant is that
    # the recovered draft was already served above.


def test_hypothesis_budget_stop_recovers_command_wrapped_task_result(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    state_path = "/task/work/scientific_hypothesis_state.json"
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, state_path),
    )
    reader_view = "# 科学假设组合\n\n本次生成提前停止，下面展示已保存的草稿。"
    monkeypatch.setattr(
        "jw.tools.scientific_hypothesis.render_persisted_hypothesis_reader_view",
        lambda path, *, partial_reason=None: reader_view,
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
        lambda inner: Command(
            update={
                "messages": [
                    ToolMessage(
                        "Model call limits exceeded: run limit (32/32)",
                        tool_call_id=str(inner.tool_call["id"]),
                    )
                ],
                "files": {"/work/a.txt": "preserved"},
            }
        ),
    )

    assert isinstance(result, Command)
    assert result.update["files"] == {"/work/a.txt": "preserved"}
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.content == reader_view
    assert message.additional_kwargs["research_router_result_status"] == "partial"
    assert (
        message.additional_kwargs["research_router_recovered_persisted_draft"] is True
    )


def test_hypothesis_result_is_passed_through_verbatim(monkeypatch) -> None:
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
    monkeypatch.setattr(
        "jw.middleware.research_router._bounded_review_action",
        lambda _request: {"kind": "released", "stage": "hypothesis"},
    )
    fake_store = MagicMock()
    fake_store.accepted_bounded_markdown.return_value = (
        "# DRAFT\n\nExact specialist body.\n\n## 独立证据审查\n"
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    def handler(inner: ModelRequest) -> ModelResponse:
        captured.append(inner)
        return ModelResponse(result=[AIMessage("Parent rewrite")])

    response = middleware.wrap_model_call(request, handler)

    assert captured[0].tools == []
    assert response.result[0].content.endswith("## 独立证据审查\n")
    fake_store.accepted_bounded_markdown.assert_called_once_with("hypothesis")


def test_hypothesis_result_recovers_single_unattributed_tool_message() -> None:
    call = {
        "name": "task",
        "args": {"subagent_type": "solar-hypothesis"},
        "id": "hypothesis-task",
    }
    messages = [
        HumanMessage("提出竞争假设", id="turn-hypothesis"),
        AIMessage("", tool_calls=[call]),
        {
            "type": "tool",
            "content": "# 科学假设组合\n\n研究者摘要",
            "tool_call_id": None,
            "status": None,
        },
    ]

    assert (
        _latest_specialist_result(messages, "solar-hypothesis")
        == "# 科学假设组合\n\n研究者摘要"
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
    assert "producer/reviewer loop is unavailable" in prepared.system_message.text


def test_full_research_route_advances_explicit_specialist_graph(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    task = _tool("task")
    human = HumanMessage("完成端到端研究", id="turn-1")
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "planning",
        "producer": "solar-planner",
        "phase": "planning",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    first = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[task]),
    )
    assert first.tool_choice is None
    assert [tool.name for tool in first.tools] == ["task"]
    assert "subagent_type='solar-planner'" in first.system_message.text

    fake_store.next_action.return_value = {
        "kind": "review",
        "stage": "planning",
        "review_mode": "planning",
        "artifact_refs": [],
    }
    second = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[task]),
    )
    assert second.tool_choice is None
    assert [tool.name for tool in second.tools] == ["task"]
    assert "subagent_type='solar-evidence'" in second.system_message.text


def test_full_research_cannot_fall_back_when_required_graph_tool_is_missing(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "planning",
        "producer": "solar-planner",
        "phase": "planning",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    prepared = _prepared(
        middleware,
        _request(route=route, tools=[_tool("read_file"), _tool("execute")]),
    )

    assert prepared.tools == []
    assert prepared.tool_choice is None


def test_full_research_model_prose_is_replaced_by_required_graph_node(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "planning",
        "producer": "solar-planner",
        "phase": "planning",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("task")]),
        lambda _inner: ModelResponse(result=[AIMessage("unreviewed draft")]),
    )

    call = response.result[0].tool_calls[0]
    assert call["name"] == "task"
    assert call["args"]["subagent_type"] == "solar-planner"


def test_full_research_missing_graph_tool_does_not_release_model_prose(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "planning",
        "producer": "solar-planner",
        "phase": "planning",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("read_file")]),
        lambda _inner: ModelResponse(result=[AIMessage("unreviewed draft")]),
    )

    assert "required task node solar-planner is unavailable" in str(
        response.result[0].content
    )
    assert "unreviewed draft" not in str(response.result[0].content)


def test_final_release_generation_keeps_only_original_question_and_latest_request(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "prepare_release",
        "stage": "final_release",
        "release_context": {
            "claims": [
                {
                    "claim_id": "accepted-claim",
                    "kind": "observation",
                    "text": "accepted integration claim",
                    "scope": "test",
                    "confidence": "low",
                }
            ],
            "required_limits": ["State the finite-sample limitation."],
        },
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )
    original = HumanMessage(
        "上一太阳活动周长度是否调制极区场对下一周期振幅的预测关系？",
        id="original-question",
    )
    latest = HumanMessage(
        "继续完成上述完整科研闭环。",
        id="latest-request",
    )
    prepared = _prepared(
        middleware,
        _request(
            route=route,
            messages=[
                original,
                AIMessage("old failed release draft with embedded JSON"),
                ToolMessage(
                    '{"status":"error","message":"verbatim validation failed"}',
                    tool_call_id="old-release-attempt",
                    name="research_release_prepare",
                ),
                HumanMessage("继续。", id="older-continuation"),
                AIMessage("another failed draft"),
                latest,
            ],
            tools=[_tool("research_release_prepare")],
        ),
    )

    assert prepared.messages == [original, latest]
    assert "concise scientific report" in str(prepared.system_message.content)
    assert "raw JSON" in str(prepared.system_message.content)


def test_full_research_prepare_release_routes_draft_through_gate(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "prepare_release",
        "stage": "final_release",
        "release_context": {
            "claims": [
                {
                    "claim_id": "accepted-claim",
                    "kind": "observation",
                    "text": "统一科研报告",
                    "scope": "test",
                    "confidence": "low",
                }
            ],
            "required_limits": [],
        },
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("research_release_prepare")]),
        lambda _inner: ModelResponse(result=[AIMessage("# 统一科研报告")]),
    )

    call = response.result[0].tool_calls[0]
    assert call["name"] == "research_release_prepare"
    assert call["args"]["draft_markdown"] == "# 统一科研报告"
    assert call["args"]["claim_citations"] == [
        {"claim_id": "accepted-claim", "draft_excerpt": "统一科研报告"}
    ]


def test_full_research_prepare_release_retries_stale_tool_calls_as_prose(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "prepare_release",
        "stage": "final_release",
        "release_context": {
            "claims": [
                {
                    "claim_id": "accepted-claim",
                    "kind": "observation",
                    "text": "统一科研报告",
                    "scope": "test",
                    "confidence": "low",
                }
            ],
            "required_limits": [],
        },
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )
    calls: list[ModelRequest] = []

    def handler(inner: ModelRequest) -> ModelResponse:
        calls.append(inner)
        if len(calls) == 1:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="I will inspect the prior files before drafting.",
                        tool_calls=[
                            {
                                "name": "read_file",
                                "args": {"path": "report.md"},
                                "id": "stale-read",
                            },
                            {"name": "ls", "args": {}, "id": "stale-ls"},
                        ],
                    )
                ]
            )
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "research_release_prepare",
                            "args": {
                                "draft_markdown": "# 统一科研报告",
                                "claim_citations": [
                                    {
                                        "claim_id": "accepted-claim",
                                        "draft_excerpt": "统一科研报告",
                                    }
                                ],
                            },
                            "id": "release-retry",
                        }
                    ],
                )
            ]
        )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("research_release_prepare")]),
        handler,
    )

    assert len(calls) == 2
    assert [_tool.name for _tool in calls[1].tools] == ["research_release_prepare"]
    assert "Do not call read_file" in str(calls[1].system_message.content)
    call = response.result[0].tool_calls[0]
    assert call["name"] == "research_release_prepare"
    assert call["args"]["draft_markdown"] == "# 统一科研报告"


def test_full_research_prepare_release_recovers_two_empty_silso_drafts(
    monkeypatch,
) -> None:
    """A completed SILSO run must not die only because Qwen returns two blanks."""

    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="local", needs_computation=True)
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "prepare_release",
        "stage": "final_release",
        "release_context": {
            "claims": [
                {
                    "claim_id": "planning-plan-v1",
                    "kind": "unknown",
                    "text": "Only completed SILSO v2.0 cycles 1-24 are in scope.",
                    "scope": "cycles 1-24",
                    "confidence": "high",
                },
                {
                    "claim_id": "hypothesis-output-v2",
                    "kind": "observation",
                    "text": "The rise-time relation supports the Waldmeier effect as a within-sample association only.",
                    "scope": "cycles 1-24",
                    "confidence": "high",
                },
                {
                    "claim_id": "experiment-result-v1",
                    "kind": "observation",
                    "text": (
                        "Verified results: cycle_length_pearson_r=-0.3242027946; "
                        "cycle_length_pearson_p=0.1222099081; "
                        "cycle_length_spearman_rho=-0.3138879473; "
                        "cycle_length_spearman_p=0.1352567203; "
                        "cycle_length_pearson_ci_low=-0.7057718028; "
                        "cycle_length_pearson_ci_high=0.0930280459; "
                        "cycle_length_spearman_ci_low=-0.6814427937; "
                        "cycle_length_spearman_ci_high=0.1337403080; "
                        "rise_time_pearson_r=-0.7494581458; "
                        "rise_time_pearson_p=2.497304927e-05; "
                        "rise_time_spearman_rho=-0.7618639497; "
                        "rise_time_spearman_p=1.521977248e-05; "
                        "rise_time_pearson_ci_low=-0.8834727462; "
                        "rise_time_pearson_ci_high=-0.5672438335; "
                        "rise_time_spearman_ci_low=-0.8866433451; "
                        "rise_time_spearman_ci_high=-0.5296511278; "
                        "decline_time_pearson_r=0.3826970436; "
                        "decline_time_pearson_p=0.0649325812; "
                        "decline_time_spearman_rho=0.3211489467; "
                        "decline_time_spearman_p=0.1259732115; "
                        "decline_time_pearson_ci_low=0.0551409292; "
                        "decline_time_pearson_ci_high=0.6414993707; "
                        "decline_time_spearman_ci_low=-0.1171426723; "
                        "decline_time_spearman_ci_high=0.6710780565; "
                        "complete_cycle_count=24; bootstrap_requested_repetitions=10000; "
                        "rise_leave_one_direction_stable=True"
                    ),
                    "scope": "cycles 1-24",
                    "confidence": "high",
                },
            ],
            "required_limits": [
                "All results describe statistical associations only; no cycle 26 prediction.",
                "Subgroups contain 12 cycles each and serial dependence is not modeled.",
            ],
        },
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )
    calls: list[ModelRequest] = []

    def handler(inner: ModelRequest) -> ModelResponse:
        calls.append(inner)
        return ModelResponse(result=[AIMessage(content="")])

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("research_release_prepare")]),
        handler,
    )

    assert len(calls) == 2
    call = response.result[0].tool_calls[0]
    assert call["name"] == "research_release_prepare"
    draft = call["args"]["draft_markdown"]
    assert "第 1—24 周" in draft
    assert "-0.7495" in draft
    assert "样本内描述性结论：高" in draft
    assert "第 26 周" in draft
    assert call["args"]["claim_citations"]


def test_bounded_data_model_prose_cannot_bypass_evidence_review(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="local",
        task_intent="data_preparation",
        required_specialist="solar-data",
    )
    fake_store = MagicMock()
    fake_store.bounded_stage_action.return_value = {
        "kind": "review",
        "stage": "data",
        "review_mode": "data",
        "artifact_refs": [],
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("task")]),
        lambda _inner: ModelResponse(result=[AIMessage("data looks fine")]),
    )

    call = response.result[0].tool_calls[0]
    assert call["name"] == "task"
    assert call["args"]["subagent_type"] == "solar-evidence"


def test_released_silso_data_uses_deterministic_final_markdown(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = {
        **_route(
            "verified_analysis",
            source_mode="local",
            task_intent="data_preparation",
            required_specialist="solar-data",
        ),
        "required_analysis_protocol": "silso_cycle_reproduction_v1",
    }
    fake_store = MagicMock()
    fake_store.bounded_stage_action.return_value = {
        "kind": "released",
        "stage": "data",
    }
    fake_store.accepted_bounded_markdown.return_value = "# 确定性 SILSO 结果"
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[]),
        lambda _inner: ModelResponse(result=[AIMessage("内部 revision 报告")]),
    )

    assert response.result[0].content == "# 确定性 SILSO 结果"
    fake_store.accepted_bounded_markdown.assert_called_with(
        "data", analysis_protocol="silso_cycle_reproduction_v1"
    )


def test_morphology_protocol_requires_full_research_route() -> None:
    prompt = (
        "请完成独立 SILSO 太阳活动周形态统计实验，生成 CSV、Markdown 和 PNG，"
        "完成 Pearson、Spearman、Bootstrap 与留一分析。"
    )
    route = _with_analysis_protocol(
        _fallback_route(prompt), text=prompt
    )
    assert route["required_analysis_protocol"] == "silso_cycle_morphology_v1"
    assert route["mode"] == "full_research"


def test_uploaded_polar_precursor_statistics_require_full_research_route() -> None:
    prompt = (
        "使用已经验证的上传 solar_precursor_cycle_features.csv，直接完成下游统计分析；"
        "报告 Pearson、Spearman、Bootstrap、留一和 MWO/WSO 分时期结果。"
    )
    route = _with_analysis_protocol(
        _fallback_route(prompt), text=prompt
    )
    assert route["required_analysis_protocol"] == "solar_polar_precursor_v1"
    assert route["mode"] == "full_research"


def test_bounded_stage_state_failure_does_not_release_model_prose(monkeypatch) -> None:
    route = _route(
        "verified_analysis",
        source_mode="local",
        task_intent="data_preparation",
        required_specialist="solar-data",
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _config: (_ for _ in ()).throw(RuntimeError("private state detail")),
    )

    response = _passthrough_accepted_bounded_stage(
        _request(route=route),
        ModelResponse(result=[AIMessage("unreviewed data result")]),
    )

    content = str(response.result[0].content)
    assert "RESEARCH REVIEW BLOCKED" in content
    assert "unreviewed data result" not in content
    assert "private state detail" not in content


def test_release_state_failure_does_not_release_model_prose(monkeypatch) -> None:
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _config: (_ for _ in ()).throw(RuntimeError("private state detail")),
    )

    response = _passthrough_accepted_release(
        _request(route=route),
        ModelResponse(result=[AIMessage("unreviewed final report")]),
    )

    content = str(response.result[0].content)
    assert "RESEARCH REVIEW BLOCKED" in content
    assert "unreviewed final report" not in content
    assert "private state detail" not in content


def test_accepted_release_delivery_marks_the_full_graph_released(monkeypatch) -> None:
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    fake_store = MagicMock()
    fake_store.accepted_release_markdown.return_value = "# Accepted report"
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = _passthrough_accepted_release(
        _request(route=route),
        ModelResponse(result=[AIMessage("model draft")]),
    )

    assert response.result[0].content == "# Accepted report"
    fake_store.mark_release_delivered.assert_called_once_with()


def test_hypothesis_state_failure_does_not_release_model_prose(monkeypatch) -> None:
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda _config: (_ for _ in ()).throw(RuntimeError("private state detail")),
    )

    response = _passthrough_hypothesis_result(
        _request(route=route),
        ModelResponse(result=[AIMessage("unreviewed hypothesis")]),
    )

    content = str(response.result[0].content)
    assert "RESEARCH REVIEW BLOCKED" in content
    assert "unreviewed hypothesis" not in content
    assert "private state detail" not in content


def test_bounded_hypothesis_rewrites_stale_producer_call_to_evidence(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_generation",
        required_specialist="solar-hypothesis",
    )
    fake_store = MagicMock()
    fake_store.bounded_hypothesis_action.return_value = {
        "kind": "review",
        "stage": "hypothesis",
        "review_mode": "hypothesis",
        "artifact_refs": [],
    }
    fake_store.bounded_stage_action.return_value = (
        fake_store.bounded_hypothesis_action.return_value
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("task")]),
        lambda _inner: ModelResponse(
            result=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-hypothesis"},
                            "id": "stale-producer-call",
                        }
                    ],
                )
            ]
        ),
    )

    call = response.result[0].tool_calls[0]
    assert call["name"] == "task"
    assert call["args"]["subagent_type"] == "solar-evidence"


def test_bounded_hypothesis_terminal_state_removes_stale_model_tool_call(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_update",
        required_specialist="solar-hypothesis",
    )
    fake_store = MagicMock()
    fake_store.bounded_hypothesis_action.return_value = {
        "kind": "terminal",
        "status": "blocked",
        "reason": "required evidence unavailable",
    }
    fake_store.bounded_stage_action.return_value = (
        fake_store.bounded_hypothesis_action.return_value
    )
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    response = middleware.wrap_model_call(
        _request(route=route, tools=[_tool("task")]),
        lambda _inner: ModelResponse(
            result=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-hypothesis"},
                            "id": "stale-terminal-call",
                        }
                    ],
                )
            ]
        ),
    )

    message = response.result[0]
    assert not message.tool_calls
    assert "status=blocked" in str(message.content)
    assert "do not claim release acceptance" in str(message.content)


def test_model_review_state_uses_active_runnable_config_when_runtime_has_none(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="mixed",
        task_intent="hypothesis_update",
        required_specialist="solar-hypothesis",
    )
    active_config = {
        "configurable": {
            "thread_id": "task-bound-thread",
            "workspace_thread_id": "task-bound-thread",
        }
    }
    seen: list[object] = []
    fake_store = MagicMock()
    fake_store.bounded_hypothesis_action.return_value = {
        "kind": "terminal",
        "status": "blocked",
    }
    fake_store.bounded_stage_action.return_value = (
        fake_store.bounded_hypothesis_action.return_value
    )
    monkeypatch.setattr("langgraph.config.get_config", lambda: active_config)
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config",
        lambda config: seen.append(config) or fake_store,
    )
    request = _request(route=route, tools=[_tool("task")]).override(
        runtime=SimpleNamespace()
    )

    response = middleware.wrap_model_call(
        request,
        lambda _inner: ModelResponse(result=[AIMessage("stale prose")]),
    )

    assert seen
    assert all(config is active_config for config in seen)
    assert "status=blocked" in str(response.result[0].content)


def test_bounded_data_route_forces_evidence_after_production(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route(
        "verified_analysis",
        source_mode="local",
        task_intent="data_preparation",
        required_specialist="solar-data",
    )
    fake_store = MagicMock()
    fake_store.bounded_stage_action.return_value = {
        "kind": "review",
        "stage": "data",
        "review_mode": "data",
        "artifact_refs": [],
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    prepared = _prepared(
        middleware,
        _request(route=route, tools=[_tool("task"), _tool("read_file")]),
    )

    assert [tool.name for tool in prepared.tools] == ["task"]
    assert "subagent_type='solar-evidence'" in prepared.system_message.text


def test_f107_full_research_inserts_verified_data_stage(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = {
        **_route("full_research", source_mode="mixed", needs_computation=True),
        "required_analysis_protocol": "f107_discontinuity_v1",
    }
    task = _tool("task")
    human = HumanMessage(
        "完成 F10.7 在 1980-1981 年不连续性的端到端研究",
        id="turn-f107",
    )
    messages = [
        human,
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "solar-planner"},
                    "id": "planner-f107",
                }
            ],
        ),
        ToolMessage("frozen", tool_call_id="planner-f107", name="task"),
    ]
    fake_store = MagicMock()
    fake_store.next_action.return_value = {
        "kind": "producer",
        "stage": "data",
        "producer": "solar-data",
        "phase": "data",
    }
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    prepared = _prepared(
        middleware,
        _request(route=route, messages=messages, tools=[task]),
    )

    assert "subagent_type='solar-data'" in prepared.system_message.text
    assert "bind_f107_dataset_semantics" in prepared.system_message.text
    assert "f107_relative_scale_jump" in prepared.system_message.text


def test_f107_hypothesis_route_does_not_require_data_receipt_before_specialist(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._workspace_verified_specialists",
        lambda _request, _protocol: set(),
    )
    route = {
        **_route(
            "verified_analysis",
            source_mode="mixed",
            needs_computation=True,
            task_intent="hypothesis_comparison",
            required_specialist="solar-hypothesis",
        ),
        "required_analysis_protocol": "f107_discontinuity_v1",
    }
    task = _tool("task")
    human = HumanMessage(
        "请生成 F10.7 在 1980 年前后不连续性的竞争假设",
        id="turn-f107-hypothesis",
    )

    prepared = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[task]),
    )

    assert [tool.name for tool in prepared.tools] == ["task"]
    assert "mandatory preliminary graph node is solar-data" not in (
        prepared.system_message.text
    )
    assert "Call task now with subagent_type='solar-hypothesis'" in (
        prepared.system_message.text
    )


def test_f107_hypothesis_task_is_rewritten_directly_to_specialist(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._persisted_hypothesis_draft_status",
        lambda _config: (True, "/task/work/scientific_hypothesis_state.json"),
    )
    monkeypatch.setattr(
        "jw.middleware.research_router._workspace_verified_specialists",
        lambda _request, _protocol: set(),
    )
    route = {
        **_route(
            "verified_analysis",
            source_mode="mixed",
            needs_computation=True,
            task_intent="hypothesis_generation",
            required_specialist="solar-hypothesis",
        ),
        "required_analysis_protocol": "f107_discontinuity_v1",
    }
    human = HumanMessage("请比较 F10.7 断点竞争假设", id="turn-f107-hypothesis")
    model_call = AIMessage(
        "",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "general-purpose"},
                "id": "task-f107-data",
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
            "verified data receipt",
            tool_call_id=str(inner.tool_call["id"]),
            name="task",
        )

    result = middleware.wrap_tool_call(request, handler)

    assert captured[0].tool_call["args"]["subagent_type"] == "solar-hypothesis"
    description = captured[0].tool_call["args"]["description"]
    assert "bind_f107_dataset_semantics" not in description
    assert "scenario premises in the request are assumptions" in description
    assert result.additional_kwargs["research_router_specialist"] == "solar-hypothesis"


def test_f107_hypothesis_route_delegates_specialist_after_data_receipt(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    monkeypatch.setattr(
        "jw.middleware.research_router._workspace_verified_specialists",
        lambda _request, _protocol: {"solar-data"},
    )
    route = {
        **_route(
            "verified_analysis",
            source_mode="mixed",
            needs_computation=True,
            task_intent="hypothesis_generation",
            required_specialist="solar-hypothesis",
        ),
        "required_analysis_protocol": "f107_discontinuity_v1",
    }

    prepared = _prepared(
        middleware,
        _request(route=route, tools=[_tool("task")]),
    )

    assert [tool.name for tool in prepared.tools] == ["task"]
    assert "subagent_type='solar-hypothesis'" in prepared.system_message.text


def test_f107_verified_analysis_binds_semantics_before_computation(
    monkeypatch,
) -> None:
    middleware = _middleware(monkeypatch)
    route = {
        **_route("verified_analysis", source_mode="local", needs_computation=True),
        "required_analysis_protocol": "f107_discontinuity_v1",
    }
    messages = [
        HumanMessage("分析 F10.7 的 1980 年断点", id="turn-f107"),
        AIMessage(
            "",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/inputs/f107.csv"},
                    "id": "read-f107",
                }
            ],
        ),
        ToolMessage("data", tool_call_id="read-f107", name="read_file"),
    ]

    prepared = _prepared(
        middleware,
        _request(
            route=route,
            messages=messages,
            tools=[_tool("bind_f107_dataset_semantics"), _tool("execute")],
        ),
    )

    assert [tool.name for tool in prepared.tools] == ["bind_f107_dataset_semantics"]
    assert "F10.7 as the response" in prepared.system_message.text


def test_terminal_research_state_suppresses_tools(monkeypatch) -> None:
    middleware = _middleware(monkeypatch)
    route = _route("full_research", source_mode="mixed", needs_computation=True)
    human = HumanMessage("完成端到端研究", id="turn-1")
    fake_store = MagicMock()
    fake_store.next_action.return_value = {"kind": "terminal", "status": "blocked"}
    monkeypatch.setattr(
        "jw.middleware.research_router.store_from_config", lambda _config: fake_store
    )

    prepared = _prepared(
        middleware,
        _request(route=route, messages=[human], tools=[_tool("task")]),
    )

    assert prepared.tool_choice is None
    assert prepared.tools == []
    assert "research run is blocked" in prepared.system_message.text


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

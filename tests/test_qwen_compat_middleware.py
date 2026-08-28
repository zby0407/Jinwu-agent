"""Qwen model/tool compatibility regression tests."""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain.agents.structured_output import ProviderStrategy
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from jw.middleware.qwen_compat import (
    QWEN_TOOL_USE_PROMPT,
    QwenToolCompatibilityMiddleware,
    QwenToolSchemaError,
    is_qwen_model,
    validate_qwen_tool_schema,
)
from jw.tools.research_review import evidence_review_submit_round


@tool
def read_dataset(path: str) -> str:
    """Read a dataset."""
    return path


def _request(*tools, messages=None, model=None, tool_choice=None):
    request = MagicMock()
    request.tools = list(tools)
    request.messages = list(messages or [])
    request.system_message = SystemMessage(content="base")
    request.model = model or ChatOpenAI(model="qwen3.7-plus", api_key="test-key")
    request.tool_choice = tool_choice
    request.response_format = None
    request.model_settings = {}

    def override(**kwargs):
        updated = MagicMock()
        updated.tools = kwargs.get("tools", request.tools)
        updated.messages = kwargs.get("messages", request.messages)
        updated.system_message = kwargs.get("system_message", request.system_message)
        updated.model = kwargs.get("model", request.model)
        updated.tool_choice = kwargs.get("tool_choice", request.tool_choice)
        updated.response_format = kwargs.get("response_format", request.response_format)
        updated.model_settings = kwargs.get("model_settings", request.model_settings)
        updated.override.side_effect = override
        return updated

    request.override.side_effect = override
    return request


@pytest.mark.parametrize(
    "model_name",
    [
        "qwen3.7-plus",
        "QWEN3-CODER",
        "qwen/qwen3.7-plus",
        "qwq-plus",
        "qwen/qwq-32b",
    ],
)
def test_qwen_model_detection_covers_qwen_family_ids(model_name):
    assert is_qwen_model(model_name)


def test_qwen_model_detection_rejects_other_families():
    assert not is_qwen_model("anthropic/claude-sonnet-4.6")


def test_qwen_accepts_portable_tool_names():
    assert validate_qwen_tool_schema([read_dataset]) == ("read_dataset",)


@pytest.mark.parametrize("reserved", ["code_interpreter", "search"])
def test_qwen_rejects_provider_reserved_custom_function_names(reserved):
    with pytest.raises(QwenToolSchemaError, match=reserved):
        validate_qwen_tool_schema([{"name": reserved}])


def test_qwen_rejects_invalid_function_name_before_remote_call():
    with pytest.raises(QwenToolSchemaError, match="letters, numbers"):
        validate_qwen_tool_schema([{"name": "read.dataset"}])


def test_middleware_uses_runtime_qwen_override():
    middleware = QwenToolCompatibilityMiddleware(default_model="claude-sonnet-4-6")
    handler = MagicMock(return_value="unreachable")
    request = _request({"name": "code_interpreter"})

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=("qwen3.7-plus", "openrouter"),
    ):
        with pytest.raises(QwenToolSchemaError, match="code_interpreter"):
            middleware.wrap_model_call(request, handler)
    handler.assert_not_called()


def test_middleware_does_not_apply_qwen_reserved_names_to_non_qwen_model():
    middleware = QwenToolCompatibilityMiddleware(default_model="claude-sonnet-4-6")
    handler = MagicMock(return_value="ok")
    request = _request(
        {"name": "code_interpreter"},
        model=ChatAnthropic(model="claude-sonnet-4-6", api_key="test-key"),
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"
    handler.assert_called_once_with(request)
    request.override.assert_not_called()


def test_runtime_subagent_model_takes_precedence_over_parent_qwen_override():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.8-max")
    handler = MagicMock(return_value="kimi-response")
    kimi_model = ChatAnthropic(
        model="kimi-for-coding",
        api_key="test-key",
        base_url="https://api.kimi.com/coding/",
    )
    request = _request(
        {"name": "code_interpreter"},
        model=kimi_model,
        messages=[HumanMessage(content="review the artifact")],
    )
    request.system_message = SystemMessage(content="[EVIDENCE_REVIEW_V2]")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=("qwen3.8-max", "custom-openai"),
    ):
        assert middleware.wrap_model_call(request, handler) == "kimi-response"

    handler.assert_called_once_with(request)
    request.override.assert_not_called()


def test_middleware_injects_qwen_evidence_contract():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    handler = MagicMock(return_value="ok")
    request = _request(read_dataset)

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared is not request
    rendered = "\n".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in prepared.system_message.content
    )
    assert QWEN_TOOL_USE_PROMPT in rendered
    assert "base" in rendered
    assert "only" in rendered
    assert "unverified reason as unresolved" in rendered
    assert "self-contained handoff" in rendered
    assert "artifact_manifest" in rendered
    assert "never copy its" in rendered
    assert "rows or numeric values" in rendered


@pytest.mark.parametrize(
    "tool_choice",
    [
        "required",
        {"type": "function", "function": {"name": "read_dataset"}},
    ],
)
def test_middleware_disables_qwen_thinking_for_forced_tool_choice(tool_choice):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, tool_choice=tool_choice)
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == tool_choice
    assert prepared.model is not request.model
    assert prepared.model.extra_body["enable_thinking"] is False


def test_forced_tool_choice_removes_stale_qwen_thinking_controls():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    model = ChatOpenAI(
        model="qwen3.7-plus",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        extra_body={
            "enable_thinking": True,
            "thinking_budget": 2048,
            "preserve_thinking": True,
        },
    )
    request = _request(read_dataset, model=model, tool_choice="required")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    assert handler.call_args.args[0].model.extra_body == {"enable_thinking": False}


def test_middleware_keeps_qwen_thinking_available_for_auto_tool_choice():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, tool_choice="auto")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.model is not request.model
    assert prepared.model.extra_body == {
        "enable_thinking": True,
        "thinking_budget": 4096,
        "preserve_thinking": True,
    }


def test_source_restricted_morphology_hypothesis_hides_discovery_tools():
    """The independent SILSO task must consume accepted A2A facts and converge."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        {"name": "kb_query"},
        {"name": "kb_read"},
        {"name": "read_file"},
        {"name": "write_todos"},
        {"name": "execute"},
        {"name": "lit_bundle_read"},
        {"name": "scientific_hypothesis_build_literature_bundle"},
        {"name": "scientific_hypothesis_build_novelty_bundle"},
        {"name": "scientific_hypothesis_bind_request"},
        {"name": "scientific_hypothesis_bind_evidence"},
        {"name": "scientific_hypothesis_update_draft"},
        messages=[HumanMessage(content="continue the bounded hypothesis stage")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\n"
            "stage=hypothesis\n"
            'analysis_protocol="silso_cycle_morphology_v1"\n'
            "Use only the accepted SILSO result capsule."
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    names = {tool["name"] for tool in prepared.tools}
    assert names == {
        "scientific_hypothesis_bind_request",
        "scientific_hypothesis_bind_evidence",
        "scientific_hypothesis_update_draft",
    }
    rendered = " ".join(
        str(prepared.system_message.content).replace("\\n", " ").split()
    )
    assert "source-restricted statistical task" in rendered
    assert "Do not call knowledge discovery tools" in rendered
    assert "one candidate per preregistered relationship" in rendered
    assert "Do not bind the same accepted excerpt twice" in rendered
    assert "A high within-sample descriptive confidence" in rendered
    assert "assign high to the rise-time within-sample descriptive claim" in rendered


def test_source_restricted_sc26_backtest_hypothesis_hides_discovery_tools():
    """The fixed SC26 backtest must not expand into an unrelated KB loop."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        {"name": "kb_query"},
        {"name": "kb_read"},
        {"name": "read_file"},
        {"name": "write_todos"},
        {"name": "execute"},
        {"name": "scientific_hypothesis_bind_request"},
        {"name": "scientific_hypothesis_bind_evidence"},
        {"name": "scientific_hypothesis_update_draft"},
        {"name": "scientific_hypothesis_get_draft"},
        {"name": "scientific_hypothesis_review_tail"},
        {"name": "scientific_hypothesis_checkpoint_draft"},
        messages=[HumanMessage(content="continue the fixed SC26 backtest stage")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\n"
            "stage=hypothesis\n"
            'analysis_protocol="solar_cycle_26_forecast_backtest_v1"\n'
            "Use only the accepted forecast/backtest result capsule."
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    names = {tool["name"] for tool in prepared.tools}
    assert names == {
        "scientific_hypothesis_bind_request",
        "scientific_hypothesis_bind_evidence",
        "scientific_hypothesis_update_draft",
        "scientific_hypothesis_get_draft",
        "scientific_hypothesis_review_tail",
        "scientific_hypothesis_checkpoint_draft",
    }
    rendered = " ".join(str(prepared.system_message.content).replace("\n", " ").split())
    assert "source-restricted statistical task" in rendered
    assert "historical backtest skill" in rendered


def test_source_restricted_morphology_hypothesis_uses_host_prebound_seed():
    """After the host bind receipt, the model cannot re-enter substring binding."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    receipt = json.dumps(
        {
            "schema_version": "scientific-hypothesis-brief-v1",
            "prebound_evidence_count": 3,
            "prebound_evidence_ids_by_relationship": {
                "cycle_length_peak": "cycle_length_vs_peak_full_stats",
                "rise_time_peak": "rise_time_vs_peak_full_stats",
                "decline_time_peak": "decline_time_vs_peak_full_stats",
            },
            "next_required_action": {
                "tool": "scientific_hypothesis_update_draft",
                "operation": "upsert_candidate",
            },
        }
    )
    request = _request(
        {"name": "scientific_hypothesis_bind_request"},
        {"name": "scientific_hypothesis_bind_evidence"},
        {"name": "scientific_hypothesis_update_draft"},
        {"name": "scientific_hypothesis_get_draft"},
        messages=[
            HumanMessage(content="continue the bounded hypothesis stage"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "scientific_hypothesis_bind_request",
                        "args": {
                            "request_input": "@work/research_quality/hypothesis_request.json"
                        },
                        "id": "bind-1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content=receipt,
                tool_call_id="bind-1",
                name="scientific_hypothesis_bind_request",
            ),
        ],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\n"
            "stage=hypothesis\n"
            'analysis_protocol="silso_cycle_morphology_v1"\n'
            "Use only the accepted SILSO result capsule."
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    names = {tool["name"] for tool in prepared.tools}
    assert "scientific_hypothesis_bind_request" not in names
    assert "scientific_hypothesis_bind_evidence" not in names
    assert "scientific_hypothesis_update_draft" in names


def test_final_release_generation_exposes_only_release_gate_tool():
    """Late middleware must not reintroduce filesystem/todo tools at release."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        {"name": "read_file"},
        {"name": "write_todos"},
        {"name": "research_release_prepare"},
        messages=[HumanMessage(content="complete the accepted research report")],
    )
    request.system_message = SystemMessage(
        content=(
            "ResearchRunStateV2\n"
            "<research_route>\n"
            'Deterministic next_action={"kind":"prepare_release",'
            '"stage":"final_release"}\n'
            "</research_route>"
        )
    )
    handler = MagicMock(
        return_value=ModelResponse(result=[AIMessage(content="release draft")])
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        middleware.wrap_model_call(request, handler)

    prepared = handler.call_args.args[0]
    assert [tool["name"] for tool in prepared.tools] == ["research_release_prepare"]
    assert prepared.tool_choice is None
    assert prepared.model_settings["parallel_tool_calls"] is False
    rendered = str(prepared.system_message.content)
    assert "only remaining action is the final release gate" in rendered
    assert "Do not call read_file" in rendered


def test_middleware_preserves_only_latest_actionable_qwen_reasoning_round():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    messages = [
        HumanMessage(content="repair the persisted plan"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "old abandoned route"},
            tool_calls=[
                {
                    "name": "read_dataset",
                    "args": {"path": "old.csv"},
                    "id": "call-old",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="old result", tool_call_id="call-old", name="read_dataset"),
        AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "current bounded action"},
            tool_calls=[
                {
                    "name": "read_dataset",
                    "args": {"path": "current.csv"},
                    "id": "call-current",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="current result",
            tool_call_id="call-current",
            name="read_dataset",
        ),
    ]
    request = _request(read_dataset, messages=messages, tool_choice="auto")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared_messages = handler.call_args.args[0].messages
    assert "reasoning_content" not in prepared_messages[1].additional_kwargs
    assert prepared_messages[3].additional_kwargs["reasoning_content"] == (
        "current bounded action"
    )


@pytest.mark.parametrize(
    "system_text",
    [
        "[RESEARCH_PRODUCER_V2]\nstage=data",
        "[EVIDENCE_REVIEW_V2]\nreview_mode=planning",
    ],
)
def test_qwen_closed_loop_uses_native_thinking_without_think_tool(system_text):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request({"name": "think_tool"}, read_dataset)
    request.system_message = SystemMessage(content=system_text)
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert [
        tool.name if hasattr(tool, "name") else tool["name"] for tool in prepared.tools
    ] == ["read_dataset"]
    assert prepared.model.extra_body["enable_thinking"] is True


def test_middleware_preserves_explicit_non_thinking_structured_model():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    model = ChatOpenAI(
        model="qwen3.7-plus",
        api_key="test-key",
        extra_body={"enable_thinking": False},
    )
    request = _request(read_dataset, model=model, tool_choice=None)
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.model.extra_body["enable_thinking"] is False
    assert "thinking_budget" not in prepared.model.extra_body


def test_middleware_compacts_oversized_tool_traceback_for_qwen():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    traceback = (
        "[TOOL ERROR] Tool failed\n"
        + ("traceback frame\n" * 200)
        + "openai.APIConnectionError: Connection error."
    )
    tool_error = ToolMessage(
        traceback,
        tool_call_id="call_failed",
        name="task",
    )
    request = _request(read_dataset, messages=[tool_error])
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared_error = handler.call_args.args[0].messages[0]
    assert prepared_error.content.startswith("[TOOL ERROR CAPSULE]")
    assert "openai.APIConnectionError: Connection error." in prepared_error.content
    assert "traceback frame" not in prepared_error.content
    assert "fingerprint=" in prepared_error.content


@pytest.mark.parametrize(
    ("system_text", "expected_budget"),
    [
        ("You are the independent Evidence Reviewer.", 8192),
        ("[RESEARCH_PRODUCER_V2]\nstage=planning", 1536),
        ("[RESEARCH_PRODUCER_V2]\nstage=data", 4096),
        ("[RESEARCH_PRODUCER_V2] bounded revision", 6144),
        ("Follow the ResearchRunStateV2 full_research graph.", 1024),
    ],
)
def test_middleware_assigns_phase_specific_qwen_thinking_budget(
    system_text, expected_budget
):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, tool_choice="auto")
    request.system_message = SystemMessage(content=system_text)
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.model.extra_body["thinking_budget"] == expected_budget


def test_planner_turn_disables_parallel_tools_and_serializes_provider_violation():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        {"name": "research_planner_get_section"},
        {"name": "research_planner_stage_revision_section"},
    )
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=planning"
    )
    handler = MagicMock(
        return_value=ModelResponse(
            result=[
                AIMessage(
                    content="I will update both sections.",
                    tool_calls=[
                        {
                            "name": "research_planner_get_section",
                            "args": {"section_name": "scope"},
                            "id": "call-read",
                            "type": "tool_call",
                        },
                        {
                            "name": "research_planner_stage_revision_section",
                            "args": {
                                "section_name": "scope",
                                "section_json": "{}",
                            },
                            "id": "call-write",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    prepared = handler.call_args.args[0]
    assert prepared.model_settings["parallel_tool_calls"] is False
    assert [call["id"] for call in response.result[0].tool_calls] == ["call-read"]
    assert response.result[0].content == ""
    assert response.result[0].response_metadata["jw_deferred_parallel_tool_calls"] == 1


def test_planner_serializes_duplicate_compact_plan_calls():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request({"name": "research_planner_create_empirical_plan"})
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=planning"
    )
    handler = MagicMock(
        return_value=ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "research_planner_create_empirical_plan",
                            "args": {"compact_plan_json": "{}"},
                            "id": "call-first",
                            "type": "tool_call",
                        },
                        {
                            "name": "research_planner_create_empirical_plan",
                            "args": {"compact_plan_json": "{}"},
                            "id": "call-second",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert [call["id"] for call in response.result[0].tool_calls] == ["call-first"]


@pytest.mark.parametrize(
    ("receipt_tool", "next_action", "expected_tool"),
    [
        (
            "research_planner_stage_revision_section",
            "commit_revision_candidate",
            "research_planner_commit_revision_candidate",
        ),
        (
            "research_planner_update_draft",
            "validate_draft",
            "research_planner_validate_draft",
        ),
        (
            "research_planner_validate_draft",
            "freeze_plan",
            "research_planner_freeze_plan",
        ),
    ],
)
def test_planner_deterministic_checkpoint_synthesizes_local_transition(
    receipt_tool, next_action, expected_tool
):
    """Argument-free planner edges execute locally inside the sub-agent.

    The Supervisor cannot close out a draft until the specialist returns, so
    validate/freeze must share the commit path instead of consuming remote Qwen
    calls or looping behind the task boundary.
    """
    tools = [
        {"name": "research_planner_get_brief"},
        {"name": "research_planner_commit_revision_candidate"},
        {"name": "research_planner_validate_draft"},
        {"name": "research_planner_freeze_plan"},
    ]
    request = _request(
        *tools,
        messages=[
            ToolMessage(
                content=json.dumps({"draft_checkpoint": {"next_action": next_action}}),
                tool_call_id="call-brief",
                name=receipt_tool,
            )
        ],
        tool_choice="auto",
    )
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=planning"
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = QwenToolCompatibilityMiddleware(
            default_model="qwen3.7-plus"
        ).wrap_model_call(request, handler)

    handler.assert_not_called()
    [message] = response.result
    assert [call["name"] for call in message.tool_calls] == [expected_tool]
    assert message.tool_calls[0]["args"] == {"request_sha256": ""}


def test_forced_tool_choice_strips_all_reasoning_content_from_history():
    """A forced ``tool_choice`` transition must not replay ANY reasoning
    channel, including the newest tool-call round that
    ``_bound_reasoning_history`` would otherwise keep. Uses a tool outside the
    planner deterministic map so the request still reaches the remote model."""
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    tools = [{"name": "research_planner_get_brief"}]
    request = _request(
        *tools,
        messages=[
            HumanMessage(content="continue"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "research_planner_get_brief",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
                additional_kwargs={"reasoning_content": "old draft plan"},
            ),
            ToolMessage(
                content=json.dumps({"draft_checkpoint": {"next_action": "draft"}}),
                tool_call_id="call-1",
                name="research_planner_get_brief",
            ),
        ],
        tool_choice={
            "type": "function",
            "function": {"name": "research_planner_get_brief"},
        },
    )
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=planning"
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.model.extra_body["enable_thinking"] is False
    for message in prepared.messages:
        assert "reasoning_content" not in message.additional_kwargs


def test_fresh_empirical_planner_exposes_only_single_compact_generation_tool():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    tools = [
        {"name": "research_planner_get_brief"},
        {"name": "research_planner_create_empirical_plan"},
        {"name": "research_planner_update_draft"},
    ]
    request = _request(
        *tools,
        messages=[
            ToolMessage(
                content=json.dumps(
                    {
                        "recommended_next_tool": (
                            "research_planner_create_empirical_plan"
                        ),
                        "draft_checkpoint": {
                            "completed_sections": [],
                            "missing_sections": ["scope"],
                        },
                    }
                ),
                tool_call_id="call-brief",
                name="research_planner_get_brief",
            )
        ],
        tool_choice="auto",
    )
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=planning"
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice is None
    assert [tool["name"] for tool in prepared.tools] == [
        "research_planner_create_empirical_plan"
    ]
    assert "Call research_planner_create_empirical_plan exactly once" in str(
        prepared.system_message.content
    )


def test_data_stage_forces_context_open_before_any_data_tool():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_precursor_cycle_table"},
    ]
    request = _request(*tools, messages=[HumanMessage(content="prepare data")])
    request.system_message = SystemMessage(content="[RESEARCH_PRODUCER_V2]\nstage=data")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == {
        "type": "function",
        "function": {"name": "solar_data_open_context"},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


def test_data_stage_synthesizes_context_open_when_route_is_always_thinking():
    request = _request(
        {"name": "solar_data_open_context"},
        messages=[HumanMessage(content="prepare data")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            "required_analysis_protocol=solar_polar_precursor_v1"
        )
    )
    handler = MagicMock(
        side_effect=RuntimeError(
            "The tool_choice parameter does not support being set to required "
            "or object in thinking mode"
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = QwenToolCompatibilityMiddleware(
            default_model="qwen3.7-plus"
        ).wrap_model_call(request, handler)

    [message] = response.result
    assert message.tool_calls == [
        {
            "name": "solar_data_open_context",
            "args": {"analysis_protocol": "solar_polar_precursor_v1"},
            "id": "local_data_solar_data_open_context",
            "type": "tool_call",
        }
    ]


@pytest.mark.asyncio
async def test_async_data_stage_synthesizes_curated_adapter_after_rejection():
    context = {
        "status": "inputs_available",
        "must_stop": False,
        "required_data_product": "solar_polar_precursor_table_v1",
        "eligible_inputs": [
            {
                "dataset_id": "silso-monthly-total-v2",
                "path": "/project/shared/silso.txt",
            },
            {
                "dataset_id": "mwo-wso-polar-field-v2",
                "path": "/project/shared/polar.csv",
            },
        ],
    }
    request = _request(
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_precursor_cycle_table"},
        messages=[HumanMessage(content="prepare data")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}"
        )
    )
    handler = AsyncMock(
        side_effect=RuntimeError(
            "The tool_choice parameter does not support being set to required "
            "or object in thinking mode"
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = await QwenToolCompatibilityMiddleware(
            default_model="qwen3.7-plus"
        ).awrap_model_call(request, handler)

    [message] = response.result
    assert message.tool_calls[0]["name"] == "prepare_solar_precursor_cycle_table"
    assert message.tool_calls[0]["args"] == {
        "sunspot_path": "/project/shared/silso.txt",
        "polar_field_path": "/project/shared/polar.csv",
    }


@pytest.mark.asyncio
async def test_async_qwen_model_call_retries_one_truncated_stream():
    class RemoteProtocolError(Exception):
        pass

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, messages=[HumanMessage(content="continue")])
    recovered = ModelResponse(result=[AIMessage(content="recovered")])
    handler = AsyncMock(
        side_effect=[
            RemoteProtocolError(
                "peer closed connection without sending complete message body"
            ),
            recovered,
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = await middleware.awrap_model_call(request, handler)

    assert response.result == recovered.result
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_async_qwen_model_call_retries_one_total_wall_timeout():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, messages=[HumanMessage(content="continue")])
    recovered = ModelResponse(result=[AIMessage(content="recovered")])
    call_count = 0

    async def handler(_request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(1)
        return recovered

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        patch(
            "jw.middleware.qwen_compat._dashscope_request_timeout",
            return_value=0.01,
        ),
    ):
        response = await middleware.awrap_model_call(request, handler)

    assert response.result == recovered.result
    assert call_count == 2


@pytest.mark.asyncio
async def test_async_qwen_model_call_stops_after_two_total_wall_timeout_retries():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, messages=[HumanMessage(content="continue")])
    call_count = 0

    async def handler(_request):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        patch(
            "jw.middleware.qwen_compat._dashscope_request_timeout",
            return_value=0.01,
        ),
        patch(
            "jw.middleware.qwen_compat._sleep_before_qwen_retry",
            new_callable=AsyncMock,
        ) as retry_sleep,
        pytest.raises(TimeoutError),
    ):
        await middleware.awrap_model_call(request, handler)

    assert call_count == 3
    assert retry_sleep.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_name", "failure"),
    [
        ("gpt-5.5", RuntimeError("peer closed connection")),
        ("qwen3.7-plus", ValueError("domain validation failed")),
    ],
)
async def test_async_model_retry_excludes_non_qwen_or_domain_errors(
    model_name, failure
):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        read_dataset,
        model=ChatOpenAI(model=model_name, api_key="test-key"),
        messages=[HumanMessage(content="continue")],
    )
    handler = AsyncMock(side_effect=failure)

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        pytest.raises(type(failure), match=str(failure)),
    ):
        await middleware.awrap_model_call(request, handler)

    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_async_qwen_model_retry_allows_two_transient_transport_retries():
    class APIConnectionError(Exception):
        pass

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(read_dataset, messages=[HumanMessage(content="continue")])
    handler = AsyncMock(
        side_effect=[
            APIConnectionError("first"),
            APIConnectionError("second"),
            ModelResponse(result=[AIMessage(content="recovered")]),
        ]
    )

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        patch(
            "jw.middleware.qwen_compat.asyncio.sleep",
            new_callable=AsyncMock,
        ) as retry_sleep,
    ):
        response = await middleware.awrap_model_call(request, handler)

    assert response.result[0].content == "recovered"
    assert handler.await_count == 3
    assert retry_sleep.await_count == 2
    assert retry_sleep.await_args_list[0].args == (20.0,)
    assert retry_sleep.await_args_list[1].args == (20.0,)


@pytest.mark.asyncio
async def test_async_qwen_thinking_tool_choice_rejection_retries_without_forced_choice():
    """Nested Qwen agents must recover when no research-stage marker is present."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        read_dataset,
        messages=[HumanMessage(content="read the registered dataset")],
        tool_choice={
            "type": "function",
            "function": {"name": "read_dataset"},
        },
    )
    recovered = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_dataset",
                        "args": {"path": "data.csv"},
                        "id": "call-1",
                    }
                ],
            )
        ]
    )
    handler = AsyncMock(
        side_effect=[
            RuntimeError(
                "The tool_choice parameter does not support being set to required "
                "or object in thinking mode"
            ),
            recovered,
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = await middleware.awrap_model_call(request, handler)

    assert response.result == recovered.result
    assert handler.await_count == 2
    safe_request = handler.await_args_list[1].args[0]
    assert safe_request.tool_choice is None
    assert [
        item.name if hasattr(item, "name") else item.get("name")
        for item in safe_request.tools
    ] == ["read_dataset"]
    rendered = "\n".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in safe_request.system_message.content
    )
    assert "forced tool_choice" in rendered


@pytest.mark.asyncio
async def test_async_qwen_retries_when_bound_model_hides_forced_choice():
    """The agent factory may add tool_choice while binding the model downstream."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        read_dataset,
        messages=[HumanMessage(content="read the registered dataset")],
        tool_choice=None,
    )
    recovered = ModelResponse(result=[AIMessage(content="done")])
    handler = AsyncMock(
        side_effect=[
            RuntimeError(
                "The tool_choice parameter does not support being set to required "
                "or object in thinking mode"
            ),
            recovered,
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = await middleware.awrap_model_call(request, handler)

    assert response.result == recovered.result
    assert handler.await_count == 2
    safe_request = handler.await_args_list[1].args[0]
    assert safe_request.tool_choice is None
    assert [item.name for item in safe_request.tools] == ["read_dataset"]


def test_data_stage_routes_curated_precursor_inputs_to_specialized_adapter():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_precursor_cycle_table"},
    ]
    context = ToolMessage(
        content=json.dumps(
            {
                "status": "inputs_available",
                "must_stop": False,
                "required_data_product": "solar_polar_precursor_table_v1",
                "eligible_inputs": [
                    {
                        "dataset_id": "silso-monthly-total-v2",
                        "path": "/project/shared/silso.txt",
                    },
                    {
                        "dataset_id": "mwo-wso-polar-field-v2",
                        "path": "/project/shared/polar.csv",
                    },
                ],
            }
        ),
        tool_call_id="call-open",
        name="solar_data_open_context",
    )
    request = _request(*tools, messages=[HumanMessage(content="prepare data"), context])
    request.system_message = SystemMessage(content="[RESEARCH_PRODUCER_V2]\nstage=data")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == {
        "type": "function",
        "function": {"name": "prepare_solar_precursor_cycle_table"},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


def test_data_stage_synthesizes_cycle_26_readiness_adapter_after_rejection():
    dataset_paths = {
        "silso-monthly-total-v2": "/project/silso.txt",
        "silso-monthly-smoothed-v2": "/project/smoothed.csv",
        "silso-cycle-extrema-v2": "/project/extrema.txt",
        "noaa-swpc-monthly-f107-v1": "/project/f107.json",
        "mwo-wso-polar-field-v2": "/project/historical-polar.csv",
        "wso-current-polar-field-v1": "/project/current-polar.html",
    }
    context = {
        "status": "inputs_available",
        "must_stop": False,
        "analysis_protocol": "solar_cycle_26_readiness_v1",
        "required_data_product": "solar_cycle_26_readiness_inventory_v1",
        "eligible_inputs": [
            {"dataset_id": dataset_id, "path": path}
            for dataset_id, path in dataset_paths.items()
        ],
    }
    request = _request(
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_cycle_26_readiness"},
        messages=[HumanMessage(content="prepare readiness evidence")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}"
        )
    )
    handler = MagicMock(
        side_effect=RuntimeError(
            "The tool_choice parameter does not support being set to required "
            "or object in thinking mode"
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = QwenToolCompatibilityMiddleware(
            default_model="qwen3.7-plus"
        ).wrap_model_call(request, handler)

    [message] = response.result
    [call] = message.tool_calls
    assert call["name"] == "prepare_solar_cycle_26_readiness"
    assert call["args"] == {
        "monthly_total_path": dataset_paths["silso-monthly-total-v2"],
        "smoothed_path": dataset_paths["silso-monthly-smoothed-v2"],
        "official_extrema_path": dataset_paths["silso-cycle-extrema-v2"],
        "f107_path": dataset_paths["noaa-swpc-monthly-f107-v1"],
        "historical_polar_path": dataset_paths["mwo-wso-polar-field-v2"],
        "current_polar_path": dataset_paths["wso-current-polar-field-v1"],
        "cutoff_date": "2026-06-30",
    }


def test_data_stage_synthesizes_cycle_26_forecast_backtest_adapter_after_rejection():
    dataset_paths = {
        "silso-monthly-total-v2": "/project/silso.txt",
        "silso-monthly-smoothed-v2": "/project/smoothed.csv",
        "silso-cycle-extrema-v2": "/project/extrema.txt",
    }
    context = {
        "status": "inputs_available",
        "must_stop": False,
        "analysis_protocol": "solar_cycle_26_forecast_backtest_v1",
        "required_data_product": "solar_cycle_26_forecast_backtest_v1",
        "eligible_inputs": [
            {"dataset_id": dataset_id, "path": path}
            for dataset_id, path in dataset_paths.items()
        ],
    }
    request = _request(
        {"name": "solar_data_open_context"},
        {"name": "run_solar_cycle_26_historical_forecast"},
        messages=[HumanMessage(content="run the historical backtest and forecast")],
    )
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}"
        )
    )
    handler = MagicMock(
        side_effect=RuntimeError(
            "The tool_choice parameter does not support being set to required "
            "or object in thinking mode"
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = QwenToolCompatibilityMiddleware(
            default_model="qwen3.7-plus"
        ).wrap_model_call(request, handler)

    [message] = response.result
    [call] = message.tool_calls
    assert call["name"] == "run_solar_cycle_26_historical_forecast"
    assert call["args"] == {
        "monthly_total_path": dataset_paths["silso-monthly-total-v2"],
        "smoothed_path": dataset_paths["silso-monthly-smoothed-v2"],
        "official_extrema_path": dataset_paths["silso-cycle-extrema-v2"],
    }


@pytest.mark.parametrize("receipt_status", ["verified", "partial", "error"])
def test_data_stage_suppresses_all_tools_only_after_verified_precursor_table(
    receipt_status: str,
):
    """The inline Data subagent stops only for a verified canonical table."""

    tools = [
        {"name": "prepare_solar_precursor_cycle_table"},
        {"name": "solar_research_analysis"},
        {"name": "dataset_statistics"},
        {"name": "audit_solar_data_quality"},
    ]
    messages = [
        HumanMessage(content="prepare the bounded Data artifact"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_solar_precursor_cycle_table",
                    "args": {
                        "sunspot_path": "/project/shared/silso.txt",
                        "polar_field_path": "/project/shared/polar.csv",
                    },
                    "id": "call-prepare",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "status": receipt_status,
                    "artifact_refs": [
                        "work/solar_data/solar_precursor_cycle_features.csv"
                    ],
                    "receipt_refs": [
                        "receipts/datasets/solar_precursor_cycle_table.json"
                    ],
                    "row_count": 11,
                }
            ),
            tool_call_id="call-prepare",
            name="prepare_solar_precursor_cycle_table",
        ),
    ]
    request = _request(*tools, messages=messages)
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\n"
            "phase=data\n"
            "stage=data\n"
            'deterministic_data_context={"required_data_product":'
            '"solar_polar_precursor_table_v1"}'
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.8-max"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    if receipt_status == "verified":
        assert prepared.tools == []
        assert prepared.tool_choice is None
    else:
        assert prepared.tools == tools


def test_data_stage_suppresses_tools_after_verified_readiness_payload_without_name():
    """A local Data transition may return a nameless but typed terminal payload."""

    tools = [
        {"name": "prepare_solar_cycle_26_readiness"},
        {"name": "solar_research_analysis"},
        {"name": "read_file"},
    ]
    messages = [
        HumanMessage(content="prepare the bounded SC26 readiness artifact"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_solar_cycle_26_readiness",
                    "args": {},
                    "id": "local-readiness",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "status": "verified",
                    "artifact_refs": [
                        "work/solar_data/solar_cycle_26_readiness_inventory.json"
                    ],
                    "receipt_refs": [
                        "receipts/datasets/solar_cycle_26_readiness_inventory.json"
                    ],
                    "launch_readiness": "insufficient_evidence",
                    "formal_classification_ready": False,
                    "testable_peak_interval_ready": False,
                }
            ),
            tool_call_id="local-readiness",
        ),
    ]
    request = _request(*tools, messages=messages)
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\n"
            "phase=data\n"
            "stage=data\n"
            'deterministic_data_context={"required_data_product":'
            '"solar_cycle_26_readiness_inventory_v1"}'
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tools == []
    assert prepared.tool_choice is None


def test_data_stage_uses_preopened_silso_context_and_forces_reproduction():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "reproduce_silso_cycle_extrema"},
    ]
    context = {
        "status": "inputs_available",
        "must_stop": False,
        "analysis_protocol": "silso_cycle_reproduction_v1",
        "required_data_product": "silso_cycle_extrema_v1",
        "eligible_inputs": [
            {"dataset_id": "silso-monthly-total-v2", "path": "/inputs/raw.csv"},
            {
                "dataset_id": "silso-monthly-smoothed-v2",
                "path": "/inputs/smoothed.csv",
            },
            {
                "dataset_id": "silso-cycle-extrema-v2",
                "path": "/inputs/extrema.txt",
            },
        ],
    }
    request = _request(*tools, messages=[HumanMessage(content="reproduce cycles")])
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}\n"
            "The Supervisor already opened this context."
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == {
        "type": "function",
        "function": {"name": "reproduce_silso_cycle_extrema"},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


def test_silso_product_never_selects_precursor_tool_from_extra_input():
    tools = [
        {"name": "reproduce_silso_cycle_extrema"},
        {"name": "prepare_solar_precursor_cycle_table"},
    ]
    context = {
        "status": "inputs_available",
        "must_stop": False,
        "analysis_protocol": "silso_cycle_reproduction_v1",
        "required_data_product": "silso_cycle_extrema_v1",
        "eligible_inputs": [
            {"dataset_id": "silso-monthly-total-v2", "path": "/inputs/raw.csv"},
            {
                "dataset_id": "silso-monthly-smoothed-v2",
                "path": "/inputs/smoothed.csv",
            },
            {
                "dataset_id": "silso-cycle-extrema-v2",
                "path": "/inputs/extrema.txt",
            },
            {
                "dataset_id": "mwo-wso-polar-field-v2",
                "path": "/inputs/polar.csv",
            },
        ],
    }
    request = _request(*tools, messages=[HumanMessage(content="reproduce cycles")])
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}"
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus").wrap_model_call(
            request, handler
        )

    assert handler.call_args.args[0].tool_choice == {
        "type": "function",
        "function": {"name": "reproduce_silso_cycle_extrema"},
    }


def test_data_stage_preopened_missing_context_does_not_force_any_tool():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "reproduce_silso_cycle_extrema"},
    ]
    context = {
        "status": "input_missing",
        "must_stop": True,
        "analysis_protocol": "silso_cycle_reproduction_v1",
        "required_data_product": "silso_cycle_extrema_v1",
        "eligible_inputs": [],
    }
    request = _request(*tools, messages=[HumanMessage(content="reproduce cycles")])
    request.system_message = SystemMessage(
        content=(
            "[RESEARCH_PRODUCER_V2]\nstage=data\n"
            f"deterministic_data_context={json.dumps(context)}"
        )
    )
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus").wrap_model_call(
            request, handler
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice is None


def test_data_stage_does_not_force_prepare_when_context_is_missing_inputs():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_precursor_cycle_table"},
    ]
    context = ToolMessage(
        content=json.dumps(
            {"status": "input_missing", "must_stop": True, "eligible_inputs": []}
        ),
        tool_call_id="call-open",
        name="solar_data_open_context",
    )
    request = _request(*tools, messages=[HumanMessage(content="prepare data"), context])
    request.system_message = SystemMessage(content="[RESEARCH_PRODUCER_V2]\nstage=data")
    handler = MagicMock(return_value="ok")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice is None
    assert prepared.model.extra_body["enable_thinking"] is True


def test_middleware_recovers_strict_tool_json_from_qwq_reasoning():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwq-plus")
    request = _request(read_dataset)
    handler = MagicMock(
        return_value=ModelResponse(
            result=[
                AIMessage(
                    content="",
                    additional_kwargs={
                        "reasoning_content": (
                            '{"name":"read_dataset",'
                            '"arguments":{"path":"/inputs/data.csv"}}'
                        )
                    },
                    response_metadata={"finish_reason": "stop"},
                )
            ]
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    message = response.result[0]
    assert isinstance(message, AIMessage)
    assert message.tool_calls == [
        {
            "name": "read_dataset",
            "args": {"path": "/inputs/data.csv"},
            "id": message.tool_calls[0]["id"],
            "type": "tool_call",
        }
    ]
    assert message.tool_calls[0]["id"].startswith("call_qwen_recovered_")
    assert message.response_metadata["finish_reason"] == "tool_calls"


def test_middleware_requires_manifest_artifact_readback_before_final_answer():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    manifest = ToolMessage(
        content=(
            "<artifact_manifest>\n"
            '{"version":1,"files":[{"path":"/outputs/results.json",'
            '"sha256":"abc"}]}\n'
            "</artifact_manifest>"
        ),
        tool_call_id="call_execute",
        name="execute",
    )
    request = _request(
        read_dataset,
        {"name": "read_file"},
        messages=[manifest],
    )
    handler = MagicMock(
        return_value=ModelResponse(
            result=[AIMessage(content="The computed answer is 42.")]
        )
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    message = response.result[0]
    assert message.content == ""
    assert message.tool_calls == [
        {
            "name": "read_file",
            "args": {"file_path": "/outputs/results.json"},
            "id": message.tool_calls[0]["id"],
            "type": "tool_call",
        }
    ]
    assert message.tool_calls[0]["id"].startswith("call_qwen_readback_")
    assert message.response_metadata["finish_reason"] == "tool_calls"


def test_middleware_allows_final_after_manifest_artifact_readback():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    manifest = ToolMessage(
        content=(
            "<artifact_manifest>\n"
            '{"version":1,"files":[{"path":"/outputs/results.json"}]}\n'
            "</artifact_manifest>"
        ),
        tool_call_id="call_execute",
        name="execute",
    )
    read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"file_path": "/outputs/results.json"},
                "id": "call_read",
                "type": "tool_call",
            }
        ],
    )
    read_result = ToolMessage(
        content='{"answer": 42}',
        tool_call_id="call_read",
        name="read_file",
    )
    final = AIMessage(content="The computed answer is 42.")
    request = _request(
        read_dataset,
        {"name": "read_file"},
        messages=[manifest, read_call, read_result],
    )
    handler = MagicMock(return_value=ModelResponse(result=[final]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [final]
    assert not response.result[0].tool_calls


def test_middleware_refreshes_hypothesis_draft_before_tail_review_after_update():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    update_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "patch_candidate"},
                "id": "call_update",
                "type": "tool_call",
            }
        ],
    )
    update_result = ToolMessage(
        content='{"status":"draft_updated"}',
        tool_call_id="call_update",
        name="scientific_hypothesis_update_draft",
    )
    proposed_review = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_review_tail",
                "args": {"review_json": '{"candidate_pool_sha256":"stale"}'},
                "id": "call_review",
                "type": "tool_call",
            }
        ],
    )
    request = _request(
        {"name": "scientific_hypothesis_get_draft"},
        {"name": "scientific_hypothesis_review_tail"},
        messages=[update_call, update_result],
    )
    handler = MagicMock(return_value=ModelResponse(result=[proposed_review]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    [call] = response.result[0].tool_calls
    assert call["name"] == "scientific_hypothesis_get_draft"
    assert call["args"] == {}
    assert call["id"].startswith("call_qwen_hypothesis_refresh_")


def test_middleware_reads_ready_hypothesis_draft_before_another_update():
    """A warning-free persisted update must transition to draft readback."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    update_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "patch_candidate"},
                "id": "call_update_ready",
                "type": "tool_call",
            }
        ],
    )
    update_result = ToolMessage(
        content=json.dumps(
            {
                "status": "draft",
                "soft_warning_count": 0,
                "return_gate": "get_draft_required",
                "next_required_action": {"tool": "scientific_hypothesis_get_draft"},
            }
        ),
        tool_call_id="call_update_ready",
        name="scientific_hypothesis_update_draft",
    )
    repeated_update = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "patch_candidate"},
                "id": "call_update_repeated",
                "type": "tool_call",
            }
        ],
    )
    request = _request(
        {"name": "scientific_hypothesis_update_draft"},
        {"name": "scientific_hypothesis_get_draft"},
        messages=[update_call, update_result],
    )
    handler = MagicMock(return_value=ModelResponse(result=[repeated_update]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    [call] = response.result[0].tool_calls
    assert call["name"] == "scientific_hypothesis_get_draft"
    assert call["args"] == {}
    assert call["id"].startswith("call_qwen_hypothesis_ready_readback_")


def test_middleware_keeps_hypothesis_repairs_while_warnings_remain():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    update_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "upsert_candidate"},
                "id": "call_update_incomplete",
                "type": "tool_call",
            }
        ],
    )
    update_result = ToolMessage(
        content=json.dumps(
            {
                "status": "draft",
                "soft_warning_count": 2,
                "return_gate": "blocked_until_warnings_resolved",
                "next_required_action": {"tool": "scientific_hypothesis_update_draft"},
            }
        ),
        tool_call_id="call_update_incomplete",
        name="scientific_hypothesis_update_draft",
    )
    proposed_repair = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "patch_candidate"},
                "id": "call_update_repair",
                "type": "tool_call",
            }
        ],
    )
    request = _request(
        {"name": "scientific_hypothesis_update_draft"},
        {"name": "scientific_hypothesis_get_draft"},
        messages=[update_call, update_result],
    )
    handler = MagicMock(return_value=ModelResponse(result=[proposed_repair]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [proposed_repair]


def test_middleware_allows_hypothesis_tail_review_after_current_draft_readback():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    update_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_update_draft",
                "args": {"operation": "patch_candidate"},
                "id": "call_update",
                "type": "tool_call",
            }
        ],
    )
    update_result = ToolMessage(
        content='{"status":"draft_updated"}',
        tool_call_id="call_update",
        name="scientific_hypothesis_update_draft",
    )
    read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_get_draft",
                "args": {},
                "id": "call_get_draft",
                "type": "tool_call",
            }
        ],
    )
    read_result = ToolMessage(
        content='{"status":"draft","candidate_pool_sha256":"current"}',
        tool_call_id="call_get_draft",
        name="scientific_hypothesis_get_draft",
    )
    proposed_review = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_review_tail",
                "args": {"review_json": '{"candidate_pool_sha256":"current"}'},
                "id": "call_review",
                "type": "tool_call",
            }
        ],
    )
    request = _request(
        {"name": "scientific_hypothesis_get_draft"},
        {"name": "scientific_hypothesis_review_tail"},
        messages=[update_call, update_result, read_call, read_result],
    )
    handler = MagicMock(return_value=ModelResponse(result=[proposed_review]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [proposed_review]


def test_middleware_checkpoints_after_successful_hypothesis_tail_review():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    review_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "scientific_hypothesis_review_tail",
                "args": {"review_json": "{}"},
                "id": "call_review",
                "type": "tool_call",
            }
        ],
    )
    review_result = ToolMessage(
        content='{"status":"tail_reviewed","candidate_pool_sha256":"current"}',
        tool_call_id="call_review",
        name="scientific_hypothesis_review_tail",
    )
    request = _request(
        {"name": "scientific_hypothesis_checkpoint_draft"},
        messages=[review_call, review_result],
    )
    handler = MagicMock(
        return_value=ModelResponse(result=[AIMessage(content="候选组合已经完成。")])
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    [call] = response.result[0].tool_calls
    assert call["name"] == "scientific_hypothesis_checkpoint_draft"
    assert call["args"] == {}
    assert call["id"].startswith("call_qwen_hypothesis_checkpoint_")


def test_middleware_stops_exact_tool_retry_after_research_review_block():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    first_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis", "description": "revise"},
                "id": "call_first",
                "type": "tool_call",
            }
        ],
    )
    blocked = ToolMessage(
        content="[RESEARCH REVIEW BLOCKED] use independent review",
        tool_call_id="call_first",
        name="task",
    )
    repeated = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": "revise", "subagent_type": "solar-hypothesis"},
                "id": "call_second",
                "type": "tool_call",
            }
        ],
    )
    request = _request(read_dataset, messages=[first_call, blocked])
    handler = MagicMock(return_value=ModelResponse(result=[repeated]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    message = response.result[0]
    assert not message.tool_calls
    assert message.content.startswith("[RESEARCH REVIEW STOP]")
    assert message.response_metadata["finish_reason"] == "stop"


def test_middleware_compacts_consecutive_identical_tool_rounds_before_qwen():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    messages = [HumanMessage(content="continue")]
    for index in range(3):
        call_id = f"call-repeat-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "subagent_type": "solar-hypothesis",
                                "description": "bounded hypothesis review route",
                            },
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=f"[RESEARCH REVIEW STOP] attempt={index}",
                    tool_call_id=call_id,
                    name="task",
                ),
            ]
        )
    messages.append(HumanMessage(content="resume with next_action"))
    request = _request(read_dataset, messages=messages)
    captured = {}

    def handler(prepared):
        captured["messages"] = list(prepared.messages)
        return ModelResponse(result=[AIMessage(content="stopped")])

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        middleware.wrap_model_call(request, handler)

    visible = captured["messages"]
    calls = [
        item for item in visible if isinstance(item, AIMessage) and item.tool_calls
    ]
    results = [item for item in visible if isinstance(item, ToolMessage)]
    assert len(calls) == 1
    assert len(results) == 1
    assert calls[0].tool_calls[0]["id"] == "call-repeat-2"
    assert "attempt=2" in str(results[0].content)
    assert isinstance(visible[-1], HumanMessage)


@pytest.mark.parametrize(
    ("tool_result", "next_args"),
    [
        ("ordinary transient failure", {"subagent_type": "solar-hypothesis"}),
        (
            "[RESEARCH REVIEW BLOCKED] revise first",
            {"subagent_type": "solar-evidence"},
        ),
    ],
)
def test_middleware_does_not_stop_nonidentical_or_nonreview_retry(
    tool_result, next_args
):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    first_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-hypothesis"},
                "id": "call_first",
                "type": "tool_call",
            }
        ],
    )
    result = ToolMessage(
        content=tool_result,
        tool_call_id="call_first",
        name="task",
    )
    retry = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": next_args,
                "id": "call_second",
                "type": "tool_call",
            }
        ],
    )
    request = _request(read_dataset, messages=[first_call, result])
    handler = MagicMock(return_value=ModelResponse(result=[retry]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [retry]


@pytest.mark.parametrize("failure_count", [0, 1])
def test_middleware_allows_at_most_one_identical_tool_error_retry(failure_count):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    messages = [HumanMessage(content="continue")]
    for index in range(failure_count):
        call_id = f"call_failed_{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-evidence"},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    "[TOOL ERROR CAPSULE]\nfingerprint=same",
                    tool_call_id=call_id,
                    name="task",
                ),
            ]
        )
    retry = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-evidence"},
                "id": "call_next",
                "type": "tool_call",
            }
        ],
    )
    request = _request(read_dataset, messages=messages)
    handler = MagicMock(return_value=ModelResponse(result=[retry]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [retry]


def test_middleware_stops_third_identical_tool_attempt_after_two_errors():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    messages = [HumanMessage(content="continue")]
    for index in range(2):
        call_id = f"call_failed_{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-evidence"},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    "[TOOL ERROR CAPSULE]\nfingerprint=same",
                    tool_call_id=call_id,
                    name="task",
                ),
            ]
        )
    third = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": "solar-evidence"},
                "id": "call_third",
                "type": "tool_call",
            }
        ],
    )
    request = _request(read_dataset, messages=messages)
    handler = MagicMock(return_value=ModelResponse(result=[third]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    message = response.result[0]
    assert not message.tool_calls
    assert message.content.startswith("[TOOL RETRY STOP]")
    assert message.response_metadata["finish_reason"] == "stop"


def test_middleware_stops_third_identical_contract_blocked_tool_attempt():
    """Bounded specialist tools must not loop on an unavailable tool name."""

    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    messages = [HumanMessage(content="continue")]
    for index in range(2):
        call_id = f"call_blocked_{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"filename": "work/data.csv"},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    "[CONTRACT TOOL BLOCKED] 'read_file' is outside this specialist's bounded tool set.",
                    tool_call_id=call_id,
                    name="read_file",
                ),
            ]
        )
    third = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"filename": "work/data.csv"},
                "id": "call_blocked_third",
                "type": "tool_call",
            }
        ],
    )
    request = _request(read_dataset, messages=messages)
    handler = MagicMock(return_value=ModelResponse(result=[third]))

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    message = response.result[0]
    assert not message.tool_calls
    assert message.content.startswith("[TOOL RETRY STOP]")


@pytest.mark.parametrize("marker_location", ["system", "delegation_message"])
@pytest.mark.parametrize("provider_shape", ["openai", "anthropic"])
def test_evidence_navigation_opens_reads_each_source_then_forces_atomic_submit(
    marker_location,
    provider_shape,
):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    tool_schemas = [
        {"name": "evidence_review_open_context"},
        {"name": "evidence_review_read_source"},
        evidence_review_submit_round,
    ]
    marker = "[EVIDENCE_REVIEW_V2]\nreview_mode=data\n"
    system = SystemMessage(
        content="Evidence Reviewer\n" + (marker if marker_location == "system" else "")
    )
    human_content = "review\n" + (
        marker if marker_location == "delegation_message" else ""
    )
    model = None
    if provider_shape == "anthropic":
        model = ChatAnthropic(
            model="kimi-for-coding",
            api_key="test-key",
            base_url="https://api.kimi.com/coding/",
        )
    request = _request(
        *tool_schemas,
        messages=[HumanMessage(content=human_content)],
        model=model,
    )
    request.system_message = system
    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        opened = middleware.wrap_model_call(
            request, lambda _prepared: pytest.fail("open is deterministic")
        )
    assert opened.result[0].tool_calls[0]["name"] == "evidence_review_open_context"

    open_call = opened.result[0]
    open_result = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "result": {
                    "artifacts": [
                        {
                            "evidence_refs": ["receipt.json", "table.csv"],
                            "claims": [],
                        }
                    ]
                },
            }
        ),
        tool_call_id=open_call.tool_calls[0]["id"],
        name="evidence_review_open_context",
    )
    request.messages = [HumanMessage(content=human_content), open_call, open_result]
    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        first_read = middleware.wrap_model_call(
            request, lambda _prepared: pytest.fail("read is deterministic")
        )
    assert first_read.result[0].tool_calls[0]["args"]["source_ref"] == "receipt.json"

    read_call = first_read.result[0]
    read_result = ToolMessage(
        content='{"ok": true, "result": {"content": "receipt"}}',
        tool_call_id=read_call.tool_calls[0]["id"],
        name="evidence_review_read_source",
    )
    request.messages.extend([read_call, read_result])
    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        second_read = middleware.wrap_model_call(
            request, lambda _prepared: pytest.fail("read is deterministic")
        )
    assert second_read.result[0].tool_calls[0]["args"]["source_ref"] == "table.csv"

    second_call = second_read.result[0]
    second_result = ToolMessage(
        content='{"ok": true, "result": {"content": "table"}}',
        tool_call_id=second_call.tool_calls[0]["id"],
        name="evidence_review_read_source",
    )
    request.messages.extend([second_call, second_result])
    captured = {}

    def handler(prepared):
        captured["tool_choice"] = prepared.tool_choice
        captured["tools"] = prepared.tools
        captured["system"] = prepared.system_message
        captured["response_format"] = prepared.response_format
        return ModelResponse(result=[AIMessage(content="submit")])

    def invoke_kimi(prepared):
        return handler(prepared)

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        if provider_shape == "anthropic":
            with patch.object(
                middleware,
                "_invoke_kimi_evidence_structured",
                side_effect=invoke_kimi,
            ):
                middleware.wrap_model_call(
                    request,
                    lambda _prepared: pytest.fail(
                        "Kimi submit bypasses the generic handler"
                    ),
                )
        else:
            middleware.wrap_model_call(request, handler)
    if provider_shape == "anthropic":
        assert captured["tool_choice"] is None
        assert captured["tools"] == []
        assert isinstance(captured["response_format"], ProviderStrategy)
        assert "The only remaining action" in middleware._message_text(
            captured["system"]
        )
        final_instruction = middleware._message_text(captured["system"])
        assert "accepted_claims" in final_instruction
        assert "evidence_role is gap" in final_instruction
        assert "must omit source_ref" in final_instruction
        assert "only minor or informational issues" in final_instruction
        assert "critical or major" in final_instruction
        assert "never solar-hypothesis" in final_instruction
        assert "one scientific_quality_claims row per artifact claim" in (
            final_instruction
        )
        assert "scope-matched complete independent units" in final_instruction
        assert "broader table row count" in final_instruction
        assert "load-bearing observable" in final_instruction
        assert "revise or block" in final_instruction
        assert "exploratory hypothesis" in final_instruction
        assert "novelty_not_assessed" in final_instruction
        assert "candidate fields" in final_instruction
    else:
        assert captured["tool_choice"] is None
        assert [
            tool.name if hasattr(tool, "name") else tool["name"]
            for tool in captured["tools"]
        ] == ["evidence_review_submit_round"]
        assert "The only remaining action" in middleware._message_text(
            captured["system"]
        )


def test_qwen_evidence_submit_uses_single_tool_auto_selection_in_thinking_mode():
    """DashScope Qwen rejects forced tool_choice even when a request disables thinking."""
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.8-max")
    human = HumanMessage(
        content="Evidence Reviewer\n[EVIDENCE_REVIEW_V2]\nreview_mode=data"
    )
    open_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_open_context",
                "args": {"review_mode": "data"},
                "id": "open",
                "type": "tool_call",
            }
        ],
    )
    open_result = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "result": {
                    "artifacts": [{"evidence_refs": ["source.json"], "claims": []}]
                },
            }
        ),
        tool_call_id="open",
        name="evidence_review_open_context",
    )
    read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_read_source",
                "args": {"review_mode": "data", "source_ref": "source.json"},
                "id": "read",
                "type": "tool_call",
            }
        ],
    )
    read_result = ToolMessage(
        content='{"ok": true, "result": {"content": "source"}}',
        tool_call_id="read",
        name="evidence_review_read_source",
    )
    request = _request(
        {"name": "evidence_review_open_context"},
        {"name": "evidence_review_read_source"},
        evidence_review_submit_round,
        messages=[human, open_call, open_result, read_call, read_result],
    )
    request.system_message = SystemMessage(
        content="Evidence Reviewer\n[EVIDENCE_REVIEW_V2]\nreview_mode=data"
    )
    captured = {}

    def handler(prepared):
        captured["request"] = prepared
        return ModelResponse(result=[AIMessage(content="submit")])

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        middleware.wrap_model_call(request, handler)

    prepared = captured["request"]
    assert prepared.tool_choice is None
    assert [
        _tool.get("name") if isinstance(_tool, dict) else _tool.name
        for _tool in prepared.tools
    ] == ["evidence_review_submit_round"]
    assert "The only remaining action" in middleware._message_text(
        prepared.system_message
    )


def test_evidence_navigation_batches_declared_source_reads_before_submit():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    source_refs = [f"source-{index:02d}.json" for index in range(30)]
    human = HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=experiment_design")
    open_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_open_context",
                "args": {"review_mode": "experiment_design"},
                "id": "open",
                "type": "tool_call",
            }
        ],
    )
    open_result = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "result": {"artifacts": [{"evidence_refs": source_refs, "claims": []}]},
            }
        ),
        tool_call_id="open",
        name="evidence_review_open_context",
    )
    request = _request(
        {"name": "evidence_review_open_context"},
        {"name": "evidence_review_read_source"},
        evidence_review_submit_round,
        messages=[human, open_call, open_result],
    )
    request.system_message = SystemMessage(content="Evidence Reviewer")

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("declared-source reads are deterministic"),
        )

    read_calls = response.result[0].tool_calls
    assert [call["args"]["source_ref"] for call in read_calls] == source_refs
    assert all(call["name"] == "evidence_review_read_source" for call in read_calls)


def test_evidence_navigation_submits_after_one_failed_declared_source_read():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.8-max")
    human = HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=planning")
    open_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_open_context",
                "args": {"review_mode": "planning"},
                "id": "open",
                "type": "tool_call",
            }
        ],
    )
    source_ref = "planner/drafts/"
    open_result = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "result": {
                    "artifacts": [{"evidence_refs": [source_ref], "claims": []}]
                },
            }
        ),
        tool_call_id="open",
        name="evidence_review_open_context",
    )
    read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_read_source",
                "args": {"review_mode": "planning", "source_ref": source_ref},
                "id": "read",
                "type": "tool_call",
            }
        ],
    )
    failed_read = ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "error": {
                    "type": "IsADirectoryError",
                    "message": "declared source is not a readable file",
                },
            }
        ),
        tool_call_id="read",
        name="evidence_review_read_source",
        status="error",
    )
    request = _request(
        {"name": "evidence_review_open_context"},
        {"name": "evidence_review_read_source"},
        evidence_review_submit_round,
        messages=[human, open_call, open_result, read_call, failed_read],
    )
    request.system_message = SystemMessage(content="Evidence Reviewer")
    captured = {}

    def handler(prepared):
        captured["request"] = prepared
        return ModelResponse(result=[AIMessage(content="submit")])

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        middleware.wrap_model_call(request, handler)

    assert "request" in captured
    assert [
        tool.get("name") if isinstance(tool, dict) else tool.name
        for tool in captured["request"].tools
    ] == ["evidence_review_submit_round"]


@pytest.mark.parametrize(
    "content",
    [
        "INDEPENDENT DATA-STAGE REVIEW — read-only.",
        "MODE: REVIEW (data review, independent, read-only)",
        "Perform an independent data review under the review contract.",
    ],
)
def test_evidence_review_mode_accepts_the_supervisors_data_review_delegations(
    content,
):
    assert QwenToolCompatibilityMiddleware._evidence_review_mode(content) == "data"


def test_evidence_review_mode_prefers_the_delegated_stage_over_earlier_contract_text():
    content = (
        "The generic contract describes hypothesis review and integration review.\n"
        "MODE: REVIEW (data review, independent, read-only)"
    )

    assert QwenToolCompatibilityMiddleware._evidence_review_mode(content) == "data"


def _kimi_evidence_final_request():
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    model = ChatAnthropic(
        model="kimi-for-coding",
        api_key="test-key",
        base_url="https://api.kimi.com/coding/",
    )
    human = HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=hypothesis")
    open_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_open_context",
                "args": {"review_mode": "hypothesis"},
                "id": "open",
                "type": "tool_call",
            }
        ],
    )
    open_result = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "result": {
                    "artifacts": [{"evidence_refs": ["source.md"], "claims": []}]
                },
            }
        ),
        tool_call_id="open",
        name="evidence_review_open_context",
    )
    read_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "evidence_review_read_source",
                "args": {
                    "review_mode": "hypothesis",
                    "source_ref": "source.md",
                },
                "id": "read",
                "type": "tool_call",
            }
        ],
    )
    read_result = ToolMessage(
        content='{"ok": true, "result": {"content": "evidence"}}',
        tool_call_id="read",
        name="evidence_review_read_source",
    )
    request = _request(
        {"name": "evidence_review_open_context"},
        {"name": "evidence_review_read_source"},
        evidence_review_submit_round,
        messages=[human, open_call, open_result, read_call, read_result],
        model=model,
    )
    request.system_message = SystemMessage(content="Evidence Reviewer")
    submission = {
        "review_mode": "hypothesis",
        "assessment_review_mode": "two_pass",
        "assessment_claims": [],
        "scientific_quality_claims": [],
        "decision": "block",
        "issues": [],
        "accepted_claims": [],
        "blocked_claims": [],
        "carry_forward_limits": [],
        "next_owner": "",
    }
    return middleware, request, submission


def test_kimi_evidence_structured_model_has_room_for_the_atomic_review():
    middleware, request, _submission = _kimi_evidence_final_request()

    assert (
        request.model._get_request_payload([HumanMessage(content="review")])[
            "max_tokens"
        ]
        == 4096
    )

    review_model = middleware._kimi_evidence_output_model(request.model)

    assert (
        review_model._get_request_payload([HumanMessage(content="review")])[
            "max_tokens"
        ]
        == 32768
    )
    assert (
        request.model._get_request_payload([HumanMessage(content="review")])[
            "max_tokens"
        ]
        == 4096
    )


def test_kimi_evidence_json_schema_becomes_one_atomic_submit_tool_call():
    middleware, request, submission = _kimi_evidence_final_request()
    parsed = evidence_review_submit_round.args_schema(**submission)
    structured = MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage(content=[{"type": "text", "text": "structured"}]),
        "parsed": parsed,
        "parsing_error": None,
    }

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        patch.object(
            ChatAnthropic,
            "with_structured_output",
            return_value=structured,
        ) as with_structured_output,
    ):
        response = middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail(
                "Kimi Evidence must bypass the generic Agent model handler"
            ),
        )

    with_structured_output.assert_called_once_with(
        evidence_review_submit_round.args_schema,
        method="json_schema",
        include_raw=True,
    )
    structured.invoke.assert_called_once()

    message = response.result[0]
    assert message.content == ""
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0]["name"] == "evidence_review_submit_round"
    assert message.tool_calls[0]["args"] == submission

    request.messages.extend(
        [
            message,
            ToolMessage(
                content='{"ok": true, "result": {"round": 1}}',
                tool_call_id=message.tool_calls[0]["id"],
                name="evidence_review_submit_round",
            ),
        ]
    )
    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        completed = middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail(
                "a persisted Evidence round must finish without another model call"
            ),
        )
    assert completed.result[0].tool_calls == []
    assert completed.result[0].content == "Evidence review round persisted."


@pytest.mark.parametrize(
    ("result", "error_type", "parsed_present", "raw_message_present"),
    [
        (
            {
                "raw": AIMessage(content="model output"),
                "parsed": None,
                "parsing_error": ValueError("token=secret-model-response"),
            },
            "ValueError",
            False,
            True,
        ),
        (
            {
                "raw": AIMessage(content="model output"),
                "parsed": None,
                "parsing_error": None,
            },
            "parsed_missing",
            False,
            True,
        ),
        (
            {
                "raw": {"content": "model output"},
                "parsed": evidence_review_submit_round.args_schema(
                    **_kimi_evidence_final_request()[2]
                ),
                "parsing_error": None,
            },
            "raw_not_ai_message",
            True,
            False,
        ),
    ],
)
def test_kimi_evidence_structured_submit_returns_limited_diagnostic_capsule(
    result, error_type, parsed_present, raw_message_present
):
    response = QwenToolCompatibilityMiddleware._evidence_structured_result(result)

    message = response.result[0]
    assert message.tool_calls == []
    assert message.response_metadata["finish_reason"] == "stop"
    assert message.content.splitlines()[:5] == [
        "[KIMI EVIDENCE STRUCTURED SUBMIT FAILED]",
        "event=kimi_evidence_structured_submit_failed",
        f"error_type={error_type}",
        f"parsed_present={str(parsed_present).lower()}",
        f"raw_message_present={str(raw_message_present).lower()}",
    ]
    lines = message.content.splitlines()
    assert len(lines) == 6
    fingerprint = lines[5].removeprefix("fingerprint=")
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    assert "secret-model-response" not in message.content
    assert "model output" not in message.content


def test_kimi_evidence_revise_uses_the_issue_owner_when_owner_is_omitted():
    middleware, _request, submission = _kimi_evidence_final_request()
    submission.update(
        {
            "decision": "revise",
            "issues": [
                {
                    "rule_id": "SAMPLE_INDEPENDENCE_AND_UNCERTAINTY",
                    "severity": "major",
                    "claim_ref": "data-output-v1",
                    "evidence_refs": [],
                    "owner": "solar-data",
                    "message": "The scope-matched count is unresolved.",
                    "required_action": "Report the homogeneous-regime count.",
                    "acceptance_test": "The count matches complete independent units.",
                }
            ],
            "next_owner": "",
        }
    )
    parsed = evidence_review_submit_round.args_schema(**submission)

    response = middleware._evidence_structured_result(
        {
            "raw": AIMessage(content=[]),
            "parsed": parsed,
            "parsing_error": None,
        }
    )

    assert response.result[0].tool_calls[0]["args"]["next_owner"] == "solar-data"


def test_kimi_evidence_normalizes_review_routing_and_quality_caps(monkeypatch):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    submission = {
        "assessment_review_mode": "planning",
        "decision": "accept_with_limits",
        "issues": [
            {
                "severity": "major",
                "owner": "solar-data",
            }
        ],
        "next_owner": "",
        "scientific_quality_claims": [
            {
                "quality_status": "release_candidate",
                "conclusion_cap": "release_candidate",
                "evidence_matrix": [
                    {
                        "scope_match": "mismatch",
                        "entailment": "not_entailed",
                    }
                ],
            }
        ],
    }

    normalized = QwenToolCompatibilityMiddleware._normalize_kimi_evidence_submission(
        submission
    )

    assert normalized["assessment_review_mode"] == "two_pass"
    assert normalized["decision"] == "revise"
    assert normalized["next_owner"] == "solar-data"
    claim = normalized["scientific_quality_claims"][0]
    assert claim["quality_status"] == "evidence_constrained"
    assert claim["conclusion_cap"] == "evidence_constrained"


def test_qwen_evidence_submit_normalizes_host_mode_and_supported_claim_ids(
    monkeypatch,
):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        evidence_review_submit_round,
        messages=[
            HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=experiment_result")
        ],
    )
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "evidence_review_submit_round",
                        "args": {
                            "review_mode": "experiment_result",
                            "assessment_review_mode": "experiment_result",
                            "assessment_claims": [
                                {
                                    "claim_id": "experiment-result-v1#claim-result",
                                    "disposition": "supported",
                                },
                                {
                                    "claim_id": "experiment-result-v1#claim-limited",
                                    "disposition": "limited_support",
                                },
                                {
                                    "claim_id": "experiment-result-v1#claim-undecided",
                                    "disposition": "undecided",
                                },
                            ],
                            "scientific_quality_claims": [],
                            "decision": "accept_with_limits",
                            "issues": [],
                            "accepted_claims": [],
                            "blocked_claims": [],
                            "carry_forward_limits": [
                                "Small-sample uncertainty remains."
                            ],
                            "next_owner": "",
                        },
                        "id": "submit",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        normalized = middleware.wrap_model_call(request, lambda _prepared: response)

    args = normalized.result[0].tool_calls[0]["args"]
    assert args["assessment_review_mode"] == "two_pass"
    assert args["accepted_claims"] == [
        "experiment-result-v1#claim-result",
        "experiment-result-v1#claim-limited",
    ]


def test_qwen_evidence_submit_parses_json_list_arguments_without_splitting_chars(
    monkeypatch,
):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        evidence_review_submit_round,
        messages=[HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=planning")],
    )
    assessment_rows = [
        {
            "claim_id": "planning-plan-v1",
            "disposition": "limited_support",
        }
    ]
    quality_rows = [
        {
            "claim_id": "planning-plan-v1",
            "claim_component": "statement",
            "evidence_matrix": [],
        }
    ]
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "evidence_review_submit_round",
                        "args": {
                            "review_mode": "planning",
                            "assessment_review_mode": "planning",
                            "assessment_claims": json.dumps(assessment_rows),
                            "scientific_quality_claims": json.dumps(quality_rows),
                            "decision": "accept_with_limits",
                            "issues": "[]",
                            "accepted_claims": "[]",
                            "blocked_claims": "[]",
                            "carry_forward_limits": json.dumps(
                                ["Registered inputs still require data-stage checks."]
                            ),
                            "next_owner": "",
                        },
                        "id": "submit",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        normalized = middleware.wrap_model_call(request, lambda _prepared: response)

    args = normalized.result[0].tool_calls[0]["args"]
    assert args["assessment_claims"] == assessment_rows
    assert args["scientific_quality_claims"] == quality_rows
    assert args["issues"] == []
    assert args["accepted_claims"] == ["planning-plan-v1"]
    assert args["blocked_claims"] == []
    assert args["carry_forward_limits"] == [
        "Registered inputs still require data-stage checks."
    ]


@pytest.mark.asyncio
async def test_async_qwen_evidence_submit_applies_the_same_contract_repair(
    monkeypatch,
):
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    request = _request(
        evidence_review_submit_round,
        messages=[
            HumanMessage(content="[EVIDENCE_REVIEW_V2]\nreview_mode=experiment_result")
        ],
    )
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "evidence_review_submit_round",
                        "args": {
                            "review_mode": "experiment_result",
                            "assessment_review_mode": "closed",
                            "assessment_claims": [
                                {
                                    "claim_id": "experiment-result-v1#claim-result",
                                    "disposition": "supported",
                                }
                            ],
                            "scientific_quality_claims": [],
                            "decision": "accept",
                            "issues": [],
                            "accepted_claims": [],
                            "blocked_claims": [],
                            "carry_forward_limits": [],
                            "next_owner": "",
                        },
                        "id": "submit",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        normalized = await middleware.awrap_model_call(
            request, AsyncMock(return_value=response)
        )

    args = normalized.result[0].tool_calls[0]["args"]
    assert args["assessment_review_mode"] == "two_pass"
    assert args["accepted_claims"] == ["experiment-result-v1#claim-result"]


def test_evidence_navigation_stops_after_two_unpersisted_submit_attempts():
    middleware, request, submission = _kimi_evidence_final_request()
    for index in range(2):
        call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "evidence_review_submit_round",
                    "args": {**submission, "next_owner": str(index)},
                    "id": f"submit-{index}",
                    "type": "tool_call",
                }
            ],
        )
        request.messages.extend(
            [
                call,
                ToolMessage(
                    content='{"ok": false, "error": "rejected"}',
                    tool_call_id=f"submit-{index}",
                    name="evidence_review_submit_round",
                ),
            ]
        )

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(
            request,
            lambda _prepared: pytest.fail("a third Evidence submission is forbidden"),
        )

    assert response.result[0].tool_calls == []
    assert response.result[0].content == (
        "Evidence review round did not persist after two attempts."
    )
    assert response.result[0].response_metadata["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_kimi_evidence_async_json_schema_becomes_atomic_submit_tool_call():
    middleware, request, submission = _kimi_evidence_final_request()
    parsed = evidence_review_submit_round.args_schema(**submission)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(
        return_value={
            "raw": AIMessage(content=[{"type": "text", "text": "structured"}]),
            "parsed": parsed,
            "parsing_error": None,
        }
    )

    with (
        patch(
            "jw.middleware.qwen_compat._read_model_override",
            return_value=(None, None),
        ),
        patch.object(
            ChatAnthropic,
            "with_structured_output",
            return_value=structured,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            AsyncMock(
                side_effect=AssertionError(
                    "Kimi Evidence must bypass the generic Agent model handler"
                )
            ),
        )

    structured.ainvoke.assert_awaited_once()
    message = response.result[0]
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0]["name"] == "evidence_review_submit_round"
    assert message.tool_calls[0]["args"] == submission


@pytest.mark.parametrize(
    "reasoning",
    [
        "I should probably read the dataset.",
        '{"name":"unknown_tool","arguments":{}}',
        '{"name":"read_dataset","arguments":"not-json"}',
    ],
)
def test_middleware_does_not_recover_ambiguous_reasoning(reasoning):
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    original = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": reasoning},
    )
    handler = MagicMock(return_value=ModelResponse(result=[original]))
    request = _request(read_dataset)

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        response = middleware.wrap_model_call(request, handler)

    assert response.result == [original]
    assert not response.result[0].tool_calls

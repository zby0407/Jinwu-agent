"""Qwen model/tool compatibility regression tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelResponse
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
    request.model_settings = {}

    def override(**kwargs):
        updated = MagicMock()
        updated.tools = kwargs.get("tools", request.tools)
        updated.messages = kwargs.get("messages", request.messages)
        updated.system_message = kwargs.get("system_message", request.system_message)
        updated.model = kwargs.get("model", request.model)
        updated.tool_choice = kwargs.get("tool_choice", request.tool_choice)
        updated.model_settings = kwargs.get(
            "model_settings", request.model_settings
        )
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
    request = _request({"name": "code_interpreter"})

    with patch(
        "jw.middleware.qwen_compat._read_model_override",
        return_value=(None, None),
    ):
        assert middleware.wrap_model_call(request, handler) == "ok"
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

    assert handler.call_args.args[0].model.extra_body == {
        "enable_thinking": False
    }


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
    assert [tool.name if hasattr(tool, "name") else tool["name"] for tool in prepared.tools] == [
        "read_dataset"
    ]
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
        ("[RESEARCH_PRODUCER_V2]\nstage=planning", 3072),
        ("[RESEARCH_PRODUCER_V2]\nstage=data", 4096),
        ("[RESEARCH_PRODUCER_V2] bounded revision", 6144),
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
    assert response.result[0].response_metadata[
        "jw_deferred_parallel_tool_calls"
    ] == 1


@pytest.mark.parametrize(
    ("receipt_tool", "next_action", "expected_tool"),
    [
        (
            "research_planner_stage_revision_section",
            "commit_revision_candidate",
            "research_planner_commit_revision_candidate",
        ),
    ],
)
def test_planner_deterministic_checkpoint_disables_thinking_and_forces_tool(
    receipt_tool, next_action, expected_tool
):
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
                content=json.dumps(
                    {"draft_checkpoint": {"next_action": next_action}}
                ),
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
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == {
        "type": "function",
        "function": {"name": expected_tool},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


@pytest.mark.parametrize(
    ("receipt_tool", "next_action"),
    [
        ("research_planner_update_draft", "validate_draft"),
        ("research_planner_validate_draft", "freeze_plan"),
    ],
)
def test_planner_no_deliberation_transition_is_not_forced_remotely(
    receipt_tool, next_action
):
    """validate/freeze carry no scientific content: the middleware must NOT pin
    a remote forced ``tool_choice`` for them. The Supervisor's deterministic
    close-out executes the transition in-process, so the request keeps its
    original (auto) choice and the model is never asked to re-emit the tool."""
    tools = [
        {"name": "research_planner_get_brief"},
        {"name": "research_planner_validate_draft"},
        {"name": "research_planner_freeze_plan"},
    ]
    request = _request(
        *tools,
        messages=[
            ToolMessage(
                content=json.dumps(
                    {"draft_checkpoint": {"next_action": next_action}}
                ),
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
        assert (
            QwenToolCompatibilityMiddleware(
                default_model="qwen3.7-plus"
            ).wrap_model_call(request, handler)
            == "ok"
        )

    prepared = handler.call_args.args[0]
    assert prepared.tool_choice == "auto"


def test_forced_tool_choice_strips_all_reasoning_content_from_history():
    """A forced ``tool_choice`` transition must not replay ANY reasoning
    channel, including the newest tool-call round that
    ``_bound_reasoning_history`` would otherwise keep."""
    middleware = QwenToolCompatibilityMiddleware(default_model="qwen3.7-plus")
    tools = [{"name": "research_planner_commit_revision_candidate"}]
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
                content=json.dumps(
                    {"draft_checkpoint": {"next_action": "commit_revision_candidate"}}
                ),
                tool_call_id="call-1",
                name="research_planner_get_brief",
            ),
        ],
        tool_choice={
            "type": "function",
            "function": {"name": "research_planner_commit_revision_candidate"},
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


def test_data_stage_forces_context_open_before_any_data_tool():
    tools = [
        {"name": "solar_data_open_context"},
        {"name": "prepare_solar_precursor_cycle_table"},
    ]
    request = _request(*tools, messages=[HumanMessage(content="prepare data")])
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=data"
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
        "function": {"name": "solar_data_open_context"},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


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
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=data"
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
        "function": {"name": "prepare_solar_precursor_cycle_table"},
    }
    assert prepared.model.extra_body["enable_thinking"] is False


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
    request.system_message = SystemMessage(
        content="[RESEARCH_PRODUCER_V2]\nstage=data"
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
    calls = [item for item in visible if isinstance(item, AIMessage) and item.tool_calls]
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

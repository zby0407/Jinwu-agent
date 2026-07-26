"""Qwen model/tool compatibility regression tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
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

    def override(**kwargs):
        updated = MagicMock()
        updated.tools = kwargs.get("tools", request.tools)
        updated.messages = kwargs.get("messages", request.messages)
        updated.system_message = kwargs.get("system_message", request.system_message)
        updated.model = kwargs.get("model", request.model)
        updated.tool_choice = kwargs.get("tool_choice", request.tool_choice)
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
    assert prepared.model is request.model
    assert prepared.model.extra_body is None


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

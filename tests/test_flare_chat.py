"""Synthetic frontend workflow tests: never acquire, train or issue real science."""

import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from jw.flare_forecast import native_data
from jw.flare_forecast.chat import bounded_tool_model, is_live_request
from jw.flare_forecast.engine import load
from jw.flare_forecast.native_data import review_live, write
from jw.flare_forecast.workflow import read
from jw.middleware.flare_forecast import FlareForecastMiddleware
from jw.middleware.research_router import ResearchRouterMiddleware
from jw.tools import flare_forecast as service

REQUEST = "帮我预测未来24h的耀斑爆发情况"


class NativeModel(BaseChatModel):
    inputs: list = Field(default_factory=list)
    data_role: bool = False
    decision: str = "accept_with_limits"
    end_early: bool = False
    wrong_facts: bool = False

    @property
    def _llm_type(self):
        return "synthetic-flare-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(list(messages))
        if not self.data_role:
            answer = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "forecast_flare_24h",
                        "id": "main-actual-test-call",
                        "args": {},
                    }
                ],
            )
        elif self.end_early:
            answer = AIMessage(content="Synthetic early end")
        elif len(self.inputs) == 1:
            answer = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "available24_read_live_bundle",
                        "id": "read-test-call",
                        "args": {},
                    }
                ],
            )
        elif len(self.inputs) == 2:
            bundle = json.loads(
                next(m.content for m in messages if isinstance(m, ToolMessage))
            )
            answer = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "available24_submit_data_review",
                        "id": "submit-test-call",
                        "args": {
                            "decision": self.decision,
                            "assessment": {
                                "scope": bundle["review_scope"],
                                "checks": ["synthetic input checks inspected"],
                                "issues": [],
                                "limitations": bundle["limitations"],
                                "facts": {
                                    "selected_nominal_tai": "synthetic_TAI",
                                    "central_patch_count": 1,
                                    "full_PRIMARY_QUALITY_word": 1024,
                                    "secondary_bit31_present_in_selected_snapshot": False,
                                    "future_truth_read": False,
                                },
                            },
                            **bundle["binding"],
                        },
                    }
                ],
            )
        else:
            answer = AIMessage(content="Synthetic reviewer completed")
        if (
            self.wrong_facts
            and answer.tool_calls
            and answer.tool_calls[0]["name"] == "available24_submit_data_review"
        ):
            answer.tool_calls[0]["args"]["assessment"]["facts"][
                "central_patch_count"
            ] = 2
        return ChatResult(generations=[ChatGeneration(message=answer)])


@pytest.mark.parametrize(
    "text",
    [
        REQUEST,
        "请给出未来24小时的太阳耀斑预报报告",
        "预测接下来一天太阳耀斑",
        "Please forecast solar flares for the next 24 hours",
        "不要重训，直接预测未来24h太阳耀斑",
    ],
)
def test_explicit_live_intent(text):
    assert is_live_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "不要预测未来24h的耀斑",
        "如何预测未来24h太阳耀斑",
        "解释未来24h耀斑预测原理",
        "修改代码让它预测未来24h耀斑",
        "预测明天的耀斑",
        "预测未来72h的耀斑",
        "分析2011年耀斑",
        "hello",
        "现在能不能做到在前端输入“帮我预测未来24h的耀斑爆发情况”就出报告？",
        "这句提示词“帮我预测未来24h的耀斑爆发情况”是什么意思",
    ],
)
def test_non_execution_intent(text):
    assert not is_live_request(text)


def test_native_data_complete_messages_and_actual_binding(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    for name in (
        "source_manifest.json",
        "live_qualification.json",
        "source_schema_validation.json",
        "source_requests.json",
        "row_audit.json",
    ):
        write(
            live / name,
            {
                "status": "synthetic_fixture",
                "selected_nominal_tai": "synthetic_TAI",
                "central_patch_count": 1,
            },
        )
    (live / "live_physical.csv").write_text("synthetic_feature\n1\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    write(contract, {"synthetic": True})
    monkeypatch.setattr(
        native_data,
        "quality_policy_input",
        lambda _: {
            "sha256": "synthetic-policy",
            "bytes": 1,
            "document": {"synthetic": True},
        },
    )
    core = SimpleNamespace(
        checked_live=lambda *a: ({}, {}, {}, None, live / "source_manifest.json"),
        validate_physical=lambda *a, **k: None,
        verify_unchanged=lambda *a: None,
    )
    monkeypatch.setattr(native_data, "load", lambda *a: core)
    model = NativeModel(data_role=True)
    path = review_live(live, contract, tmp_path / "review", model)
    bundle_message = next(m for m in model.inputs[1] if isinstance(m, ToolMessage))
    assert bundle_message.name == "available24_read_live_bundle"
    assert (
        json.loads(bundle_message.content)["live_physical_csv"]
        == "synthetic_feature\n1\n"
    )
    received = json.loads(bundle_message.content)
    assert received["registered_quality_policy"]["document"] == {"synthetic": True}
    assert received["review_decision_contract"]["scope"] == received["review_scope"]
    assert "registered_quality_policy" in model.inputs[0][0].content
    binding = read(path.parent / "native_review_binding.json")
    assert (
        json.loads(bundle_message.content)["registered_row_audit_projection"][
            "unselected_per_patch_details_read_by_model"
        ]
        is False
    )
    result, hashes = load("available24_model").native_review(
        path, binding["manifest_sha256"], binding["contract_sha256"]
    )
    assert result["decision"] == "accept_with_limits"
    assert len(hashes) == 4
    assert binding["actual_tool_call_id"] == "submit-test-call"
    session = read(path.parent / "session.json")
    assert session["model_calls"] == 3 and session["tool_calls"] == 2
    with pytest.raises(ValueError, match="facts differ"):
        review_live(
            live,
            contract,
            tmp_path / "wrong_fact_review",
            NativeModel(data_role=True, wrong_facts=True),
        )
    assert not (tmp_path / "wrong_fact_review/data_review.json").exists()
    assert read(tmp_path / "wrong_fact_review/session.json")["status"] == "failed"


def forecast_fixture():
    return {
        "issued_utc": "2026-10-14T10:00:00Z",
        "prediction_window": {
            "start": "2026-10-14T10:00:00Z",
            "end": "2026-10-15T10:00:00Z",
        },
        "probabilities": {"C1.0+": 0.9, "M1.0+": 0.3},
        "live_manifest_sha256": "synthetic",
        "native_Data_review_sha256": "synthetic",
    }


def install_pipeline(
    monkeypatch, *, eligible=True, decision="accept_with_limits", render_failure=False
):
    calls = []
    monkeypatch.setattr(service, "load_forest", lambda _: object())

    def acquire(assets, output):
        calls.append("fetch")
        output.mkdir(parents=True)
        write(output / "live_qualification.json", {"reason": "synthetic_ineligible"})
        contract = output.parent / "contract.json"
        write(contract, {})
        return {
            "status": "eligible" if eligible else "failed",
            "contract": str(contract),
        }

    def review(live, contract, output, model):
        calls.append("review")
        output.mkdir()
        write(output / "review.json", {"decision": decision})
        return output / "review.json"

    def issue(assets, live, contract, review, output):
        calls.append("issue")
        output.mkdir()
        result = forecast_fixture()
        write(output / "issued_forecast.json", result)
        return result

    def report(value, assets, output):
        calls.append("report")
        if render_failure:
            raise ValueError("Synthetic renderer failure")
        output.mkdir(parents=True)
        (output / "report.md").write_text("Synthetic report", encoding="utf-8")
        (output / "report.html").write_text("Synthetic HTML", encoding="utf-8")

    monkeypatch.setattr(service, "fetch_live", acquire)
    monkeypatch.setattr(service, "review_live", review)
    monkeypatch.setattr(service, "issue", issue)
    monkeypatch.setattr(service, "build_report", report)
    return calls


def test_one_turn_deduplicates_and_new_equal_turn_is_separate(tmp_path, monkeypatch):
    calls = install_pipeline(monkeypatch)
    messages = [HumanMessage(content=REQUEST, id="human1")]
    first = service.execute_forecast(
        tmp_path, messages, "call1", object, tmp_path / "assets"
    )
    assert first["status"] == "completed"
    again = service.execute_forecast(
        tmp_path, messages, "call2", object, tmp_path / "assets"
    )
    assert first == again and calls == ["fetch", "review", "issue", "report"]
    third = service.execute_forecast(
        tmp_path,
        [*messages, HumanMessage(content=REQUEST, id="human2")],
        "call3",
        object,
        tmp_path / "assets",
    )
    assert first["attempt_id"] != third["attempt_id"]
    assert calls.count("issue") == 2


@pytest.mark.parametrize(
    "eligible,decision",
    [(False, "accept_with_limits"), (True, "reject"), (True, "revise")],
)
def test_bad_input_or_review_never_issues(tmp_path, monkeypatch, eligible, decision):
    calls = install_pipeline(monkeypatch, eligible=eligible, decision=decision)
    result = service.execute_forecast(
        tmp_path, [HumanMessage(content=REQUEST)], "main", object, tmp_path / "assets"
    )
    assert result["status"] == "not_issued" and not result["forecast_issued"]
    assert "issue" not in calls


def test_report_failure_preserves_issued_window_and_no_repeat(tmp_path, monkeypatch):
    calls = install_pipeline(monkeypatch, render_failure=True)
    messages = [HumanMessage(content=REQUEST)]
    result = service.execute_forecast(
        tmp_path, messages, "main", object, tmp_path / "assets"
    )
    assert result["status"] == "issued_report_failed" and result["forecast_issued"]
    original = read(tmp_path / result["forecast_path"])
    assert original["prediction_window"] == forecast_fixture()["prediction_window"]
    assert (
        service.execute_forecast(
            tmp_path, messages, "main", object, tmp_path / "assets"
        )
        == result
    )
    assert calls.count("issue") == 1


def test_frontend_graph_one_native_tool_and_verified_download_paths(
    tmp_path, monkeypatch
):
    install_pipeline(monkeypatch)
    monkeypatch.setattr(service, "workspace_root_from_config", lambda _: tmp_path)
    monkeypatch.setattr(
        "jw.middleware.flare_forecast.workspace_root_from_config", lambda _: tmp_path
    )
    monkeypatch.setattr(service, "reviewer_model", lambda _: object())
    model = NativeModel()
    graph = create_agent(
        model,
        tools=[service.forecast_flare_24h],
        middleware=[ResearchRouterMiddleware(model=model), FlareForecastMiddleware()],
    )
    result = graph.invoke({"messages": [HumanMessage(content=REQUEST)]})
    assert (
        len(model.inputs) == 1
    )  # No generic classifier/research or hallucinated final call.
    final = result["messages"][-1].content
    assert "90.00%" in final and "30.00%" in final
    assert "2026-10-14 18:00:00" in final
    assert "report.md" in final and "report.html" in final
    assert len(list((tmp_path / "outputs").rglob("issued_forecast.json"))) == 1


def test_modified_report_is_not_returned_as_verified(tmp_path, monkeypatch):
    install_pipeline(monkeypatch)
    messages = [HumanMessage(content=REQUEST)]
    result = service.execute_forecast(
        tmp_path, messages, "main", object, tmp_path / "assets"
    )
    (tmp_path / result["report_path"]).write_text("Tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        service.execute_forecast(
            tmp_path, messages, "main", object, tmp_path / "assets"
        )


def test_provider_parallel_calls_blocked_without_execution():
    middleware = FlareForecastMiddleware()
    request = ModelRequest(
        model=NativeModel(),
        tools=[service.forecast_flare_24h],
        messages=[HumanMessage(content=REQUEST)],
        state={},
    )
    calls = [
        {"name": "forecast_flare_24h", "id": f"parallel{i}", "args": {}}
        for i in range(2)
    ]
    response = middleware.wrap_model_call(
        request,
        lambda _: ModelResponse(result=[AIMessage(content="", tool_calls=calls)]),
    )
    assert not response.result[0].tool_calls
    assert "本次未获取" in response.result[0].content


@pytest.mark.asyncio
async def test_async_frontend_graph_returns_report_once(tmp_path, monkeypatch):
    calls = install_pipeline(monkeypatch)
    monkeypatch.setattr(service, "workspace_root_from_config", lambda _: tmp_path)
    monkeypatch.setattr(
        "jw.middleware.flare_forecast.workspace_root_from_config", lambda _: tmp_path
    )
    monkeypatch.setattr(service, "reviewer_model", lambda _: object())
    model = NativeModel()
    graph = create_agent(
        model,
        tools=[service.forecast_flare_24h],
        middleware=[ResearchRouterMiddleware(model=model), FlareForecastMiddleware()],
    )
    result = await graph.ainvoke({"messages": [HumanMessage(content=REQUEST)]})
    assert "report.html" in result["messages"][-1].content
    assert calls.count("issue") == 1


def test_deepseek_named_tool_payload_disables_default_thinking_locally():
    from langchain_openai import ChatOpenAI
    from jw.llm.patches import _patch_deepseek_reasoning_passback

    model = ChatOpenAI(
        model="deepseek-v4-pro",
        api_key="synthetic-key",
        base_url="https://api.deepseek.com",
        extra_body={"thinking": {"type": "enabled"}, "other": "preserved"},
        reasoning_effort="high",
    )
    _patch_deepseek_reasoning_passback(model)
    safe = bounded_tool_model(model)
    assert safe.extra_body == {"thinking": {"type": "disabled"}, "other": "preserved"}
    assert safe.reasoning_effort is None and safe.max_retries == 0
    assert safe.root_client.max_retries == 0
    assert model.extra_body["thinking"]["type"] == "enabled"
    request = ModelRequest(
        model=model,
        tools=[service.forecast_flare_24h],
        messages=[HumanMessage(content=REQUEST)],
        model_settings={"reasoning_effort": "high"},
        state={},
    )
    prepared = FlareForecastMiddleware()._prepare(request)
    assert prepared.model_settings["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in prepared.model_settings
    payload = prepared.model._get_request_payload(
        request.messages, **prepared.model_settings
    )
    assert "reasoning_effort" not in payload
    assert payload["extra_body"]["thinking"]["type"] == "disabled"


@pytest.mark.parametrize(
    "model_name,endpoint",
    [
        ("qwen3.8-max", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("qwen3.8-plus", "https://gateway.example.test/v1"),
        ("qwen3.7-max", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ],
)
def test_qwen_wire_payload_and_complete_tool_message_survive_passback(
    model_name, endpoint
):
    from langchain_openai import ChatOpenAI
    from jw.llm.patches import _patch_qwen_reasoning_passback

    original = ChatOpenAI(
        model=model_name,
        api_key="synthetic-key",
        base_url=endpoint,
        extra_body={
            "enable_thinking": True,
            "thinking_budget": 8192,
            "preserve_thinking": True,
        },
    )
    _patch_qwen_reasoning_passback(original)
    blob = '完整工具结果\\引号"' * 3000
    messages = [
        HumanMessage(content=REQUEST),
        AIMessage(
            content="",
            tool_calls=[{"name": "forecast_flare_24h", "args": {}, "id": "wire-call"}],
        ),
        ToolMessage(content=blob, tool_call_id="wire-call", name="forecast_flare_24h"),
    ]
    request = ModelRequest(
        model=original,
        tools=[service.forecast_flare_24h],
        messages=messages,
        state={},
        model_settings={
            "extra_body": {"preserve_thinking": True, "thinking_budget": 4096}
        },
    )
    prepared = FlareForecastMiddleware()._prepare(request)
    payload = prepared.model._get_request_payload(messages, **prepared.model_settings)
    assert payload["extra_body"]["enable_thinking"] is False
    assert "preserve_thinking" not in payload["extra_body"]
    assert "thinking_budget" not in payload["extra_body"]
    assert payload["messages"][-1]["content"] == blob
    assert original.extra_body["enable_thinking"] is True
    assert prepared.model.root_client.max_retries == 0


def test_data_role_uses_frontend_model_provider_instead_of_old_default(monkeypatch):
    selected = []
    monkeypatch.setattr(
        "jw.config.get_effective_config",
        lambda: SimpleNamespace(model="deepseek-v4-pro", provider="deepseek"),
    )
    monkeypatch.setattr(
        "jw.llm.get_chat_model",
        lambda **kwargs: (selected.append(kwargs), NativeModel())[1],
    )
    service.reviewer_model(
        {"configurable": {"model": "qwen3.8-max", "model_provider": "custom-openai"}}
    )
    assert selected == [
        {
            "model": "qwen3.8-max",
            "provider": "custom-openai",
            "max_retries": 0,
            "timeout": 150,
        }
    ]

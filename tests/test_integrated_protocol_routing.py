"""The merged B1 and cycle tools share routing, without sharing scientific scope."""

from unittest.mock import MagicMock

import pytest

from jw.middleware.research_router import (
    ResearchRouterMiddleware,
    _bounded_route_stages,
    _fallback_route,
    _with_analysis_protocol,
)
from jw.research_protocols import (
    B1_FLARE_CASE_PROTOCOL,
    SILSO_CYCLE_ASSOCIATIONS_PROTOCOL,
    SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
    detect_analysis_protocol,
    routing_request_text,
)
from jw.tools import get_tool_bundles


@pytest.mark.parametrize(
    "prompt",
    [
        "Use b1-flare-case-v1.",
        "Please compute B1-FLARE-CASE-V1.",
        "请计算 `b1-flare-case-v1` 的现有冻结案例。",
        "不要运行 SC3-SC6；请计算 b1-flare-case-v1。",
        "不要复现 SILSO 周期极值，而是计算 b1-flare-case-v1。",
        "请计算 b1-flare-case-v1；不要计算 SILSO 周期均值和累计活动。",
        "请计算 b1-flare-case-v1；来源比较提及 SC3-SC6 的 SILSO 周期形态。",
        "## 来源\n- SILSO 周期极值\n## 本轮目标\n请计算 b1-flare-case-v1。",
    ],
)
def test_explicit_b1_survives_shared_middleware_preprocessing(prompt):
    assert detect_analysis_protocol(prompt) == B1_FLARE_CASE_PROTOCOL
    cleaned = routing_request_text(prompt)
    assert B1_FLARE_CASE_PROTOCOL in cleaned
    assert detect_analysis_protocol(cleaned) == B1_FLARE_CASE_PROTOCOL
    assert (
        detect_analysis_protocol(routing_request_text(cleaned))
        == B1_FLARE_CASE_PROTOCOL
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "研究 B1 耀斑触发前兆。",
        "A2 JW-FD flare prediction",
        "Use b1-flare-case-v10.",
        "Use b1-flare-case-v1-extension.",
        "## Sources\n- Use b1-flare-case-v1.\n- SILSO data",
        "## 输入清单\n1. b1-flare-case-v1\n2. b1-sharp-11158-v1",
        "只总结这些来源\n## 来源\nb1-flare-case-v1\n",
        "来源：Use b1-flare-case-v1.\n请说明材料是否齐全。",
        "> Use b1-flare-case-v1.\n请解释引用格式。",
        "```text\nUse b1-flare-case-v1.\n```\n请解释这段示例。",
        "| 来源 | 协议 |\n| --- | --- |\n| 例子 | b1-flare-case-v1 |",
        "请读取 https://example.org/b1-flare-case-v1 并说明来源。",
        "请读取 `inputs/b1-flare-case-v1.json` 的文件头。",
        "请计算 `inputs/b1-flare-case-v1.csv` 的文件行数。",
        "Do not run b1-flare-case-v1.",
        "不要运行 b1-flare-case-v1。",
        "不要计算 SILSO 的均值，b1-flare-case-v1 也不要运行。",
        "b1-flare-case-v1 is not requested.",
        "b1-flare-case-v1 不做。",
        "仅做文献综述；讨论 b1-flare-case-v1，不进行新的计算。",
        "Literature review only: discuss b1-flare-case-v1; no new computation.",
    ],
)
def test_metadata_negation_and_adjacent_topics_do_not_opt_in_to_b1(prompt):
    assert detect_analysis_protocol(prompt) == "none"
    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)
    assert route.get("required_analysis_protocol", "none") == "none"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "不要计算 b1-flare-case-v1；请计算 SILSO 周期均值与累计活动。",
            SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
        ),
        (
            "请计算 SILSO 周期均值与累计活动。\n## Sources\n- b1-flare-case-v1",
            SILSO_CYCLE_DESCRIPTIVE_PROTOCOL,
        ),
        (
            "不要计算 b1-flare-case-v1；请计算 SC3-SC6 关联 "
            "[D_PROTOCOL=silso_cycle_associations_v1] [D_BATCH=D2]。",
            SILSO_CYCLE_ASSOCIATIONS_PROTOCOL,
        ),
    ],
)
def test_explicit_cycle_target_remains_routable_alongside_excluded_b1(prompt, expected):
    assert detect_analysis_protocol(prompt) == expected
    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)
    assert route["required_analysis_protocol"] == expected
    assert route["required_specialist"] == "solar-data"
    assert _bounded_route_stages(route) == ("data",)


@pytest.mark.parametrize(
    ("prompt", "stages"),
    [
        ("请计算 b1-flare-case-v1 并生成数据报告。", ("data",)),
        ("请计算 b1-flare-case-v1，不要形成假设。", ("data",)),
        (
            "请计算 b1-flare-case-v1。\n> 生成三个科学假设。\n仅输出数据结果。",
            ("data",),
        ),
        (
            "请计算 b1-flare-case-v1 并形成至多三个假设。不要完整研究。",
            ("data", "hypothesis"),
        ),
        (
            "使用现有数据计算 b1-flare-case-v1，比较已提出的科学假设。",
            ("data", "hypothesis"),
        ),
    ],
)
@pytest.mark.parametrize("classifier_available", [False, True])
def test_b1_fallback_and_classifier_preserve_requested_stage_boundary(
    monkeypatch, prompt, stages, classifier_available
):
    monkeypatch.setattr("jw.middleware.research_router.disable_thinking", lambda x: x)
    model = MagicMock()
    classifier = model.with_structured_output.return_value
    if classifier_available:
        # A classifier must not add a hypothesis or full loop to a Data-only turn.
        classifier.invoke.return_value = {
            "mode": "full_research",
            "source_mode": "mixed",
            "needs_computation": True,
            "task_intent": "hypothesis_generation",
            "required_specialist": "solar-hypothesis",
            "reason": "synthetic classifier response",
        }
    else:
        classifier.invoke.side_effect = RuntimeError("classifier unavailable")
    middleware = ResearchRouterMiddleware(model=model)
    route = middleware._route_sync(prompt)
    assert route["mode"] == "verified_analysis"
    assert route["required_analysis_protocol"] == B1_FLARE_CASE_PROTOCOL
    assert route["needs_computation"] is True
    assert _bounded_route_stages(route) == stages
    sent_messages = classifier.invoke.call_args.args[0]
    assert B1_FLARE_CASE_PROTOCOL in sent_messages[-1]["content"]


@pytest.mark.asyncio
async def test_b1_async_fallback_also_keeps_data_then_hypothesis(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("jw.middleware.research_router.disable_thinking", lambda x: x)
    model = MagicMock()
    model.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("classifier unavailable")
    )
    middleware = ResearchRouterMiddleware(model=model)
    route = await middleware._route_async(
        "请计算 b1-flare-case-v1 并形成至多三条科学假设。不要完整研究。"
    )
    assert route["required_analysis_protocol"] == B1_FLARE_CASE_PROTOCOL
    assert route["mode"] == "verified_analysis"
    assert _bounded_route_stages(route) == ("data", "hypothesis")


def test_explicit_full_b1_research_is_not_silently_narrowed():
    prompt = "请使用 b1-flare-case-v1 完成完整研究。"
    route = _with_analysis_protocol(_fallback_route(prompt), text=prompt)
    assert route["mode"] == "full_research"
    assert route["required_analysis_protocol"] == B1_FLARE_CASE_PROTOCOL


def test_registered_solar_bundle_contains_both_computation_families():
    names = [tool.name for tool in get_tool_bundles()["solar-features"]]
    expected = {
        "solar_data_open_context",
        "run_silso_cycle_descriptive",
        "run_silso_cycle_phase",
        "run_silso_gnbb2_annual",
        "run_silso_cycle_sensitivity",
        "run_silso_cycle_associations",
        "run_b1_flare_case",
        "reproduce_silso_cycle_extrema",
        "run_silso_cycle_morphology",
    }
    assert expected <= set(names)
    assert len(names) == len(set(names))

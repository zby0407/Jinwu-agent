from pathlib import Path

import pytest

from jw.reproduction.suite import H1_PROMPT, H2_PROMPT
from jw.research_protocols import (
    F107_DISCONTINUITY_PROTOCOL,
    SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
    SILSO_CYCLE_REPRODUCTION_PROTOCOL,
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
    detect_analysis_protocol,
    is_literature_only_request,
    routing_request_text,
)


@pytest.fixture
def actual_request() -> str:
    return (Path(__file__).parent / "fixtures/literature_scope_request.md").read_text(
        encoding="utf-8"
    )


def test_full_evidence_review_request_cannot_be_replaced_by_a_data_protocol(
    actual_request,
):
    assert is_literature_only_request(actual_request)
    assert detect_analysis_protocol(actual_request) == "none"
    prose = routing_request_text(actual_request)
    assert "不进入 Data/Experiment" in prose
    assert "INPUT_BINDING_BLOCKED" in prose
    assert "TableCyclesMiMa.txt" not in prose
    assert (
        "a4b5b8812c9e966f013c55655176cbd22531e7afd0c304cb93cc38e00e50e4a0" not in prose
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "完成全文证据综述。不要开展新的回归、数值实验或预测。核验 SILSO 周期极值。",
        "仅做文献与既有资料核验。检查 SILSO 周期极值，不进入 Data/Experiment。",
        "只允许文献与既有资料核验，说明 F10.7 断点争议。",
        "仅基于文献完成证据综述；不进行新的计算。比较极区磁场前兆解释。",
        "文献与现有证据的全文审查；不进行新统计检验。涉及 SILSO 极值及周期。",
        "只允许已发表证据综述，涉及 F10.7 的 1980 年不连续性。",
        "Full-text evidence review only. Do not run data analysis or experiments.",
        "Review the full texts; no new computation. Discuss SILSO cycle extrema.",
        "Do a literature review without new regression or simulations.",
        "Literature review only: review polar-field precursors and cycle minima.",
    ],
)
def test_explicit_literature_scope_synonyms_take_precedence(prompt):
    assert is_literature_only_request(prompt)
    assert detect_analysis_protocol(prompt) == "none"


def test_source_inventory_and_hash_table_alone_do_not_select_a_protocol(actual_request):
    sources = actual_request.split("## 5.", 1)[1].split("## 6.", 1)[0]
    hashes = actual_request.split("## 6.", 1)[1]
    for section in (sources, hashes):
        assert detect_analysis_protocol(section) == "none"


@pytest.mark.parametrize(
    "prompt",
    [
        "inputs/solar_cycle/silso/cycle_extrema_v2/TableCyclesMiMa.txt",
        "- `inputs/F107_1980_breakpoint.csv`\n- `inputs/SILSO_cycle_extrema.csv`",
        "## Sources\n- Reproduce SILSO cycle extrema (a paper title)\n- F10.7 breakpoint study",
        "来源：Use SILSO cycle extrema\n参考文献：Analyze F10.7 drift",
        "| 来源 | 题名 |\n| -- | -- |\n| SILSO | Reproduce cycle extrema |",
        "> Reproduce SILSO cycle extrema.\n只是引用材料，请解释引用格式。",
        "```text\nAnalyze the F10.7 breakpoint\n```\n列出材料缺口。",
        "请读取 `inputs/silso/solar_cycle_extrema.csv` 的文件头。",
        "请核验 `C:\\sources\\F107_1980_breakpoint.csv` 的 SHA256。",
        "请分析资料准备是否齐全：\n- `silso-cycle-extrema-v2`",
    ],
)
def test_paths_bibliography_and_quoted_material_are_not_computation_intent(prompt):
    assert detect_analysis_protocol(prompt) == "none"


def test_explicit_file_read_keeps_evidence_dependency_without_filename_authority():
    cleaned = routing_request_text("请读取 `inputs/SILSO_cycle_extrema.csv`。")
    assert "读取" in cleaned
    assert "file" in cleaned
    assert "SILSO" not in cleaned


@pytest.mark.parametrize(
    "prompt",
    [
        "不要分析 F10.7 在 1980 年的断点。",
        "禁止复现 SILSO cycles 21-24 extrema。",
        "不要运行 SILSO 周期形态统计。",
        "Do not analyze the F10.7 breakpoint or run regression.",
        "Do not run F10.7 regression, Bootstrap or simulation.",
        "F10.7 在 1980 年的断点分析不做。",
        "No need to analyze the F10.7 breakpoint or calculate regression.",
        "F10.7 regression is not requested.",
        "不进入 Data/Experiment，不开展新回归、Bootstrap、模拟、预测或统计检验。",
    ],
)
def test_negated_computation_does_not_become_an_affirmative_request(prompt):
    assert detect_analysis_protocol(prompt) == "none"
    positive = routing_request_text(prompt, affirmative_only=True)
    assert not any(
        term in positive.casefold() for term in ("bootstrap", "回归", "regression")
    )


@pytest.mark.parametrize(
    ("prompt", "protocol"),
    [
        (
            "不要分析 F10.7 的断点；请使用 SILSO 复现周期21至24的极值和上升时间。",
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        ),
        (
            "不要复现 SILSO 周期极值，而是比较极小附近极区磁场作为下一周期振幅前兆。",
            SOLAR_POLAR_PRECURSOR_PROTOCOL,
        ),
        (
            "Do not reproduce SILSO cycle extrema, but analyze the F10.7 breakpoint.",
            F107_DISCONTINUITY_PROTOCOL,
        ),
        (
            "先完成文献综述，不做预测；请复现 SILSO 周期21至24极值。",
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        ),
        (
            "先做文献综述，再计算 SILSO 周期21至24的极值。",
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        ),
        (
            "仅使用文献中的数据，计算 SILSO 周期21至24的极值。",
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        ),
        (
            "Only use published evidence; compute the SILSO cycle extrema for cycles 21-24.",
            SILSO_CYCLE_REPRODUCTION_PROTOCOL,
        ),
        (H1_PROMPT, SILSO_CYCLE_MORPHOLOGY_PROTOCOL),
        (H2_PROMPT, SOLAR_POLAR_PRECURSOR_PROTOCOL),
        (
            "Can polar fields predict the next solar cycle amplitude from the following cycles?",
            SOLAR_POLAR_PRECURSOR_PROTOCOL,
        ),
        (
            "上一活动周长度是否调制极区场对下一活动周振幅的前兆关系？",
            SOLAR_POLAR_PRECURSOR_PROTOCOL,
        ),
        (
            "使用已经验证的上传 solar_precursor_cycle_features.csv，直接完成下游统计分析；"
            "报告 Pearson、Spearman、Bootstrap、留一和 MWO/WSO 分时期结果。",
            SOLAR_POLAR_PRECURSOR_PROTOCOL,
        ),
    ],
)
def test_actual_calculation_requests_remain_routable(prompt, protocol):
    assert not is_literature_only_request(prompt)
    assert detect_analysis_protocol(prompt) == protocol


def test_scope_cleaning_preserves_negation_until_affirmative_view_is_requested():
    text = "只做研究计划，不做实验；请读取 `inputs/source.csv`。"
    assert "不做实验" in routing_request_text(text)
    assert "不做实验" not in routing_request_text(text, affirmative_only=True)

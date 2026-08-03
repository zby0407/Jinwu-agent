from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scientific_hypothesis.reader_view import (  # noqa: E402
    render_hypothesis_reader_markdown,
)


def _candidate(candidate_id: str, statement: str, objective: str) -> dict:
    return {
        "id": candidate_id,
        "statement": statement,
        "applicability": "只适用于统一口径后的两个太阳活动周期比较。",
        "mechanism": {
            "summary": "候选机制改变上升阶段的可观测形态",
            "physical_basis": "当前只有机制动机。",
            "required_premises": ["候选机制确实发生变化", "代理量能够反映该变化"],
        },
        "scope_conditions": {
            "does_not_apply_when": ["分组复算后预测方向相反"],
            "generalization_limits": ["不能外推到其他太阳活动周期"],
        },
        "predictions": [
            {
                "statement": f"{candidate_id} 预期分组复算后方向保持一致",
                "would_weaken_if": f"{candidate_id} 的预测方向发生反转",
            }
        ],
        "alternative_explanations": ["相同现象也可能由测量误差造成"],
        "confounders": ["观测口径变化可能伪装成机制变化"],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "evidence_gaps": ["尚无直接的跨周期比较证据"],
        "epistemic_status": {"empirical_support": "none"},
        "uncertainty": {
            "sources": ["代理量与真实物理量之间的映射不确定"],
            "implications": "映射失败会使机制链无法识别",
        },
        "next_test": {
            "objective": objective,
            "discriminating_power": "结果稳定时削弱 H0_meas_null，反转时削弱 H5_far",
        },
        "confidence": {"level": "low", "basis": "目前缺少直接实证支持"},
    }


def test_reader_view_keeps_scientific_boundaries_but_hides_internal_audit() -> None:
    measurement = _candidate(
        "H0_meas_null",
        "观测差异可能主要来自测量和处理方法，而不是真实物理差异。",
        "先做处理方法敏感性分析",
    )
    deep = _candidate(
        "H5_far",
        "深层状态变化可能改变环向场形成的时滞。",
        "比较深层结构代理量",
    )
    snapshot = {
        "draft_sha256": "a" * 64,
        "draft": {
            "research_question": "周期 24 的恢复过程是否比周期 25 更慢？",
            "candidates": [measurement, deep],
        },
        "tail_review": {
            "schema_version": "scientific-hypothesis-tail-review-v2",
            "candidates": [
                {
                    "candidate_id": "H0_meas_null",
                    "generation_operator": "measurement_null",
                    "search_region": "null_control",
                    "tail_metrics": {
                        "mechanism_distance": "low",
                        "prediction_disagreement": "high",
                        "expected_information_gain": "high",
                        "falsifiability": "high",
                        "evidence_risk": "low",
                        "test_cost": "low",
                    },
                },
                {
                    "candidate_id": "H5_far",
                    "generation_operator": "regime_boundary",
                    "search_region": "positive_tail",
                    "tail_metrics": {
                        "mechanism_distance": "high",
                        "prediction_disagreement": "medium",
                        "expected_information_gain": "medium",
                        "falsifiability": "medium",
                        "evidence_risk": "high",
                        "test_cost": "high",
                    },
                },
            ],
        },
    }

    markdown = render_hypothesis_reader_markdown(snapshot)

    assert "## 先说结论" in markdown
    assert "待核验的研究前提" in markdown
    assert "最先做的检验" in markdown
    assert markdown.index("先做处理方法敏感性分析") < markdown.index(
        "比较深层结构代理量"
    )
    for label in (
        "主张",
        "适用边界",
        "成立需要",
        "怎样与其他解释区分",
        "其他可能性与混杂",
        "证据与不确定性",
        "最值得做的下一步",
        "当前把握",
    ):
        assert label in markdown
    for internal in (
        "draft_sha256",
        "schema_version",
        "null_control",
        "positive_tail",
        "Pareto",
        "rubric_reward",
        "H0_meas_null",
        "H5_far",
    ):
        assert internal not in markdown
    assert len(markdown) < 6_000


def test_reader_view_marks_recovered_budget_stop_as_partial() -> None:
    snapshot = {
        "soft_warning_count": 1,
        "soft_warnings": [{"code": "ungrounded_numeric_threshold"}],
        "draft": {
            "research_question": "一个待核验问题",
            "candidates": [_candidate("H1", "一种待检验解释。", "执行区分性复算")],
        },
    }

    markdown = render_hypothesis_reader_markdown(
        snapshot,
        partial_reason="model budget stopped",
    )

    assert "本次生成提前停止" in markdown
    assert "不能把下面内容当作最终结论" in markdown
    assert "没有依据的数值门槛" in markdown
    assert "不能把它当作最终结论" in markdown


def test_reader_view_removes_compact_ids_and_awkward_internal_phrasing() -> None:
    candidate = _candidate(
        "H1_meridional_slowdown",
        (
            "周期24恢复较慢主要由两个周期极小期附近经向环流速度差异驱动："
            "周期24附近的环流较慢。"
        ),
        "比较两个周期的经向环流",
    )
    candidate["scope_conditions"]["generalization_limits"] = [
        "不能外推到非相邻太阳活动周期"
    ]
    candidate["predictions"][0]["statement"] += "。"
    candidate["predictions"][0]["would_weaken_if"] = "如果H1的预测方向发生反转"
    candidate["confidence"]["basis"] = "缺少实证，故置信度为medium。"
    snapshot = {
        "draft": {
            "research_question": "周期24是否恢复得更慢？",
            "candidates": [candidate],
        },
        "tail_review": {
            "candidates": [
                {
                    "candidate_id": "H1_meridional_slowdown",
                    "generation_operator": "modal_baseline",
                }
            ]
        },
    }

    markdown = render_hypothesis_reader_markdown(snapshot)

    assert "## 候选 1：经向环流速度差异" in markdown
    assert "H1" not in markdown
    assert "置信度为medium" not in markdown
    assert "**当前把握：** 低。缺少实证" in markdown
    assert "不能外推为：不能外推到" not in markdown
    assert "外推限制：非相邻太阳活动周期" in markdown
    assert "。；" not in markdown
    assert "如果如果" not in markdown


def test_reader_view_translates_process_language_into_human_prose() -> None:
    hemispheric = _candidate(
        "H1_hemispheric_asynchrony",
        (
            "周期24的双峰主要由南北半球黑子活动上升的时间不同步造成："
            "两个半球先后达到局部峰值。"
        ),
        "分别比较南北半球的局部峰值",
    )
    hemispheric["evidence_gaps"] = ["尚无半球独立序列分析绑定为直接证据"]
    hemispheric["confidence"]["basis"] = (
        "该解释且有 Wiki 中半球耦合机制的一般性支持。"
        "置信度为中等，因为尚无直接实证支持。"
    )
    global_state = _candidate(
        "H2_global_dynamo_two_stage",
        (
            "周期24的双峰反映了全球发电机内部的两阶段环向场生成过程："
            "两个阶段分别产生局部峰值。"
        ),
        "比较两个半球的拐点是否同步",
    )
    coupling = _candidate(
        "H3_coupling_breakdown",
        ("周期24的双峰源于上升期期间跨赤道磁耦合的暂时减弱：两个半球短暂独立演化。"),
        "寻找跨赤道耦合暂时减弱的独立证据",
    )
    snapshot = {
        "draft": {
            "research_question": (
                "把下面这句话仅作为待核验的研究前提，而不是已证实事实："
                "周期24的双峰主要来自南北半球不同步。"
                "请形成长尾假设组合。"
            ),
            "candidates": [hemispheric, global_state, coupling],
        },
        "tail_review": {
            "schema_version": "internal",
            "candidates": [
                {
                    "candidate_id": candidate["id"],
                    "generation_operator": operator,
                    "search_region": region,
                    "rubric_reward": 1,
                    "tail_metrics": {
                        "expected_information_gain": "high",
                        "falsifiability": "high",
                        "test_cost": "medium",
                        "evidence_risk": "high",
                    },
                }
                for candidate, operator, region in (
                    (hemispheric, "modal_baseline", "modal_baseline"),
                    (global_state, "premise_reversal", "positive_tail"),
                    (coupling, "symmetry_break", "negative_tail"),
                )
            ],
        },
    }

    markdown = render_hypothesis_reader_markdown(snapshot)

    assert "把下面这句话" not in markdown
    assert "## 候选 1：南北半球黑子活动上升的时间不同步" in markdown
    assert "## 候选 2：全球发电机内部的两阶段环向场生成过程" in markdown
    assert "## 候选 3：跨赤道磁耦合的暂时减弱" in markdown
    assert "尚无半球独立序列分析能够作为直接证据" in markdown
    assert "背景材料" in markdown
    assert "置信度为中等" not in markdown
    for internal in (
        "Wiki",
        "绑定",
        "审查记录",
        "任务状态",
        "schema_version",
        "rubric_reward",
        "modal_baseline",
        "positive_tail",
        "negative_tail",
        "Pareto",
    ):
        assert internal not in markdown

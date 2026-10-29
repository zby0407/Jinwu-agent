"""Synthetic authoring-language checks; no B3 observations or scientific results."""

from __future__ import annotations

from copy import deepcopy

import pytest

from research_quality.contracts import ContractError, validate_analysis_claim_contract
from tests.test_quality_indexed_forward_validation import _contract


@pytest.mark.parametrize(
    "design",
    [
        "不用训练相关，只做 out-of-sample 逐期留出验证。",
        "不以训练相关评价预测；采用样本外误差。",
        "禁止使用训练相关系数作为验证；采用时间顺序逐期留出。",
        "训练相关性不能替代样本外验证；实际使用 rolling-origin 误差。",
        "训练相关不是验证依据；实际使用 out-of-sample 误差。",
        "out-of-sample rolling-origin; do not use training correlation as validation.",
        "Training correlation cannot replace out-of-sample validation; use temporal holdout errors.",
        "Training correlation is not evidence of skill; use out-of-sample errors.",
        "Never use train correlation for validation; external holdout errors define the comparison.",
        "No use of training correlation is allowed; perform out-of-sample validation.",
        "out-of-sample errors rather than training correlation determine the comparison.",
        "本研究明确不使用训练相关；实际采用样本外误差。",
        "We do not use training correlation; use out-of-sample errors.",
    ],
)
def test_explicit_training_correlation_exclusion_with_affirmative_oos_is_allowed(
    design,
):
    value = _contract(design)
    original = deepcopy(value)
    assert validate_analysis_claim_contract(value) == original
    assert value == original


@pytest.mark.parametrize(
    "design",
    [
        "out-of-sample label; training correlation establishes predictive validity.",
        "样本外标签下仍用训练相关证明预测有效。",
        "不用训练相关；但仍用训练相关判定样本外预测有效。",
        "Do not use training correlation, but use training correlation to establish out-of-sample validity.",
        "样本外标签下不得不用训练相关证明预测有效。",
        "样本外标签下不能不使用训练相关。",
        "没有禁止使用训练相关；也采用样本外误差。",
        "不禁止使用训练相关；也采用样本外误差。",
        "不只用训练相关，还采用样本外误差。",
        "Not only training correlation but also out-of-sample errors determine success.",
        "It is not prohibited to use training correlation; use out-of-sample errors too.",
        "Training correlation is not the only criterion; use out-of-sample errors too.",
        "样本外评估；并非不使用训练相关作为预测判据。",
        "样本外评估；不是不使用训练相关作为预测判据。",
        "样本外评估；禁止不使用训练相关作为预测判据。",
        "out-of-sample; it is false that we do not use training correlation as validation.",
        "样本外评估；有人声称不使用训练相关作为预测判据。",
        "out-of-sample; a witness claims we do not use training correlation as validation.",
    ],
)
def test_affirmative_mixed_or_double_negative_training_claim_still_rejects(design):
    value = _contract(design)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="training correlation"):
        validate_analysis_claim_contract(value)
    assert value == original


@pytest.mark.parametrize(
    "design",
    [
        "不用训练相关，也不做样本外验证。",
        "Do not use training correlation; do not conduct rolling-origin out-of-sample validation.",
        "训练相关不能替代样本外验证。",
        "Training correlation cannot replace out-of-sample validation.",
        "Do not use training correlation as evidence of out-of-sample skill.",
        "样本外验证不采用；不用训练相关。",
        "rolling-origin is not used; training correlation is not evidence of skill.",
        "样本外训练相关不能替代验证。",
        "Out-of-sample training correlation cannot establish skill.",
        "不用训练相关；没有计划进行样本外验证。",
        "Do not use training correlation; there are no plans to perform out-of-sample validation.",
        "时间外推不做；按周期顺序逐步扩张训练集；第k周的预测只允许使用第k周极小时刻之前可得的信息。",
    ],
)
def test_negative_only_oos_mentions_cannot_satisfy_prediction_gate(design):
    value = _contract(design)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(value)
    assert value == original


@pytest.mark.parametrize(
    ("estimand", "analysis"),
    [
        ("只描述本次合成材料，不进行预测。", "只描述分布，不做预报。"),
        ("描述性合成检查，不作预测。", "当前描述，预测不是本次目标。"),
        (
            "A synthetic descriptive quantity, not a predictive claim.",
            "No forecasting claim is made.",
        ),
        ("A synthetic descriptive quantity.", "This is not a forecast."),
        ("只描述合成材料，不预测。", "本次不预报。"),
        ("A synthetic descriptive quantity.", "We do not forecast."),
    ],
)
def test_explicitly_nonpredictive_mentions_do_not_invent_a_forecasting_requirement(
    estimand, analysis
):
    value = _contract(
        "Synthetic descriptive consistency check only; no future validation is proposed."
    )
    value.update(estimand=estimand, primary_analysis=analysis)
    original = deepcopy(value)
    assert validate_analysis_claim_contract(value) == original
    assert value == original


@pytest.mark.parametrize(
    ("estimand", "analysis"),
    [
        ("不进行预测。", "但本方案预测后续目标。"),
        ("Not a prediction.", "However this method forecasts a future target."),
        ("不只是预测目标。", "描述与预测同时进行。"),
        ("不能不进行预测。", "Synthetic method."),
        ("Not only a forecasting claim.", "Synthetic method."),
        ("不进行预测；下一活动周为目标。", "Synthetic method."),
        ("Not a prediction; next cycle is the target.", "Synthetic method."),
        ("Synthetic predictive quantity.", "Forecasting has not been executed yet."),
        ("Synthetic descriptive quantity.", "We do not not forecast."),
        ("并非不进行预测。", "Synthetic method."),
        ("不是本方案不进行预测。", "Synthetic method."),
        ("只作合成描述，不进行", "预测后续目标。"),
    ],
)
def test_remaining_positive_mixed_or_next_cycle_target_keeps_oos_requirement(
    estimand, analysis
):
    value = _contract("Synthetic descriptive consistency check only.")
    value.update(estimand=estimand, primary_analysis=analysis)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(value)
    assert value == original


def test_monthly_independence_guard_is_not_waived_by_training_negation():
    value = _contract("不用训练相关；以 out-of-sample 逐期误差评估。")
    value.update(
        estimand="Synthetic next cycle prediction.",
        independent_sample_unit="monthly record",
    )
    with pytest.raises(ContractError, match="monthly records"):
        validate_analysis_claim_contract(value)


@pytest.mark.parametrize("label", ["时间外推", "前向链式", "滚动起点", "留未来验证"])
def test_chinese_design_labels_alone_do_not_replace_complete_cutoff_description(label):
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(_contract(label))


@pytest.mark.parametrize(
    "unit",
    [
        "完整太阳活动周，不是月度点",
        "solar cycles (not monthly records)",
        "完整太阳活动周（月度点不是独立单位）",
        "solar cycles rather than monthly records",
    ],
)
def test_explicit_cycle_units_do_not_become_monthly_units_because_of_exclusions(unit):
    value = _contract("rolling-origin out-of-sample validation.")
    value.update(estimand="Synthetic next cycle target.", independent_sample_unit=unit)
    original = deepcopy(value)
    assert validate_analysis_claim_contract(value) == original
    assert value == original


@pytest.mark.parametrize(
    "unit",
    [
        "monthly records, not solar cycles",
        "solar cycles and monthly records",
        "完整太阳活动周，也使用月度点",
        "solar cycles, not only monthly records",
        "solar cycles, not not monthly records",
        "完整太阳活动周，不是不使用月度点",
        "not monthly records",
        "not solar cycles; not monthly records",
        "不是完整太阳活动周，也不是月度点",
        "not monthly records; target is solar cycles",
    ],
)
def test_positive_mixed_double_negative_or_undefined_actual_unit_still_rejects(unit):
    value = _contract("rolling-origin out-of-sample validation.")
    value.update(estimand="Synthetic next cycle target.", independent_sample_unit=unit)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="monthly records"):
        validate_analysis_claim_contract(value)
    assert value == original

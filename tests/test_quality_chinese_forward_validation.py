"""Offline authoring-language fixtures, never scientific inputs or conclusions."""

from __future__ import annotations

import pytest

from research_quality.contracts import (
    ANALYSIS_CLAIM_VERSION,
    ContractError,
    validate_analysis_claim_contract,
)

TRAIN = "训练集只含签发时刻早于检验周期的周期"
INPUT = "每一折的预测子只使用该折信息截止前已完成的观测"
PROCESS = "禁止使用跨越截止的平滑或后验校准版本"
EXACT_FIRST_DESIGN = (
    "时间外推设计：按周期分块，训练集只含签发时刻早于检验周期的周期；"
    "留一周期与前向链式两种切分均预注册。"
    "每一折的预测子只使用该折信息截止前已完成的观测，"
    "禁止使用跨越截止的平滑或后验校准版本。"
    "区间以周期为重采样单位做分块 bootstrap；同时报告可用配对数与功效上限。"
    "不把月度点当作独立重采样单位，不做随机 K 折（会造成同周期内泄漏）。"
)


def contract(validation: str) -> dict:
    fields = (
        "estimand independent_sample_unit observation_cutoff information_set "
        "primary_analysis baseline validation_design decision_rule missingness "
        "censoring data_revision measurement_regime effect_size uncertainty_interval "
        "sensitivity_analysis influence_analysis"
    ).split()
    value = dict.fromkeys(fields, "Synthetic unexecuted method fixture")
    value.update(
        schema_version=ANALYSIS_CLAIM_VERSION,
        estimand="Synthetic predictive response",
        primary_analysis="Synthetic forecast protocol",
        independent_sample_count=None,
        measurement_kind="not_assessed",
        validation_design=validation,
        outcome_branches=[
            {"outcome": "Fixture A", "claim_update": "Conditional fixture A"},
            {"outcome": "Fixture B", "claim_update": "Conditional fixture B"},
        ],
    )
    return value


def test_complete_chinese_temporal_description_no_longer_requires_english_alias():
    value = contract(EXACT_FIRST_DESIGN)
    result = validate_analysis_claim_contract(value)
    assert result == value
    assert result["validation_design"] == EXACT_FIRST_DESIGN


@pytest.mark.parametrize(
    "description",
    [
        EXACT_FIRST_DESIGN.replace("时间外推设计", "滚动起点设计"),
        "前向链式验证；" + TRAIN + "；" + INPUT + "；" + PROCESS,
        "时间外推\n" + TRAIN + "；\n" + INPUT + "；\n" + PROCESS,
        EXACT_FIRST_DESIGN + "禁止使用未来标签调参。",
    ],
)
def test_equivalent_explicit_cutoff_descriptions_are_accepted(description):
    assert validate_analysis_claim_contract(contract(description))


@pytest.mark.parametrize(
    "description",
    [
        "时间外推",
        "前向验证",
        "按周期分块，留一周期交叉验证",
        EXACT_FIRST_DESIGN.replace(TRAIN, "训练集使用全部周期"),
        EXACT_FIRST_DESIGN.replace(INPUT, "每折使用全部观测"),
        EXACT_FIRST_DESIGN.replace(PROCESS, "允许跨越截止的平滑"),
        EXACT_FIRST_DESIGN + "采用随机 K 折作为主验证。",
        EXACT_FIRST_DESIGN + "不做随机 K 折，仅采用随机切分。",
        EXACT_FIRST_DESIGN + "使用未来标签调参。",
        EXACT_FIRST_DESIGN + "纳入未来观测完成选择。",
    ],
)
def test_new_language_path_rejects_partial_or_contradictory_designs(description):
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(contract(description))


def test_existing_training_correlation_rejection_is_retained():
    with pytest.raises(ContractError, match="training correlation"):
        validate_analysis_claim_contract(
            contract(EXACT_FIRST_DESIGN + "以训练相关性作为预测成立依据。")
        )


def test_existing_independent_cycle_unit_rejection_is_retained():
    value = contract(EXACT_FIRST_DESIGN)
    value.update(
        estimand="Synthetic next cycle forecast", independent_sample_unit="月度记录"
    )
    with pytest.raises(ContractError, match="monthly records"):
        validate_analysis_claim_contract(value)


def test_existing_english_authoring_path_remains_compatible():
    assert validate_analysis_claim_contract(contract("rolling-origin temporal holdout"))

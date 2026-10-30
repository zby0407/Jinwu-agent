"""Indexed temporal-language fixtures are engineering syntax, never science."""

from __future__ import annotations

from copy import deepcopy

import pytest

from research_quality.contracts import (
    ANALYSIS_CLAIM_VERSION,
    ContractError,
    validate_analysis_claim_contract,
)

EXACT_INDEXED_DESIGN = (
    "时间外推验证：按周期顺序逐步扩张训练集；"
    "第k周的预测只允许使用第k周极小时刻之前可得的信息。"
)


def _contract(validation: str) -> dict:
    # Every field is authored explicitly for this unexecuted technical fixture.
    return {
        "schema_version": ANALYSIS_CLAIM_VERSION,
        "estimand": "Synthetic predictive protocol used only to test syntax.",
        "independent_sample_unit": "Synthetic independent block, not measured data.",
        "independent_sample_count": None,
        "observation_cutoff": "Synthetic proposed cutoff; no observation is claimed.",
        "information_set": "Synthetic proposed information boundary.",
        "primary_analysis": "Synthetic forecast method, never executed.",
        "baseline": "Synthetic comparison definition with no measured performance.",
        "validation_design": validation,
        "decision_rule": "Synthetic prospective rule; no decision is asserted.",
        "missingness": "Synthetic missingness handling remains unexecuted.",
        "censoring": "Synthetic censoring assessment remains unexecuted.",
        "data_revision": "Synthetic version handling with no actual source loaded.",
        "measurement_regime": "Synthetic measurement definition only.",
        "measurement_kind": "not_assessed",
        "effect_size": "No effect has been measured in this engineering fixture.",
        "uncertainty_interval": "No statistical interval has been estimated.",
        "sensitivity_analysis": "Synthetic sensitivity method is only proposed.",
        "influence_analysis": "Synthetic influence method is only proposed.",
        "outcome_branches": [
            {
                "outcome": "Synthetic possible outcome A",
                "claim_update": "Synthetic conditional interpretation A",
            },
            {
                "outcome": "Synthetic possible outcome B",
                "claim_update": "Synthetic conditional interpretation B",
            },
        ],
    }


@pytest.mark.parametrize(
    "description",
    [
        EXACT_INDEXED_DESIGN,
        (
            "时间外推设计；按活动周期顺序滚动扩张训练集；"
            "第i活动周的预测只允许使用第i活动周极小时刻之前可得的信息。"
        ),
        (
            "留未来验证：按周期顺序逐步扩展训练集。"
            "第i周的预测只允许使用第i周极小时刻之前可得的信息。"
        ),
        (
            "时间外推验证。\n按 活动周期 顺序 逐步 扩展 训练集；"
            "第 k 周的预测 只允许 使用 第 k 周 极小时刻 之前 可得的信息。"
        ),
        (
            "时间外推验证:按周期顺序滚动扩展训练集,"
            "第k周的预测只允许使用第k周极小时刻之前可得的信息。"
        ),
        (
            "留未来验证；按活动周期顺序逐步扩展训练集；"
            "第I活动周的预测只允许使用第I活动周极小时刻之前可得的信息。"
        ),
    ],
)
def test_complete_indexed_temporal_descriptions_are_accepted_without_rewriting(
    description,
):
    value = _contract(description)
    original = deepcopy(value)
    assert len(value) == 20
    result = validate_analysis_claim_contract(value)
    assert result == original
    assert value == original
    assert result["validation_design"] == description


@pytest.mark.parametrize(
    "description",
    [
        "时间外推验证",
        "留未来验证",
        "时间外推验证；按周期顺序逐步扩张训练集。",
        EXACT_INDEXED_DESIGN.replace("逐步扩张训练集", "安排训练集"),
        EXACT_INDEXED_DESIGN.replace("按周期顺序逐步扩张训练集", "逐步扩张训练集"),
        EXACT_INDEXED_DESIGN.replace("只允许使用", "使用"),
        EXACT_INDEXED_DESIGN.replace("极小时刻之前", "极小时刻之后"),
        EXACT_INDEXED_DESIGN.replace("极小时刻之前", "极小时刻附近"),
        EXACT_INDEXED_DESIGN.replace("极小时刻", "极大时刻"),
        EXACT_INDEXED_DESIGN.replace("第k周极小时刻", "第k+1周极小时刻"),
        EXACT_INDEXED_DESIGN.replace("第k周极小时刻", "第i周极小时刻"),
    ],
)
def test_missing_or_mismatched_target_boundaries_still_reject_without_mutation(
    description,
):
    value = _contract(description)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(value)
    assert value == original


@pytest.mark.parametrize(
    "contradiction",
    [
        "采用随机 K 折作为主验证。",
        "使用未来标签调参。",
        "纳入未来观测补充训练。",
        "不做随机 K 折；采用随机切分。",
        "禁止使用未来标签；使用未来标签完成校准。",
    ],
)
def test_active_random_or_future_material_contradictions_are_not_overridden(
    contradiction,
):
    value = _contract(EXACT_INDEXED_DESIGN + contradiction)
    original = deepcopy(value)
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(value)
    assert value == original


def test_training_correlation_remains_rejected_after_indexed_language_match():
    value = _contract(EXACT_INDEXED_DESIGN + "以训练相关性作为预测成立依据。")
    original = deepcopy(value)
    with pytest.raises(ContractError, match="training correlation"):
        validate_analysis_claim_contract(value)
    assert value == original


def test_out_of_sample_text_in_other_fields_cannot_replace_validation_design():
    value = _contract("按周期分块核查，使用完整记录。")
    value["primary_analysis"] = "Synthetic forecast using rolling-origin holdout."
    value["information_set"] = EXACT_INDEXED_DESIGN
    value["decision_rule"] = "Synthetic out-of-sample result rule, not executed."
    original = deepcopy(value)
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(value)
    assert value == original


def test_existing_english_rolling_origin_route_keeps_its_original_contract():
    value = _contract("rolling-origin temporal holdout")
    original = deepcopy(value)
    assert validate_analysis_claim_contract(value) == original
    assert value == original


def test_indexed_language_never_relaxes_closed_structural_fields():
    value = _contract(EXACT_INDEXED_DESIGN)
    value["missingness_note"] = "Synthetic unregistered field must not be repaired."
    original = deepcopy(value)
    with pytest.raises(ContractError, match="missingness_note"):
        validate_analysis_claim_contract(value)
    assert value == original

"""Versioned source text and contracts for the H1/H2 reproduction suite."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

SUITE_ID = "solar-h1-h2-v1"
SCHEMA_VERSION = "jw-reproduction-launch-v1"
MODEL_NAME = "qwen3.7-max"
MODEL_PROVIDER = "dashscope"
PROJECT_ID = "default"


@dataclass(frozen=True)
class ReproductionCase:
    case_id: str
    prompt: str
    declared_inputs: tuple[str, ...]
    expected_artifacts: tuple[str, ...]

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


H1_PROMPT = (
    "“请完成一次范围受控、独立且可复核的 SILSO 太阳活动周形态实验。”核心要求是："
    "使用 SILSO v2.0 第 1—24 周，分析周期长度、上升时间—峰值、下降时间—峰值及早期/现代"
    "稳定性；使用官方极值表；计算 Pearson、Spearman、bootstrap、逐周期留一；生成 CSV、报告"
    "和散点图；不得把相关性写成因果机制。"
)

H2_PROMPT = (
    "实际任务文本为：“检验极小期极区场对下一太阳活动周峰值的历史预测技能。”随后固定为："
    "使用已登记的极区前兆表和回执；前五个周期训练，逐步留出第 20—24 周；拟合极区场线性"
    "模型；与训练均值、持续性基线比较；固定种子 `20260828`，进行 10,000 次活动周级 "
    "bootstrap；检查时间泄漏及 MWO/WSO 测量制度差异。"
)

CASES = (
    ReproductionCase(
        case_id="H1",
        prompt=H1_PROMPT,
        declared_inputs=("SILSO v2.0 official cycle minima/maxima table",),
        expected_artifacts=("CSV", "analysis report", "scatter plots"),
    ),
    ReproductionCase(
        case_id="H2",
        prompt=H2_PROMPT,
        declared_inputs=("registered polar precursor table", "data receipt"),
        expected_artifacts=("CSV", "prediction-skill report", "diagnostic figures"),
    ),
)

CASE_BY_ID = {case.case_id: case for case in CASES}

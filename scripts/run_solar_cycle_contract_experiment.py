from __future__ import annotations

import json
import sys
from pathlib import Path

from automatic_experiment import service
from automatic_experiment.contracts import DESIGN_VERSION, RESPONSE_VERSION, default_request
from automatic_experiment.state import task_workspace


TASK = """研究太阳活动周开始后的早期上升速度能否预测同一活动周的峰值强度。
使用已冻结的数据、计划和假设，在一个阶段内完成输入审计、Spearman 相关及其 p 值、
逐周留一线性预测、训练折均值基线、1000 次置换检验、控制最低活动水平的偏相关、
多个早期窗口的敏感性分析和前后时期稳健性分析。所有特征必须严格早于峰值，
所有拟合和基线都在留一训练折内重算，不得模拟或扩充样本。"""


MEASUREMENTS = [
    ("cycle_count", "主分析活动周数量", "diagnostic", "个活动周", "主分析纳入的已完成活动周数量。"),
    ("common_window_count", "共同窗口样本数量", "diagnostic", "个活动周", "所有早期观察窗口均可使用的活动周数量。"),
    ("rank_correlation", "早期上升速度与峰值强度的秩相关系数", "primary", "", "衡量两项活动周特征的单调关联。"),
    ("rank_correlation_p_value", "秩相关检验概率", "secondary", "", "在无关联假设下观察到当前或更极端秩相关的概率。"),
    ("loocv_mae", "逐周留一预测平均绝对误差", "primary", "未注明", "逐周留一预测与真实峰值强度的平均绝对偏差。"),
    ("baseline_mae", "训练折均值基线平均绝对误差", "secondary", "未注明", "每次仅用训练活动周均值预测留出活动周的平均绝对偏差。"),
    ("permutation_p_value", "置换检验概率", "secondary", "", "随机重排峰值强度后达到当前秩相关绝对值的经验概率。"),
    ("raw_pearson", "控制前的线性相关系数", "secondary", "", "偏相关分析前的线性相关参考值。"),
    ("partial_correlation", "控制最低活动水平后的偏相关系数", "secondary", "", "分别去除最低活动水平线性关系后两项残差的相关系数。"),
    ("correlation_difference", "控制前后相关系数差", "diagnostic", "", "控制前线性相关系数减去偏相关系数。"),
    ("window_12_rank_correlation", "一年窗口秩相关系数", "secondary", "", "共同样本上一年早期窗口与峰值强度的秩相关。"),
    ("window_18_rank_correlation", "一年半窗口秩相关系数", "secondary", "", "共同样本上一年半早期窗口与峰值强度的秩相关。"),
    ("window_24_rank_correlation", "两年窗口秩相关系数", "secondary", "", "共同样本上两年早期窗口与峰值强度的秩相关。"),
    ("window_30_rank_correlation", "两年半窗口秩相关系数", "secondary", "", "共同样本上两年半早期窗口与峰值强度的秩相关。"),
    ("window_36_rank_correlation", "三年窗口秩相关系数", "secondary", "", "共同样本上三年早期窗口与峰值强度的秩相关。"),
    ("window_correlation_range", "观察窗口相关系数跨度", "diagnostic", "", "共同样本上各早期窗口秩相关系数的最大值减最小值。"),
    ("early_rank_correlation", "较早时期秩相关系数", "secondary", "", "较早一半活动周中两项特征的秩相关。"),
    ("late_rank_correlation", "较晚时期秩相关系数", "secondary", "", "较晚一半活动周中两项特征的秩相关。"),
    ("temporal_correlation_difference", "前后时期相关系数差", "diagnostic", "", "较晚时期秩相关系数减去较早时期秩相关系数。"),
]


WORKER_CODE = r'''import json
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


def _loocv(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    predictions = np.empty_like(y, dtype=float)
    baselines = np.empty_like(y, dtype=float)
    for index in range(len(y)):
        train = np.ones(len(y), dtype=bool)
        train[index] = False
        matrix = np.column_stack([np.ones(int(train.sum())), x[train]])
        intercept, slope = np.linalg.lstsq(matrix, y[train], rcond=None)[0]
        predictions[index] = intercept + slope * x[index]
        baselines[index] = float(np.mean(y[train]))
    denominator = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum((y - predictions) ** 2)) / denominator
    mae = float(np.mean(np.abs(y - predictions)))
    baseline_mae = float(np.mean(np.abs(y - baselines)))
    return predictions, baselines, r_squared, mae, baseline_mae


def _residuals(values, control):
    values = np.asarray(values, dtype=float)
    control = np.asarray(control, dtype=float)
    matrix = np.column_stack([np.ones(len(control)), control])
    fitted = matrix @ np.linalg.lstsq(matrix, values, rcond=None)[0]
    return values - fitted


def run_experiment(context):
    features = pd.read_csv(context["input_path_by_id"]["features"])
    required = ["cycle", "min_date", "min_sn", "peak_date", "peak_sn",
                "slope_12", "usable_12", "slope_18", "usable_18",
                "slope_24", "usable_24", "slope_30", "usable_30",
                "slope_36", "usable_36"]
    missing_columns = [name for name in required if name not in features.columns]
    if missing_columns:
        raise ValueError("required feature columns are missing")

    usable_two_year = features["usable_24"].astype(bool)
    complete_main = features[["slope_24", "peak_sn", "min_sn"]].notna().all(axis=1)
    minimum_dates = pd.to_datetime(features["min_date"])
    peak_dates = pd.to_datetime(features["peak_date"])
    month_gaps = (
        (peak_dates.dt.year - minimum_dates.dt.year) * 12
        + peak_dates.dt.month
        - minimum_dates.dt.month
    )
    audit_passed = bool(
        len(features) == 24
        and bool(usable_two_year.all())
        and bool(complete_main.all())
        and bool((month_gaps > 24).all())
    )
    if not audit_passed:
        raise ValueError("the leakage or completeness audit did not pass")

    x = features["slope_24"].to_numpy(dtype=float)
    y = features["peak_sn"].to_numpy(dtype=float)
    minimum = features["min_sn"].to_numpy(dtype=float)
    rank_correlation, rank_p = spearmanr(x, y)
    predictions, baselines, _, main_mae, baseline_mae = _loocv(x, y)

    rng = np.random.default_rng(int(context["seed"]))
    permutation_values = np.empty(1000, dtype=float)
    for index in range(1000):
        permutation_values[index] = spearmanr(x, rng.permutation(y)).statistic
    permutation_p = float(
        (1 + np.sum(np.abs(permutation_values) >= abs(float(rank_correlation)))) / 1001
    )

    raw_pearson = float(pearsonr(x, y).statistic)
    partial_correlation = float(
        pearsonr(_residuals(x, minimum), _residuals(y, minimum)).statistic
    )
    correlation_difference = raw_pearson - partial_correlation

    common_mask = np.ones(len(features), dtype=bool)
    for window in (12, 18, 24, 30, 36):
        common_mask &= features[f"usable_{window}"].astype(bool).to_numpy()
        common_mask &= features[f"slope_{window}"].notna().to_numpy()
    common_y = y[common_mask]
    window_correlations = {}
    for window in (12, 18, 24, 30, 36):
        window_x = features.loc[common_mask, f"slope_{window}"].to_numpy(dtype=float)
        window_correlations[window] = float(spearmanr(window_x, common_y).statistic)
    window_range = max(window_correlations.values()) - min(window_correlations.values())

    early = features["cycle"].to_numpy(dtype=int) <= 12
    late = ~early
    early_correlation = float(spearmanr(x[early], y[early]).statistic)
    late_correlation = float(spearmanr(x[late], y[late]).statistic)
    temporal_difference = late_correlation - early_correlation

    results = {
        "cycle_count": int(len(features)),
        "common_window_count": int(common_mask.sum()),
        "rank_correlation": float(rank_correlation),
        "rank_correlation_p_value": float(rank_p),
        "loocv_mae": float(main_mae),
        "baseline_mae": float(baseline_mae),
        "permutation_p_value": float(permutation_p),
        "raw_pearson": float(raw_pearson),
        "partial_correlation": float(partial_correlation),
        "correlation_difference": float(correlation_difference),
        "window_12_rank_correlation": float(window_correlations[12]),
        "window_18_rank_correlation": float(window_correlations[18]),
        "window_24_rank_correlation": float(window_correlations[24]),
        "window_30_rank_correlation": float(window_correlations[30]),
        "window_36_rank_correlation": float(window_correlations[36]),
        "window_correlation_range": float(window_range),
        "early_rank_correlation": float(early_correlation),
        "late_rank_correlation": float(late_correlation),
        "temporal_correlation_difference": float(temporal_difference),
        "data_audit_passed": bool(audit_passed),
    }
    results_path = context["output_dir"] / "experiment_results.json"
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    predictions_path = context["output_dir"] / "loocv_predictions.csv"
    pd.DataFrame(
        {
            "cycle_id": features["cycle"].astype(int),
            "observed_peak": y,
            "predicted_peak": predictions,
            "training_mean_baseline": baselines,
        }
    ).to_csv(predictions_path, index=False)

    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "cycle_count", "value": int(len(features)), "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "common_window_count", "value": int(common_mask.sum()), "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "rank_correlation", "value": float(rank_correlation), "unit": "", "role": "primary", "source_artifact": "experiment_results.json"},
            {"name": "rank_correlation_p_value", "value": float(rank_p), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "loocv_mae", "value": float(main_mae), "unit": "未注明", "role": "primary", "source_artifact": "experiment_results.json"},
            {"name": "baseline_mae", "value": float(baseline_mae), "unit": "未注明", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "permutation_p_value", "value": float(permutation_p), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "raw_pearson", "value": float(raw_pearson), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "partial_correlation", "value": float(partial_correlation), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "correlation_difference", "value": float(correlation_difference), "unit": "", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "window_12_rank_correlation", "value": float(window_correlations[12]), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "window_18_rank_correlation", "value": float(window_correlations[18]), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "window_24_rank_correlation", "value": float(window_correlations[24]), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "window_30_rank_correlation", "value": float(window_correlations[30]), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "window_36_rank_correlation", "value": float(window_correlations[36]), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "window_correlation_range", "value": float(window_range), "unit": "", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "early_rank_correlation", "value": float(early_correlation), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "late_rank_correlation", "value": float(late_correlation), "unit": "", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "temporal_correlation_difference", "value": float(temporal_difference), "unit": "", "role": "diagnostic", "source_artifact": "experiment_results.json"},
        ],
        "result_items": [
            {"id": "data_audit_passed", "display_name": "输入完整性与时间截断审计", "value_kind": "boolean", "value": bool(audit_passed), "unit": "", "role": "diagnostic", "source_artifact": "experiment_results.json"}
        ],
        "artifacts": [
            {"path": "experiment_results.json", "kind": "json", "description": "全部数值结果和输入审计结论。"},
            {"path": "loocv_predictions.csv", "kind": "csv", "description": "逐活动周留一预测、真实峰值和训练折均值基线。"},
        ],
        "warnings": ["较长早期窗口的共同可用样本少于主分析样本。"],
        "endpoint_results": [
            {"id": "complete_analysis", "status": "completed", "summary": "完成输入审计、预测评估、控制分析和稳健性分析。"}
        ],
        "scientific_payload": {
            "primary_estimand": "前二十四个月上升速度与同一活动周峰值强度的秩相关系数",
            "estimate": float(rank_correlation),
            "interval": None,
            "equivalence_bounds": None,
            "sensitivity": "比较多个共同可用早期窗口和前后历史时期的估计。",
            "uncertainty_reasons": ["历史活动周数量有限，且并非独立同分布的随机样本。"],
        },
    }
'''


def build_request() -> dict:
    request = default_request(TASK)
    request["input_refs"] = [
        {
            "id": "features",
            "path": "inputs/cycle_slope_features.csv",
            "description": "已审计的活动周早期特征与峰值表。",
            "required": True,
        },
        {
            "id": "monthly",
            "path": "inputs/SN_m_tot.csv",
            "description": "月均太阳黑子数来源表，用于核对数据来源。",
            "required": True,
        },
        {
            "id": "plan",
            "path": "inputs/research_plan.json",
            "description": "已冻结的研究计划。",
            "required": True,
        },
        {
            "id": "hypotheses",
            "path": "inputs/hypothesis_portfolio.json",
            "description": "已冻结的候选假设组合。",
            "required": True,
        },
    ]
    request["resource_budget"]["wall_seconds"] = 300
    request["resource_budget"]["cpu_seconds"] = 240
    request["resource_budget"]["max_attempts"] = 2
    request["run_budget"]["max_stages"] = 1
    request["run_budget"]["max_total_attempts"] = 2
    request["run_budget"]["total_wall_seconds"] = 900
    return request


def build_response(request: dict) -> dict:
    return {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "task": request["task"],
        "response_kind": "experiment_ready",
        "normalized_task": request["task"],
        "design_summary": "在同一受限阶段内完成无泄漏预测评估与预先指定的稳健性分析。",
        "clarifications": [],
        "blockers": [],
        "method_fit": "suitable",
    }


def build_design(request: dict) -> dict:
    measurement_plan = [
        {
            "name": name,
            "display_name": display_name,
            "role": role,
            "unit": unit,
            "scientific_meaning": meaning,
        }
        for name, display_name, role, unit, meaning in MEASUREMENTS
    ]
    measurement_names = [row[0] for row in MEASUREMENTS]
    return {
        "schema_version": DESIGN_VERSION,
        "task_name": request["task_name"],
        "normalized_task": request["task"],
        "design_summary": "在同一受限阶段内完成无泄漏预测评估与预先指定的稳健性分析。",
        "method_fit": "suitable",
        "input_ids": ["features", "monthly", "plan", "hypotheses"],
        "research_frame": {
            "primary_question": "活动周开始后的早期上升速度能否预测同一活动周的峰值强度？",
            "analysis_mode": "带置换检验的历史样本预测评估。",
            "claim_scope": "结论只描述所提供的已完成历史活动周，不作因果或外推主张。",
            "input_evidence": [
                {
                    "input_id": "features",
                    "role": "主分析观测",
                    "intended_use": "提供早期上升速度、峰值强度、最低活动水平和时间边界。",
                    "limitations": "历史活动周数量有限，并非随机抽样。",
                },
                {
                    "input_id": "monthly",
                    "role": "来源核对材料",
                    "intended_use": "确认主分析观测来自真实月度太阳活动序列。",
                    "limitations": "本阶段不重新识别活动周边界。",
                },
                {
                    "input_id": "plan",
                    "role": "研究路线依据",
                    "intended_use": "限定主问题、基线和稳健性分析范围。",
                    "limitations": "计划不是实验结果。",
                },
                {
                    "input_id": "hypotheses",
                    "role": "解释候选依据",
                    "intended_use": "组织预测、数学耦合、窗口敏感性和年代稳定性的检验。",
                    "limitations": "候选假设本身不构成证据。",
                },
            ],
            "supported_questions": [
                "早期上升速度与峰值强度的关联和逐周留一预测表现。",
                "控制最低活动水平后的关联变化。",
                "共同样本上不同早期窗口和前后时期的稳健性。",
            ],
            "deferred_questions": ["不同活动周边界算法的重新识别与外部前瞻验证。"],
            "assumptions": [
                "已冻结特征表的时间边界和活动周编号可复核。",
                "线性模型仅用于预测基线比较，不作物理机制解释。",
            ],
            "threats_to_validity": [
                "历史活动周数量有限。",
                "活动周之间可能存在长期非平稳性。",
                "相关和偏相关不能建立因果关系。",
            ],
            "literature_basis": "本阶段只检验已冻结计划和候选假设，不新增外部文献主张。",
        },
        "measurement_plan": measurement_plan,
        "result_plan": [
            {
                "id": "data_audit_passed",
                "display_name": "输入完整性与时间截断审计",
                "value_kind": "boolean",
                "role": "diagnostic",
                "unit": "",
                "scientific_meaning": "确认主分析观测完整且早期观察窗口严格早于峰值。",
            }
        ],
        "method_decisions": [
            {
                "id": "single_stage",
                "decision_key": "integrated_stage",
                "decision": "在一个受限阶段内完成全部预先指定分析。",
                "rationale": "所有分析共享同一份不可变输入且不产生后续阶段依赖。",
                "basis_kind": "user_request",
                "source_refs": [],
                "alternatives": ["拆分为多个阶段并传递中间产物"],
                "claim_limit": "阶段合并不改变各分析的统计口径。",
            },
            {
                "id": "leave_one_cycle_out",
                "decision_key": "cross_validation",
                "decision": "每次留出一个完整活动周，并仅在其余活动周上拟合模型和基线。",
                "rationale": "样本量有限且用户要求避免训练评价泄漏。",
                "basis_kind": "user_request",
                "source_refs": ["features"],
                "alternatives": ["固定训练测试划分"],
                "claim_limit": "结果只估计当前历史样本上的逐周留一表现。",
            },
            {
                "id": "training_mean_baseline",
                "decision_key": "prediction_baseline",
                "decision": "每个留出折使用该折训练活动周的峰值均值作为基线。",
                "rationale": "该基线不使用留出活动周的目标值。",
                "basis_kind": "method_standard",
                "source_refs": ["逐周留一训练均值基线定义"],
                "alternatives": ["使用全样本均值，但会泄漏留出目标"],
                "claim_limit": "基线只用于相对预测误差比较。",
            },
            {
                "id": "bounded_permutation",
                "decision_key": "permutation_count",
                "decision": "使用固定随机种子的有限次置换估计经验概率。",
                "rationale": "在运行预算内提供可复算的无关联参照。",
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": [],
                "alternatives": ["穷举所有排列"],
                "claim_limit": "经验概率具有有限置换次数带来的蒙特卡洛分辨率。",
            },
            {
                "id": "common_window_population",
                "decision_key": "window_comparison_population",
                "decision": "不同早期窗口只在所有窗口均可用的共同活动周上比较。",
                "rationale": "固定评价样本，避免样本构成变化混入窗口差异。",
                "basis_kind": "method_standard",
                "source_refs": ["features"],
                "alternatives": ["每个窗口使用各自全部可用活动周"],
                "claim_limit": "窗口结果只适用于共同可用活动周。",
            },
            {
                "id": "linear_residual_control",
                "decision_key": "partial_correlation",
                "decision": "分别去除最低活动水平的线性关系后计算残差相关。",
                "rationale": "用于检查由共同最低活动水平引起的线性统计耦合。",
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": ["hypotheses"],
                "alternatives": ["秩偏相关或非线性条件模型"],
                "claim_limit": "控制结果不能解释为物理贡献比例。",
            },
        ],
        "paired_comparison_audits": [],
        "criteria": [
            {
                "id": "audit_complete",
                "statement": "输入完整性与时间截断审计给出可核验结论。",
                "basis_kind": "user_request",
                "basis_text": "用户要求所有早期信息严格早于峰值且不得模拟数据。",
                "source_refs": ["features", "monthly"],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": ["cycle_count"],
                "result_refs": ["data_audit_passed"],
                "endpoint_refs": ["complete_analysis"],
            },
            {
                "id": "main_prediction",
                "statement": "主关联、逐周留一预测、训练折均值基线和置换参照均被报告。",
                "basis_kind": "user_request",
                "basis_text": "用户要求无泄漏预测、基线和真实计算结果。",
                "source_refs": ["features"],
                "artifact_refs": ["experiment_results.json", "loocv_predictions.csv"],
                "measurement_refs": [
                    "rank_correlation",
                    "rank_correlation_p_value",
                    "loocv_mae",
                    "baseline_mae",
                    "permutation_p_value",
                ],
                "result_refs": [],
                "endpoint_refs": ["complete_analysis"],
            },
            {
                "id": "coupling_control",
                "statement": "控制最低活动水平前后的相关估计和差值均被报告。",
                "basis_kind": "user_request",
                "basis_text": "冻结候选假设要求检查线性统计耦合，但不作因果贡献解释。",
                "source_refs": ["features", "hypotheses"],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": [
                    "raw_pearson",
                    "partial_correlation",
                    "correlation_difference",
                ],
                "result_refs": [],
                "endpoint_refs": ["complete_analysis"],
            },
            {
                "id": "window_estimates",
                "statement": "共同评价样本上各早期窗口的秩相关估计及其跨度均被报告。",
                "basis_kind": "user_request",
                "basis_text": "冻结计划要求在固定评价样本上报告不同早期窗口的估计。",
                "source_refs": ["features", "plan"],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": [
                    "common_window_count",
                    "window_12_rank_correlation",
                    "window_18_rank_correlation",
                    "window_24_rank_correlation",
                    "window_30_rank_correlation",
                    "window_36_rank_correlation",
                    "window_correlation_range",
                ],
                "result_refs": [],
                "endpoint_refs": ["complete_analysis"],
            },
            {
                "id": "temporal_estimates",
                "statement": "较早和较晚时期的相关估计及其差值均被报告。",
                "basis_kind": "user_request",
                "basis_text": "冻结候选假设要求检查历史时期变化。",
                "source_refs": ["features", "hypotheses"],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": [
                    "early_rank_correlation",
                    "late_rank_correlation",
                    "temporal_correlation_difference",
                ],
                "result_refs": [],
                "endpoint_refs": ["complete_analysis"],
            },
        ],
        "artifact_plan": [
            {
                "id": "numeric_results",
                "path": "experiment_results.json",
                "kind": "json",
                "description": "全部数值结果和输入审计结论。",
                "producer_stage_id": "integrated_analysis",
            },
            {
                "id": "leave_one_out_predictions",
                "path": "loocv_predictions.csv",
                "kind": "csv",
                "description": "逐活动周留一预测、真实峰值和训练折均值基线。",
                "producer_stage_id": "integrated_analysis",
            },
        ],
        "experiment_stages": [
            {
                "id": "integrated_analysis",
                "objective": "完成输入审计、无泄漏预测评估、控制分析和稳健性分析。",
                "input_ids": ["features", "monthly", "plan", "hypotheses"],
                "consumes_artifact_ids": [],
                "produces_artifact_ids": ["numeric_results", "leave_one_out_predictions"],
                "prerequisite_stage_ids": [],
                "join_policy": "all",
                "method_outline": "核对时间截断后，在完整历史活动周上执行秩相关和逐周留一线性预测，以训练折均值为基线；再在共同评价样本和前后时期上比较预先指定估计。",
                "measurement_refs": measurement_names,
                "result_refs": ["data_audit_passed"],
                "endpoint_ids": ["complete_analysis"],
                "criterion_refs": [
                    "audit_complete",
                    "main_prediction",
                    "coupling_control",
                    "window_estimates",
                    "temporal_estimates",
                ],
                "outcome_rules": {
                    "completed": "所有声明的测量、审计结论和产物均通过核验。",
                    "inconclusive": "计算有效但历史样本不足以支持所请求的解释。",
                    "input_missing": "任一必需的冻结输入不可用。",
                    "evidence_conflict": "核验后的结果之间存在无法协调的冲突。",
                    "method_invalid": "时间截断或逐周留一方法不能回答当前问题。",
                    "technical_failure": "代码、进程、结果合同或产物核验失败。",
                    "budget_reached": "在受限时间或尝试次数内无法完成。",
                },
                "transitions": {
                    "completed": "completed_interpretable",
                    "inconclusive": "high_uncertainty",
                    "input_missing": "input_missing",
                    "evidence_conflict": "high_uncertainty",
                    "method_invalid": "method_mismatch",
                    "technical_failure": "technical_failure",
                    "budget_reached": "budget_stopped",
                },
                "execution": {
                    "entry_file": "experiment.py",
                    "dependencies": ["numpy", "pandas", "scipy"],
                    "deterministic": True,
                    "seed": 1729,
                    "expected_artifacts": [
                        "experiment_results.json",
                        "loocv_predictions.csv",
                    ],
                },
            }
        ],
        "interpretation_policy": {
            "primary_estimand": "前二十四个月上升速度与同一活动周峰值强度的秩相关系数",
            "null_rule": "没有等效界限和区间时，不把未通过显著性判断写成无效或等效。",
            "uncertainty_rule": "结论强度由历史样本量、逐周留一表现、窗口和时期变化共同限制。",
            "partial_rule": "只有部分预先指定分析通过核验时才报告部分结果。",
        },
    }


def build_assessment(preview: dict) -> dict:
    return {
        "proposed_outcome": "completed_interpretable",
        "stage_outcome": "completed",
        "rationale": "真实输入上的主分析、基线比较、最低活动水平控制、五种窗口比较和前后时期拆分均产生了可核验结果。",
        "criterion_results": [
            {
                "criterion_id": criterion["criterion_id"],
                "status": "met",
                "explanation": "该判据引用的测量、结果或产物均已由执行记录核验。",
            }
            for criterion in preview["criterion_evidence"]
        ],
        "uncertainty_reasons": [
            "历史活动周数量有限。",
            "活动周之间可能存在长期非平稳性。",
            "相关和预测表现不能建立因果关系。",
        ],
        "null_assessment": None,
        "report_narrative": {
            "title": "太阳活动周早期上升速度与峰值强度的无泄漏预测评估",
            "objective": "检验活动周开始后的早期上升速度能否预测同一活动周的峰值强度。",
            "data_scope": "分析使用已审计的完成活动周观测，并以真实月度太阳活动序列、冻结研究计划和候选假设作为来源与解释边界。",
            "method": "先核对早期窗口严格早于峰值，再执行秩相关、逐周留一线性预测和训练折均值基线；随后进行置换检验、最低活动水平控制，在共同样本上比较五种窗口，并比较前十二个与后十二个活动周。",
            "interpretation": "早期上升速度与峰值强度的关联和预测表现应结合基线、控制分析及窗口和时期变化共同解释，不能据此作因果主张。",
            "evidence_strength": "证据来自不可变真实输入上的可复算测量和逐活动周预测，但只支持当前历史样本范围内的结论。",
            "claim_boundary": "结果不代表对未来未见活动周的外部验证，也不区分统计关联背后的具体太阳物理机制。",
            "limitations": [
                "历史活动周数量限制了预测性能估计的精度和外推范围。",
                "共同窗口分析使用较少的活动周，因此只用于敏感性界定。",
                "长期非平稳性可能使前后时期估计不同。",
            ],
            "next_steps": [
                "在新增完整活动周出现后进行严格前瞻外部验证。",
                "用预先冻结的替代活动周边界定义复核窗口敏感性。",
            ],
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_solar_cycle_contract_experiment.py TASK_WORKSPACE")
    workspace = Path(sys.argv[1]).expanduser().resolve()
    with task_workspace(workspace):
        request = build_request()
        bound = service.bind_request({"request": request})
        run_id = bound["run_id"]
        inspected = service.inspect_inputs(run_id)
        input_snapshot = inspected.get("input_snapshot", {})
        if inspected.get("status") != "inputs_snapshotted" or input_snapshot.get(
            "missing_required_ids"
        ):
            print(json.dumps(inspected, ensure_ascii=False, indent=2))
            return 2
        checked = service.validate_and_store_design(
            run_id,
            build_response(request),
            build_design(request),
        )
        if checked["status"] != "design_validated":
            print(json.dumps(checked, ensure_ascii=False, indent=2))
            return 3
        prepared = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": WORKER_CODE}],
            None,
            "首次正式尝试：执行已经校验的单阶段无泄漏分析。",
        )
        attempt_id = prepared["attempt_id"]
        executed = service.execute(run_id, attempt_id)
        execution_facts = executed["execution_facts"]
        if (
            execution_facts.get("sandbox_exit_code") != 0
            or execution_facts.get("output_inventory_error") is not None
        ):
            print(json.dumps(executed, ensure_ascii=False, indent=2))
            return 4
        preview = service.verify(run_id, attempt_id, None)
        if preview["status"] != "assessment_required":
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return 5
        verified = service.verify(run_id, attempt_id, build_assessment(preview))
        if verified["status"] != "verified":
            print(json.dumps(verified, ensure_ascii=False, indent=2))
            return 6
        finalized = service.finalize(run_id)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "report_path": str(
                        workspace / "experiment" / "runs" / run_id / "report.md"
                    ),
                    "state_path": str(
                        workspace / "experiment" / "runs" / run_id / "state.json"
                    ),
                    "outcome": finalized["outcome"],
                    "report_sha256": finalized["report_sha256"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

# Chinese reader-facing scientific labels intentionally use full-width punctuation.
# ruff: noqa: RUF001
import json
import sys
from pathlib import Path

from automatic_experiment import service
from automatic_experiment.contracts import (
    DESIGN_VERSION,
    RESPONSE_VERSION,
    default_request,
)
from automatic_experiment.state import task_workspace

TASK = """检验太阳活动周谷值后第7至24个月的月均太阳黑子数上升率，能否预测同一活动周的官方峰值强度。
使用SILSO Version 2.0官方极值标签和严格截至第24个月可得的输入，在一个阶段内完成输入审计、
全样本Spearman关联、以SC1至SC12为初始训练集的扩展窗口滚动回测、逐折训练均值基线、
5000次成对bootstrap误差改进区间以及逐周期预测输出。任何预测折不得使用被预测周期或更晚周期拟合。"""


MEASUREMENTS = [
    (
        "feature_cycle_count",
        "完成输入审计的活动周数量",
        "diagnostic",
        "个活动周",
        "进入分析的完整历史活动周数量。",
    ),
    (
        "official_minimum_asof_candidate_count",
        "发布时可重建官方谷值的活动周数量",
        "diagnostic",
        "个活动周",
        "截至发布时的可用平滑序列把官方谷值列为最低值候选的活动周数量。",
    ),
    (
        "future_input_violation_count",
        "未来输入违规数量",
        "diagnostic",
        "个观测",
        "特征中晚于预测发布时点的观测数量。",
    ),
    (
        "target_timing_violation_count",
        "目标时序违规数量",
        "diagnostic",
        "个活动周",
        "官方峰值不晚于预测发布时点的活动周数量。",
    ),
    (
        "rank_correlation",
        "早期上升率与官方峰值强度的秩相关系数",
        "primary",
        "",
        "衡量严格早期上升率与官方峰值强度的单调关联。",
    ),
    (
        "rolling_test_cycle_count",
        "滚动回测活动周数量",
        "diagnostic",
        "个活动周",
        "按时间顺序逐一预测的历史活动周数量。",
    ),
    (
        "rolling_candidate_mae",
        "滚动回测候选模型平均绝对误差",
        "primary",
        "太阳黑子数",
        "仅使用更早活动周拟合的上升率模型平均绝对误差。",
    ),
    (
        "rolling_baseline_mae",
        "滚动回测训练均值基线平均绝对误差",
        "secondary",
        "太阳黑子数",
        "每折只用更早活动周峰值均值预测的平均绝对误差。",
    ),
    (
        "rolling_mae_improvement",
        "滚动回测平均绝对误差改进",
        "primary",
        "太阳黑子数",
        "训练均值基线误差减去候选模型误差。",
    ),
    (
        "rolling_candidate_rmse",
        "滚动回测候选模型均方根误差",
        "secondary",
        "太阳黑子数",
        "候选模型逐周期预测的均方根误差。",
    ),
    (
        "rolling_baseline_rmse",
        "滚动回测训练均值基线均方根误差",
        "secondary",
        "太阳黑子数",
        "训练均值基线逐周期预测的均方根误差。",
    ),
    (
        "rolling_rmse_improvement",
        "滚动回测均方根误差改进",
        "secondary",
        "太阳黑子数",
        "训练均值基线均方根误差减去候选模型均方根误差。",
    ),
    (
        "mae_improvement_ci_low",
        "平均绝对误差改进区间下限",
        "secondary",
        "太阳黑子数",
        "成对bootstrap误差改进分布的百分位下限。",
    ),
    (
        "mae_improvement_ci_high",
        "平均绝对误差改进区间上限",
        "secondary",
        "太阳黑子数",
        "成对bootstrap误差改进分布的百分位上限。",
    ),
    (
        "candidate_absolute_error_win_count",
        "候选模型逐周期绝对误差胜出数量",
        "diagnostic",
        "个活动周",
        "候选模型绝对误差小于训练均值基线的回测活动周数量。",
    ),
]


WORKER_CODE = r"""import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def _fit_predict(train_x, train_y, test_x):
    matrix = np.column_stack([np.ones(len(train_x)), train_x])
    intercept, slope = np.linalg.lstsq(matrix, train_y, rcond=None)[0]
    return float(intercept + slope * test_x)


def run_experiment(context):
    features = pd.read_csv(context["input_path_by_id"]["features"])
    manifest = json.loads(context["input_path_by_id"]["manifest"].read_text(encoding="utf-8"))
    required = [
        "cycle", "official_min_date", "official_max_date", "official_max_sn",
        "issue_date", "feature_month_count", "rise_rate_monthly_7_24",
        "max_input_date", "official_minimum_is_asof_candidate",
        "future_input_count", "target_after_issue",
    ]
    missing = [column for column in required if column not in features.columns]
    if missing:
        raise ValueError("required optimized feature columns are missing")

    features = features.sort_values("cycle").reset_index(drop=True)
    dates_available = pd.to_datetime(features["max_input_date"]) <= pd.to_datetime(features["issue_date"])
    target_after_issue = features["target_after_issue"].astype(bool)
    boundary_available = features["official_minimum_is_asof_candidate"].astype(bool)
    future_input_violations = int(features["future_input_count"].sum())
    target_timing_violations = int((~target_after_issue).sum())
    audit_passed = bool(
        len(features) == 24
        and features["cycle"].tolist() == list(range(1, 25))
        and bool((features["feature_month_count"] == 18).all())
        and bool(dates_available.all())
        and bool(boundary_available.all())
        and future_input_violations == 0
        and target_timing_violations == 0
        and manifest.get("schema_version") == "solar-cycle-asof-features-v1"
    )
    if not audit_passed:
        raise ValueError("strict as-of input audit did not pass")

    x = features["rise_rate_monthly_7_24"].to_numpy(dtype=float)
    y = features["official_max_sn"].to_numpy(dtype=float)
    rank_correlation = spearmanr(x, y).statistic

    predictions = []
    baselines = []
    test_cycles = []
    observed = []
    training_counts = []
    for test_index in range(12, len(features)):
        train_x = x[:test_index]
        train_y = y[:test_index]
        predictions.append(_fit_predict(train_x, train_y, x[test_index]))
        baselines.append(float(np.mean(train_y)))
        test_cycles.append(int(features.loc[test_index, "cycle"]))
        observed.append(float(y[test_index]))
        training_counts.append(int(test_index))

    predictions = np.asarray(predictions, dtype=float)
    baselines = np.asarray(baselines, dtype=float)
    observed = np.asarray(observed, dtype=float)
    candidate_abs_error = np.abs(observed - predictions)
    baseline_abs_error = np.abs(observed - baselines)
    candidate_mae = float(np.mean(candidate_abs_error))
    baseline_mae = float(np.mean(baseline_abs_error))
    mae_improvement = baseline_mae - candidate_mae
    candidate_rmse = float(np.sqrt(np.mean((observed - predictions) ** 2)))
    baseline_rmse = float(np.sqrt(np.mean((observed - baselines) ** 2)))
    rmse_improvement = baseline_rmse - candidate_rmse
    candidate_win_count = int(np.sum(candidate_abs_error < baseline_abs_error))

    rng = np.random.default_rng(int(context["seed"]))
    bootstrap_improvements = np.empty(5000, dtype=float)
    for bootstrap_index in range(5000):
        sample = rng.integers(0, len(observed), size=len(observed))
        bootstrap_improvements[bootstrap_index] = float(
            np.mean(baseline_abs_error[sample]) - np.mean(candidate_abs_error[sample])
        )
    ci_low, ci_high = np.quantile(bootstrap_improvements, [0.025, 0.975])

    results = {
        "feature_cycle_count": int(len(features)),
        "official_minimum_asof_candidate_count": int(boundary_available.sum()),
        "future_input_violation_count": future_input_violations,
        "target_timing_violation_count": target_timing_violations,
        "rank_correlation": float(rank_correlation),
        "rolling_test_cycle_count": int(len(observed)),
        "rolling_candidate_mae": candidate_mae,
        "rolling_baseline_mae": baseline_mae,
        "rolling_mae_improvement": mae_improvement,
        "rolling_candidate_rmse": candidate_rmse,
        "rolling_baseline_rmse": baseline_rmse,
        "rolling_rmse_improvement": rmse_improvement,
        "mae_improvement_ci_low": float(ci_low),
        "mae_improvement_ci_high": float(ci_high),
        "candidate_absolute_error_win_count": candidate_win_count,
        "strict_asof_audit_passed": audit_passed,
    }
    (context["output_dir"] / "experiment_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pd.DataFrame(
        {
            "cycle_id": test_cycles,
            "training_cycle_count": training_counts,
            "observed_official_peak": observed,
            "candidate_prediction": predictions,
            "training_mean_baseline": baselines,
            "candidate_absolute_error": candidate_abs_error,
            "baseline_absolute_error": baseline_abs_error,
        }
    ).to_csv(context["output_dir"] / "rolling_predictions.csv", index=False)
    pd.DataFrame(
        {"mae_improvement": bootstrap_improvements}
    ).to_csv(context["output_dir"] / "bootstrap_mae_improvement.csv", index=False)

    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurements": [
            {"name": "feature_cycle_count", "value": int(len(features)), "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "official_minimum_asof_candidate_count", "value": int(boundary_available.sum()), "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "future_input_violation_count", "value": future_input_violations, "unit": "个观测", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "target_timing_violation_count", "value": target_timing_violations, "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "rank_correlation", "value": float(rank_correlation), "unit": "", "role": "primary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_test_cycle_count", "value": int(len(observed)), "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
            {"name": "rolling_candidate_mae", "value": candidate_mae, "unit": "太阳黑子数", "role": "primary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_baseline_mae", "value": baseline_mae, "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_mae_improvement", "value": mae_improvement, "unit": "太阳黑子数", "role": "primary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_candidate_rmse", "value": candidate_rmse, "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_baseline_rmse", "value": baseline_rmse, "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "rolling_rmse_improvement", "value": rmse_improvement, "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "mae_improvement_ci_low", "value": float(ci_low), "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "mae_improvement_ci_high", "value": float(ci_high), "unit": "太阳黑子数", "role": "secondary", "source_artifact": "experiment_results.json"},
            {"name": "candidate_absolute_error_win_count", "value": candidate_win_count, "unit": "个活动周", "role": "diagnostic", "source_artifact": "experiment_results.json"},
        ],
        "result_items": [
            {"id": "strict_asof_audit_passed", "display_name": "严格预测时可得性审计", "value_kind": "boolean", "value": audit_passed, "unit": "", "role": "diagnostic", "source_artifact": "experiment_results.json"}
        ],
        "artifacts": [
            {"path": "experiment_results.json", "kind": "json", "description": "严格输入审计、全样本关联和滚动回测测量。"},
            {"path": "rolling_predictions.csv", "kind": "csv", "description": "SC13至SC24逐周期滚动预测和同折训练均值基线。"},
            {"path": "bootstrap_mae_improvement.csv", "kind": "csv", "description": "5000次成对bootstrap平均绝对误差改进分布。"},
        ],
        "warnings": ["滚动回测只有12个测试活动周，bootstrap区间不消除周期非独立性。"],
        "endpoint_results": [
            {"id": "complete_asof_backtest", "status": "completed", "summary": "完成严格预测时可得性审计、关联检验和扩展窗口滚动回测。"}
        ],
        "scientific_payload": {
            "primary_estimand": "严格截至第24个月可得的早期上升率在扩展窗口滚动回测中相对训练均值基线的平均绝对误差改进",
            "estimate": mae_improvement,
            "interval": [float(ci_low), float(ci_high)],
            "equivalence_bounds": None,
            "sensitivity": "同时报告全样本秩相关、逐周期胜出数量和均方根误差改进。",
            "uncertainty_reasons": ["滚动测试仅包含12个活动周。", "相邻太阳活动周不是独立同分布随机样本。"],
        },
    }
"""


def build_request() -> dict:
    request = default_request(TASK)
    request["input_refs"] = [
        {
            "id": "features",
            "path": "inputs/solar_cycle_asof_features_v1.csv",
            "description": "严格预测时可得的SC1至SC24早期上升率特征。",
            "required": True,
        },
        {
            "id": "manifest",
            "path": "inputs/solar_cycle_asof_features_v1.manifest.json",
            "description": "特征构建配置、来源哈希和数据审计清单。",
            "required": True,
        },
        {
            "id": "monthly",
            "path": "inputs/SN_m_tot.csv",
            "description": "SILSO Version 2.0原始月均太阳黑子数。",
            "required": True,
        },
        {
            "id": "smoothed",
            "path": "inputs/SN_ms_tot_V2.0.csv",
            "description": "SILSO Version 2.0官方13个月平滑月序列。",
            "required": True,
        },
        {
            "id": "extrema",
            "path": "inputs/TableCyclesMiMa.txt",
            "description": "SILSO Version 2.0官方活动周极值表。",
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
        "design_summary": "在严格预测时可得性审计后执行按历史顺序扩展训练窗口的滚动回测。",
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
        "design_summary": "在严格预测时可得性审计后执行按历史顺序扩展训练窗口的滚动回测。",
        "method_fit": "suitable",
        "input_ids": ["features", "manifest", "monthly", "smoothed", "extrema"],
        "research_frame": {
            "primary_question": "严格截至活动周谷值后第24个月可得的早期上升率，能否改善官方峰值强度的历史顺序预测？",
            "analysis_mode": "带成对bootstrap误差区间的扩展窗口历史回测。",
            "claim_scope": "结论只适用于SC1至SC24的官方Version 2.0周期标签和预先固定的第7至24个月特征。",
            "input_evidence": [
                {
                    "input_id": "features",
                    "role": "主分析观测",
                    "intended_use": "提供固定早期窗口上升率、官方峰值标签和时序审计列。",
                    "limitations": "历史活动周数量有限。",
                },
                {
                    "input_id": "manifest",
                    "role": "数据谱系",
                    "intended_use": "核对来源哈希、构建配置和预构建审计。",
                    "limitations": "清单不能替代实验内复核。",
                },
                {
                    "input_id": "monthly",
                    "role": "原始观测来源",
                    "intended_use": "证明早期特征来自真实月均观测。",
                    "limitations": "早期历史观测质量随年代变化。",
                },
                {
                    "input_id": "smoothed",
                    "role": "边界可得性核对",
                    "intended_use": "验证第24个月发布时官方谷值已是可用平滑序列的最低候选。",
                    "limitations": "官方平滑序列具有居中计算带来的六个月可得性滞后。",
                },
                {
                    "input_id": "extrema",
                    "role": "目标与周期标签",
                    "intended_use": "提供官方周期谷值、峰值日期和峰值强度。",
                    "limitations": "属于回顾性官方标签。",
                },
            ],
            "supported_questions": [
                "固定早期上升率与官方峰值的关联。",
                "只使用更早活动周训练时相对训练均值基线的误差改进。",
                "官方谷值在发布时是否可由当时可用数据重建。",
            ],
            "deferred_questions": [
                "第26活动周的实际数值预测。",
                "加入极区场或F10.7后的增量价值。",
            ],
            "assumptions": [
                "官方Version 2.0峰值是当前任务的目标口径。",
                "第7至24个月窗口在运行前固定。",
                "线性模型只作为可解释预测基线。",
            ],
            "threats_to_validity": [
                "只有12个按时间顺序测试的活动周。",
                "活动周之间可能存在长期非平稳性。",
                "早期上升率与峰值相关不能单独建立发电机因果机制。",
            ],
            "literature_basis": "早期上升率对应Waldmeier效应的WE2方向；本阶段只检验当前不可变输入。",
        },
        "measurement_plan": measurement_plan,
        "result_plan": [
            {
                "id": "strict_asof_audit_passed",
                "display_name": "严格预测时可得性审计",
                "value_kind": "boolean",
                "role": "diagnostic",
                "unit": "",
                "scientific_meaning": "确认所有特征在发布时可得、官方谷值可重建且目标仍位于未来。",
            }
        ],
        "method_decisions": [
            {
                "id": "fixed_window",
                "decision_key": "early_feature_window",
                "decision": "使用官方谷值后第7至24个月的原始月均观测拟合单一线性上升率。",
                "rationale": "排除最初六个月的周期重叠，并避免观察结果后选择窗口。",
                "basis_kind": "method_standard",
                "source_refs": ["monthly", "extrema"],
                "alternatives": ["扫描多个窗口后选择表现最佳者"],
                "claim_limit": "结果只适用于当前预先固定窗口。",
            },
            {
                "id": "asof_boundary",
                "decision_key": "boundary_availability",
                "decision": "按六个月平滑可得性滞后截断官方平滑序列，核对第24个月时官方谷值是否属于最低候选。",
                "rationale": "避免把完整周期后才能确定的边界静默当作发布时已知。",
                "basis_kind": "method_standard",
                "source_refs": ["smoothed", "extrema"],
                "alternatives": ["只检查特征日期早于峰值"],
                "claim_limit": "这是历史as-of重建，不代表实时业务系统已实现。",
            },
            {
                "id": "expanding_backtest",
                "decision_key": "historical_validation",
                "decision": "先用SC1至SC12训练，随后按时间顺序逐一预测SC13至SC24，并在每折扩展训练集。",
                "rationale": "阻止未来活动周进入历史预测折。",
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": ["features"],
                "alternatives": ["随机交叉验证", "逐周留一交叉验证"],
                "claim_limit": "初始训练规模是预算内预先固定的历史评价选择。",
            },
            {
                "id": "paired_bootstrap",
                "decision_key": "uncertainty",
                "decision": "对同一12个测试活动周的基线与候选绝对误差差进行5000次成对bootstrap。",
                "rationale": "保留同一测试活动周上的成对比较关系。",
                "basis_kind": "bounded_pragmatic_choice",
                "source_refs": ["features"],
                "alternatives": ["不报告误差改进区间"],
                "claim_limit": "区间不消除活动周之间的非独立性。",
            },
        ],
        "paired_comparison_audits": [],
        "criteria": [
            {
                "id": "strict_input_audit",
                "statement": "所有早期输入在发布时可得，官方谷值在发布时可重建，且峰值目标仍位于未来。",
                "basis_kind": "user_request",
                "basis_text": "优化必须修复首跑中未检查周期起点可得性的缺口。",
                "source_refs": [
                    "features",
                    "manifest",
                    "monthly",
                    "smoothed",
                    "extrema",
                ],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": [
                    "feature_cycle_count",
                    "official_minimum_asof_candidate_count",
                    "future_input_violation_count",
                    "target_timing_violation_count",
                ],
                "result_refs": ["strict_asof_audit_passed"],
                "endpoint_refs": ["complete_asof_backtest"],
            },
            {
                "id": "association_measured",
                "statement": "固定早期窗口与官方峰值强度的秩相关被报告。",
                "basis_kind": "user_request",
                "basis_text": "保留首跑的关联测量但改用官方标签和严格早期输入。",
                "source_refs": ["features"],
                "artifact_refs": ["experiment_results.json"],
                "measurement_refs": ["rank_correlation"],
                "result_refs": [],
                "endpoint_refs": ["complete_asof_backtest"],
            },
            {
                "id": "rolling_backtest_complete",
                "statement": "历史顺序滚动回测同时报告候选模型、同折训练均值基线、误差改进及其区间。",
                "basis_kind": "user_request",
                "basis_text": "优化必须用只包含更早活动周的训练集评估预测表现。",
                "source_refs": ["features"],
                "artifact_refs": [
                    "experiment_results.json",
                    "rolling_predictions.csv",
                    "bootstrap_mae_improvement.csv",
                ],
                "measurement_refs": [
                    "rolling_test_cycle_count",
                    "rolling_candidate_mae",
                    "rolling_baseline_mae",
                    "rolling_mae_improvement",
                    "rolling_candidate_rmse",
                    "rolling_baseline_rmse",
                    "rolling_rmse_improvement",
                    "mae_improvement_ci_low",
                    "mae_improvement_ci_high",
                    "candidate_absolute_error_win_count",
                ],
                "result_refs": [],
                "endpoint_refs": ["complete_asof_backtest"],
            },
        ],
        "artifact_plan": [
            {
                "id": "numeric_results",
                "path": "experiment_results.json",
                "kind": "json",
                "description": "严格输入审计、全样本关联和滚动回测测量。",
                "producer_stage_id": "asof_backtest",
            },
            {
                "id": "rolling_predictions",
                "path": "rolling_predictions.csv",
                "kind": "csv",
                "description": "SC13至SC24逐周期滚动预测和同折训练均值基线。",
                "producer_stage_id": "asof_backtest",
            },
            {
                "id": "bootstrap_distribution",
                "path": "bootstrap_mae_improvement.csv",
                "kind": "csv",
                "description": "5000次成对bootstrap平均绝对误差改进分布。",
                "producer_stage_id": "asof_backtest",
            },
        ],
        "experiment_stages": [
            {
                "id": "asof_backtest",
                "objective": "核验严格预测时可得性并完成扩展窗口历史回测。",
                "input_ids": ["features", "manifest", "monthly", "smoothed", "extrema"],
                "consumes_artifact_ids": [],
                "produces_artifact_ids": [
                    "numeric_results",
                    "rolling_predictions",
                    "bootstrap_distribution",
                ],
                "prerequisite_stage_ids": [],
                "join_policy": "all",
                "method_outline": "复核全部时序审计列，在固定早期窗口上测量秩相关；随后从SC13开始，每折仅用更早周期拟合线性模型和训练均值基线，并对同一测试周期的误差差做成对bootstrap。",
                "measurement_refs": measurement_names,
                "result_refs": ["strict_asof_audit_passed"],
                "endpoint_ids": ["complete_asof_backtest"],
                "criterion_refs": [
                    "strict_input_audit",
                    "association_measured",
                    "rolling_backtest_complete",
                ],
                "outcome_rules": {
                    "completed": "全部时序审计、测量和逐周期产物通过核验。",
                    "inconclusive": "计算有效但误差改进区间跨越零或样本不足以支持方向判断。",
                    "input_missing": "任一官方来源或冻结特征不可用。",
                    "evidence_conflict": "关联结果与滚动回测结果给出无法协调的方向。",
                    "method_invalid": "严格时序审计失败或滚动训练不能回答当前问题。",
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
                        "rolling_predictions.csv",
                        "bootstrap_mae_improvement.csv",
                    ],
                },
            }
        ],
        "interpretation_policy": {
            "primary_estimand": "严格截至第24个月可得的早期上升率在扩展窗口滚动回测中相对训练均值基线的平均绝对误差改进",
            "null_rule": "平均绝对误差改进区间跨越零时，不宣称候选模型稳定优于基线。",
            "uncertainty_rule": "结论强度由12个滚动测试周期、成对误差区间和逐周期胜出数量共同限制。",
            "partial_rule": "严格时序审计未通过时，不报告预测性能为可解释科学结果。",
        },
    }


def build_assessment(preview: dict) -> dict:
    values = {
        item["name"]: item["value"]
        for item in preview["trusted_worker_result"]["measurements"]
    }
    ci_low = float(values["mae_improvement_ci_low"])
    if ci_low > 0:
        outcome = "completed_interpretable"
        stage_outcome = "completed"
        interpretation = "候选模型在当前滚动测试周期上的平均绝对误差低于训练均值基线，且成对bootstrap区间保持为正。"
    else:
        outcome = "high_uncertainty"
        stage_outcome = "inconclusive"
        interpretation = "候选模型的点估计误差改进为正，但成对bootstrap区间跨越零，当前样本不足以支持稳定优于基线。"
    return {
        "proposed_outcome": outcome,
        "stage_outcome": stage_outcome,
        "rationale": interpretation,
        "criterion_results": [
            {
                "criterion_id": criterion["criterion_id"],
                "status": "met",
                "explanation": "该判据引用的时序审计、测量或产物均已由执行记录核验。",
            }
            for criterion in preview["criterion_evidence"]
        ],
        "uncertainty_reasons": [
            "滚动测试只有12个活动周。",
            "活动周之间可能存在长期非平稳性。",
            "bootstrap区间不消除周期非独立性。",
        ],
        "null_assessment": None,
        "report_narrative": {
            "title": "严格预测时可得的太阳活动周早期上升率滚动回测",
            "objective": "检验截至活动周谷值后第24个月可得的早期上升率能否改善官方峰值强度预测。",
            "data_scope": "使用SILSO Version 2.0月均总黑子数、官方13个月平滑序列和SC1至SC24官方周期极值；SC13至SC24用于按时间顺序滚动测试。",
            "method": "以谷值后7至24个月期间原始月均观测的线性斜率为唯一预测量。每个测试活动周只用更早周期拟合线性模型，并与同折训练峰值均值比较；对12个成对绝对误差差做5000次bootstrap。",
            "interpretation": interpretation,
            "evidence_strength": "证据来自官方周期标签、严格输入时序审计和真实历史顺序回测；强度受测试周期数量限制。",
            "claim_boundary": "结果不构成第26活动周数值预测，也不证明早期上升率与太阳发电机机制之间的因果关系。",
            "limitations": [
                "只有12个滚动测试活动周。",
                "早期历史观测质量可能随年代变化。",
                "单变量线性模型不能表示全部周期动力学。",
            ],
            "next_steps": [
                "冻结相同口径后加入极区场前兆，检验是否提供独立增量。",
                "新增完整活动周后进行真正的前瞻外部验证。",
            ],
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_solar_cycle_asof_experiment.py TASK_WORKSPACE")
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
            run_id, build_response(request), build_design(request)
        )
        if checked["status"] != "design_validated":
            print(json.dumps(checked, ensure_ascii=False, indent=2))
            return 3
        prepared = service.prepare(
            run_id,
            [{"path": "experiment.py", "content": WORKER_CODE}],
            None,
            "首次正式尝试：执行严格预测时可得性审计和扩展窗口滚动回测。",
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

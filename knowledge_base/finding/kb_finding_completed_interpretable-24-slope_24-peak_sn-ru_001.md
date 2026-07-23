---
id: "kb_finding_completed_interpretable-24-slope_24-peak_sn-ru_001"
type: "finding"
title: "[completed_interpretable] 实验任务：检验太阳活动周开始后前24个月的上升速度（slope_24）能否在不偷看未来的前提下预测本周峰值强度（peak_sn）。\n\n上游产物：\n- 规划 ru"
source_type: "historical_run"
source_ref: "question_64975383f928-20260723T063653Z-388a9b6e"
confidence: "low"
status: "candidate"
valid_range: ""
related_ids: []
provenance: {"run_id": "question_64975383f928-20260723T063653Z-388a9b6e", "agent": "automatic_experiment"}
version: 1
created_at: "2026-07-23T06:57:45+00:00"
updated_at: "2026-07-23T06:57:45+00:00"
created_by: "automatic_experiment"
---

## statement

前24个月上升速度与峰值强度之间存在强正单调关联（Spearman ρ = 0.769）。1000次置换检验显示，无一次置换产生与观测值相当或更大的相关系数（经验p = 0.000）。LOOCV预测误差（38.88个太阳黑子数）低于均值基线（56.51个太阳黑子数），表明前24个月上升速度包含预测信息。然而，其预测精度不及传统上升率（22.22个太阳黑子数），后者使用了完整上升期数据。泄漏审计确认前24个月上升速度的计算依赖周期谷值，而周期谷值需等待周期结束后才能确定，构成未来信息泄漏。逐一排除每个有效周期后，Spearman ρ在0.741至0.811之间，排除任一周期后正关联方向一致。

## run_id

question_64975383f928-20260723T063653Z-388a9b6e

## uncertainty

周期谷值的确定需要观测完整周期，前24个月上升速度因此包含未来泄漏，不能视为完全无泄漏的预测变量; 23个有效周期的样本量限制了统计功效和LOOCV误差估计的稳定性; 排除前24个月上升速度为负的周期可能引入选择偏差

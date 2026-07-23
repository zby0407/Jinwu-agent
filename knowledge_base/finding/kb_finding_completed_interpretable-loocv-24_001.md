---
id: "kb_finding_completed_interpretable-loocv-24_001"
type: "finding"
title: "[completed_interpretable] ## 实验：早期上升速率预测太阳周峰值（LOOCV）\n\n### 目标\n检验每个太阳周开始后前 24 个月的上升速率能否预测该周期的最终峰值。\n\n### 数据\n-"
source_type: "historical_run"
source_ref: "question_3e2a787ae508-20260723T013846Z-3a5a3222"
confidence: "low"
status: "candidate"
valid_range: ""
related_ids: []
provenance: {"run_id": "question_3e2a787ae508-20260723T013846Z-3a5a3222", "agent": "automatic_experiment"}
version: 1
created_at: "2026-07-23T01:50:00+00:00"
updated_at: "2026-07-23T01:50:00+00:00"
created_by: "automatic_experiment"
---

## statement

在模拟数据上，前24个月上升速率对峰值展现出较强的预测能力（R²=0.8013676485465032），平均绝对误差18.85667221252361，优于均值基线（MAE=48.62723927020636）。这表明Waldmeier效应在方法层面是可检测的。

## run_id

question_3e2a787ae508-20260723T013846Z-3a5a3222

## uncertainty

当前实验使用模拟数据而非真实太阳黑子观测数据，结果需要进一步验证; 样本量仅24个周期，统计估计的不确定性较大; 未考虑早期周期（1-5）数据质量问题

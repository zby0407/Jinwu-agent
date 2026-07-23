---
id: "kb_finding_completed_interpretable-smoke-test-20260722-n-60_002"
type: "finding"
title: "[completed_interpretable] 合成数据方法 smoke test（非真实太阳活动研究）。固定随机种子 20260722，分别生成 n=60 的 Poisson(lambda=100) 与 P"
source_type: "historical_run"
source_ref: "question_0dd6d944eb0e-20260722T104426Z-d126f563"
confidence: "low"
status: "candidate"
valid_range: ""
related_ids: []
provenance: {"run_id": "question_0dd6d944eb0e-20260722T104426Z-d126f563", "agent": "automatic_experiment"}
version: 1
created_at: "2026-07-22T10:54:40+00:00"
updated_at: "2026-07-22T10:54:40+00:00"
created_by: "automatic_experiment"
---

## statement

Poisson(λ=100) 组样本均值为 100.95，Poisson(λ=105) 组样本均值为 102.87，均值差为 1.92。Welch t 检验 t 统计量为 1.074，双侧 p 值为 0.285，未拒绝两组均值相等的零假设。2000 次 bootstrap 均值差 95% 区间为 [-1.68, 5.25]，包含零。两种方法在本次合成数据上均未检出 λ=100 与 λ=105 两组之间的均值差异。

## run_id

question_0dd6d944eb0e-20260722T104426Z-d126f563

## uncertainty

合成 Poisson 数据与真实太阳活动数据分布特征不同，本 smoke test 结果不适用于评估真实数据的分析方法; 仅使用单一随机种子，未评估不同种子下检出能力的变异性; 未报告统计功效分析，无法判断未检出是由于效应量过小还是样本量不足

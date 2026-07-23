---
id: "kb_finding_completed_interpretable-smoke-test-20260722-n-60_001"
type: "finding"
title: "[completed_interpretable] 合成数据方法 smoke test。固定随机种子 20260722，分别生成 n=60 的 Poisson(lambda=100) 与 Poisson(lamb"
source_type: "historical_run"
source_ref: "question_c10a2d068c26-20260722T071540Z-c645eff3"
confidence: "low"
status: "candidate"
valid_range: ""
related_ids: []
provenance: {"run_id": "question_c10a2d068c26-20260722T071540Z-c645eff3", "agent": "automatic_experiment"}
version: 1
created_at: "2026-07-22T07:28:53+00:00"
updated_at: "2026-07-22T07:28:53+00:00"
created_by: "automatic_experiment"
---

## statement

lambda=100组样本均值为99.87计数，lambda=105组样本均值为105.25计数，均值差为5.38计数。威尔奇t检验t统计量为-2.787，双侧p值为0.00621，拒绝两组均值相等的零假设。2000次自助法重抽样均值差的95%百分位区间为[1.70, 9.05]计数，不包含零。两种方法均检出了两组均值差异。

## run_id

question_c10a2d068c26-20260722T071540Z-c645eff3

## uncertainty

仅使用单次随机种子，结果受特定种子实现影响，不能代表方法在重复抽样下的一般统计功效; lambda差为5相对于lambda=100的效应量较小，本次检出能力不能外推至其他参数组合; 自助法区间基于对原始样本的有放回重抽样，其覆盖概率依赖于原始样本的代表性

---
id: "kb_finding_completed_interpretable-smoke-test-20260722-n-60_003"
type: "finding"
title: "[completed_interpretable] 合成数据方法smoke test。固定随机种子20260722，分别生成n=60的Poisson(lambda=100)与Poisson(lambda=105)"
source_type: "historical_run"
source_ref: "question_f4b958fca49c-20260722T113622Z-0f9a829e"
confidence: "low"
status: "candidate"
valid_range: ""
related_ids: []
provenance: {"run_id": "question_f4b958fca49c-20260722T113622Z-0f9a829e", "agent": "automatic_experiment"}
version: 1
created_at: "2026-07-22T11:40:01+00:00"
updated_at: "2026-07-22T11:40:01+00:00"
created_by: "automatic_experiment"
---

## statement

lambda=100组样本均值为100.95计数，lambda=105组样本均值为102.87计数，观测到的均值差为1.92计数（lambda=105减lambda=100）。Welch t统计量为-1.074，双侧p值为0.285，未达到0.05显著性水平。bootstrap 95%百分位区间为[-1.42, 5.28]计数，包含零值。两种方法均未检出已知均值差异，结论一致。

## run_id

question_f4b958fca49c-20260722T113622Z-0f9a829e

## uncertainty

单次随机种子实现，观测均值差(1.92)远低于真实均值差(5)，结果高度依赖抽样变异; 未进行统计功效分析或多种子重复实验，无法量化II型错误率; bootstrap使用与数据生成相同的种子，bootstrap重采样与原始数据非独立

---
id: kb_experiment_paradigm_flare_baselines_001
type: experiment_paradigm
title: 耀斑预测气候率、持续性与可解释基线
source_type: literature
source_ref: "Camporeale 2025, Space Weather, doi:10.1029/2025SW004546"
confidence: high
status: canonical
valid_range: GOES阈值全日面或活动区概率预测；基线必须使用与候选相同的历史可用信息和测试实例
related_ids: [kb_data_source_swpc_flare_forecast_archive_001, kb_experiment_paradigm_probabilistic_calibration_001, kb_experiment_paradigm_rare_event_metrics_001]
---

复杂耀斑模型必须与零成本或低复杂度基线在同一预测实例上比较。最低配置包括训练期气候率和
只用发布时间前信息的持续性；存在 SRS 或区域历史时，再加入 McIntosh/区域分类或小型正则
逻辑模型。

## 基线规则

- 气候率只从每个训练折计算，不能使用测试期事件率；
- 持续性窗口和缺测处理在实验前固定；
- 候选和基线使用同一目标、预测窗、缺测筛选和评价样本；
- 所有概率基线同样接受校准和可靠性评价；
- 若复杂模型没有稳定改善基线，不以模型新颖性替代预测技能。

活动周相位或总体活动水平可以作为条件气候率，但分层规则必须在训练数据中确定，并与无条件
气候率同时报告，避免把更细分的事后分组当作独立技能。

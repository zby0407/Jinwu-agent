---
id: kb_experiment_paradigm_flare_chronological_backtest_001
type: experiment_paradigm
title: 耀斑预测时间推进与活动区隔离回测
source_type: literature
source_ref: "Ahmadzadeh et al. 2021, ApJS 254, 23, arXiv:2103.07542"
confidence: high
status: canonical
valid_range: 具有issue_time、活动区/事件组标识和重叠观测窗的太阳耀斑分类或概率预测数据
related_ids: [kb_concept_forecast_time_availability_001, kb_data_source_hmi_sharp_001, kb_experiment_paradigm_rare_event_metrics_001]
---

耀斑预测回测应模拟历史发布时间，只使用当时可得数据训练和生成概率。重叠时间窗、同一活动区
和同一耀斑事件产生的样本高度相关，随机逐行切分会把近重复样本分散到训练和测试并夸大技能。

## 最小设计

1. 固定预测目标、issue time、观测窗和预测窗。
2. 外层测试采用未来连续时间段；训练只用更早资料。
3. 需要评估未见活动区泛化时，NOAA/HARP 区域不得跨分区。
4. 同一事件的重叠窗口保持在同一事件组，必要时设置时间 embargo。
5. 插补、标准化、采样、特征选择、调参、校准和阈值都只在训练层完成。
6. 最终测试保持自然基率，不做过采样或欠采样。

回测结果支持的只是其切分所定义的泛化范围；按活动区隔离和按未来时间推进回答不同问题。

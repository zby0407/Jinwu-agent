---
id: kb_concept_forecast_time_availability_001
type: concept
title: 预测时点可用性与特征泄漏
source_type: literature
source_ref: "Ahmadzadeh et al. 2021, ApJS 254, 23, arXiv:2103.07542"
confidence: high
status: canonical
valid_range: 具有明确发布时间、数据延迟、观测窗和预测窗的太阳耀斑历史回测、模拟业务和实时概率预测
related_ids: [kb_concept_flare_forecast_target_001, kb_experiment_paradigm_flare_chronological_backtest_001]
---

预测时点可用性要求每个输入在预报发布时已经产生、传输、处理并可供该预测流程读取。
观测发生在发布时间之前并不自动意味着特征可用；近实时与事后定标产品、人工整理目录、
居中平滑、完整活动区寿命摘要和事后事件关联都可能引入未来信息。

## 最小时间合同

每个预测实例记录 `issue_time`、`data_cutoff`、观测窗和预测窗。观测窗结束不得晚于
数据截止时点，数据截止不得晚于发布时间，预测窗不得早于发布时间。下载或查询时间、
产品版本、数据延迟和缺测状态随输入快照保存。

## 强制边界

- 事后定标数据可用于科学回顾，但不能直接宣称模拟了实时预测。
- 缺失、延迟和尚未发布不能编码为活动平静或零耀斑。
- 预处理、阈值、校准和特征选择同样受可用时点约束。
- 若无法重建历史时点的数据版本和延迟，结果只能称为回顾性研究，不能称为模拟业务能力。

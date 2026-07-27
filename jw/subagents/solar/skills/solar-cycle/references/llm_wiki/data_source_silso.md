---
id: kb_data_source_silso_001
type: data_source
title: SILSO国际太阳黑子数
source_type: dataset_doc
source_ref: "https://www.sidc.be/SILSO/datafiles"
confidence: high
status: canonical
valid_range: 总黑子数日值自1818年、月均值自1749年、年均值自1700年；具体以所用SILSO版本说明为准
related_ids: [kb_concept_sunspot_cycle_001, kb_concept_proxy_relationship_drift_001, kb_experiment_paradigm_backtest_001]
---

SILSO（Sunspot Index and Long-term Solar Observations）由比利时皇家天文台维护，是太阳黑子数
长期研究的规范数据来源。研究中必须记录产品、版本、下载日期和时间聚合；“使用 SILSO”
本身不足以唯一确定输入序列。

## 与本项目最相关的产品

- 日总黑子数：适合事件时间和短时间聚合，但不应把每日点当作独立活动周样本。
- 月均总黑子数：适合活动周形态、上升和下降阶段分析。
- 13 个月平滑月值：适合事后定义极小与极大；居中平滑会使用未来月份。
- 年均总黑子数：适合长时段比较，但会抹去极值时刻和短时形态。
- 半球黑子数：直接 SILSO 南北半球序列的覆盖期远短于总黑子数。

## 版本和口径边界

- 2015 年发布的 v2.0 对历史序列进行了系统修订；不同主版本不可静默拼接。
- 早期记录的观测者数量、标定和不确定性与现代时期不同。
- 黑子数是视觉计数构成的活动代理，不直接测量磁通、日冕辐射或耀斑能量。
- 以平滑曲线确定的极值日期属于事后量；预测任务必须按当时可获得的数据重建。

## 在假设阶段如何使用

SILSO 可定义活动周边界、强度和形态基线。任何新增前兆或代理指标都应与只使用 SILSO
历史信息的基线比较；若优势只在特定版本、平滑方式或活动水平下出现，应优先考虑口径和
非线性解释，而不是直接声称太阳发电机发生变化。

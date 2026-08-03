---
id: kb_concept_full_disk_active_region_forecast_001
type: concept
title: 全日面与活动区耀斑预测
source_type: dataset_doc
source_ref: "NASA CCMC Flare Scoreboard, https://ccmc.gsfc.nasa.gov/scoreboards/flare/"
confidence: high
status: canonical
valid_range: GOES C/M/X阈值的全日面和可编号活动区概率预测
related_ids: [kb_concept_flare_forecast_target_001, kb_concept_flare_event_association_001, kb_data_source_noaa_solar_region_summary_001]
---

全日面预测回答太阳可见面在预测窗内是否发生目标事件；活动区预测回答指定区域是否发生
目标事件。二者的样本、标签缺失、可观测范围和用户用途不同，不能只靠改变输出列名互换。

## 区域到全日面

若把多个区域概率合成为全日面概率，必须声明区域事件的条件依赖处理。简单独立假设下可用
`1 - ∏(1-p_i)`，但同日区域共同受活动水平影响且可能不独立；未编号区域和临边/背面活动
还会造成漏项。合成方法需要独立校准和全日面验证。

## 评价边界

- 活动区划分变化会改变区域样本数和重复观测。
- 全日面预测可利用全局持续性，但不能证明正确定位了源区。
- 区域模型应报告编号映射、临边限制和区域出现/消失处理。
- 两种粒度必须分别设气候率和持续性基线。

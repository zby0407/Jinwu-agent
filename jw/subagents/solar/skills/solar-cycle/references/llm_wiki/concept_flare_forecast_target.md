---
id: kb_concept_flare_forecast_target_001
type: concept
title: 太阳耀斑概率预测目标
source_type: dataset_doc
source_ref: "NASA CCMC Flare Scoreboard, https://ccmc.gsfc.nasa.gov/scoreboards/flare/"
confidence: high
status: canonical
valid_range: 具有明确发布时间、空间单元、GOES阈值和预测窗的全日面或活动区耀斑概率预测
related_ids: [kb_concept_goes_flare_class_001, kb_concept_full_disk_active_region_forecast_001, kb_concept_forecast_time_availability_001]
---

一个耀斑概率预测实例由空间单元、目标阈值、发布时间和预测窗共同定义。例如“在给定
发布时间后的 24 小时内，全日面至少发生一次 M1.0+ 耀斑的概率”。缺少任一部分时，
预测概率没有唯一可核验的事件含义。

## 必填维度

- 空间单元：全日面或指定活动区；
- 目标：明确 GOES 超阈值或预先定义的事件计数；
- 发布时间与数据截止时点；
- 观测窗和预测窗；
- 输出：概率、计数分布或其他预先定义的量。

概率是目标事件在绑定条件下的预测，不是科学假设为真的置信度。目标、窗口或标签算法改变
会形成新的任务版本，不能只更新模型版本而保持同一任务 id。

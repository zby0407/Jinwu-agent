---
id: kb_concept_flare_event_association_001
type: concept
title: 耀斑事件窗口与活动区关联
source_type: dataset_doc
source_ref: "NOAA/NCEI GOES Flare Report ReadMe, https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes16/l2/docs/GOES_Flare_Report_ReadMe.pdf"
confidence: high
status: canonical
valid_range: 使用GOES事件记录构建全日面或NOAA/HARP活动区预测标签
related_ids: [kb_concept_goes_flare_class_001, kb_concept_full_disk_active_region_forecast_001, kb_data_source_goes_flare_catalog_001]
---

标签构建必须固定事件采用峰值时刻、开始时刻或其他时间锚点，并固定预测窗端点是否包含。
同一研究不得根据结果在不同锚点之间切换。建议为每个预测实例保留窗口内最强事件、事件数、
事件 id 和时间锚点，即使主标签只是是否发生超阈值事件。

## 区域关联

活动区预测还需绑定 NOAA 活动区编号、HARP 映射和位置来源。未定位、临边、背面、多区域
复合和编号缺失事件必须按预注册规则处理；不能把无法归属的事件任意分配给最接近区域。

## 边界

- 全日面标签不要求可靠区域归属，但不能验证区域级模型的定位能力。
- 区域级预测概率合成为全日面概率需要声明依赖假设。
- 同一事件产生的重叠样本应保留共同 `event_group_id`，防止跨训练与测试泄漏。

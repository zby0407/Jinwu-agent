---
id: kb_concept_goes_flare_class_001
type: concept
title: GOES软X射线耀斑等级
source_type: dataset_doc
source_ref: "NOAA/NCEI GOES-R XRS L2 Data User's Guide, https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes16/l2/docs/GOES-R_XRS_L2_Data_Users_Guide.pdf"
confidence: high
status: canonical
valid_range: GOES XRS 0.1–0.8 nm软X射线辐照度事件；具体事件算法、卫星和产品版本须随数据声明
related_ids: [kb_concept_flare_forecast_target_001, kb_concept_flare_event_association_001, kb_data_source_goes_flare_catalog_001]
---

GOES A、B、C、M、X 等级按软 X 射线 0.1–0.8 nm 通道的峰值辐照度数量级划分；
同一字母内的数字给出该数量级的倍数。它是观测到的软 X 射线峰值等级，不等于耀斑释放的
总能量、持续时间、积分通量、CME 强度或地球空间影响。

## 预测使用

预测目标必须写成明确的超阈值事件，例如未来 24 小时是否至少发生一次 `M1.0+` 事件，
而不是只写“强耀斑”。多阈值概率应保持逻辑嵌套，并分别报告事件基率和评价结果。

## 观测边界

- 事件开始、峰值、结束和背景估计由具体产品算法定义。
- 连续事件、升高的背景、探测器饱和和电子污染会影响事件量。
- 跨 GOES 代际比较必须保留卫星、校准、算法和质量标志。
- 事件等级不能提供可靠活动区归属时，允许全日面标签，但不得伪造区域标签。

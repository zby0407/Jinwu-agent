---
id: kb_data_source_noaa_solar_region_summary_001
type: data_source
title: NOAA Solar Region Summary
source_type: dataset_doc
source_ref: "NCEI SWPC Products and Data, https://www.ncei.noaa.gov/products/space-weather/partners/swpc-products-and-data"
confidence: high
status: canonical
valid_range: NOAA/SWPC日度太阳活动区摘要的存档覆盖；具体起始日期、发布时间和字段随产品版本核验
related_ids: [kb_concept_full_disk_active_region_forecast_001, kb_concept_flare_event_association_001, kb_data_source_hmi_sharp_001]
---

NOAA Solar Region Summary（SRS）提供日度编号活动区、位置、面积和太阳黑子分类等业务信息，
可用于构造基于 McIntosh/区域历史的简单预测基线，并连接 NOAA 区域编号与其他观测产品。

## 使用要求

- 保存原始文件、发布时间或归档时间、检索日期和哈希；
- 明确某一区域在发布时间是否已编号；
- 记录区域重编号、合并、拆分、临边消失和回归规则；
- 不用后续日报修正历史 issue-time 快照；
- 字段缺失与无区域必须分开。

SRS 是业务摘要而非完整矢量磁场数据。其分类可作为可解释基线或区域上下文，不能替代
HMI/SHARP 的磁场测量，也不能单独证明爆发机制。

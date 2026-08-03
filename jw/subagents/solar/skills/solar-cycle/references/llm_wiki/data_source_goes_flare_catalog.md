---
id: kb_data_source_goes_flare_catalog_001
type: data_source
title: NOAA/NCEI GOES耀斑事件报告
source_type: dataset_doc
source_ref: "NOAA/NCEI GOES Flare Report ReadMe, https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes16/l2/docs/GOES_Flare_Report_ReadMe.pdf"
confidence: high
status: canonical
valid_range: 当前复合报告覆盖GOES-08至GOES-19的科学质量事件，具体年份、卫星和更新状态以下载产品元数据为准
related_ids: [kb_concept_goes_flare_class_001, kb_concept_flare_event_association_001, kb_concept_forecast_time_availability_001]
---

NOAA/NCEI GOES Flare Report 是以 GOES XRS Level-2 科学质量耀斑摘要为基础的事件级
复合产品，提供开始、峰值、结束、辐照度、等级、背景、卫星来源、质量信息以及可得的位置。
NetCDF 适合保留完整元数据，CSV 适合审查，但必须同时保存对应元数据说明。

## 使用要求

- 记录文件 URL、检索时间、内容哈希、产品版本和覆盖范围；
- 以主卫星及其替代规则为准，保留实际卫星字段；
- 保留饱和、序列事件、位置来源和不确定性；
- 将科学质量回顾产品与近实时探测产品分开；
- 不把目录中没有事件的月份自动解释为完整有效观测。

旧 fixed-width XRS 报告与新的复合 Level-2 报告在格式、校准、算法和位置能力上不同。旧解析器
只能作为迁移参考，不能未经版本验证继续充当当前权威数据绑定。

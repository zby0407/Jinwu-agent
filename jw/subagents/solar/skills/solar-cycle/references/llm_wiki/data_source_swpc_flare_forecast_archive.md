---
id: kb_data_source_swpc_flare_forecast_archive_001
type: data_source
title: NOAA/SWPC耀斑概率预报存档
source_type: dataset_doc
source_ref: "NCEI SWPC Products and Data, https://www.ncei.noaa.gov/products/space-weather/partners/swpc-products-and-data"
confidence: high
status: canonical
valid_range: SWPC日度或每12小时发布产品的公开存档；产品名称、覆盖、发布时间和阈值定义须逐版本核验
related_ids: [kb_concept_flare_forecast_target_001, kb_experiment_paradigm_flare_baselines_001, kb_experiment_paradigm_probabilistic_calibration_001]
---

SWPC 历史预测产品可提供已经真实发布的 C/M/X 概率、发布时间和预测日，是检验业务预测的
重要比较对象。它与用最新数据重算的研究模型不同：存档概率在结果发生前已经发布，可支持
真正的实时预测验证。

## 使用要求

- 保留原始产品文本、产品名称、发布时间、预测日期、检索时间和哈希；
- 解析阈值、日界线、全日面/区域粒度和缺报规则；
- 不用后续 forecast discussion 或观测结果修补历史概率；
- 产品格式或发布频率变化时分版本解析；
- 使用第三方 scoreboard 数据时同时遵守其使用和署名规则。

SWPC 概率既可以作为外部业务比较，也可形成持续性/人工预报基线，但不得在训练集中把未来
SWPC 预测作为当时模型输入。

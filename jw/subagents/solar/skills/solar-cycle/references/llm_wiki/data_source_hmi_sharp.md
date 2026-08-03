---
id: kb_data_source_hmi_sharp_001
type: data_source
title: SDO/HMI Space-weather Active Region Patches
source_type: dataset_doc
source_ref: "JSOC HMI SHARP documentation, https://jsoc.stanford.edu/doc/data/hmi/sharp/old/sharp.MB.htm"
confidence: high
status: canonical
valid_range: SDO/HMI观测期内的HARP/SHARP近实时或定标活动区磁场产品；具体series、segment和QUALITY须声明
related_ids: [kb_concept_full_disk_active_region_forecast_001, kb_experiment_paradigm_flare_chronological_backtest_001]
---

SHARP 提供 HMI Active Region Patch 的矢量磁场数据、派生磁场参数、坐标和质量信息，是
活动区耀斑预测常用的光球磁场输入。每次使用必须记录 JSOC series、HARPNUM、时间范围、
cadence、segment/keyword、查询时间和原始记录 id。

## 关键限制

- near-real-time 与 definitive series 的延迟、定标和稳定性不同；
- `QUALITY`、反演、180 度消歧、投影和临边几何影响可用性；
- HARP 与 NOAA 活动区不是恒定一一映射；
- 派生磁参数可与磁复杂度相关，但不是日冕自由能或磁重联的直接观测；
- SDO 时代训练结果不能无校准外推到 MDI/SMARP 或更早时期。

预测特征只使用发布时间前可得记录。完整区域寿命、事后最大值和未来确定的边界不得作为输入。

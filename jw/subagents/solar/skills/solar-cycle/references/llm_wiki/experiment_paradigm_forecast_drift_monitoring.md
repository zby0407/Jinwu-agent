---
id: kb_experiment_paradigm_forecast_drift_monitoring_001
type: experiment_paradigm
title: 耀斑预测数据漂移与技能监测
source_type: literature
source_ref: "Goodwin, Sadykov & Martens 2024, ApJ 964, 163, doi:10.3847/1538-4357/ad276c"
confidence: medium
status: canonical
valid_range: 跨活动周阶段、仪器/产品版本和连续业务时间运行的太阳耀斑概率模型
related_ids: [kb_concept_forecast_time_availability_001, kb_experiment_paradigm_probabilistic_calibration_001, kb_experiment_paradigm_flare_baselines_001]
---

耀斑基率、活动区总体分布、太阳活动周阶段、输入产品版本和实时数据质量都会随时间变化。
历史平均技能不能保证当前概率保持校准，因此模型发布后仍需按固定时间窗口和事件数监测。

## 监测内容

- 输入缺测率、质量标志、特征范围和区域覆盖；
- 目标事件基率及 C/M/X 比例；
- Brier skill、可靠性、POD/FAR/CSI 等随时间变化；
- 相对于气候率、持续性和当前业务基线的技能；
- 按活动周阶段、临边距离和产品版本的失败切片。

## 启动规则

重校准、重训练或暂停发布条件应在看到未来结果前定义。数据 schema/算法改变、概率系统性偏高
或偏低、候选不再优于基线、关键输入长期不可用都可触发重新验证。重训练后必须产生新模型
版本并保留旧预测，不能回写历史概率。

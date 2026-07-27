---
id: kb_hypothesis_template_polar_precursor_001
type: hypothesis_template
title: 极区场前兆假设模板
source_type: literature
source_ref: "Schatten, K. H., & Pesnell, W. D. 1993, GRL, 20, 2275; Petrovay, K. 2020, Living Reviews in Solar Physics 17, 2"
confidence: medium
status: canonical
valid_range: cycles with polar-field or proxy measurements near minimum
related_ids: [kb_mechanism_babcock_leighton_001, kb_concept_polar_field_observable_001, kb_mechanism_hemispheric_coupling_001, kb_experiment_paradigm_backtest_001]
---

## 使用前必须固定

- 极区量：仪器、纬度孔径、投影订正、时间平均和南北半球合成方式；
- 预测时点：活动周 N 的哪个日期，允许使用哪些观测；
- 目标：活动周 N+1 的峰值、积分活动量、上升速度或半球目标；
- 比较基线：历史均值、前一周强度或不含极区量的同一模型。

## 候选 H1：极区种子场

在固定口径下，活动周 N 极小期附近的轴向偶极/极区场越强，活动周 N+1 的预先声明目标越高。
其物理动机来自 Babcock-Leighton 框架中极向种子场向下一周环向场的转化。

**预测：** 该关系应在逐活动周留出中方向一致，并在控制前一周强度和预测时点后仍提供增益。

**削弱条件：** 关系对极区定义、半球合成或单个活动周高度敏感，或在严格回测中不能优于基线。

## 候选 H2：半球抵消

北、南极区量分别包含信息，但合成为一个全日面值时因符号或相位差而抵消。

**预测：** 分半球建模应比单一合成量更稳定，且误差与半球相位差相关。

**削弱条件：** 分半球后没有新增信息，或优势仅来自样本增加/调参。

## 候选 H3：观测与处理口径

表观前兆关系的一部分来自季节投影、仪器标定、极区填补或时间窗口选择。

**预测：** 关系强度会随观测产品或处理版本系统变化；统一口径后效应减弱。

**削弱条件：** 多仪器、统一处理和敏感性分析下效应量保持稳定。

## 候选 H0：无稳定新增信息

在当前少量活动周中，极区量与下一周强度的关系不足以稳定超越简单基线。

**预测：** 外层留出误差没有一致改善，区间覆盖不佳，模型排序受单个周支配。

## 最有区分力的下一项检验

用预先固定的预测时点和极区口径，进行逐活动周留出；同时比较：

1. 简单基线；
2. 单一合成极区量；
3. 北、南半球分量；
4. 加入仪器/处理版本敏感性后的模型。

报告逐周预测和误差，不只报告总体相关系数。直接极区观测覆盖周数有限，因此结论应以效应量、
稳定性和不确定区间表述，不能写成确定性预报。

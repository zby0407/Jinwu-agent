---
id: kb_experiment_paradigm_indicator_drift_001
type: experiment_paradigm
title: F10.7与太阳黑子数关系漂移检验
source_type: derived
source_ref: "Tapping, K. F. 2013, Space Weather 11, 394-406, doi:10.1002/swe.20064; Solar-Cycle Co-Scientist falsification design"
confidence: medium
status: canonical
valid_range: 具有明确版本、口径和时间戳的F10.7与太阳黑子数重叠序列
related_ids: [kb_concept_f107_flux_001, kb_concept_proxy_relationship_drift_001, kb_experiment_paradigm_backtest_001]
---

该范式检验 F10.7—太阳黑子数映射是否具有超出活动水平、相位与测量版本的时间变化。目标
不是寻找最显著的断点，而是区分稳定非线性、相位迟滞、测量变化和真正的剩余漂移。

## 最小设计

1. 固定 F10.7 口径、黑子数版本、重叠时间、聚合尺度和缺失值规则。
2. 先拟合不含时间漂移的基线，包括合理的非线性活动水平项。
3. 加入活动周阶段或上升/下降期项，检验迟滞解释。
4. 仅在前两类模型仍有结构性残差时，比较分段、时间变系数或状态空间漂移模型。
5. 用逐活动周留出或前推预测比较模型，断点和复杂度选择必须限制在训练折。
6. 对替代数据版本、聚合尺度和高/低活动区间做敏感性分析。

## 区分性预期

- 稳定非线性解释：加入曲率后，所谓跨周斜率差明显减弱。
- 相位解释：条件于上升/下降阶段后，历时残差减弱。
- 测量解释：差异与版本、校准或口径切换对齐，并在一致口径中减弱。
- 剩余漂移解释：变化在留出周和替代处理下仍出现，且不能由活动范围差异解释。

## 报告与停止规则

报告每周残差、参数不确定性和模型比较，而不是只给相关系数。若样本不足以区分这些解释，
结论应为“当前数据不可辨识”；不得把未拒绝稳定关系写成已证明稳定，也不得把探索性断点写成
太阳内部状态跃迁。

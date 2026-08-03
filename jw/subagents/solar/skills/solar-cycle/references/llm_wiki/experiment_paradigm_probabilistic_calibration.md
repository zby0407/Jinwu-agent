---
id: kb_experiment_paradigm_probabilistic_calibration_001
type: experiment_paradigm
title: 耀斑概率校准与可靠性检验
source_type: literature
source_ref: "Nishizuka et al. 2021, ApJ, arXiv:2007.02564"
confidence: high
status: canonical
valid_range: 二元GOES超阈值概率预测；校准集与最终测试集必须隔离
related_ids: [kb_experiment_paradigm_flare_baselines_001, kb_experiment_paradigm_rare_event_metrics_001]
---

概率校准检验“预测为 p 的实例是否以约 p 的频率发生目标事件”。排序或 TSS 较好不保证概率
可靠，Brier 分数也应与参考预测和可靠性图共同解释。

## 最小设计

1. 在训练时期内保留独立校准层或使用嵌套交叉验证。
2. 校准器、概率截断和任何阈值不得查看最终测试结果。
3. 报告 Brier score、相对于训练期基线的 Brier skill、可靠性分箱及每箱样本量。
4. 同时报告分辨率或排序能力，避免把接近气候率的保守概率误称为高技能。
5. 对 M/X 稀有事件给出块 bootstrap 或其他保留时间/区域结构的不确定性。

若高概率区样本稀少，可靠性结论只能写为不确定；不能用平滑曲线掩盖分箱计数。

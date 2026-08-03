---
id: kb_experiment_paradigm_rare_event_metrics_001
type: experiment_paradigm
title: 稀有耀斑事件的概率与列联表评价
source_type: literature
source_ref: "Leka et al. 2019, ApJS 243, 36, arXiv:1907.02905"
confidence: high
status: canonical
valid_range: C/M/X超阈值二元预测；结果解释必须附目标基率、样本量和事件定义
related_ids: [kb_experiment_paradigm_probabilistic_calibration_001, kb_experiment_paradigm_flare_chronological_backtest_001]
---

耀斑尤其 X 级事件高度不平衡，始终预测“不发生”也可能得到很高准确率。因此准确率不能作为
唯一或主要资格指标，任何分数都必须随事件定义、基率、样本量和阈值报告。

## 概率指标

优先报告 Brier score/Brier skill、可靠性与分辨率；需要排序评价时补充 PR-AUC，并说明
参考基率。ROC-AUC 不能代替概率校准。

## 阈值指标

在不用最终测试集选择的阈值上报告 TP、TN、FP、FN，以及 POD、FAR、CSI、TSS、HSS 和
precision。不同指标强调漏报、虚警或相对随机/气候基线，模型排序可能随指标改变。

## 不确定性

按时间块、活动区或事件组重采样，不能把重叠窗口当独立样本。X 级样本过少时应报告区间和
事件级结果，不用单个高 TSS 宣称稳定泛化。

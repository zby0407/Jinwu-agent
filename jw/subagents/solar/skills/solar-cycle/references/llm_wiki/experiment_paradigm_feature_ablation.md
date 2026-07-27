---
id: kb_experiment_paradigm_feature_ablation_001
type: experiment_paradigm
title: 特征组消融与独立信息检验
source_type: textbook
source_ref: "Hastie, Tibshirani & Friedman 2009, The Elements of Statistical Learning, 2nd ed.; Solar-Cycle Co-Scientist leakage-control adaptation"
confidence: high
status: canonical
valid_range: 小样本、跨活动周预测中的嵌套特征比较；结论限于所用数据、模型族和验证设计
related_ids: [kb_experiment_paradigm_backtest_001, kb_concept_f107_flux_001, kb_concept_flare_cycle_relation_001]
---

特征消融用于检验某一观测域在基线之外是否提供可泛化的信息。太阳活动周样本很少，且大量
特征是同一时间序列的确定性变换；因此，应消融预先定义的特征组，而不是把单列重要度解释为
物理贡献。

## 最小设计

1. 在每个外层训练折内完成缺失值处理、标准化、特征筛选和超参数选择。
2. 使用逐活动周留出或时间前推的外层验证，禁止随机拆分月份或把同一活动周放进训练与测试。
3. 比较固定基线、基线加候选特征组、以及必要的替代代理组。
4. 对每个留出周报告配对误差差、方向一致性和不确定性，不只报告汇总均值。
5. 检查结论是否由单个异常活动周驱动。

## 解释边界

- 若 A 是 B 的确定性变换，移除 A 而保留 B 不能证明 A 没有物理作用；它只说明该模型未从
  A 获得额外预测信息。
- 高相关代理应做组消融或条件增量比较。单变量排列重要度会在相关特征之间任意分配贡献。
- “加入后性能改善”是模型与数据条件下的预测证据，不是因果机制证据。
- 若候选特征只在活动周结束后才能计算，它不能支持实时前兆假设，即使回测性能提高。

## 决策规则

只有当增量在多数外层留出周方向一致、对合理预处理和模型族不敏感、且预测时点无泄漏时，
才把“独立信息”置信度上调。否则应保留为不确定或数据依赖，而不是用单个最优分数接受假设。

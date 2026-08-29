---
name: solar-interaction-regime-testing
description: 当太阳周期模型的交互项区间跨零、正负方向都缺乏支持，或需要检验 effect modification、非线性与 regime 替代解释时使用。
---

# 太阳周期交互与 Regime 检验

## 核心原则

物理调制不等同于固定符号的线性交互。正、负交互均证据不足时保留加性模型，并把问题改写为“关系是否随预先定义的状态改变”，不能强迫二选一。

## 可检验模型

先定义响应 `Y`、前兆 `P`、候选调制量 `L` 和预测时点：

- **M0 加性模型：** `Y ~ P + L`。
- **M1 线性交互：** `Y ~ P + L + P×L`，不预设正负；报告交互区间与样本外增量。
- **M2 一个替代分支：** 只有外部物理依据预先给出阈值、非线性形式或 regime，且每个状态有足够独立周期时才使用。不得在同一小样本中同时搜索切点、函数和结论。

M1/M2 都不稳定时，结论为 `inconclusive_low_power` 或 `no_detectable_modification`，不是转向相反符号。

## 识别性与预测检查

1. 在训练折内中心化连续变量；检查主效应、交互项、年代和测量制度的共线性。
2. 周期对很少时严格限制自由度；代理与直接观测差异优先作为测量敏感性，不堆叠参数。
3. 未来预测使用 chronological rolling-origin；leave-one-cycle-out 只作影响诊断。
4. 重采样必须说明 exchangeability、时间依赖、低功效和区间宽度。

## 证据输出

同时报告 M0/M1/M2 的逐周期结果、交互区间、跨折符号稳定性、相对 M0 的 out-of-sample 增量、测量制度敏感性和 identifiability。仪器年代不能直接命名为太阳 regime。

## 判定

- `signed_interaction_supported`：方向、区间和样本外增量一致。
- `regime_modification_supported`：预注册 M2 稳定优于 M0，且不是测量制度产物。
- `no_detectable_modification`：有足够辨别力，但 M1/M2 无实质增量。
- `inconclusive_low_power`：样本或识别性不足，保留零假设和正负方向。

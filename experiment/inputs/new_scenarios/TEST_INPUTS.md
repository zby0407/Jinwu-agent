# 新场景测试输入（可直接粘贴）

每个场景给出 /automatic-experiment 弹框里应输入的完整任务文本。输入数据均已生成在 inputs/new_scenarios/ 下。

---

## 场景 1：新实验类型 —— 特征消融 + 分周期回测

任务文本：

```
请结合 inputs/new_scenarios/research_plan_feedback.md 与 inputs/new_scenarios/data_feature_feedback.json，使用 inputs/new_scenarios/cycle25_proxy_features.csv，自主设计并真实执行当前数据能够支持的有界实验。

需要完成：
1. 比较未校准与校准后的跨仪器一致性；
2. 对 geomag_index 辅助特征进行消融：分别用“仅 hmi_candidate_g”和“hmi_candidate_g + geomag_index”两种特征组合估计 wso_reference_g，报告两种方案在同一留出段上的误差差异；
3. 进行分周期回测：用 rising 相位数据校准，在 maximum + declining 相位数据上评估，再反向用 late 相位校准、early 相位评估，报告两个方向的误差；
4. 针对两个质量标记行分别做敏感性分析。

把当前不能回答的前兆外推问题明确保留为限制，不得把合成演示数据写成真实太阳观测或第 26 太阳活动周预测。
```

---

## 场景 2：阻塞/澄清分支 —— 数据不足 + 方法不匹配

任务文本：

```
请使用 inputs/upstream_handoff_demo/polar_overlap_features.csv，检验极区磁场与下一太阳活动周振幅之间的前兆关系，并给出第 26 太阳活动周振幅的预测区间。

要求：
1. 建立从 wso_reference_g 到下一活动周振幅的回归模型；
2. 给出振幅预测值和 95% 置信区间；
3. 用历史数据回测该预测模型。

如果当前数据不足以完成上述任务，请明确说明缺少什么数据，不要编造结果。
```

预期：应进入 execution_blocked / method_mismatch / clarification，而不是强行产出预测。

---

## 场景 3：多阶段实验 —— max_stages > 1

任务文本：

```
请结合 inputs/new_scenarios/research_plan_feedback.md 与 inputs/new_scenarios/data_feature_feedback.json，使用 inputs/new_scenarios/cycle25_proxy_features.csv，分两个阶段完成实验。

第一阶段：审计数据、比较未校准与校准后的跨仪器一致性、完成质量标记敏感性分析。

第二阶段：基于第一阶段发现，选择误差更低的校准方案，进行分周期回测（rising 校准 → maximum/declining 评估，以及反向），并报告分相位误差是否稳定。

两个阶段的结果都写入报告，保持阶段间测量口径一致。
```

---

## 场景 4：重放验证（验证弹框路由修复）

在 /automatic-experiment 弹框中输入：

```
重放 question_107e36411fea-20260720T100533Z-7953a0ea
```

预期：应触发真实 replay（复制 inputs、标记 replay_of、生成 replay 记录），而不是返回 method_mismatch。

---

## 场景 5：继续验证（可选）

在 /automatic-experiment 弹框中输入：

```
继续 <某个已存在的 run_id>
```

预期：应触发 continuation 准备流程，而不是当作新任务绑定。

# 三 Agent 多问题合同覆盖证明

- 状态：`passed`
- 模式：`offline_contract`
- 不同问题：9/9
- 角色覆盖：Planner=3，Experiment=3，Hypothesis=3
- 实验覆盖：E0_data_vintage_audit, E1_cycle_segmentation_baseline, E2_waldmeier_leave_one_cycle_out, E3_f107_phase_stratified_drift, E4_extended_hemispheric_calibration, E5_polar_precursor_robustness, E6_low_order_dynamo_family_ablation, E7_negative_controls_and_placebos, E8_clean_reproduction
- 模型调用：`false`

该证明只验证参数化入口、缺题停止、任务—计划精确绑定、ResearchPlan 1.0 合同与 E0–E8 路由覆盖。它**不评估 Qwen 对这些问题的 live 回答质量**，也不替代 12 案例 × 3 重复的正式 live proof。

| 案例 | 角色 | 预期实验 | 合同结果 |
|---|---|---|---|
| Q01_data_vintage_scope | b3-research-planner | E0_data_vintage_audit | passed |
| Q02_causal_cycle_segmentation | b3-research-planner | E1_cycle_segmentation_baseline | passed |
| Q03_waldmeier_robustness | b3-research-planner | E2_waldmeier_leave_one_cycle_out, E7_negative_controls_and_placebos | passed |
| Q04_f107_proxy_drift | b3-hypothesis | E3_f107_phase_stratified_drift, E7_negative_controls_and_placebos | passed |
| Q05_hemispheric_overlap_calibration | b3-experiment | E4_extended_hemispheric_calibration | passed |
| Q06_polar_precursor_small_sample | b3-hypothesis | E5_polar_precursor_robustness, E7_negative_controls_and_placebos | passed |
| Q07_dynamo_family_ablation | b3-hypothesis | E6_low_order_dynamo_family_ablation, E7_negative_controls_and_placebos | passed |
| Q08_negative_control_accounting | b3-experiment | E7_negative_controls_and_placebos | passed |
| Q09_clean_reproduction_gate | b3-experiment | E8_clean_reproduction | passed |

错误：[]

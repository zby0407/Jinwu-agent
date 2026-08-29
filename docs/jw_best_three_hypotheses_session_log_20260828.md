# JW 三个优先科学假设整理台账（2026-08-28）

## 输入

用户请求：

> 请你根据我们之前的结果整理jw agent提出的三个最好的科学假设出来

## 整理范围

本轮只使用当前仓库已存在的 JW 结果，不新增联网数据，不把未完成运行改写为成功结果。核对的主要材料为：

- `docs/第26太阳活动周-P5-P6评委展示稿.md`
- `outputs/cycle_morphology/cycle_morphology_strength_report.md`
- `outputs/sc26_direct_test/sc26_formal_forecast_report.md`
- `docs/真实前端科学问题与闭环结果.md`
- `docs/太阳活动AI-Scientist多子Agent科研闭环技术报告.md`

## 输出

形成读者可见整理稿：

- `docs/jw_best_three_scientific_hypotheses_20260828.md`

选出的三条假设为：

1. 上升时间越短，活动周峰值越高（Waldmeier 效应的统计表述；历史样本内高置信）。
2. 第 26 周期峰值低于第 25 周期暂定峰值（方向性概率预测；数值技能仍低置信，等待未来真值）。
3. 上一活动周长度会削弱极区场对下一周振幅的预测斜率（β3<0 的交互假设；当前低置信且证据受限）。

## 关键执行证据

- 使用读者可见科研写作规则检查成品边界。
- 可见文本审校命令：
  `python3 /home/zzz/.agents/skills/writing-reader-facing-content/scripts/audit_visible_text.py docs/jw_best_three_scientific_hypotheses_20260828.md`
- 审校结果：`No advisory findings.`

## 边界

本轮没有新增截图；本轮确实执行了 headed WebUI 续接，但未进入新的实验执行或发布阶段。文稿明确区分历史统计关系、可等待真值检验的预测和证据不足的机制探索，不把任何一条写成太阳发电机因果机制已经得到证明。

## 2026-08-28 续接验证记录

在完成组合排序实现后，使用同一原始太阳活动问题启动全新的 headed production WebUI 会话。线程
`01a046c6-139a-78b0-a17b-f1025a074445` 先由 `main_cycle_morphology.v26.portfolio` 创建，随后因外层终端中断而按持久化状态恢复；恢复前状态为 Planning=`accepted_with_limits`、Data=`produced`，并保留数据输出和运行台账。

恢复调用通过 `research/review/evals/run_webui_resume.mjs` 从生产 WebUI composer 提交，浏览器模式为 headed。Qwen 真实调用已完成两条证据登记、三条候选假设、尾审，并实际调用了 `scientific_hypothesis_rank_portfolio`；外层中断发生在该工具结果尚未写入状态之前，因此不把排序视为已完成。之后发现线程残留了错误的 `qwen` provider 覆盖，两个续跑记录明确为 `runtime_error`（`Unsupported provider='qwen'`），没有当作科研失败；脚本随即增加 `/model reset` 恢复路径。

清除覆盖后又从同一 headed WebUI 提交续跑，生成 run `01a0472e-60ec-7071-bdc5-b2c14586093e`。该 run 已正常进入 `qwen3.8-max`，但因长时间没有新的工具回执而被有界终止；持久化状态仍停在尾审：`portfolio_ranking=null`、`checkpoint=null`，故本次真实运行不构成完整闭环验收，也不提升科学结论等级。所有中断、配置错误和未完成状态均予以保留。

本次实现层验证已完成：根目录隔离临时目录 pytest 为 `3836 passed, 13 skipped, 6 warnings, 8 subtests passed`；WebUI 测试 `48 passed`；WebUI production build 成功；变更范围 Ruff 与 `git diff --check` 通过。读者可见文稿已用 writing-reader-facing-content 审校，未发现 advisory findings。上述自动化证据与真实模型/科研证据分开记录。

## 2026-08-28 真实极区前兆复核（Qwen 不参与）

本轮 Kimi 额度已耗尽，因此没有发起新的 Kimi 调用；确定性数据处理和实验执行由本地宿主完成，后续需要模型判断时统一使用 Qwen。历史 Kimi 回执保留为历史记录，不作为本轮证据。

权威数据登记于 `research/review/evals/runs/jw_solar_upgrade_20260828/project_root`，包括 SILSO v2.0 月度太阳黑子数、SILSO 平滑值与极值表、MWO–WSO 极区场、NOAA F10.7 和当前 WSO 页面。真实 H2 使用 SILSO 月度序列与 MWO–WSO 极区孔径场构造 10 个相邻活动周对，并完成 5 个严格时间顺序留出折（第 20—24 周）、训练均值与持续性双基线、固定种子 `20260828` 的 10,000 次 bootstrap、MWO/WSO 制度检查和逐折留一。

正式实验运行 `question_f30956e10616-20260828T121022Z-42d44151` 的终态为 `high_uncertainty`。候选 MAE `26.972`，训练均值基线 MAE `40.026`，点估计改善 `13.053`；bootstrap 95% 区间 `[-6.999, 31.299]` 跨零，因此不能宣称稳定预测技能。MWO 仅有 1 个测试折，WSO 有 4 个测试折；可评估方向未反转。H3 因没有登记的轴向偶极矩或日面拼图谐波输入而保持 `blocked_by_data`。

实验最初发现回执缺少与 worker 结果同名的顶层指标键，确定性核验将其拒绝为技术失败；修复回执合同后使用同一真实输入重新运行并通过验证。独立从 `rolling_predictions.csv` 和 `bootstrap_mae_improvement.csv` 重算的 MAE 与区间与回执完全一致。

读者可见三个假设稿已按接受状态更新：第二条改为真实极区场预测，第三条改为方向中性的“轴向偶极矩额外信息”假设，不再把普通极区场或未验证交互项当作轴向偶极矩证据。旧版本保留在 `docs/jw_best_three_scientific_hypotheses_20260828.legacy.md`，仅供过程追溯；当前稿已通过 `writing-reader-facing-content` 可见文本审校。

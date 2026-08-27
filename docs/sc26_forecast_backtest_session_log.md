# SC26 历史回测与正式预测会话记录

## 任务输入

用户要求：先核对 GitHub 是否有最新项目文件；若当前代码领先远端则先提交 PR；随后利用已登记数据和可核验的在线 SILSO 数据，对已完成太阳活动周做严格时间顺序历史回测，将预测峰值与实际峰值对比并绘制清晰曲线，在此基础上正式给出第26太阳活动周峰值预测；保留输入、输出、命令、截图和失败记录，并整理可展示文稿。

## 版本与远端核验

- 工作区：`/home/zzz/2026tzb/8.20.4`
- 分支：`codex/8.20.4-b3-acceptance-20260820`
- 已推送提交：`adc9269 feat(research): close autonomous B07 loop and publish evidence`
- GitHub：`https://github.com/zby0407/Jinwu-agent/pull/31`
- 核验结论：远端 `main` 当时为 `e68044a`，当前代码基于该提交并包含本地新增闭环修复，因此已先推送到 PR #31。PR 为开放草稿；本次新增预测代码尚未在本条目结束前再次推送。

## 数据输入

本次从官方 SILSO 地址取得并保存：

1. `inputs/SN_m_tot_V2.0.txt`，月度总太阳黑子数，SHA-256 `e83932c7a47a12c4826e3ed5ca48da0a49ef6da98aebb9580e2b83ff272d87a7`；
2. `inputs/SN_ms_tot_V2.0.csv`，13个月平滑月序列，SHA-256 `1289e5922889f26f4f322babe14f210442634c85ce02d1af8ccc050d2cc839da`；
3. `inputs/TableCyclesMiMa.txt`，官方活动周极小期/极大期表，SHA-256 `a4b5b8812c9e966f013c55655176cbd22531e7afd0c304cb93cc38e00e50e4a0`。

取得日期：2026-08-27。来源地址：

- <https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt>
- <https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv>
- <https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt>

## 执行记录

### 首次失败（保留）

命令首次读取官方月度 TXT 时失败：加载器把六列 SILSO 格式的第3列小数年份当成了“日”，并在带有临时标记 `*` 的最新行触发 `pandas.errors.ParserError`。这是真实阻断，不作成功运行计数。

### 修复

修复 `scripts/build_solar_cycle_asof_features.py`：支持官方当前的六列格式、末尾临时标记和旧七列测试夹具；通过第3列数值范围区分小数年份与日字段。

### 成功执行

```text
python3 scripts/run_sc26_historical_forecast.py \
  --monthly research/review/evals/runs/sc26_forecast_backtest_20260827/inputs/SN_m_tot_V2.0.txt \
  --smoothed research/review/evals/runs/sc26_forecast_backtest_20260827/inputs/SN_ms_tot_V2.0.csv \
  --extrema research/review/evals/runs/sc26_forecast_backtest_20260827/inputs/TableCyclesMiMa.txt \
  --output-dir research/review/evals/runs/sc26_forecast_backtest_20260827/results
```

程序输出摘要（原始完整 JSON 见 `results/run_summary.json`）：

```json
{
  "cycles": 25,
  "same_cycle_mae": 31.492449107835625,
  "same_cycle_baseline_mae": 42.726084691236004,
  "lag_peak_mae": 45.99851778834578,
  "lag_peak_baseline_mae": 42.42238066697664,
  "lag_peak_mae_improvement_ci95": [-11.590422869291944, 3.8906180154420387],
  "cycle_26_point_estimate": 174.99411497816038,
  "cycle_26_predictive_interval_95": [65.80607396181932, 277.6561818601972],
  "bootstrap_seed": 20260827,
  "bootstrap_repetitions": 10000,
  "confidence": "low"
}
```

独立的严格 as-of 特征构建也成功完成：24 行（SC1–SC24）、24/24 官方谷值候选、未来输入违规 0、目标时序违规 0、每周 18 个特征月。

## 统计口径

- SC1–SC24 是完整历史活动周；SC25 只作为预测 SC26 的已知前驱，不作为完整回测目标。
- 同周模型以谷值后第7–24个月原始月均值 OLS 斜率预测该周官方峰值，按 SC1–12 训练、SC13–24 测试。
- 主模型以 SC(t−1) 官方峰值预测 SC(t) 峰值，按 SC2–24 构建配对样本，在 SC10–24 做扩展窗口回测；训练均值是固定基线。
- bootstrap 以活动周为单位，固定种子 20260827，重复 10,000 次；正式预测区间加入历史残差。

## 产物与截图

- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_cycle_features.csv`
- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_forecast_predictions.csv`
- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_formal_forecast.json`
- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_forecast_visualization.png`
- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_historical_backtest_report.md`
- `research/review/evals/runs/sc26_forecast_backtest_20260827/results/sc26_formal_forecast_report.md`

截图/视觉核验文件：`sc26_forecast_visualization.png`，已用本地图像查看器打开，确认包含三个面板、实际值与预测值对照、完美预测参考线、第26周点估计和95%区间。

## 真实生产 WebUI B08

使用 `research/review/evals/sc26_formal_forecast_webui_v1.json` 和 `launch_sc26_formal_webui.sh` 启动了全新生产 WebUI，线程 ID 为 `01a0429c-02e7-7dd0-a08c-a6d5e9525131`，观察地址为 `http://127.0.0.1:4723/?threadId=01a0429c-02e7-7dd0-a08c-a6d5e9525131`。该运行真实完成了规划模型调用、规划工件和规划审查，并成功进入数据阶段调度；随后内部 `solar-data` A2A 调度长时间没有产生下一阶段终态。运行已通过 API 取消，状态和部分工件保留在 `.sc26-webui-workspace-20260827/projects/default/runs/run_01a0429c-02e7-7dd0_b7baa35c/`。因此 B08 只能报告为“真实 WebUI 部分执行、数据阶段阻断”，不能替代确定性回测与正式预测的完成证据。

## 解释边界

主模型点估计约 175，但历史回测 MAE 略差于训练均值，改进区间跨零，因此正式置信度标为低；这不是工程失败，而是对当前数据支持强度的诚实结论。真实 SC26 峰值尚未完成，最终误差只能在未来官方标签发布后评估。

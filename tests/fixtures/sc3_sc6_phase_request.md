请在本次新任务的绑定输入上，执行 B 阶段 SILSO 周期相位与幅度归一化。只做 B，保留正常 Data 科研审查。

先使用 Supervisor 注入的 bounded data context；协议应为 silso_cycle_phase_v1。唯一计算入口为 run_silso_cycle_phase。输入路径以实际绑定清单为准：
- monthly_total_path: inputs/monthly.txt
- smoothed_path: inputs/smoothed.csv
- official_extrema_path: inputs/boundaries.txt
- a_metrics_path: inputs/a_metrics.csv
- a_parameters_path: inputs/a_parameters.json
- parameters_path: inputs/phase_parameters.json

这五项数据/A 基线与 B 参数必须核验字节数、SHA-256 和任务绑定。A 表须由工具对冻结原始数据重新核对一致；不从聊天或旧会话记忆填入峰值。缺失输入、哈希不符或不具备新工具时，保存具体失败，不能切到 morphology、SC21—24 极值复现、H1/H2 或其他实验。

方法以 phase_parameters.json 为准：SC1—24 完整周期，每周期官方极小到下次极小映射到相位 0—1，101 点等距网格；使用发布的 13 月平滑序列做分段线性插值。下一极小月仅为变换端点，不重新计入 A。不得外推或跨缺月插值。

分别保存保幅曲线和除以 A 周期内真实平滑峰值的归一化曲线，不减极小值，不按网格最大值二次缩放。峰值相位与下降/上升时间比来自 A 的真实日期；零上升时间标记比值缺失。

目标为 SC3/4/5/6，参照为 SC1/2/7/8/9/10。参照形态为六条归一化曲线的周期等权均值；RMS 包含网格两个端点且点等权。输出目标到参照的距离、六个参照逐个留一距离、24 周期全部两两距离，以及形态指标。

Q2 仅作探索性判断：目标 RMS 严格超过全部六个背景留一 RMS 才称超出该参照背景范围；否则记当前参照下不支持异常形态。目标与背景所用均值的周期数分别为六和五，不能写成同分布检验、p 值或显著性结论。允许各目标判定不同。归一化丢失幅度信息，不据此否认 A 的活动强度结果。

必须保存真实 phase_curves.csv、reference_curve.csv、background_loo.csv、target_comparisons.csv、pairwise_distances.csv、cycle_shape_metrics.csv、hypothesis_Q2.json、两张曲线图、B_phase_report.md、输入副本、参数、代码、锁文件和 run_manifest.json。科研审查引用实际产物，而非只引用计算回执。输入只读，输出新目录，成功与失败均审计。

不执行 C/D、GNbb2 对照、bootstrap、预测或机制模拟，不调用网络更新冻结输入，不预设 SC3—SC6 都弱或 SC5/SC6 形态异常。不得把软件测试、输入预检或 run success 当作科研成功。工具产物生成时 not_reviewed，后续裁决另存，终答须区分两者。

数值判定补充：严格超过背景的计算需满足差值 > 1e-12 + 1e-12×背景最大距离，仅防止浮点误差误判；它不是观测不确定性或统计显著性阈值。

# 怀柔 SMFT 极区序列 1987–2026 特征分析

## 结论状态

本报告是工程诊断结果，不是完成几何与物理标定的正式极区磁场产品。输入产品的
`product_status` 为 `diagnostic_unvalidated`。原始数值只能在相同
`instrument_epoch` 和 `signal_unit` 内比较；年代内稳健标准分数只能用于发现异常和
比较相对变化，不能视为跨仪器物理校准。

知识边界沿用：

- `kb_data_source_huairou_smft_polar_1987_2001`
- `kb_concept_polar_field_observable_001`
- `kb_mechanism_hemispheric_coupling_001`

## 输入与复现

分析输入为服务器批处理产生的合并表：

- daily：1836 行，SHA-256
  `300bbc9fed0358f4f9f09c22a138623da317543b8d859c43ea17c005a34001d7`
- monthly：447 行，SHA-256
  `328420a08ec8fa3d72b4151d6330b37dbc2e7b641f382b5ea4f35f9adad865d0`
- 服务器运行摘要 SHA-256
  `2c890e7e3a208454787a3782143e0a9ff227e79658742e361347437d3d9de3b7`

复现命令：

```powershell
.\.venv\Scripts\python.exe `
  jw\subagents\solar\skills\solar-cycle\scripts\analyze_polar_huairou_features.py `
  --daily server-results\run_2014_2026\data\huairou_polar_precursor_1987_2026_daily.csv `
  --monthly server-results\run_2014_2026\data\huairou_polar_precursor_1987_2026_monthly.csv `
  --run-summary server-results\run_2014_2026\run_summary.json `
  --output-dir server-results\run_2014_2026\feature-analysis
```

分析目录包含 daily/monthly 派生特征、南北半球配对特征、年度与仪器年代摘要、季节性
诊断、异常行、PNG 图、JSON 摘要和独立 SHA-256 清单。生成结果位于忽略目录
`server-results/`，不应提交原始或派生观测数据到 GitHub。

## 数据一致性检查

- daily 和 monthly 主键均无重复。
- 所有必要数值有限，`field_mean_abs > 0`，`valid_pixel_ratio` 位于 `[0, 1]`。
- `field_mean_corrected = field_mean_raw - field_mean_center` 的最大绝对误差：
  daily 为 `2.84e-12`，monthly 为 `7.96e-13`。
- `polarity_strength = abs(field_mean_corrected)` 的最大绝对误差为 0。
- 使用 daily 独立重新聚合得到的 monthly 与服务器 monthly 完全一致。
- 服务器原始结果清单中的 46 个 SHA-256 条目全部通过复核，5 条处理错误记录与
  `run_summary.json` 计数一致。
- 服务器盘点支持 5718 个文件，未知格式和读取错误均为 0；5713 个文件成功处理，
  2022 年 5 个退化信号文件被明确记录为处理错误。

## 覆盖特征

- 日期范围：1987-12-29 至 2026-07-15。
- 范围内完全没有数据的年份：1988、2010–2013、2018–2019、2021。
- 月记录：北半球 224 行，南半球 223 行；217 个月同时具有南北半球记录，且配对行的
  仪器年代和信号单位一致。
- 最长连续缺口：南半球 2009-11 至 2014-05（55 个月），南半球 2017-08 至
  2022-02（55 个月），北半球 2009-12 至 2014-05（54 个月）。
- 月记录的观测日占当月自然日比例中位数仅为 9.68%；447 条月记录中有 124 条仅含
  1 个观测日。月值不能当作均匀连续采样。
- 2020 年仅有北半球 2 个观测日，没有同期南半球数据。

## 仪器年代与尺度

序列至少包含以下独立尺度：

| 仪器年代 | 实际覆盖 | 信号单位 | 解释 |
|---|---|---|---|
| `legacy_dat_1987_2001` | 1987–2001 | `detector_count_proxy` | 旧 `.dat` 工程强度代理 |
| `pulnix_fit16_2002_2008` | 2002–2008 | `detector_count_proxy` | FITS 16 位计数代理 |
| `pulnix_fit32_2009_2010` | 仅 2009 | `header_calibrated_proxy` | 样本极少 |
| `imperx_fit32_2014` | 2014-06 至 2014-12 | `header_calibrated_proxy` | 独立未验证几何年代 |
| `imperx_fit32_2015_2017` | 2015–2017 | `header_calibrated_proxy` | 独立未验证几何年代 |
| `imperx_fit32_2018_2026` | 2020、2022–2026-04 | `header_calibrated_proxy` | 中心圆孔径诊断 |
| `hsos_fit32_2026_schema_v2` | 2026-05 至 2026-06 | `header_calibrated_proxy` | 新头结构，无重叠校准期 |
| `hsos_fit32_2026_schema_v3` | 2026-07 | `header_calibrated_proxy` | 7 月混合头结构，无重叠校准期 |

例如旧 `.dat` 的 `field_mean_abs` 月中位数约 151 detector counts，而 2015–2017
IMPERX 的月中位数约为 3.6–4.0 header-calibrated proxy。两者数量级差异首先反映
处理口径和单位差异，不能解释为太阳长期变化。

## 派生特征

脚本输出以下主要诊断特征：

- `field_abs_robust_z_epoch`：在“仪器年代 × 信号单位 × 半球”组内，使用中位数和
  MAD 标准化的 `field_mean_abs`。
- `field_corrected_robust_z_epoch`：同口径的有符号校正均值诊断；不作为主要极区强度。
- `observed_day_fraction`：该月实际观测日数除以自然日数。
- `field_abs_pair_mean`：仅在南北半球仪器年代和单位相同的月份计算。
- `field_abs_asymmetry_ns = (N-S)/(N+S)`：仅对可比较南北配对计算。
- `signed_opposite_sign_diagnostic`：仅用于检查有符号均值行为，不用于认定真实磁极性。
- 稳健异常阈值默认为 `abs(robust_z) >= 5`，且每组至少 6 个样本；异常是复核队列，
  不是自动删除规则。

## 主要统计观察

1. 217 个可比较南北配对月的绝对不对称幅度中位数为 0.0898，即典型配对月的
   N/S 强度差约占两者总和的 9%。这描述观测代理的半球不对称，不能直接证明半球
   耦合机制。
2. 在样本较充足的年代，南北 `field_mean_abs` 月值呈中等到较高同步：旧 `.dat`
   Pearson 为 0.62（113 对），PULNIX 16 位为 0.81（36 对），2015–2017 IMPERX
   为 0.79（15 对），2018–2026 IMPERX 为 0.83（42 对）。2014 年只有 7 对且
   Pearson 为 -0.27，不足以形成稳定结论。
3. 共标记 196 条 daily 和 37 条 monthly 稳健异常。异常主要集中在旧 `.dat` 的高
   强度尾部，以及少数现代低覆盖月份；它们应结合原图、观测日数和中心参考区复核，
   不应直接裁剪。
4. 2020-01 北半球只有 2 个观测日，`field_mean_abs=24.38`，在对应 IMPERX 年代内
   稳健 z 值为 13.89；它同时缺少南半球配对，应列为高优先级质量复核点。
5. 2022-03-19 北半球 `field_mean_corrected=-352.99`，但
   `field_mean_abs=5.79`。这表明中心参考区会令有符号诊断产生巨大偏移，也支持继续把
   `field_mean_abs` 作为主要工程强度代理。2022 年 3 月南半球虽有 5 个退化文件失败，
   同日仍有 5 个有效观测进入 daily 结果。
6. 2026 年 1–4 月、5–6 月、7 月分别属于 IMPERX、HSOS v2、HSOS v3 三个年代，
   彼此没有同步重叠观测。因此 2026 年内部的台阶变化不能作为太阳变化解释，也无法仅
   由当前档案完成交叉标定。
7. 月份分组的年代内稳健中位数存在可见季节结构，且不同半球/年代幅度不同。这与视向
   投影和未验证几何的预期混杂一致，进入前兆模型前必须做季节/几何敏感性分析。

## 使用边界与下一步

- 当前可用于缺测分析、异常审计、同年代内相对变化和候选特征生成。
- 不应直接对全时期原始 `field_mean_abs` 拟合一条长期趋势。
- 不应把 `field_mean_corrected` 的符号当作已验证的南北磁极性。
- 进入太阳周期前兆实验时，应按完整活动周留出，固定极小期窗口，并在每个训练折内部
  处理缺测和标准化；不得随机拆分月份。
- 最优先的外部验证是：太阳 WCS/固定纬度孔径、P0/P1 手性、`CALIBRAT` 物理含义，
  以及与 HMI/WSO 重叠期的南北半球交叉检查。

以上观察是数据特征和质量诊断，不是因果机制证据，也不是第 26 活动周的正式预报。

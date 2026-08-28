# SILSO 太阳活动周形态—峰值强度统计实验

## 1. 数据来源、版本与范围

仅使用已注册的 SILSO v2.0 月度总数、13 个月平滑序列和官方极值/边界表。输入路径：`/home/zzz/2026tzb/8.20.4/.morphology-workspace-v24-20260827/projects/default/shared/data/solar_cycle/silso/monthly_total_v2/SN_m_tot_V2.0.txt`、`/home/zzz/2026tzb/8.20.4/.morphology-workspace-v24-20260827/projects/default/shared/data/solar_cycle/silso/monthly_smoothed_v2/SN_ms_tot_V2.0.csv`、`/home/zzz/2026tzb/8.20.4/.morphology-workspace-v24-20260827/projects/default/shared/data/solar_cycle/silso/cycle_extrema_v2/TableCyclesMiMa.txt`。官方极值表完整支持第 1—24 周；第 25 周仅作为第 24 周的下一极小边界，不作为样本。未联网补充数据，未使用极区磁场或 F10.7。

逐一核对 24 个官方最大日期后，峰值均能在注册的 13 个月平滑序列中定位。第 3 周在极值表与月度平滑序列之间相差 0.1（264.2 对 264.3）；本实验严格按预先声明的变量定义采用最大日期对应的平滑序列值 264.3，并在逐周期质量备注中保留该差异。

## 2. 变量定义

- 周期长度 = 本周官方极小月至下一周官方极小月的日历月差 / 12。
- 上升时间 = 本周官方极小月至本周官方极大月的日历月差 / 12。
- 下降时间 = 本周官方极大月至下一周官方极小月的日历月差 / 12。
- 峰值强度 = 本周官方极大日期在 SILSO v2.0 13 个月平滑序列中的太阳黑子数。
- 独立重采样单位为完整活动周；每行一个周期。早期组固定为第 1—12 周，较现代组固定为第 13—24 周。

## 3. 完整逐周期分析表

| cycle_number | minimum_date | maximum_date | next_minimum_date | cycle_length_years | rise_time_years | decline_time_years | peak_smoothed_sunspot_number | observation_period_group | data_quality_note |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1755-02 | 1761-06 | 1766-06 | 11.333333333333334 | 6.333333333333333 | 5.0 | 144.1 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. 18th-century observations have lower historical observing density. |
| 2 | 1766-06 | 1769-09 | 1775-06 | 9.0 | 3.25 | 5.75 | 193.0 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. 18th-century observations have lower historical observing density. |
| 3 | 1775-06 | 1778-05 | 1784-09 | 9.25 | 2.9166666666666665 | 6.333333333333333 | 264.3 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. Extrema-table peak 264.2 differs from the 13-month series value 264.3; the declared variable definition uses the series value. |
| 4 | 1784-09 | 1788-02 | 1798-04 | 13.583333333333334 | 3.4166666666666665 | 10.166666666666666 | 235.3 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 5 | 1798-04 | 1805-02 | 1810-07 | 12.25 | 6.833333333333333 | 5.416666666666667 | 82.0 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 6 | 1810-07 | 1816-05 | 1823-05 | 12.833333333333334 | 5.833333333333333 | 7.0 | 81.2 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 7 | 1823-05 | 1829-11 | 1833-11 | 10.5 | 6.5 | 4.0 | 119.2 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 8 | 1833-11 | 1837-03 | 1843-07 | 9.666666666666666 | 3.3333333333333335 | 6.333333333333333 | 244.9 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 9 | 1843-07 | 1848-02 | 1855-12 | 12.416666666666666 | 4.583333333333333 | 7.833333333333333 | 219.9 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 10 | 1855-12 | 1860-02 | 1867-03 | 11.25 | 4.166666666666667 | 7.083333333333333 | 186.2 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 11 | 1867-03 | 1870-08 | 1878-12 | 11.75 | 3.4166666666666665 | 8.333333333333334 | 234.0 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 12 | 1878-12 | 1883-12 | 1890-03 | 11.25 | 5.0 | 6.25 | 124.4 | early | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 13 | 1890-03 | 1894-01 | 1902-01 | 11.833333333333334 | 3.8333333333333335 | 8.0 | 146.5 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 14 | 1902-01 | 1906-02 | 1913-07 | 11.5 | 4.083333333333333 | 7.416666666666667 | 107.1 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 15 | 1913-07 | 1917-08 | 1923-08 | 10.083333333333334 | 4.083333333333333 | 6.0 | 175.7 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 16 | 1923-08 | 1928-04 | 1933-09 | 10.083333333333334 | 4.666666666666667 | 5.416666666666667 | 130.2 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 17 | 1933-09 | 1937-04 | 1944-02 | 10.416666666666666 | 3.5833333333333335 | 6.833333333333333 | 198.6 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 18 | 1944-02 | 1947-05 | 1954-04 | 10.166666666666666 | 3.25 | 6.916666666666667 | 218.7 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 19 | 1954-04 | 1958-03 | 1964-10 | 10.5 | 3.9166666666666665 | 6.583333333333333 | 285.0 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 20 | 1964-10 | 1968-11 | 1976-03 | 11.416666666666666 | 4.083333333333333 | 7.333333333333333 | 156.6 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 21 | 1976-03 | 1979-12 | 1986-09 | 10.5 | 3.75 | 6.75 | 232.9 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 22 | 1986-09 | 1989-11 | 1996-08 | 9.916666666666666 | 3.1666666666666665 | 6.75 | 212.5 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 23 | 1996-08 | 2001-11 | 2008-12 | 12.333333333333334 | 5.25 | 7.083333333333333 | 180.3 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |
| 24 | 2008-12 | 2014-04 | 2019-12 | 11.0 | 5.333333333333333 | 5.666666666666667 | 116.4 | modern | Official SILSO v2.0 extrema dates and complete next-minimum boundary; C25 is boundary-only. |

## 4. 三组关系、p 值与 bootstrap 区间

Pearson 与 Spearman 均报告双侧 p 值。Bootstrap 以完整活动周为重采样单位，固定随机种子 `20260826`，请求重复 `10000` 次；表中同时列出排除常量重采样后的实际有效次数。

| Relation | n | Pearson r (two-sided p) | Spearman rho (two-sided p) | Pearson 95% bootstrap | Spearman 95% bootstrap | Valid bootstrap |
|---|---:|---:|---:|---|---|---:|
| cycle length vs peak strength | 24 | -0.3242 (0.1222) | -0.3139 (0.1353) | [-0.7058, 0.0930] | [-0.6814, 0.1337] | 10000/10000 |
| rise time vs peak strength | 24 | -0.7495 (<0.0001) | -0.7619 (<0.0001) | [-0.8835, -0.5672] | [-0.8866, -0.5297] | 10000/10000 |
| decline time vs peak strength | 24 | 0.3827 (0.0649) | 0.3211 (0.1260) | [0.0551, 0.6415] | [-0.1171, 0.6711] | 10000/10000 |

## 5. 逐周期留一敏感性分析

### cycle length vs peak strength（Pearson 影响最大：周期 4；Spearman 影响最大：周期 4）

| 删除周期 | Pearson r | Spearman rho | n |
|---:|---:|---:|---:|
| 1 | -0.3203 | -0.3132 | 23 |
| 2 | -0.3288 | -0.3172 | 23 |
| 3 | -0.2460 | -0.2380 | 23 |
| 4 | -0.4889 | -0.4369 | 23 |
| 5 | -0.2684 | -0.2657 | 23 |
| 6 | -0.2329 | -0.2400 | 23 |
| 7 | -0.3562 | -0.3253 | 23 |
| 8 | -0.2799 | -0.2380 | 23 |
| 9 | -0.3796 | -0.4023 | 23 |
| 10 | -0.3257 | -0.3077 | 23 |
| 11 | -0.3618 | -0.3795 | 23 |
| 12 | -0.3231 | -0.3077 | 23 |
| 13 | -0.3123 | -0.3063 | 23 |
| 14 | -0.3138 | -0.2885 | 23 |
| 15 | -0.3313 | -0.3245 | 23 |
| 16 | -0.3671 | -0.3562 | 23 |
| 17 | -0.3188 | -0.3093 | 23 |
| 18 | -0.3079 | -0.3122 | 23 |
| 19 | -0.3119 | -0.2981 | 23 |
| 20 | -0.3203 | -0.3102 | 23 |
| 21 | -0.3123 | -0.3051 | 23 |
| 22 | -0.3075 | -0.2984 | 23 |
| 23 | -0.3353 | -0.3315 | 23 |
| 24 | -0.3346 | -0.3093 | 23 |

### rise time vs peak strength（Pearson 影响最大：周期 5；Spearman 影响最大：周期 9）

| 删除周期 | Pearson r | Spearman rho | n |
|---:|---:|---:|---:|
| 1 | -0.7623 | -0.7541 | 23 |
| 2 | -0.7561 | -0.7771 | 23 |
| 3 | -0.7269 | -0.7392 | 23 |
| 4 | -0.7403 | -0.7583 | 23 |
| 5 | -0.7056 | -0.7323 | 23 |
| 6 | -0.7248 | -0.7412 | 23 |
| 7 | -0.7403 | -0.7452 | 23 |
| 8 | -0.7382 | -0.7580 | 23 |
| 9 | -0.7655 | -0.8006 | 23 |
| 10 | -0.7492 | -0.7580 | 23 |
| 11 | -0.7404 | -0.7583 | 23 |
| 12 | -0.7457 | -0.7481 | 23 |
| 13 | -0.7703 | -0.7689 | 23 |
| 14 | -0.7922 | -0.7867 | 23 |
| 15 | -0.7511 | -0.7570 | 23 |
| 16 | -0.7525 | -0.7481 | 23 |
| 17 | -0.7487 | -0.7585 | 23 |
| 18 | -0.7430 | -0.7742 | 23 |
| 19 | -0.7820 | -0.7927 | 23 |
| 20 | -0.7572 | -0.7570 | 23 |
| 21 | -0.7465 | -0.7620 | 23 |
| 22 | -0.7462 | -0.7734 | 23 |
| 23 | -0.7612 | -0.7640 | 23 |
| 24 | -0.7393 | -0.7452 | 23 |

### decline time vs peak strength（Pearson 影响最大：周期 7；Spearman 影响最大：周期 14）

| 删除周期 | Pearson r | Spearman rho | n |
|---:|---:|---:|---:|
| 1 | 0.3642 | 0.2760 | 23 |
| 2 | 0.3967 | 0.3175 | 23 |
| 3 | 0.4237 | 0.3654 | 23 |
| 4 | 0.3291 | 0.2700 | 23 |
| 5 | 0.3350 | 0.2546 | 23 |
| 6 | 0.4321 | 0.4046 | 23 |
| 7 | 0.3253 | 0.2685 | 23 |
| 8 | 0.4102 | 0.3654 | 23 |
| 9 | 0.3639 | 0.2883 | 23 |
| 10 | 0.3818 | 0.3337 | 23 |
| 11 | 0.3460 | 0.2700 | 23 |
| 12 | 0.3766 | 0.2804 | 23 |
| 13 | 0.4236 | 0.3472 | 23 |
| 14 | 0.4350 | 0.4179 | 23 |
| 15 | 0.3840 | 0.2967 | 23 |
| 16 | 0.3581 | 0.2734 | 23 |
| 17 | 0.3819 | 0.3353 | 23 |
| 18 | 0.3811 | 0.3338 | 23 |
| 19 | 0.4229 | 0.3650 | 23 |
| 20 | 0.3957 | 0.3452 | 23 |
| 21 | 0.3881 | 0.3436 | 23 |
| 22 | 0.3842 | 0.3337 | 23 |
| 23 | 0.3832 | 0.3337 | 23 |
| 24 | 0.3577 | 0.2715 | 23 |

## 6. 早期与较现代时期比较

分组边界预先固定，未按统计结果调整；各组 n=12，因此区间宽度与检验功效均需谨慎解释。

### early（n=12）

| Relation | n | Pearson r (two-sided p) | Spearman rho (two-sided p) | Pearson 95% bootstrap | Spearman 95% bootstrap | Valid bootstrap |
|---|---:|---:|---:|---|---|---:|
| cycle length | 12 | -0.3015 (0.3409) | -0.2802 (0.3777) | [-0.8335, 0.3518] | [-0.8397, 0.4837] | 10000/10000 |
| rise time | 12 | -0.8937 (0.0001) | -0.8581 (0.0004) | [-0.9706, -0.7685] | [-0.9926, -0.5295] | 10000/10000 |
| decline time | 12 | 0.5177 (0.0847) | 0.4939 (0.1027) | [0.1156, 0.8200] | [-0.0966, 0.8792] | 10000/10000 |

### modern（n=12）

| Relation | n | Pearson r (two-sided p) | Spearman rho (two-sided p) | Pearson 95% bootstrap | Spearman 95% bootstrap | Valid bootstrap |
|---|---:|---:|---:|---|---|---:|
| cycle length | 12 | -0.3979 (0.2002) | -0.3298 (0.2951) | [-0.7634, -0.0258] | [-0.7249, 0.2884] | 10000/10000 |
| rise time | 12 | -0.5177 (0.0847) | -0.6409 (0.0247) | [-0.8536, -0.1896] | [-0.8681, -0.1759] | 10000/10000 |
| decline time | 12 | 0.0586 (0.8565) | -0.1121 (0.7287) | [-0.7997, 0.6923] | [-0.8624, 0.5970] | 10000/10000 |

## 7. 图表说明

`cycle_morphology_relationships.png` 含周期长度、上升时间、下降时间与峰值强度的三个散点图。每个点标注周期编号，颜色区分固定的早期/较现代组，黑线为全样本线性拟合；图内给出 Pearson r 与双侧 p 值。拟合线仅用于展示统计关系，不代表因果机制。

## 8. 主要结论

**上升时间—峰值强度：稳定负相关，支持 Waldmeier 效应的统计表述。** 全样本 Pearson r=-0.7495（p=<0.0001），Spearman ρ=-0.7619（p=<0.0001）；两种 bootstrap 区间均完全低于 0。逐周期留一后 Pearson r 范围为 [-0.7922, -0.7056]，Spearman ρ 范围为 [-0.8006, -0.7323]，方向未改变。早期组与较现代组点估计也均为负（Pearson -0.8937 与 -0.5177）。因此，对历史第 1—24 周中这一描述性关系给出中高置信度；它不证明太阳发电机因果机制。
**周期总长度—峰值强度：负向点估计，但证据不足以判为稳定关系。** 全样本 Pearson r=-0.3242（p=0.1222），Spearman ρ=-0.3139（p=0.1353），全样本两种 bootstrap 区间均跨 0。两个时期点估计同为负，但样本各仅 12 个周期，且现代组 Pearson 区间与参数 p 值、Spearman 区间并不一致，故只视为待进一步检验的迹象。
**下降时间—峰值强度：时期不稳定。** 全样本 Pearson r=0.3827（p=0.0649），Spearman ρ=0.3211（p=0.1260）。Pearson 百分位 bootstrap 区间略高于 0，但 Spearman 区间跨 0；早期组为中等正向，较现代组接近 0。因此不能把下降时间关系表述为跨时期稳定规律。

### 结论置信度分层

| 结论 | 置信度 | 依据 |
|---|---|---|
| 数据范围、日期边界与 24 行周期表 | 高 | 注册 SILSO v2.0 输入、官方边界、逐日期交叉核对与确定性行数检查 |
| 上升时间与峰值强度的历史负相关 | 中高 | Pearson/Spearman、双侧 p 值、bootstrap、留一和分时期方向总体一致 |
| 周期长度与峰值强度的稳定关系 | 低至中 | 点估计为负，但全样本区间跨 0，分组不确定性较大 |
| 下降时间与峰值强度的稳定关系 | 低 | 指标与时期结果不一致 |

## 9. 局限性与不可作出的因果推断

样本量仅 24 个完整周期，早期/较现代各 12 个；相邻活动周可能存在序列依赖，早期历史观测质量也较低。Pearson 反映线性关系并可能受个别周期影响，因此与 Spearman、bootstrap 和留一结果联合解释。三组关系及两种相关量未作事后筛选；不显著、不稳定和指标不一致均保留。

这些结果只说明已结束历史周期中的统计关联。它们不能证明太阳发电机因果机制，不能把第 25 周当作完整样本，也不能用于分析或预测第 26 周。

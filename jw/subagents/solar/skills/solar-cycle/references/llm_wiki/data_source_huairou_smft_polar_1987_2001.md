---
id: kb_data_source_huairou_smft_polar_1987_2001
type: data_source
title: 怀柔太阳观测站 SMFT 极区纵向磁场（1987–2001，.dat 归档）
source_type: dataset_doc
source_ref: "Huairou Solar Observing Station, Beijing Astronomical Observatory; 35 cm SMFT longitudinal magnetograph"
confidence: medium
status: canonical
valid_range: 1987-12-29 to 2001-10-10; .dat legacy archive; 2002 onward stored as FITS and is not processed by this pipeline
related_ids: [kb_concept_polar_field_observable_001, kb_hypothesis_template_polar_precursor_001]
---

## 数据集概述

本数据集是怀柔太阳观测站（Huairou Solar Observing Station, HSOS）使用
35 cm 太阳磁场望远镜（SMFT）拍摄的**极区纵向磁图**历史归档，时间范围
**1987–2001 年**。2002 年起同一数据源改用标准 FITS 格式，与本阶段处理
的 `.dat` 档案在图像尺寸、像素尺度、文件头结构上均不同，因此本条目仅
覆盖 `.dat` 部分。

## 文件组织

目录结构示例：

```text
极区前兆/
  1990/
    apr/
      01/
        l501npla.dat   # 北极大视场，a 序列
        l501npla.dat   # 南极大视场，a 序列
        l501nplb.dat   # 北极大视场，b 序列
        l501splb.dat   # 南极大视场，b 序列
        s501npla.dat   # 北极小视场（默认不采用）
      ...
```

文件名约定（不区分大小写）：

| 字符位置 | 示例 | 含义 |
|---|---|---|
| 首字母 | `l` / `s` / `v` / ... | 仪器/视图前缀；处理时只采用 512×512 大视场帧 |
| 数字段 | `501` | 观测批次/日内序号 |
| 半球 | `npl` / `spl` | 北极（North Polar）/ 南极（South Polar） |
| 末尾字母 | `a`, `b`, `c`... | 同一日内多次观测序列号 |

## 图像格式

- **位深**：16-bit signed integer
- **字节序**：little-endian（与 FITS 标准 big-endian 相反）
- **大视场**：512×512 像素，文件实际大小常见为 524367 B（512×512×2 B 图像 + 79 B ASCII trailer）或 524416 B（128 B trailer）
- **小视场**：256×512 像素，但中心区域普遍存在饱和/无效数据，**默认不采用**
- `.dat` 文件没有标准 FITS 头；前 524288 B（大视场）直接为图像数据，后续字节为可变长度 ASCII trailer，可忽略

## 处理口径

本项目采用**简化工程口径**，用于数据特征子 Agent 的探索性分析，而非正式
太阳物理发布产品。由于 `.dat` 档案缺少标准坐标头信息，南北极磁图在简单
空间口径下**无法稳定分离出物理上合理的反号信号**（同号比例约 70%）。因此
处理策略从“有符号极区磁场”降级为“**极区磁场强度代理**”：

1. **极帽选取**
   - 北极（NPL）：图像顶部 100 行
   - 南极（SPL）：图像底部 100 行
2. **零偏校正**：用中心 256×256 背景区的中位数估计仪器零偏
3. **绝对强度代理**：对每个像素先减零偏，再取绝对值，最后做像素级平均
4. **符号平均仅作诊断**：`field_mean_corrected` 保留，但不作为周期级聚合主字段
5. **小视场帧**：默认跳过；其中心参考区存在大量饱和值，会污染零偏估计

## 输出字段

`load_polar_huairou.py` 生成两张表：

- **日表**：`date, hemisphere, field_mean_raw, field_mean_center, field_mean_corrected, field_mean_abs, valid_pixel_ratio, n_obs`
- **月表**：`year, month, hemisphere, field_mean_raw, field_mean_center, field_mean_corrected, field_mean_abs, n_days, polarity_strength`

其中 `field_mean_abs` 是进入 `build_features.py` 周期级聚合的**主字段**。

## 周期级聚合规则

在 `build_features.py` 中，对每个太阳活动周极小期（`start_date`）取前后
各 12 个月窗口：

- 南北极分别计算窗口内 `field_mean_abs` 的均值，得到 `polar_proxy_abs_n` 和
  `polar_proxy_abs_s`
- 单半球有效月数 ≥ 3 才视为有数据
- 至少一个半球满足阈值时，`polar_proxy_abs_combined` 取可用半球 `field_mean_abs` 的均值
- 数据质量字段 `polar_data_quality` 标记为 `good` / `single_hemisphere` / `insufficient`
- `polar_proxy_signed_n/s` 仅作为诊断字段保留

## 覆盖与质量说明

- **1987–1989**：极稀疏（1987 年仅 4 个文件，1988 年仅 1 个），不足以代表
  第 22 活动周极小期
- **1990–1997**：覆盖较好，1995–1997 年最密集
- **1998–2001**：观测密度明显下降
- 本数据不能与 2002 年后的 FITS 数据直接合并，除非先做跨格式/跨仪器校准

## 已知限制

- 未做球面坐标投影、径向磁场改正、P 角改正
- 未做仪器长期漂移标定
- 零偏校正依赖“中心区域宁静”假设，对活动区污染或饱和帧敏感
- 样本周期数有限，不能独立支撑强统计结论

## 使用建议

- 作为**极区前兆假设**的候选输入特征
- 与 SILSO 黑子数、F10.7 等代理联合建模时，应明确标注为“怀柔 SMFT 工程代理”
- 结论强度受样本量约束；建议与 WSO 等更长序列的极区磁场产品做交叉验证

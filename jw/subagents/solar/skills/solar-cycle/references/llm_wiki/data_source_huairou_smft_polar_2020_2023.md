---
id: kb_data_source_huairou_smft_polar_2020_2023
type: data_source
title: 怀柔太阳观测站 SMFT 极区纵向磁场（2020–2023）
source_type: instrument_archive
source_ref: "Huairou Solar Observing Station; 35 cm Solar Magnetic Field Telescope"
confidence: medium
status: processed
valid_range: 2020-01-25 to 2023-12-17; no accepted polar frames in 2021
related_ids: [kb_data_source_huairou_smft_polar_1987_2017]
---

## 已验证格式

本地归档 `D:\极区前兆` 共含 6381 个 FITS 文件。按文件名筛选出 1151 个
`npl`/`spl` 极区候选文件，分布为：2020 年 99 个、2021 年 0 个、2022 年
474 个、2023 年 578 个。

候选文件均为 `IMPERX 1M48`、`BITPIX=32`、`NAXIS=3`、
`NAXIS1=NAXIS2=992`、`NAXIS3=2`，并带 `CALIBRAT=10000`。该布局与
2015–2017 已验证 IMPERX 数据相同，使用标准 FITS 字节序。

## 生产参数

- 信号：`calibrated_vi = CALIBRAT * (plane0-plane1)/(plane0+plane1)`。
- 极区孔径：图像中心半径 150 像素的圆。
- 参考区：中心圆外至图像内切圆边界的环带。
- 主代理量：扣除参考区中位数后的像素绝对值中位数 `field_mean_abs`。
- 默认跳过全日面、非 `npl`/`spl` 和小视场文件。
- `field_mean_abs` 非有限或不大于零的退化帧作为坏帧写入错误日志。

## 处理结果

- `data/huairou_polar_precursor_2020_2023_daily.csv`：76 行日记录。
- `data/huairou_polar_precursor_2020_2023_monthly.csv`：34 行月记录。
- `artifacts/polar_processing_2020_2023_errors.jsonl`：5 个退化帧，均为
  2022-03-19 南极序列；其余 1146 个观测进入汇总。
- `data/huairou_polar_precursor_1987_2023_daily.csv`：1559 行合并日记录。
- `data/huairou_polar_precursor_1987_2023_monthly.csv`：376 行合并月记录。

覆盖情况：2020 年只有北极观测；2021 年没有极区观测；2022–2023 年同时有
南北极观测。因此 2020 和 2021 不能当作完整双半球连续序列。

## 复现命令

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\load_polar_huairou.py `
  --polar-dir "D:\极区前兆" `
  --start-year 2020 --end-year 2023 `
  --fit-signal calibrated_vi `
  --fit-aperture-mode center-circle --fit-center-radius 150 `
  --output data\huairou_polar_precursor_2020_2023_daily.csv `
  --monthly-output data\huairou_polar_precursor_2020_2023_monthly.csv `
  --errors-output artifacts\polar_processing_2020_2023_errors.jsonl
```

合并时使用 `merge_polar_outputs.py`，不得以覆盖方式处理重复的
日期/半球键。所有跨仪器时期分析必须保留 `instrument_epoch`、`camera`、
`signal_definition`、`signal_unit` 和 `calibration_status`，只在同一时期内部
解释代理量幅度。

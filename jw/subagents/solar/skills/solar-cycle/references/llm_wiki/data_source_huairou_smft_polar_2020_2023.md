---
id: kb_data_source_huairou_smft_polar_2020_2023
type: data_source
title: 怀柔太阳观测站 SMFT 极区纵向磁场（2020–2023）
source_type: instrument_archive
source_ref: "Huairou Solar Observing Station; 35 cm Solar Magnetic Field Telescope"
confidence: medium
status: diagnostic_failed_validation_gate
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

## 临时诊断参数（非生产参数）

- 临时信号：`calibrated_vi = CALIBRAT * (plane0-plane1)/(plane0+plane1)`；
  P0/P1 与左右圆偏振的顺序尚未由同日外部磁图确定，因此符号未验证。
- 临时孔径：图像中心半径 150 像素的圆。该孔径跨越太阳边缘，不能解释为
  固定日面纬度，禁止用于生产极区前兆。
- 参考区：中心圆外至图像内切圆边界的环带。
- 主代理量：扣除参考区中位数后的像素绝对值中位数 `field_mean_abs`。
- 默认跳过全日面、非 `npl`/`spl` 和小视场文件。
- `field_mean_abs` 非有限或不大于零的退化帧作为坏帧写入错误日志。

## 诊断结果

- `data/huairou_polar_precursor_2020_2023_daily.csv`：76 行日记录。
- `data/huairou_polar_precursor_2020_2023_monthly.csv`：34 行月记录。
- `artifacts/polar_processing_2020_2023_errors.jsonl`：5 个退化帧，均为
  2022-03-19 南极序列；其余 1146 个观测进入汇总。
- `data/huairou_polar_precursor_1987_2023_daily.csv`：1559 行合并日记录。
- `data/huairou_polar_precursor_1987_2023_monthly.csv`：376 行合并月记录。

这些 CSV 仅保留为诊断产物，不是通过验证的科学产品。覆盖情况：2020 年只有北极观测；2021 年没有极区观测；2022–2023 年同时有
南北极观测。因此 2020 和 2021 不能当作完整双半球连续序列。

## 验证门槛状态（2026-08-04）

- P0/P1 配准：通过。分层抽样 60 帧的平面相关系数最低为 0.998249，
  相位相关整数峰均为零像素位移。
- P0/P1 符号：未通过。文献给出 `V/I=(Vl-Vr)/(Vl+Vr)`，但 FITS 头未声明
  P0/P1 分别对应 `Vl` 还是 `Vr`，尚无同日有符号磁图确认。
- `CALIBRAT`：部分确认但未通过。所有抽样头均为 10000，且文献存在
  `C_L=10000 G` 的线性弱场标定；本批 FITS 头没有单位或方法注释，不能仅凭
  关键字确认其适用性。
- 太阳圆盘/WCS：未通过。抽样 60/60 均缺 `CTYPE/CRPIX/CDELT` 标准 WCS；
  `SIZE_PIX='0.242*2.242 ARC.'` 自相矛盾，`LATITUDE/LONGITUD` 也不能复原
  日面坐标。
- 固定日面纬度掩膜：未通过。当前中心圆落在太阳盘内的比例随帧变化约
  0.177–1.000，不是固定纬度区域。生产实现至少需要可靠日缘拟合、B0/P 角、
  相机滚转和像元尺度。
- SOLIS/HMI 同日有符号一致性：未通过。2020–2023 本地没有对照磁图；
  SOLIS/VSM 在 2017-10-22 后离线迁站，HMI 对照在未提供本地文件且禁止下载
  的条件下不能做像素级或区域平均符号检验。

在上述未通过项解决前，不得优化月平均，也不得进行跨年代绝对幅度标定。

## 复现命令

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\load_polar_huairou.py `
  --polar-dir "D:\极区前兆" `
  --start-year 2020 --end-year 2023 `
  --fit-signal calibrated_vi `
  --fit-aperture-mode center-circle --fit-center-radius 150 `
  --allow-unvalidated-geometry `
  --output data\huairou_polar_precursor_2020_2023_daily.csv `
  --monthly-output data\huairou_polar_precursor_2020_2023_monthly.csv `
  --errors-output artifacts\polar_processing_2020_2023_errors.jsonl
```

合并时使用 `merge_polar_outputs.py`，不得以覆盖方式处理重复的
日期/半球键。所有跨仪器时期分析必须保留 `instrument_epoch`、`camera`、
`signal_definition`、`signal_unit` 和 `calibration_status`，只在同一时期内部
解释代理量幅度。

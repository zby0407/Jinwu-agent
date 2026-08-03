---
id: kb_data_source_huairou_smft_polar_1987_2017
type: data_source
title: 怀柔太阳观测站 SMFT 极区纵向磁场（1987–2017）
source_type: instrument_archive
source_ref: "Huairou Solar Observing Station; 35 cm Solar Magnetic Field Telescope"
confidence: medium
status: diagnostic
valid_range: 1987-12-29 to 2017; 2010 has no accepted polar frames locally
related_ids: [kb_data_source_huairou_smft_polar_1987_2001]
---

## 当前状态

1987–2001 年 `.dat` 数据已经形成日表和月表。2002 年后的 FITS 支持已进入诊断阶段；在双层信号和孔径经人工确认前，不生成 2002–2017 全量正式序列。

不同年代没有同期交叉标定，合并表必须保留 `instrument_epoch`、`camera`、`source_format`、`signal_definition`、`signal_unit` 和 `calibration_status`。跨年代下游实验只能作为探索性结果，不能把原始幅度解释为连续、同单位的物理磁场。

## 已验证的文件格式

| 年代 | 图像布局 | BITPIX | 相机 | 字节序处理 |
|---|---:|---:|---|---|
| 2002–2008 | 640×480，单层 | 16 | PULNIX 6701AN | 文件载荷为非标准小端；读取后必须按确定规则交换字节 |
| 2009–2010 | 2×1000×992 | 32 | PULNIX 6701AN | 文件载荷为非标准小端；读取后必须按确定规则交换字节 |
| 2015–2017 | 2×992×992 | 32 | IMPERX 1M48 | 标准 FITS 字节序，不交换 |

解码只能依据 `shape + BITPIX + CAMERA` 的已知组合，禁止依据像素数值范围自动猜测。所有数据转为浮点后再做差分、除法和统计；非有限值、零填充、整型端点饱和值以及 V/I 零分母均作为无效像素。

## 双层信号诊断

双层文件当前同时输出以下候选信号：

- `plane0`
- `plane1`
- `difference = plane0 - plane1`
- `vi = (plane0 - plane1) / (plane0 + plane1)`
- `calibrated_vi = CALIBRAT × vi`

真实样本显示两个原始 plane 高度相关，派生的差分和 V/I 才呈现明显磁结构。因此生产 CLI 在诊断确认前不提供双层默认信号，必须显式传入 `--fit-signal` 或兼容参数 `--fit-plane`。

## 孔径候选

- 640×480：南北极分别取顶部/底部条带，诊断高度为 80、100、120 行；中心 320×240 区域作为零偏参考。
- 992×1000 和 992×992：比较中心圆半径 100、150、200，中心方 200×200、300×300，以及顶部/底部 100 行条带。
- 诊断输出位于 `artifacts/aperture_test/`，包括逐文件 CSV、日汇总、稳定性摘要、信号切片、孔径曲线和有效像素率分布。

## 过滤与覆盖

- 默认跳过文件名或 `HSOS_NO` 含 `wpl` 的全日面文件。
- 默认跳过 `S*` 小视场或 `CONTENT='S'` 文件。
- 仅接受 `npl`/`spl` 极区标识以及已知的 NAXIS、BITPIX、shape、camera 组合。
- 本地 2010 年共 15 个 FITS，全部为 `wpl`，因此正式极区序列保留 2010 缺口。
- 本地处理范围为 2002–2010、2015–2017；2011–2014 和 2018–2026 待服务器处理。

## 诊断与服务器命令

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\diagnose_polar_huairou.py `
  --polar-dir "D:\极区前兆" `
  --output-dir artifacts\aperture_test
```

参数确认后，服务器按年运行同一加载器，并为每年提供独立日表、月表和错误日志。模板中的 `<SIGNAL>`、`<APERTURE_MODE>` 和相应尺寸必须替换为诊断后确认的值：

```powershell
python jw\subagents\solar\skills\solar-cycle\scripts\load_polar_huairou.py `
  --polar-dir "<SERVER_ARCHIVE>" `
  --start-year <YEAR> --end-year <YEAR> `
  --fit-signal <SIGNAL> --fit-aperture-mode <APERTURE_MODE> `
  --output "data/huairou_<YEAR>_daily.csv" `
  --monthly-output "data/huairou_<YEAR>_monthly.csv" `
  --errors-output "artifacts/huairou_<YEAR>_errors.jsonl"
```

服务器待处理年份分块为 2011–2014 和 2018–2026。参数确认前不得批量执行该模板。

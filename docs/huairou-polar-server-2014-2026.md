# 怀柔 SMFT 2014–2026 服务器批处理

本流程生成工程诊断序列。太阳 WCS、固定日面纬度掩膜、P0/P1 手性与
`CALIBRAT` 的物理含义完成外部验证前，不得把输出描述为正式极区前兆产品。

## 1. 本地测试与打包

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_load_polar_huairou.py `
  tests\test_validate_polar_huairou.py `
  tests\test_inventory_polar_huairou.py `
  tests\test_run_polar_huairou_server.py `
  --basetemp .tmp\pytest-polar -q

.\.venv\Scripts\python.exe `
  jw\subagents\solar\skills\solar-cycle\scripts\prepare_polar_server_bundle.py `
  --historical-daily data\huairou_polar_precursor_1987_2017_daily.csv `
  --historical-monthly data\huairou_polar_precursor_1987_2017_monthly.csv `
  --output polar-server-bundle

scp -r .\polar-server-bundle USER@SERVER:/srv/huairou-polar
```

历史输入可以晚于 2014；批处理器只保留 2014 以前的行，新服务器结果从 2014
开始覆盖。

## 2. 建立服务器环境

```bash
ssh USER@SERVER
cd /srv/huairou-polar
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-polar.txt
```

## 3. 先盘点版式

```bash
./.venv/bin/python inventory_polar_huairou.py \
  --polar-dir "/实际服务器归档路径" \
  --start-year 2014 --end-year 2026 \
  --output artifacts/inventory_2014_2026.json \
  --records-output artifacts/inventory_2014_2026.csv
```

检查 JSON 中的 `unsupported_files` 和 `read_error_files`。任一非零时不得运行批
处理；应把 JSON/CSV 下载回本地，根据真实 FITS 样本补充并测试格式支持。空年份
会列在 `empty_years`，属于覆盖缺口但不会被伪造数据填补。

`duplicate_files` 记录存在同尺寸无后缀原件的 `(1)/(2)` 下载副本；这些副本不
参与统计。`excluded_files` 仅记录经过审计的已知坏文件或非 SMFT 派生布局，具体
原因保留在 inventory CSV 中。任何未被这些明确规则覆盖的未知布局仍会使批处理
停止。

2026 年部分文件使用缺少 `CAMERA` 的 HSOS 新头结构。只有同时满足审计签名
（双层 992×992、BITPIX=32、BSCALE=1、BZERO=32767、`CONTENT='L'`、
`HSOS_NUMBER`、`TIME_OBS`、CALIBRAT=10000、WAVE=5324、STOKES=3）时，才作为
独立的 `hsos_fit32_2026_schema_v2` 诊断年代接收；`CONTENT='Q'` 明确排除。

## 4. 运行诊断批处理

```bash
./.venv/bin/python run_polar_huairou_server.py \
  --polar-dir "/实际服务器归档路径" \
  --start-year 2014 --end-year 2026 \
  --workers 8 \
  --fit-signal calibrated_vi \
  --fit-aperture-mode center-circle \
  --fit-center-radius 150 \
  --allow-unvalidated-geometry \
  --historical-daily reference/huairou_historical_daily.csv \
  --historical-monthly reference/huairou_historical_monthly.csv \
  --output-root run_2014_2026
```

单层 640×480 文件会自动改走 `polar-strip`，不会使用中心圆。输出包括逐年
daily/monthly CSV、逐年 JSONL 错误日志、2014–2026 新序列、1987–2026 合并序列、
`run_summary.json` 和 `checksums.sha256`。

## 5. 取回并验收

```powershell
scp -r USER@SERVER:/srv/huairou-polar/run_2014_2026 .\server-results\
```

确认 `run_summary.json` 中：

- `product_status` 为 `diagnostic_unvalidated`；
- `unsupported_files` 和 `read_error_files` 均为零；
- 每年错误率、空年份和南北半球覆盖符合归档实际；
- 结果不存在重复的日期/半球或年份/月/半球键。

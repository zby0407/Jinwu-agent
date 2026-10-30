# 太阳活动月度数据整合

金乌支持从 WDC-SILSO 获取月均总太阳黑子数，从 NOAA SWPC 获取观测月均 F10.7，按用户指定月份范围整合为可复算的数据表。任务不需要预先绑定研究数据。

## 使用方式

向智能体提交包含数据源、时间窗和输出要求的请求，例如：

> 请下载 SILSO 月均总太阳黑子数和 NOAA SWPC 观测月均 F10.7，整合 2010-01 至 2024-12 的数据。按月份对齐，保留原始文件、缺失、来源与复算脚本，保存到当前工作区的 solar_monthly 子目录。

路由识别为 `data_integration` 后直接委派 `scientific-data` 子智能体，由其调用 `sci_data_solar_monthly`。工具只在用户授权公开数据获取后联网，默认不联网。结果包含实际统计与完成摘要，直接返回主界面。

开发者也可调用相同处理入口：

```python
from pathlib import Path
from jw.scientific_data.solar_monthly import integrate_solar_monthly

result = integrate_solar_monthly(
    Path("workspace/solar_monthly"),
    "2010-01",
    "2024-12",
    allow_network=True,
)
print(result)
```

输出目录应使用新目录。成功状态为 `completed` 或 `completed_with_missing`；下载失败返回 `acquisition_failed`，解析错误和冲突重复明确报错。

## 来源与处理

- [SILSO 月均总太阳黑子数 V2.0](https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt)：读取第 4 列的月均总太阳黑子数，为无量纲指标。
- [NOAA SWPC 观测月度指标](https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json)：读取 `f10.7`，单位为 sfu（10⁻²² W·m⁻²·Hz⁻¹）。

处理采用完整月份网格，保留未匹配月份；来源中的缺失哨兵转换为空值并记录原因，不做插补。完全相同的重复月份合并并保留来源位置，冲突重复终止处理。输出采用观测月均值，不使用平滑值或预测值。

F10.7 来自加拿大 Penticton 观测。NOAA 接口中的另一列黑子数也引用 SIDC，不能与 SILSO 黑子数重复计作独立证据；本流程合并的是 SILSO 黑子数与 F10.7。

## 产物与复算

| 产物 | 内容 |
|---|---|
| `merged_monthly.csv` | 月份、两列观测量、原始值、来源网址、行号或 JSON 索引、缺失原因 |
| `sources.json` | 来源、下载时间、字段含义、单位和原始文件位置 |
| `quality_report.json` | 覆盖范围、缺失、重复、数值范围和描述性相关系数 |
| `findings.md` | 中文结果说明与适用范围 |
| `acquisition.json`、`sources/` | 下载结果与原始文件 |
| `replay.py` | 使用保存的原始文件离线复算 |

安装同版本金乌后，在产物目录运行 `python replay.py`，结果写入 `replayed/`。脚本依赖同版 `jw.scientific_data.solar_monthly` 模块。历史数据可能由上游修订，离线复算使用本次保存的输入。

## 验证记录

2026-10-31，Windows 打包客户端使用 DashScope / qwen3.7-plus 完成一次无预置输入的真实任务：覆盖 2010-01 至 2024-12 的 180 个月，两列均无缺失、无重复。360 个数值与独立下载的参考数据一致，360 处原始来源位置核对通过，离线复算一致。启动至可发送 10.368 秒，提交至完成 25.613 秒；实际界面完成摘要已检查，退出后前后端端口关闭。

相关自动检查：

```bash
python -m pytest tests/scientific_data tests/test_research_router_middleware.py \
  tests/test_scientific_data_routing.py tests/test_langgraph_manager.py \
  tests/test_local_health_proxy.py -q
```

上述 378 项测试通过，覆盖缺失、下载失败、冲突重复、非法月份、离线复算、完整子智能体委派与故障代理下的本地健康检查。最终 Windows 运行时的 79 项检查通过，前端生产构建完成。客户端配置、原始运行会话和桌面构建产物不随源码提交。

## 适用范围

本适配器通过维护的官方来源目录定位两个特定月度序列，尚未验证开放网页发现与任意数据格式整合。相关系数仅作描述，不用于因果或预测结论。真实模型任务为单次样本，不能推导稳定提速比例、首次安装冷启动改善或赛道整体成功率。

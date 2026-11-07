# 第二步：用合同接入表格数据

目标是复用已有读取、映射、对齐、质量检查和数据包流程。以下是离线工程夹具配置，不是科研案例、展示选题或跨领域验收。

## 已有能力与本轮补齐

现有合同支持变量、别名、字段类型、单位、标识、范围及合并键；读取入口已有 CSV/TSV/TXT/DAT、JSON/JSONL、HTML、Markdown、PDF、XLSX。读取能力不表示任意复杂表格都能正确解析。已有缺失、重复、非有限值、单位、范围、冲突和覆盖检查，以及修复记录、人工问题队列和来源追溯。

本轮将合同字段覆盖为运行内的领域视图，使新字段的 identifier/time/label/quantity/quality 类型、别名、单位贯通映射和对齐，且不修改全局插件注册表。已有插件仍可复用；无需为每个问题注册 Python 插件。

新增请求级 `time_system`、`reference_mjd`，以及来源列级同名覆盖。声明随合同和字段映射保存，数据包的 `field_mapping.csv` 也包含这两列。

## 接入示例

通过正常 JW 的 `sci_data_contract` / `sci_data_run` 提供 `request_json`：

```json
{
  "research_goal": "工程夹具：读取配置化测量表",
  "domain": "generic",
  "entity_class": "fixture",
  "identifier_fields": ["sample_id"],
  "time_field": "elapsed",
  "time_system": "days_relative",
  "reference_mjd": 51544,
  "observables": [
    {"name": "sample_id", "kind": "identifier", "aliases": ["specimen"]},
    {"name": "elapsed", "kind": "time", "aliases": ["tick"]},
    {"name": "length", "kind": "quantity", "unit": "m", "aliases": ["reading"]},
    {"name": "group", "kind": "label", "aliases": ["batch"]}
  ],
  "value_ranges": {"length": [0, 2]}
}
```

输入表头可为 `specimen,tick,reading [cm],batch`。长度按确定单位关系转为 m，原值和原时间保留。范围按规范单位配置。别名只表达列含义，不凭名称猜单位。

调用 `sci_data_run` 时用 `inputs` 指向任务工作区内已有文件；复用原件可设置 `auto_discover=false`、`allow_network=false`。不必重新检索或下载。

个别来源没有写明单位或使用不同时间口径时，提供 `column_overrides_json`：

```json
[
  {"source": "second.csv", "column": "reading", "observable": "length", "unit": "cm"},
  {"source": "second.csv", "column": "tick", "observable": "elapsed", "time_system": "seconds", "reference_mjd": 51545}
]
```

`source` 为来源 ID 或文件 basename。字段必须存在于合同或选定插件。时间优先使用列覆盖，再使用请求声明，最后沿用现有列名识别。相对秒／天需要明确的 MJD 参考时刻；缺失时保留原文并报告歧义。当前主要复用 ISO、MJD/JD、相对秒／天；不新增时区、闰秒、BJD 物理校正或任意时间格式支持。复杂时标需后续按真实需求适配。

需要区分不同类别的同时刻观测时，将标签字段加入 `merge_keys`，例如 `["entity_id", "observable", "time", "group"]`。源表无标识列但有明确对象归属时，复用已有 `entity_overrides_json`。无时间轴的数据尚需单独适配，不视为本轮已完成。

## 结果与后续边界

检查数据包中的规范观测表、`field_mapping.csv`、`quality_issues.csv`、来源清单和修复日志；未知单位、不明确时标和来源冲突不应解释为自动解决。

规范观测 CSV 同时导出 `attributes_json` 和按实际属性生成的 `attribute:<name>` 列，保留 label 字段及作为合并键使用的属性；列名统一加前缀，避免覆盖标准数值字段。`provenance.jsonl` 同样保留属性，CSV 的来源摘要包含表、行、列定位。旧版数据包没有这些字段时应保留旧版，另生成新版本，不能据 `0 issues` 推断旧导出保留了所有语义。

第二步小范围收尾已在正常 JW/WebUI 用已有超新星69条记录验证：generic合同生成v1，发现标签导出遗漏后保留旧版、修复通用导出并另生成v2，由主JW核对和打包；前端产物页可查询、预览并提供下载入口。宿主没有直接运行处理内核或科研脚本。

这完成了当前小样例所需的第二步收尾，第二步全部格式与多领域迁移范围仍 partial。第三步已有正常 JW/WebUI 连续审批、同线程反馈、产物预览及实际下载的小范围验收；更广的多会话、故障恢复和全格式回归不在该验收范围。展示问题由用户另定，详细测试和冻结 E2E 后续按需安排。


科研工具不附加维护者隔离实验的模型次数、时长或流量额度；正常工具审批、工作区保护和采集策略继续生效。模型原有请求超时仍保留，达到本地请求总时限时准确报告超时，不伪装成网络故障并自动重试。

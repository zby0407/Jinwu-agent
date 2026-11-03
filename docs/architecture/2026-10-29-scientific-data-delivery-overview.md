# 科学数据整合子 Agent · 交付说明

本文面向评审与使用者，回答赛题要求的四件事：**输入需求、数据来源、处理流程、
结构化输出与结果解释逻辑**，并逐条对应赛题方向 1-A 的要求与加分项。方案的完整
模块合同见 `2026-10-29-scientific-data-integration-kernel-spec.md`，实测数字与
其局限见 `2026-10-29-scientific-data-snia-benchmark-report.md`。

- 代码：`jw/scientific_data/`（内核）、`jw/tools/scientific_data.py`（工具面）、
  `jw/subagents/scientific-data/`（子 Agent）
- 测试：`tests/scientific_data/`（含 SN Ia 基准与金标准）
- 分支：`feat/scientific-data-integration-20261029`

## 1. 输入需求：用户说什么，系统就编译成什么

用户只需用自然语言说清"研究什么、要哪些观测量、对象是谁"，例如
"我希望研究 Ia 型超新星的光变曲线，要 V 波段的星等随时间变化"。

这句话被编译成 `DataRequirementContract`（`jw/scientific_data/contracts.py`）：
`research_goal`、`domain_plugin`（如 `snia_light_curve`）、`observables`
（含目标单位、别名、取值范围）、`entity_class`、`time_field`、`merge_keys`、
`coverage_window`、`target_entity_count`、`min_records_per_entity`、
`accepted_roles`、`budget`（来源数/抓取数/表格数/时长）。

合同里**说不清的地方不会替用户猜**：缺研究目标、未知领域插件、未知观测量、
覆盖率区间不完整、缺实体类别，都会写成 `ambiguities` 并回报给用户，由
`sci_data_contract` 的返回值直接可见。合同的 `fingerprint()` 是后续所有产物
的复现锚点。

## 2. 数据来源：先规划角色，再在策略下取数

`sci_data_plan` 依据合同生成取数计划，按来源角色排序：
作者自附表 → 正式发表文献 → 补充材料 → 开放目录/数据库 → 图表数字化。
角色不是装饰，它决定解析与可信度路径（`SourceRole`、`SourceRecord.priority()`）。

用户没给材料时，`sci_data_discover` 调用文献检索做自动查找，把命中结果转成
候选 `SourceRecord`（标题、DOI、URL、同行评议、可信度），并明确回报检索
**实际处于哪个状态**：`ok` / `unavailable` / `policy_blocked` / `no_results` /
`skipped_all`。检索不可用时不会用编造的文献填空——这是硬约束，不是礼貌。

文献检索只回答“哪篇论文提到这个目标”，不回答“哪个文件能直接下载”。多源数据
发现（`data_providers.py`）补上后半段：一次有界查询并发访问 Zenodo、Dataverse、
OpenAlex，把命中规范化为文件级候选（下载直链、格式、大小、许可、DOI、角色），
再交给同一个 `SourceCatalog` 与获取策略。单个仓库超时或限流只记 `unavailable`
与 `provider_status`，不拖垮其它仓库，也不产生编造的候选。大于预算的文件（默认
50 MB）直接跳过并给出 `too_large` 原因；`.zip` 下载后由 `archives.py` 在成员数、
单成员大小、总量三重上限内展开，路径穿越、绝对路径、`__MACOSX` 元数据一律拒绝
或忽略，展开出的表格沿用同一套解析与溯源。`sci_data_run` 在
`allow_network=True` 且 `auto_discover=True` 时自动执行这一发现步骤，结果写入
ledger 的 `notes`（每个仓库的状态与命中数）。仓库开关与端点在
`JW_DATA_PROVIDERS`（逗号分隔，默认 `zenodo,dataverse,openalex`）与
`JW_ZENODO_ENDPOINT` / `JW_DATAVERSE_ENDPOINT` / `JW_OPENALEX_ENDPOINT`。

抓取由 `AcquisitionPolicy` 把关：默认**禁止联网**，只在用户给出 `allowed_hosts`
白名单并显式开启时才下载；被拒绝的来源记 `SOURCE_HOST_DENIED` 并说明理由。
落盘文件名带内容哈希前缀，同内容重复抓取只存一份。

## 3. 处理流程：十个阶段，每阶段留回执

| 阶段 | 模块 | 做什么 |
| --- | --- | --- |
| discover | `discovery.py`/`sources.py` | 检索候选、登记本地来源、去重、排优先级 |
| acquire | `acquisition.py` | 策略内抓取、哈希校验、断点续取 |
| extract | `tabular.py`/`html_tables.py`/`markdown_tables.py`/`documents.py`(PDF)/`archives.py`(ZIP) | CSV/TSV/JSON/JSONL/HTML/Markdown/PDF 表格解析；ZIP 在安全上限内展开后按成员格式解析 |
| semantics | `semantics.py` | 列 → 观测量映射（含歧义检测与备选） |
| identity | `entities.py` | 实体识别与合并（别名、坐标） |
| alignment | `alignment.py` | 长表化、单位与时间规范化、去重与冲突保留 |
| quality | `quality.py` | 缺失、重复、单位、越界、覆盖、符号、冲突检测 |
| repair | `repair.py` | 仅可逆、保值修正自动执行；其余进人工队列 |
| human | `human_loop.py` | 生成带选项的问题，写回答复并重校验 |
| package | `package.py` | 输出数据包、报告、清单与哈希 |

每个阶段写一条 `StageReceipt`（状态、计数、产物、错误），进入
`IntegrationLedger` 落盘，支持断点续跑与幂等复用。

三个关键机制：

- **单位与时间**：以量纲向量为基础（`units.py`），复合单位解析、Jy/mJy/uJy/sfu
  等确定性换算；magnitude 之间不换算（需零点），温度单位（°C/°F）显式拒绝换算。
  时间用标准库历法实现（`timescales.py`），MJD/JD/ISO/相位可互转，裸数字时间
  落在 MJD 包络内但未声明时标记 `ambiguous` 而不擅自采用。
- **图表数字化校验**（`figures.py`）：轴刻度最小二乘拟合（支持对数轴）、像素噪声
  转数值不确定度、反向投影叠加校验；轴刻度不足、拟合残差过大、声明的线性/对数
  与实际不符，都产出 `AXIS_TICK_UNREADABLE` / `AXIS_SCALE_SUSPECT` /
  `FIGURE_OVERLAY_MISMATCH`。数字化值 `value_kind=digitized`、附像素级不确定度，
  永不冒充原始测量。
- **人工回路**（`human_loop.py`）：冲突、实体身份、单位未确认、坐标轴解读等问题
  生成带 `options`/`recommended`/`evidence` 的问题；回答写入 `RepairLedgerEntry`
  （actor=user）并触发重校验。评审结论是证据——被否决的候选值不会被静默删除，
  而是加 `superseded_by:<authority>` 标记保留。

### PDF 表格：先看版面，再谈兜底

PDF 是唯一没有"单元格"概念的格式，因此读取分两级，且每级都留痕：

1. **文本层（默认，本地）**：PyMuPDF 读取每个词的坐标，按纵向中心聚类成行；
   有框线的表交给 `find_tables()` 按结构还原，无框线的表按词间距聚类切列
   （`columns_from="find_tables"` / `"word_geometry"`）。词坐标保留"SN 2011fe"
   这类含空格单元格，不再被空白切分拆成两列。
2. **云端兜底（显式开启）**：仅当页面没有可用文本、或页面含图且文本过短
   （`CLOUD_TEXT_FLOOR=200`）时，才把该页渲染成 PNG 调表格识别 API；需要
   `allow_network=True` 且配置了凭据，否则返回结构化警告而不是静默跳过。
   结果按文件哈希+页码+供应商缓存，断网可用缓存回放。云端表置信度 0.45、
   `format="pdf_cloud_ocr"`，并标记 `cloud_table_ocr`，供人工复核。

凭据走环境变量（优先）或 `config.yaml`：`BAIDU_OCR_API_KEY` /
`BAIDU_OCR_SECRET_KEY`（百度表格文字识别），或通用端点
`JW_PDF_CLOUD_ENDPOINT` + `JW_PDF_CLOUD_API_KEY`。`JW_PDF_CLOUD_PROVIDER`
可显式指定 `baidu`/`generic`，不填时按已有凭据推断。缓存目录
`JW_PDF_CLOUD_CACHE_DIR`（默认 `~/.cache/jw/pdf_cloud`）。

前端也有入口：**配置 → 数据源 → 解析接口**（`/api/scientific-data/interfaces`）。
PDF 表格、图表、数据仓库三组设置都可以在那里填写；保存写入本机
`~/.config/jw/config.yaml`（0600），界面只显示掩码，环境变量始终优先。

### 图表：本地只做有标定的数字化，读图交给云端

图表有两条路径，按证据强度分开：

1. **本地数字化（默认，可离线校验）**：调用方给出轴刻度与像素点（`figures_json`
   的 `x_ticks`/`y_ticks`/`series`），系统拟合坐标变换、换算单位、生成叠加图
   并核对残差。数值标记为 `digitized`，永远不冒充原始测量。
2. **云端读图（显式开启）**：页面含图、有真实文本层（图表标题/轴标签/图注）、
   且本地没有产出表时，才可把整页渲染成 PNG，按固定提示词要求模型返回
   `series | x | y` 的 markdown 表（`cloud_figures.py`）。结果置信度 0.4、
   `format="pdf_cloud_figure"`、标记 `cloud_figure_extraction`，按文件哈希+
   页码+模型+DPI 缓存，断网可回放。没有文本层的页面仍走表格 OCR：扫描件不是
   图表，拿读图提示词去问只会得到编造的点。

开关是 `sci_data_run(extract_figures=true)` 或 `WorkflowOptions.extract_figures`；
端点 `JW_FIGURE_CLOUD_ENDPOINT`（接受 `{image_base64, prompt, mode}` 并返回
`text`/`markdown`/`tables` 的 JSON 网关），凭据 `JW_FIGURE_CLOUD_API_KEY`，
模型名 `JW_FIGURE_CLOUD_MODEL`，缓存目录 `JW_FIGURE_CLOUD_CACHE_DIR`
（默认 `~/.cache/jw/pdf_figures`）。每一页都是一次计费调用，所以默认关闭。

## 4. 结构化输出：一个目录即可交付

`write_package` 产出的标准数据包：

| 文件 | 内容 |
| --- | --- |
| `integrated_observations.csv` | 合并后的多源长表（原始值与规范化值并存） |
| `source_catalog.csv` | 来源目录：角色、提供方、检索状态、哈希、失败原因 |
| `field_mapping.csv` | 每个源列的映射结果、置信度、是否采纳、换算说明 |
| `quality_issues.csv` | 问题清单：代码、严重度、状态、修复策略、是否阻断 |
| `repair_log.csv` | 修正台账：谁改的、改前改后、是否可逆、复核结论、哈希 |
| `provenance.jsonl` | 逐条溯源：来源、文件、行号/像素、提取器、置信度 |
| `data_quality_report.md` | 数据质量报告（八节，含方法与局限） |
| `run_manifest.json` | 运行清单：合同指纹、阶段回执、产物哈希 |
| `README_data.md` | 数据包使用说明 |
| `human_review.json` | 人工问题与答复（存在待确认问题时） |
| `figure_overlays/<id>.csv` | 图表数字化点与叠加校验残差 |

关键设计：原始值与规范化值**并存**（`value_raw`/`unit_raw`/`time_raw`）；
缺失值不插补，而是带原因（未报告/未解析/未观测/不适用/探测不到/已屏蔽）；
跨源冲突两条都留、标记 `conflict` 并进人工队列，绝不取平均。

## 5. 结果解释逻辑：为什么这份数据能用

报告第 6-8 节回答三件事：每个数字来自哪一行/哪个像素；哪些结论被证明、哪些只是
自洽；哪些数据缺口会限制研究。系统不隐式提升可信度——低置信映射仍是低置信，
数字化值仍是数字化值，检索失败仍记为失败。

## 6. 对照赛题要求

| 赛题要求 | 实现位置 | 验证 |
| --- | --- | --- |
| 输入研究目标即可用 | `contract_from_request` | `test_workflow.py`、`test_snia_benchmark.py` |
| 自动查找论文/数据库/表格/补充材料 | `discovery.py`、`sources.py`、`acquisition.py` | `test_discovery.py`、`test_sources.py` |
| 从正文、表格、附件、图表提取 | `tabular.py`、`html_tables.py`、`markdown_tables.py`、`documents.py`、`figures.py` | `test_tabular.py`、`test_html_markdown.py`、`test_documents.py`、`test_figures.py` |
| 输出合并的多源 CSV | `package.py` | `test_package.py`、基准 `package_self_explaining` |
| 数据清洗、字段对齐、来源标注 | `semantics.py`、`alignment.py`、`package.py` | `test_semantics.py`、`test_alignment.py` |
| 图表数据识别/提取/校验方式说明 | `figures.py`（标定数字化）/ `cloud_figures.py`（云端读图）+ 报告第 6 节 | `test_figures.py`、`test_cloud_figures.py`、基准 `axis_calibration_drives_values` |
| **加分**：识别缺失、重复、单位不一致 | `quality.py` | `test_quality.py`、基准 6 类注入 |
| **加分**：识别坐标轴/图例解析错误 | `figures.py` | 基准 `misdeclared_axis_scale_reported` |
| **加分**：修正或寻求人类建议后修正 | `repair.py`、`human_loop.py` | `test_repair.py`、`test_human_loop.py`、基准 `answering_conflict_changes_package` |
| 可复现 | `ledger_io.py`、`run_manifest.json` | 基准 determinism 1.00 |

## 7. 明确的边界

1. **不联网也能跑**，联网需显式白名单；发现能力的召回率未在真实目录上测过。
2. **PDF 文本层由 PyMuPDF 按版面读取**：有框线用结构识别，无框线按词间距聚类；
   重建表仍带 `pdf_text_reconstruction` 标记。多行单元格、跨列表头、双栏页面会
   失效。扫描件本地不 OCR，只有在允许联网且配置凭据时才走云端表格识别。
3. **图表数字化默认需要像素标定旁文件**：读数由上游读图步骤或用户提供，本模块
   负责拟合、换算、校验与标记。云端读图（`extract_figures`）省掉标定旁文件，
   但返回的是模型对整页的转写，置信度 0.4：不会区分拟合线/数据点、对对数轴和
   双轴图的读数可能错、颜色与图例的对应关系也可能错；它适合"先拿到候选数值再
   人工核对"，不适合直接当原始测量使用。
4. 基准只用一份 PDF 之外的九类来源、单图两标记点、六类注入异常；更宽的真实
   语料仍是下一步工作。

# Data Agent 与数据编排调整说明

## 结论

8 月 16 日早期的 r15–r19 验收阶段没有修改 Data Agent 本体，调整集中在下游数据编排、自动实验和审查衔接层。随后根据新的质量优先要求，Data Agent 已增加任务绑定的学术文献检索、网页来源获取、小型数据代码分析和结构化数据回执。r15–r19 对应增强前代码；r20 已加载增强代码，但没有调用新增 Harness 检索或托管代码分析，因此不能作为这两条路径的端到端验收。新增能力及其验证见 `Qwen_Harness与Data_Agent增强说明_2026-08-16.md`。

## 直接数据编排调整

### 1. 从逐周期表构造周期配对表

在 `jw/middleware/research_review_orchestration.py` 中增加了逐周期表到相邻周期配对表的确定性转换。每个样本以相邻太阳活动周期构成：

- 预测周期为太阳活动周期 N，目标周期为太阳活动周期 N+1；
- 上一活动周长度由相邻两个极小期日期之差计算；
- 极区场预测量取周期 N 结束极小期对应的极区场值；
- 后续周期振幅取周期 N+1 的平滑黑子数峰值；
- 同时保留测量体制、预测发布时刻、信息集边界和样本单位等字段。

转换结果保存为周期配对分析表及其说明文件，供实验设计和执行阶段读取。该转换不增加新观测，也不改写 Data Agent 的原始特征值。

### 2. 明确时间顺序和可用信息

配对表显式记录每个样本的起止极小期、预测量截止时间、目标是否在预测时刻可用，并记录后续评估必须遵守的训练边界要求。实验执行时还需逐折记录实际训练范围，以便在留一或扩展窗评估中按当时可用的信息集重新拟合，避免把目标周期的后验信息用作预测特征。

### 3. 将已接受的数据产物传递给实验阶段

编排层会从已接受的 Data artifact 中读取实际产物路径，将逐周期表、周期配对表及相应说明文件复制到当前任务的 `inputs/` 目录。当前代码与回归测试要求实验结果阶段继续使用同一已审查运行及其快照，不重新生成数据请求；8 月 19–20 日的完整 WebUI 运行已验证这一连续性，详见本文末节。

### 4. 保留数据局限

数据审查中确认的限制会继续传递到后续阶段，包括：

- 当前 v2 产物保留 cycle 14 边界行，可形成 14→15 至 23→24 共 10 个请求周期对；历史 v1 只有 15→16 至 23→24 的 9 对，不能进入实验；
- 相邻周期对共享极小期历元，样本并非完全独立，有效信息量低于名义样本数；
- 1976 年前的极区场来自 MWO 光斑代理，之后来自 WSO 磁像仪，测量体制与年代相互混杂；
- 极小期和峰值采用居中平滑后的事后标签，不适合作为实时预测信息。

这些限制不会因数据转换而消失，也不会被写成已解决的问题。

## 同轮自动实验与评审支撑调整

与数据编排同一轮还调整了自动实验和审查衔接，但这些修改不等同于增加 Data Agent 数据源：

- `jw/research_protocols.py` 增加周期配对、滚动原点评估、MAE/RMSE 和 MWO/WSO 敏感性等实验要求。
- `jw/middleware/research_router.py` 把需要真实观测检验的假设问题路由到完整研究流程，并注入相应实验要求。
- `jw/tools/automatic_experiment.py` 与 `src/automatic_experiment/` 支持紧凑的单阶段设计、WSL 原生执行、报告、验证及实验记录衔接。
- `src/research_review/adapters.py` 把已验证的实验记录转换为后续假设更新可用的结果摘要。
- 两个自动实验 Schema 的本轮修改是扩展有界务实选择的可用取值，未新增 Data Agent 输入字段。

## Data Agent 后续增强

Data Agent 现在同时使用两条资料获取路径：

- 学术文献路径通过 ADS、OpenAlex、arXiv、Crossref 等既有接口完成问题绑定、检索、正文或摘要缓存、摘录和证据包构造；
- 可选网页增强路径使用 Qwen `web_search` 获取外部线索，再由本地抓取器保存可定位页面文本。官方 `web_extractor` 在真实探针中对思考模式有强制要求且响应耗时不稳定，因此不再作为唯一抓取入口。

对于小型、公开、已绑定的 CSV/JSON/文本数据，Data Agent 可以把内容及任务内相对路径传给 Qwen `code_interpreter`，保存代码和执行输出。超过大小限制或需要完整实验控制的数据分析继续交给确定性本地工具或 Automatic Experiment，不把托管解释器当作本地文件系统。

### 结构化上下文、周期表与审查恢复

full-research Data context 现在同时绑定任务 ID、规范化研究问题、输入清单、已接受 Planning 版本，并记录计划实际选择的必需数据集。只有该上下文确认缺少必需数据集或给出 `must_stop=true` 时，`REQUIRED_DATA_INPUT_UNAVAILABLE` 才能形成永久阻断；Reviewer 在输入可用时误用该标签，会转为有界 Data 返修，不能单独扩大用户问题的数据范围。

逐周期表升级为 `solar-precursor-cycle-table-v2`，保留构造 14→15 周期对所需的 cycle 14 边界行；周期配对回执覆盖 14→15 至 23→24，并记录数据集 ID、字段类型、单位、符号约定、行数、周期覆盖、时间顺序、不确定性字段、输出引用和明确缺口。若预测量测量日期无法从来源核验，回执标为 `partial / analysis_table_incomplete` 并记录 `PREDICTOR_MEASUREMENT_DATES_NOT_VERIFIED`；该部分产物可以进入 Data 审查清单以暴露缺口，但不能通过 Experiment preflight。

Data 主张由任务本地回执、canonical manifest 和实际输出确定性生成，不再以生产者自由文本代替表结构与来源边界。旧 v1 九对表不能进入实验；合法但不完整的 v2 表会保留结构化 gaps，供 Evidence 判断应返修还是在限制条件下继续。

### 8 月 18 日最终权威边界

full-research Data context 进一步逐项核对当前 `input_manifest.json` 的数据集 ID、路径、字节数和内容摘要，并绑定当前已接受的 Planning artifact 与 verdict。分析协议、必需数据产品、数据集列表、缺失列表和停止条件由任务问题、Planning 实际选择与共享协议映射重新计算；任务输入、Harness trace、请求文件和 provider 原始响应不能冒充 Data 输出。

Planning 明确选择的数据集与研究协议不一致时，系统生成 `PLAN_DATASET_PROTOCOL_CONFLICT`，把问题交回 Planning 修订。该冲突不累计为 Data 专家失败，也不直接形成永久阻断；Planning 没有明确选择时才使用协议默认数据集。

Harness 回执只有在当前任务、当前调用、来源清单、文件字节数和内容摘要全部一致时，才能把网页正文或派生计算投影为候选证据。无有效代码执行、无输出工件、空网页正文、旧调用或零字节文件均只保留为过程记录。这样可以让 Data Agent 使用千问的检索和代码解释器能力，同时保持外部线索、网页原文、派生计算和已审查数据产品之间的证据差别。

### r24 的真实终态与修复定位

`r24`（`next_stage_polar_length.full.r24`）已以 `research_blocked` 收尾，页面没有最终回答；
权威工作区的 `run_state.json` 为 `blocked/data`，Planning 已接受而 Data 未形成可接受的
canonical artifact，后续阶段没有启动。运行元数据位于
`research/review/evals/runs/next_stage_polar_length.full.r24/`，不能用辅助的
`r24_runtime_data/` 目录替代它。

只读复现显示，本轮 Data context 已有两个已登记的项目输入、前兆表 CSV 和 v2 回执；首要缺口
不是数据缺失或前兆表版本不一致，而是审查器重建 `eligible_inputs` 时拒绝了合法的
`/project/data/...` 虚拟路径，导致 full-research context 无法通过权威性预检。现已在
`jw/research_review.py` 增加受限的 `projects/<project>/shared/data` 映射，并要求项目数据注册清单、
文件字节数和内容摘要同时匹配；非 `/project/data/` 的绝对路径、越界路径和符号链接仍会被拒绝。
新增回归测试覆盖已登记项目输入可被审查器重建，以及未登记共享文件被拒绝。

针对 Token Plan 的协议缺口，`jw/research_harness.py` 仍按端点选择协议。其
`compatible-mode/v1` 只走 Chat Completions：Data Agent 的小型计算请求由千问返回
`run_python` function call，宿主把当前清单中的输入复制到独立的 `python_workspace`，在
bubblewrap 隔离环境中执行，并将代码、标准输出、输出文件和校验回执写入当前 Harness 调用目录。
普通回答或消息中的代码字段仍为 `partial`，不计为计算证据；不支持 Responses 网页搜索的入口
则返回结构化缺口并使用任务级只读文献工具。一次真实最小探针已得到 `completed` 回执和一份
派生输出文件，但这只证明计算路径和证据落盘可运行，不代表太阳物理结论成立。

## 涉及文件

- `jw/middleware/research_review_orchestration.py`：周期配对表构造、数据产物暂存和后续阶段输入传递。
- `jw/research_protocols.py`、`jw/middleware/research_router.py`：研究路由和实验要求。
- `jw/tools/automatic_experiment.py`：紧凑的单阶段实验设计入口。
- `src/automatic_experiment/contracts.py`、`executor.py`、`reporting.py`、`service.py`、`verification.py`：实验设计、执行、记录、报告和结果校验。
- `src/research_review/adapters.py`：实验结果到后续假设更新摘要的转换。
- `jw/research_harness.py`：任务级检索、网页抽取、代码解释器调用和证据回执。
- `jw/tools/solar_feature.py`、`jw/subagents/solar/solar_data.yaml`：Data Agent 的资料获取、代码分析和工具使用要求。
- `jw/tools/knowledge_base.py`：面向 Data Agent 的只读文献检索工具组。
- `research/experiment/specs/automatic_experiment_design_v1.schema.json`、`automatic_experiment_request_v1.schema.json`：实验请求与设计的结构约束及有界选择取值。
- `tests/test_research_review_v2.py`、`tests/test_research_protocols.py`、`tests/test_research_router_middleware.py` 及自动实验相关测试：周期配对关系、时间边界、输入传递、路由协议和实验运行续接的回归测试。

这些调整没有把新的观测值或科学结论预写入 Data Agent；模型只能使用当前任务已绑定的数据、实际检索来源和真实工具输出。

## 已完成的局部真实验证与边界

### r15 的输入读取和口径复核

r15 真实读取的是 8 月 2 日获取、8 月 12 日登记到项目目录的两份既有数据，运行时没有下载新文件。Data 阶段生成了太阳活动周期 15–24 的 10 行逐周期特征表，并在后续确定性转换中生成周期 15→16 至 23→24 的 9 个相邻周期对。这里的 10 行是逐周期观测单位，9 对才是相邻周期预测分析单位；历史审查材料中曾把二者写成“10 对”，该计数语义错误已在派生表和实验输入合同中更正。

r15 的周期配对表已经进入实验设计输入，合同中的 `independent_sample_count=9`，并记录了 rolling-origin 训练边界、MWO proxy/WSO magnetograph 测量体制和目标峰值在预测时不可用等限制。但该运行最终阻断在 Experiment Design，尚未形成真实实验结果、样本外误差或完整闭环结论。

### r17 的 workspace 根分裂

r17 的正确运行根中存在两份已登记输入，错误后端根为空；Data 在错误根返回 `input_missing` 是路径绑定不一致的直接结果，不能解释为 Data Agent 缺少数据。本轮已把正式 launcher 的 `JW_WORKSPACE_DIR` 统一到当前运行绑定的根目录，r18 用该入口重新验收。

### 历史 r18

r18 的 Data 阶段已形成 `inputs_available`，`eligible_inputs=2`，并由 Data Evidence 以
`accept_with_limits` 接受；Hypothesis 也以 `accept_with_limits` 收尾。实验设计文件已经生成
并通过自动实验模块的本地结构校验，但实验设计生产子 Agent 的 `qwen3.8-max` 调用连续两次
返回 `403 AccessDenied.Unpurchased`，运行在 `experiment_design` 阶段阻断。因此尚无实验设计
Evidence、真实实验执行、样本外误差、Hypothesis Update、Integration 或 Final Release。
该次运行发生在 Data Agent 增强前；这一阻断属于当时的模型权限与运行时验收边界，不能归因于真实数据缺失。

### r19–r21 的后续边界

r19 在 Planning Evidence 原子提交处阻断，Data 未启动。r20 已加载 Data 增强代码，Planning 以 `accept_with_limits` 通过，Data 读取两份登记输入并生成十行前兆表，但当时的自由文本适配没有完整投影表结构、单位、符号、逐行时间关系、不确定性和有效样本边界；Reviewer 还把题外 cycle 25/26 输入误写为必需项，旧状态机因此在 Data 阶段停止。Data 入口的两个强制工具选择请求曾返回 HTTP 400，兼容层为两个确定性过渡动作生成本地工具调用后继续；本轮没有调用外部 Harness 或托管代码分析，也没有进入实验。

r21 在 Planning artifact 注册前发生浏览器监控、后端和 WebUI 进程丢失，最后状态仍为 `active/planning`，三类审查产物均为 0。该结果属于运行时失败，不能用于评价 Data 工件或科学假设。r19–r21 的详细运行证据见 `research/review/REAL_CLOSED_LOOP_RUN_LOG.md`。

### r22 的真实 Data 运行

r22 采用独立 tmux 会话保持后端、WebUI 和 headed browser harness 存活。Planning 与 Data 各形成一套完整 Evidence 三件套，两轮均为 `accept_with_limits`。Data context 为 `inputs_available`，必需数据集为 `silso-monthly-total-v2` 与 `mwo-wso-polar-field-v2`，缺失列表为空且 `must_stop=false`。前兆表回执为 v2 verified，保留 cycle 14 边界行和 cycles 15–24，共 11 行；回执声明 14→15 至 23→24 的 10 对均可构造，同时保留三项明确的数据方法与不确定性缺口。

Data Agent 同轮调用了 8 次受控代码分析 Harness：3 次读取超时，5 次 provider 返回 completed 但没有分析条目或分析工件。Data artifact 没有把这些空回执投影为候选证据，最终依靠结构化本地产物进入审查。运行随后在 Hypothesis 生产调用发生 `APIConnectionError`，没有 Hypothesis artifact、周期配对分析回执、实验设计、实验结果或后续科学结论。该结果证明 Data 增强路径的一部分在真实 WebUI 中运行，但也暴露出空分析输出应降为 partial 的状态判定缺口。

工程测试、真实模型调用、实验执行和科学有效性是不同证据层级。最终复审修复后的目标矩阵为 `310 passed`，全量 Python 测试为 `3629 passed, 13 skipped, 6 warnings, 8 subtests passed`；Ruff F、Python 编译、Shell/Node 语法、WebUI `25` 项 Node 测试、production build 与 `git diff --check` 均已通过。ESLint 为 0 error，保留 8 条既有 Fast Refresh warning；production build 保留既有 Turbopack tracing warning。真实生产验收仍须使用重启后的全新会话，不能把 r22 的 Data 接受或这些工程检查写成实验或科学结论。

### r23 的 Data 阶段边界

r23 在 Planning 生成 artifact 后即因千问思考模式的 `tool_choice=required/object` 兼容问题阻断，
Data 尚未启动。因此 r23 不提供新的 Data 工件或科学证据，也不能用于评价新增检索和代码分析
工具的端到端效果。该轮失败记录与修复后的 Evidence 自动选择路径已分别保存在运行目录和代码
回归中，下一次全新会话需重新验证 Data context、Harness 回执和后续实验连续性。

## 8 月 19 日 r31–r33 接管核对

这一节把最近三次全新 headed 会话与当前磁盘产物分开记录，避免把运行中的草稿或历史失败误写成
科研结论。

### r31：Planning 阶段阻断

r31 在 Planning Evidence 调用连续两次没有落下与当前工件对应的 `ReviewVerdictV2` 后停止。该轮
没有形成可接受的 Data artifact，不能评价本轮 Data Agent 或 Harness。原始目录和日志仍作为运行时
失败证据保留。

### r32：Data 已接受，Hypothesis checkpoint 未形成

r32 的 Planning 和 Data 各形成一份 artifact、assessment、scientific-quality assessment 与
verdict，决策均为 `accept_with_limits`。Data 产物实际包含 11 行逐周期表，可构造
14→15 至 23→24 共 10 个相邻周期对；它继续声明 MWO 代理、WSO 测量制度、周 15 前回退值、居中
平滑的回顾性标签和 `n_eff≤10` 的限制。该轮 Hypothesis 草稿虽已生成并多次尝试 checkpoint，但
没有形成 checkpoint 绑定，也没有实验设计、实验结果或最终发布工件。因此 r32 不能被描述为完整
闭环。

### 本轮已落地的最小质量修复

针对 r32 暴露的 Hypothesis 合同问题，`src/scientific_hypothesis/harness.py` 将模型可见的
`scientific_quality` 结构补齐，并把四个 `evidence_confidence_caps` 字段限制为协议枚举
`exploratory`、`evidence_constrained`、`release_candidate`。`solar_hypothesis.yaml` 同步写明：

- `checkpoint_draft` 是结构化交接，不等于 freeze 或 publish；
- `needs_revision` 只能修复指定字段，最多再检查一次，仍失败就保留草稿并停止；
- 证据等级只能由绑定证据和独立审查提升，确定性代码不替模型选择候选或提高置信度。

这组改动只修正结构化交接和失败边界，不替换模型提出的科学候选，也不增加观测数据。

### r33 结束前快照（以运行目录为准）

截至 2026-08-19 07:07（北京时间），r33 仍在同一 headed 会话中运行。Planning 与 Data 已各以
`accept_with_limits` 落盘；Data canonical artifact 为 `v0001`，其 CSV 和两份数据回执位于当前
运行目录。Hypothesis 已生成一个低置信、`exploratory_hypothesis` 草稿并绑定任务内数据限制，正
在读取文献证据包；checkpoint、实验设计、实验结果、整合和最终发布尚未形成。该快照只是工程与
模型运行状态，不能当作交互效应的实证结果；运行结束后的终态、时间和后续工件以
`research/review/evals/runs/` 及 `projects/default/runs/` 中的实际文件为准。

## 8 月 19–20 日同一任务完整闭环验收

### Data Agent 与实验输入连续性

随后另起的完整复验使用任务 `01a017a8-cf80-7371-a437-2079b63d13ff` 及其持久化工作区。Data 阶段的
v2 周期表保留 cycle 14 边界行以及 cycles 15–24，共 11 行；由此构造 14→15 至 23→24 共 10 个
相邻周期对。Data artifact 及其独立审查均为 `accept_with_limits`，确认的是输入、来源、字段语义和
限制足以进入实验，不代表交互效应已经成立。

编排层随后把同一周期表、周期配对表和数据回执传入自动实验。`experiment_scope.json` 固定了同一
问题、同一输入快照和同一实验运行，避免续跑时重新生成或更换分析样本。真实实验
`question_0555d8c0e646-20260819T095630Z-beb9e677` 保留两次执行记录：attempt-001 完成了真实计算，
但结果区间的依据字段未通过追溯；attempt-002 补齐区间依据后重新执行并通过结果核验。失败的首次
记录没有被覆盖。

本次完整闭环没有再增加观测数据，也没有在 Final Release 阶段改写 Data Agent。Data Agent 的源码
增强仍是本文前述的 `solar_data.yaml`、`solar_feature.py`、`knowledge_base.py` 和
`research_harness.py`；本轮新增改动集中在实验范围续接、最终报告生成和 Evidence 审查职责划分。

### 数据支持的科学结论边界

在这 10 个周期对上，交互系数点估计为 `β3=12.0217`，周期对重抽样区间为
`[-71.2719, 106.2204]`，覆盖零；加性零模型置换尾部比例为 `0.5193`。交互模型滚动原点
`MAE/RMSE=81.27/117.63`，均高于加性模型的 `34.06/43.44`。剔除 23→24 对或采用极小日期最长
情景会使方向翻转，五项预注册支持条件均未通过。因此数据只支持“不确定、低功效”的结论；不能
据此判定交互存在、方向明确、预测改善或物理机制成立，也不能外推至周期 25。

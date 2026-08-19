# Qwen Harness 与 Data Agent 增强说明

## 目的

本次调整面向太阳活动科学研究闭环，目标是让数据阶段能够获得更完整的外部资料、执行可复查的计算，并把这些结果以可定位的来源交给独立 Evidence 阶段审查。调整不把检索成功或模型生成的数字直接当作科学结论。

## 已完成的修改

### 1. 增加 Qwen Harness 调用层

新增 `jw/research_harness.py`，为支持 Responses 的端点保留原有接口，同时为 Token Plan 的
OpenAI-compatible Chat Completions 端点提供兼容路径：

- `web_search`：检索候选来源，并由任务内网页抓取器保存原文；
- `code_interpreter`：针对任务工作区内已经绑定的数据文件执行计算；在 Token Plan Chat
  Completions 中由唯一的 `run_python` function tool 承担同一职责。

调用层与现有 Supervisor、Hypothesis、Evidence 的 Chat Completions/function-tool 主流程分开，因此不会改变既有多轮工具调用和模型路由。请求摘要、响应、来源文件、抽取文本、计算记录和错误均写入当前研究工作区的 `research_review/harness/<task_id>/`。

官方兼容文档列出的业务空间入口是 `chat/completions`；对该入口发送 `/responses` 会得到
404。因此代码按端点选择协议，不再把 `/responses` 失败当作可重复的 Data 分析动作。

官方 `web_extractor` 要求开启思考模式，真实组合探针在限定时间内未稳定返回，因此当前不把它作为唯一原文入口。网页抽取由受控本地抓取器完成；失败页面会保留为缺口。每次调用生成 `harness-evidence-v1` 回执，记录研究问题、分析焦点、工具类型、来源 URL、定位信息、摘录、文件状态、工具轨迹、截断状态和限制条件。授权信息不会写入请求文件、响应文件或回执。

### 2. Data Agent 增加两项研究增强工具

在 `jw/tools/solar_feature.py` 和 `jw/subagents/solar/solar_data.yaml` 中增加：

- `solar_research_evidence`：接受原始研究问题、单一焦点和有界查询，生成待审查的网页来源包；搜索摘要标为外部线索，抽取文本保留来源与定位。
- `solar_research_analysis`：只允许使用当前任务已登记的输入，或已经生成的 `work/solar_data/` 数据产物。小型 UTF-8、CSV 和 JSON 文件以内容和任务内相对路径传入托管代码解释器；超过 300,000 字节或非文本输入转交确定性本地工具或 Automatic Experiment。

Data Agent 还增加了 `knowledge-base-literature` 工具组。学术检索优先使用 ADS、OpenAlex、arXiv、Crossref 等既有接口，依次完成研究问题绑定、来源搜索、正文或摘要缓存、摘录核对和冻结证据包。普通网页检索只补充明确的覆盖缺口。

Data Agent 仍必须先打开确定性数据上下文，继续遵守 `eligible_inputs`、数据来源回执、时间边界、代理变量说明和周期级独立样本要求。新增工具不能访问猜测路径，也不能把代理量写成内部磁场的直接测量。

### 3. 将 Harness 结果接入研究工件

`src/research_review/adapters.py` 现在识别 Data Agent 返回的 `harness-evidence-v1` 内容，把其绑定信息和已声明来源投影到 `ResearchArtifactV2` 的 `evidence_refs` 与 `payload.harness_evidence`。Data 阶段的 canonical source 扫描也纳入 `research_review/harness/`，因此 Evidence 可以按现有读取流程逐个检查回执、原文和计算记录。

Harness 结果不会自动写入 Knowledge Base 的 canonical 条目。只有经过任务绑定、来源定位和独立审查后，才可以作为后续知识候选或主张的支持、反对或限制信息。

### 4. 改善 Evidence 原子提交的安全修正

在 `jw/tools/research_review.py` 中增加了只降低主张强度的预处理：

- `gap` 证据行若带有来源路径，会清除该路径，避免把缺口伪装成来源；
- 有证据缺口时，`release_candidate` 自动降为 `evidence_constrained`；
- 接受型结果必须在 `accepted_claims` 中显式列出当前上下文返回的精确 claim ID；缺失或空列表会使原子提交失败，三类审查文件均不落盘；
- 其余来源、独立性、定位、结论上限和发布条件仍由原有严格检查处理。

同时在 `solar_evidence.yaml` 中补充了原子提交的修正规则，使 Reviewer 能根据工具错误只修改具体字段。

### 5. 8 月 17 日补充加固

本轮又补了四层与千问 Pro 套餐 Harness 直接相关的边界：

- `jw/tools/solar_feature.py` 会先校验 `/project/...` 输入是否已在当前任务的清单里登记，再把合格文件暂存到任务内 `inputs/project/`，并生成对应的 `harness-input-staging-*.json` 回执；未登记、路径不合格或重复来源都不会进入托管分析。
- `jw/research_harness.py` 的计算路径明确要求调用 `code_interpreter`，普通说明文字、思路叙述或返回中的 `code` 字段都不能冒充计算已经执行。
- `jw/subagents/solar/solar_data.yaml` 明确要求：对同一 `solar_research_analysis`，如果连续两次返回 `error` 或 `partial`，就停止重复重试，改为返回已验证的确定性产物和限制说明。
- `jw/middleware/qwen_compat.py` 现在把 `[CONTRACT TOOL BLOCKED]` 和 JSON 形式的 `status=error` 视为可计入重试上限的工具失败；同一调用第三次再出现时就直接停止，避免 Data specialist 在同一错误上空转。

这四项加固只改变数据和审查边界，不提高证据等级，也不把检索摘要、说明文本或失败回执自动升级成科学结论。

### 6. 8 月 18 日 Token Plan Chat 兼容与宿主计算隔离

针对真实 r24 暴露的 `/responses 404`，`QwenHarnessClient` 增加了 Chat Completions
兼容分支。分析请求只向千问暴露 `run_python` function tool，并显式关闭该请求的思考模式，
避免业务空间拒绝内置 `code_interpreter` 或 `required/object` tool choice。千问先返回代码，
宿主再在每次调用独立的 `python_workspace` 中执行；输入文件由任务清单复制到该目录，代码经过
AST 导入和路径检查，并通过 bubblewrap 隔离网络、进程空间、环境变量和工作目录。执行时间、
代码大小、日志大小和输出文件数量均有上限。输出文件复制到 `calculations/files/`，执行代码、
标准输出、返回码、文件摘要和回执一起保存。

普通 prose、消息中的 `code` 字段或没有实际执行记录的回答仍只产生 `partial`，不会被当作计算
证据。Token Plan Chat 入口不提供 Responses `web_search` 工具时，系统生成结构化缺口并转回
任务级只读文献路径；不会反复请求同一个不支持的工具。

## 测试与检查

新增并通过以下定向测试：

- Harness 请求解析、来源落盘、文件清单、失败记录和工作区越界检查；
- Data Agent 两项新工具、输入边界和工具名称注册；
- Harness 证据适配与 Data 工件绑定；
- Evidence 缺口降级、接受主张显式 ID 和空列表回滚；
- Evidence Reviewer 提示中的提交字段要求。

8 月 17 日加固完成后，Task 4 的 Data 与审查覆盖集为 `224 passed`。随后在安装项目已锁定的 `solar` 可选依赖后，全量 Python 测试为 `3564 passed, 13 skipped, 6 warnings, 8 subtests passed`。8 月 18 日完成最终复审修复后，最新目标矩阵为 `310 passed`，全量 Python 测试为 `3629 passed, 13 skipped, 6 warnings, 8 subtests passed`。Ruff F、Python 编译、Shell/Node 入口语法与 `git diff --check` 均通过；WebUI Node 测试为 `25/25`，ESLint 为 0 error 和 8 个既有 Fast Refresh warning，production build 成功并保留既有 Turbopack tracing warning。这些结果属于确定性工程验证，不替代真实模型运行或科学有效性判断。

真实最小探针结果如下：

- `qwen3.8-max` 普通 Responses 请求完成；
- `qwen3.8-max web_search` 完成，保存 9 条外部线索，其中 4 个页面形成可读原文，共 13 个任务工件；
- `qwen3.8-max code_interpreter` 对任务绑定的小型 CSV 完成代码执行，保存一份执行记录；
- 两次成功回执均未发现 API Key 值。

8 月 17 日另以 `QwenHarnessClient.collect_evidence` 运行了一次独立网页证据探针。该探针以 `partial` 收尾，共保存 33 个来源条目和 33 个工件：其中 29 个搜索结果仍是外部线索，4 个页面形成可读原文；一个页面因 HTTP 403 未能抽取。任务目录共有 36 个文件，对运行时 Key 精确值的递归扫描未发现匹配。该结果证明受控检索、部分失败记录和落盘路径可运行，不证明任何太阳物理命题成立。回执位于 `research/review/evals/runs/harness_probe.20260817.r1/research_review/harness/harness-probe-20260817-r1/run-8b6a5c0da5a116bf/receipt.json`。

### 8 月 18 日最终加固

最终复审进一步收紧了运行时和证据边界：网页请求把已核验的公网单播地址绑定到实际连接，禁用环境代理，并对最多 5 次重定向逐跳重新解析和核验；响应以 raw stream 读取，显式请求 identity 编码，保存上限为 250 KB，截断页面降为 `partial`，压缩响应在解压前拒绝。

Evidence 适配不再信任 producer envelope 自报的状态和条目。canonical receipt 必须属于当前任务与当前调用，进入当前来源清单，并与清单中的字节数和内容摘要一致；旧调用、目录外路径、重复清单、零字节工件和未由回执声明的页面均不能产生候选证据。代码分析还要求有效输入与非空派生计算工件；provider 返回完成但只有说明文本、失败或排队中的 code call、空 outputs，以及普通 message 中的 `code` 字段，均不会被当作已执行计算。

full-research Data context 同时绑定当前输入清单和已接受的 Planning artifact/verdict。若 Planning 明确选择的数据集与研究协议冲突，系统生成 `PLAN_DATASET_PROTOCOL_CONFLICT` 并交回 Planning 修订，不把冲突累计为 Data 专家失败或永久缺数状态。完整修复记录见 `.superpowers/sdd/final-review-fix-round2-report.md`。

### 8 月 19 日 r31–r33 质量边界补充

最近的真实复验进一步验证了两个边界。第一，Data 阶段的 `accept_with_limits` 只确认当前任务的
数据、来源和结构化限制已经可以交给下游；它不表示极区场预测关系已经被估计，更不表示机制得到
验证。第二，Hypothesis 的结构化 checkpoint 必须在证据绑定、尾部审查和字段修订完成后才建立；
运行中生成的 draft、literature bundle 或工具调用记录都不能替代 checkpoint。

r32 因 checkpoint 交接没有形成而停在 Hypothesis 之前的完整闭环之外。为此本轮把四个
`evidence_confidence_caps` 的可用值固定为三个协议枚举，并明确有限返修和停止规则。该修复使
Qwen 的长思考输出仍可保留探索性候选，同时让不符合合同的输出显式失败；它不会通过默认值、字
符串替换或本地评分把证据等级抬高。

r33 当时使用 Token Plan 的 OpenAI-compatible `chat/completions`，而不是不存在的 `/responses` 路径。
Data Agent 的文献、网页和小型计算结果均保留调用、来源、输入摘要、输出和限制回执；只有任务、
清单、字节数和内容摘要一致的回执才进入候选证据。当时 r33 尚在 Hypothesis 文献绑定阶段，尚无
实验指标或最终科学结论；后续独立任务的终态已在运行台账中另行更新。

## 配置与调用边界

Harness 使用当前 `custom-openai` 配置中的 Base URL 和 API Key。配置仍由既有 JW 配置加载机制提供，Key 不写入仓库，也不出现在运行日志和研究回执中。主流程不需要设置全局 `JW_USE_RESPONSES_API=true`。

当前生产验收以 `qwen3.8-max/custom-openai` 承担 Supervisor、Planning、Data、Evidence、Hypothesis 与 Experiment 的生成和审查调用；普通推理与多轮工具继续使用兼容 Chat Completions 的主路径。若端点支持 Responses，网页检索和托管代码解释器沿用 Responses；对 Token Plan 兼容入口，代码分析走上述受限 `run_python` 分支，学术检索走任务级文献工具。这样可以利用千问 Pro 套餐的推理能力，同时让每类工具输出分别保留来源与状态。`t2i_search`、`i2i_search`、图片生成和语音能力没有自动接入当前逐周期表格研究：这些能力只有在研究问题确实包含可核验的图像或语音观测时才有科学增益，不能用一般图片替代观测数据或文献证据。

### 证据等级

搜索摘要只能作为外部线索；网页抽取文本需要保留 URL 和定位；代码解释器结果必须同时具备输入来源、代码或执行记录、输出文件和指标定义。视觉或图片检索结果只能作为补充资料，不能在没有可复查数据时直接形成定量观测。

## 外部方法调研与采用

本轮对近期论文和公开科研 Agent 项目进行了只读核对。采用的是窄接口和证据方法，不整体引入新的大型编排框架：

- [OpenScholar](https://github.com/AkariAsai/OpenScholar) 与 [PaperQA2](https://arxiv.org/abs/2409.13740) 的多源检索、重排、正文摘录和引文核验思路，用于区分元数据、摘要、正文和网页来源；
- [Agent Laboratory](https://github.com/SamuelSchmidgall/AgentLaboratory) 的“摘要筛选—读取原文—纳入综述”两级路径，用于限制摘要级证据的结论强度；
- [AI Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) 与 [BioDiscoveryAgent](https://proceedings.iclr.cc/paper_files/paper/2025/file/4252dc94531833029000f85dc5fac792-Paper-Conference.pdf) 的实验分支记录、失败保留和结果反馈，用于后续自动实验改进；
- [ScienceAgentBench](https://arxiv.org/abs/2410.05080) 的真实程序执行与结果判定分层，用于保持“程序运行、自动测试、科学解释”三个证据层级；
- [ToolUniverse](https://github.com/mims-harvard/ToolUniverse) 的科学工具分组与健康探针思路，用于让 Data Agent 只看到当前阶段需要的文献和数据工具。

没有采用大规模本地论文向量库、通用 SuperAgent 全套运行时、同模型多角色投票或不隔离的 `exec()` 方案。Stars、论文生成完成和模型审稿通过均不作为科学有效性的证据。

## 真实运行状态

使用新千问 Base URL 的 r19 全新 headed WebUI 会话已完成 Planning 生产，千问请求持续返回 HTTP 200，未出现鉴权或工作区错误。该会话最终在 Planning 的 Evidence 原子提交处阻断：Reviewer 多次提交包含虚构缺口来源、发布结论上限不一致和遗漏 `accepted_claims`，因此没有落盘 Evidence 三件套。该失败记录保留在：

- `research/review/evals/runs/next_stage_polar_length.full.r19/metadata.json`
- `projects/default/runs/run_01a00aff-2ac8-7dc3_efd2a077/research_review/run_state.json`

r19 证明新千问接口可用，但不证明完整科学闭环已通过。

r20 在增强代码加载后使用全新 headed WebUI 会话运行。Planning 以 `accept_with_limits` 通过，Data 以 `block` 收尾，持久化状态为 `blocked/data`；Data 读取了两份登记输入并生成十行前兆表，但当时的适配结果没有完整投影表结构、单位、符号、时间顺序、不确定性和有效样本边界。Reviewer 又把超出原问题范围的 cycle 25/26 输入写成必需项，旧状态机因此把可返修问题升级为永久阻断。该轮没有调用外部 Harness 或托管代码分析，也没有进入实验或形成科学结论。

r21 使用全新 headed WebUI 会话启动后，浏览器监控、后端和 WebUI 临时执行进程在 Planning 期间同时消失。最后持久化状态为 `active/planning`，Planning artifact 与三类审查文件均为 0；后端最后事件是 Qwen HTTP 200，未记录 shutdown traceback。该结果属于运行时进程丢失，不是科学审查结论。

r22 将后端、WebUI 和 headed browser harness 放入三个独立 tmux 会话，并以新线程重新提交同一原始问题。运行耗时 3207.258 秒，Planning 与 Data 各形成一份 artifact 和一套完整审查三件套，两轮 verdict 均为 `accept_with_limits`。Data context 确认两项计划数据集均存在；v2 前兆表为 11 行，包含 cycle 14 边界行及 cycles 15–24，可覆盖 14→15 至 23→24 共 10 个请求周期对，同时保留预测窗口尚未按计划实现、目标振幅不确定性未计算和极小期日期不确定性未计算三项缺口。

r22 实际调用了受控代码分析 Harness，共留下 8 份回执：3 份因 `ReadTimeout` 标为 `error`，5 份 provider 返回 `completed`，但后者均为 0 个分析条目和 0 个分析工件。Data 仍依靠确定性本地产物完成审查，没有把这些空回执写成统计结果。随后 Hypothesis 生产调用以 `APIConnectionError` 结束，headed WebUI harness 终态为 `runtime_error`；持久化状态停在 `active/data`，Hypothesis artifact 为 0，实验设计、实验结果、Hypothesis Update、Integration 和 Final Release 均未启动。

r22 因而只证明增强后的 Data context、v2 结构化产物、Harness 失败回执和两轮 Evidence 持久化路径被真实运行到，不形成交互作用的科学结论。其观察链接为 <http://127.0.0.1:4717/?threadId=01a01073-6dc6-7670-bc42-cd81f1074a70>；运行目录为 `research/review/evals/runs/next_stage_polar_length.full.r22/`。本轮暴露的“provider completed 但无必需分析工件”状态缺口与后续安全审查 findings 需在修复、重启服务后由全新会话复验。

r23 在上述最终加固后以全新 headed production WebUI 会话运行，页面仍只提交原始问题。Planning
生成了 `v0001` artifact，但其后的必需 specialist 两次收到千问思考模式对
`tool_choice=required/object` 的 400 拒绝，运行以 `research_blocked`、`blocked/planning` 收尾；
没有 Data/Hypothesis/Experiment 产物或科学结论。该失败暴露了 Evidence 原子提交的 Qwen 兼容
缺口：即使请求附带 `enable_thinking=false`，该业务空间仍可能按思考模式拒绝 object tool choice。
修复后，Qwen Evidence 提交阶段只暴露唯一提交工具并保持自动选择，完整参数由专用指令约束；
r23 原始目录和日志保留，需由新的全新会话验证修复。

### r24 已收尾的真实现场

`r24`（`next_stage_polar_length.full.r24`）已经自然收尾。线程为
`01a01122-23fc-7e42-8b2f-e56ccae51b10`，观察链接为
<http://127.0.0.1:4717/?threadId=01a01122-23fc-7e42-8b2f-e56ccae51b10>；页面终态为
`research_blocked`，`has_answer=false`。权威运行目录为
`projects/default/runs/run_01a01122-23fc-7e42_74c48881/`，其持久化状态为
`blocked/data`：Planning 已接受，Data 被阻断，后续 Hypothesis、Experiment Design、Experiment
Result、Integration 和 Final Release 均未启动。运行元数据和页面证据保存在
`research/review/evals/runs/next_stage_polar_length.full.r24/`；同名的
`r24_runtime_data/` 只是迁移后的会话辅助目录，不是本轮阶段状态的权威来源。

本轮已经生成 Data context、前兆表 CSV 和对应回执，但 Data canonical 预检未通过，失败回执为
`research_review/failures/data/tool-failure-0001.json`，原因码为
`REQUIRED_SPECIALIST_FAILED_TWICE`。后端日志仍保留一次 `POST /responses` 返回 `404 Not Found`
以及后续分析调用；这些调用记录不构成实验结果或太阳物理结论。

对 r24 工作区的只读复现进一步定位到一个工程缺口：任务清单中的合法
`/project/data/...` 虚拟路径被审查器当作普通绝对路径拒绝，full-research Data context 因而无法
通过权威性检查。现已增加受限的项目共享目录映射、注册清单核对、字节数与内容摘要校验，并补充
回归测试；该修复不放宽任意绝对路径，也不把 r24 改写成成功运行。修复后的全新 headed production
WebUI 会话仍需重新验证完整闭环。

### 8 月 18 日兼容分支最小真实探针

在不打印或持久化凭据的前提下，使用当前 Token Plan 配置运行了一次小型真实分析：千问返回
`run_python`，宿主实际执行了 pandas 读取、行列计数和输出文件写入；回执为 `completed`，含
一条 `derived_calculation`、一条 `derived_output`，工具协议记录为 `chat_completions`，没有
错误或警告。该探针只验证协议、隔离执行和回执落盘，不支持任何太阳物理结论；完整生产闭环
仍须由全新 headed WebUI 会话复验。

## 8 月 19–20 日完整链路实测补充

随后另起的生产 WebUI 任务完成了 Planning、Data、Hypothesis、Experiment Design、Experiment
Result、Integration 和 Final Release 七个正式阶段；Hypothesis 阶段保留实验前版本和实验后
更新版本。Token Plan 的 OpenAI-compatible
`chat/completions` 请求在最终 r38、r39 续跑中均返回 HTTP 200；最终报告由
`qwen3.8-max/custom-openai` 生成，并由 Qwen Evidence 路线以 `accept_with_limits` 审查。该结果
证明当前端点、工具调用、持久化续跑和最终交付路径可以协同工作，但同模型家族审查不等于外部独立
科学复核。

Final Release 的职责也在本轮收敛：确定性代码只检查 Integration 已接受、报告非空、引用结构合法，
且引用指向已接受的 Integration 主张；限制是否充分表达、引文是否在语义上支持正文、是否新增无依据
数字或因果/原创性措辞，以及是否夹带原始 JSON 和内部运行内容，统一交给 Final Evidence 模型判断。
生成最终报告时，模型只看到原始科研问题与当前继续请求，不再重放此前失败草稿和校验错误。最终接受
的报告为 49 行中文科学报告，没有复制原始 JSON、内部 ID 或调试记录。

本轮最终阶段没有调用图片、语音或视频能力，也没有为了使用套餐权益而强行增加 Harness 工具。数据已
由任务内结构化表和真实实验记录充分绑定时，继续调用网页搜索或代码解释器不会提高该结论的证据等级；
这些工具仍只在新的数据、文献或计算缺口出现时按需启用。

8 月 20 日交付前对当前工作树重新执行了完整工程检查：Python 全量测试为
`3668 passed, 13 skipped, 6 warnings, 8 subtests passed`，WebUI Node 测试为 `25/25`，Python 编译、
两个工作区的差异格式检查和 production build 均通过。production build 保留既有的 Turbopack
tracing 警告。上述结果证明当前代码与构建可运行，不改变本次太阳物理结论的高不确定性边界。

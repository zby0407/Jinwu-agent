# Research Review 2.0

本目录保存整体科研闭环的评测政策与冻结集成挑战集。运行时实现位于
`src/research_review/`、`jw/research_review.py` 和
`jw/middleware/research_review_orchestration.py`。

## Agent runtime 与评测 harness 的边界

产品运行时由 `jw/agent_harness.py`、Research Router 和
`ResearchRunStateV2` 共同约束。能力清单声明用户可获得的能力及其真实
tool bundle、specialist owner；路由状态记录 harness 版本和当前能力；完整
研究任务的阶段、返修、审查和终止仍由确定性研究状态机决定。系统启动时
会核对能力清单与实际注册项，避免提示词声称存在一个运行时并未装配的能力。

本目录的 WebUI harness 只承担外部验收：像普通用户一样输入提示词、记录
真实 thread、观察结构化状态并保存产物。它不得替 Agent 指定内部阶段、直接
调用研究工具或在运行中提供科研指导。`main_task_frontend_v1.json` 是
`natural_user` 用例；显式包含内部 route、stage 或 specialist 的用例只能作为
集成诊断，不计入自主性结果。

当前唯一主科学验收案例是 `MAIN-SC26-B06`，研究第 26 太阳活动周预测的证据成熟度与
机制解释。`next_stage_closed_loop_frontend_v1.json` 中的周期长度交互问题是工程与科研
闭环压力基准，上升时间问题是仓库内可见迁移基准；二者都不能作为 external hidden 或
发布门证据。案例 ID 与历史运行记录保持不变。

长任务创建真实 WebUI thread 后，runner 会输出 `observer_ready` 事件和
`observer_url`。该链接指向同一个真实会话，供人工在浏览器中观察；自动化
浏览器是否 headless 不影响观察链接。

## 执行边界

- Supervisor 只执行状态机、预算、依赖失效和 issue owner 路由。
- Planner、Data、Hypothesis、Experiment 只能修改自己的任务级产物。
- `solar-evidence` 仅能读取当前 artifact 明确引用的来源，并提交
  `ReviewVerdictV2`；它不能修改生产者产物、写知识库或自证异构二审。
- 产物一旦固定便不再更改。科学 claim、证据关系、阶段或上游绑定发生实质变化时形成
  新版本，并使基于旧版本的审查结论失效；仅渲染文字、时间戳或工作状态计数变化时复用
  原 artifact。Canonical v1 文件若在 checkpoint 后发生实质变化，服务端强制 `block`。
- `ResearchRunStateV2` 同时保存 Supervisor 的强制 stage DAG 与 Planner 冻结
  route DAG；生产者返修会记录它基于哪个旧版本与哪条问题单重新提交，
  只表示重新提交，不代表验收已通过。
- 全图 action budget 和 Evidence review budget 都持久化在任务工作区，页面刷新、
  重新打开阶段或进程重启不会补回预算。
- 最终可见科研报告必须来自已接受 claims，包含所有 carried limits，并依次通过
  integration、final release 和独立二审。模型返回的自由文本不能绕过
  必需节点。

## Evidence 审查模式

- `closed`：只审生产者已经提交并实际引用的证据，不主动补充外部材料。
- `two_pass`：先完成同样的 closed 审查，再查任务本地文献缓存；只有关键缺口仍会
  改变路由时，才进行至多 2 次网页检索并读取至多 5 个返回页面。检索失败、空结果
  或页面截断只记为不确定性，不能单独构成否决理由。
- Evidence 在每轮 verdict 前必须写入恰好一个 `ReviewAssessmentV1`；其中每个当前
  claim 恰好出现一次，记录支持和反对证据、判断、关键不确定性、置信度及下一项
  区分性检验。assessment 用于复盘，不代替 `ReviewVerdictV2` 的路由决定。
- Evidence 在同一轮还必须写入恰好一个 `ScientificQualityAssessmentV1`。该 sidecar
  保存 claim-level Evidence Matrix、source independence、full-text/abstract scope、
  方法门禁与原创性边界，同样不能绕过 `ReviewVerdictV2`。
- 当前评测采用显式多模型分工：Supervisor、Planner、Hypothesis、Experiment 和
  integration synthesis 使用 `qwen3.8-max/custom-openai`；Data、Knowledge、路由与
  辅助判断使用 `qwen3.7-plus/custom-openai`；Data 只接受运行时 model override，
  不为这次评测修改其源码。
- `solar-evidence` 使用 Kimi for Coding 路线的 `kimi-k3/kimi-coding`（运行时模型名
  `kimi-for-coding`，已知上下文窗口 1M）。two-pass 继续使用项目本地缓存及受限网页
  检索，不启用 Kimi 官方 web search。
- `ReviewVerdictV2` 只允许 `accept`、`accept_with_limits`、`revise` 和 `block`。
  Evidence 审查后直接交付可保留的科学假设、现有证据、反证和限制；无法建立的原创性、
  重要性或强因果主张必须删去或降级，若其不可替代则阻断。
- 生产运行不自动跨模型家族回退。真实探针失败时只能由 harness 显式换配置并重启，
  同时记录实际 `reviewer_family`、`heterogeneous` 和 Evidence 回合完整性。

模式由后端启动前设置的 `JW_EVIDENCE_REVIEW_MODE=closed|two_pass` 固定，运行中不
切换。模型或模式变化后应重启后端，并用真实 Evidence 路径重新验证。

## 真实 WebUI 评测

每次真实运行的输入、模型、终态、科学结果、问题和处置状态统一追加到
[`REAL_CLOSED_LOOP_RUN_LOG.md`](REAL_CLOSED_LOOP_RUN_LOG.md)。该台账区分生产 WebUI
闭环、局部 Agent 验证、模型探针和科学结论；失败运行不得被后续成功结果覆盖。

正式入口是生产构建，不接受直接 SDK 调用替代。先构建 `webui/dist`，然后分别启动
后端和前端：

```bash
bash research/review/evals/run_eval_backend.sh kimi two_pass
bash research/review/evals/run_eval_webui.sh
```

`all_visible_e2e_v2.json` 是 18 例可见清单；每例包含 prompt、上传文件、审查模式、
reviewer、预期结论类别和重复次数。运行器会为每次执行创建新的浏览器配置、任务、
线程与 run，通过真实上传控件提交附件，并保存 metadata、状态、assessment、回答、
截图和经过筛选的浏览器事件。前端生成真实 `threadId` 后，运行器立即输出
`observer_url`；正式检测必须把该链接发给观察者，使其能在自己的浏览器中查看同一个
生产会话。自动化浏览器是否无头不改变这一要求。两个并发任务使用不同调试端口。

正式 36 次回归按以下三个阶段执行；切换 closed/two_pass 前必须用对应模式重启后端：

```bash
node research/review/evals/run_eval_campaign.mjs closed-core
node research/review/evals/run_eval_campaign.mjs two-pass-core
node research/review/evals/run_eval_campaign.mjs two-pass-rest
```

最后由 `summarize_webui_runs.py` 汇总工程门、模式时延和用量。重大问题召回率、干净
案例误阻断率和科学评分只接受独立标注，不从自由文本或流程成功状态自动推断。

`high_quality_review_visible_v1.json` 另行冻结 10 个内容质量案例，覆盖机制与原创性、
rolling backtest、阴性结果、low power、小样本、measurement drift、综合阶段新增主张和
`do_not_launch`。它要求当前 8.12.1 的 fresh baseline 及冻结后的每例 3 次回归；仍只属于
可见开发集，不能充当 hidden 或专家科学验收。

## 8.12.1 SCI 同行初审研究包改造

当前实现新增 `AnalysisClaimContractV1` 与 `ScientificQualityAssessmentV1`，修复
hypothesis blocked/clarification 状态被投影为 mechanism、evidence link 被状态文件路径
替代以及 `limits` 误作 support 的问题。Hypothesis 的 long-tail 距离与原创性判定已经
分离，nearest-prior-art 使用三轴缓存检索并按 literature family 去重；未经覆盖检索只能
标记 `novelty_not_assessed`。

Evidence 可在 artifact 已声明的 task-local PDF/Markdown/HTML 中按页或 section 检索，
并记录 locator、entailment、scope match 与 independence group。Abstract-only、Wiki、
review、simulation 和同家族重复来源均受到确定性 quality cap。Final release 对新增数值、
强因果、原创性和预测/置信区间措辞执行差分门禁。Kimi Evidence 在同一原子回合内生成
claim-level assessment、科学质量 sidecar 和路由 verdict。

实现说明、当前证据和未通过的外部科学验收见
`SCI_PEER_PRE_REVIEW_STATUS.md`。截至 2026-08-16 的最新本地验证，Qwen 兼容重试与
当前工作树重新执行的相关定向测试为 `581 passed`（其中 8 个 subtests），根目录全量
pytest 为 `3439 passed, 13 skipped, 6 warnings, 8 subtests passed`；自动实验子项目为
`66 passed`，知识库子项目为 `43 passed`。这些数字只覆盖工程回归，不替代真实科研验收。
WebUI Node 测试为 `25 passed`，production build 已成功并保留一个已知 Turbopack tracing
warning。

2026-08-16 的 fresh headed 外部案例复验保留了 r10–r17 的失败现场：r10、r11 为
`research_blocked`，r12 为 Qwen 流式连接导致的 `runtime_error`，r13 因 reviewer 配置
未显式传入而退化为同家族审查并在 planning 阶段阻断；r14 没有终态回执，不能称为完成；
r15 读取两份既有登记数据后在实验设计阶段阻断；r16 在 Planning reviewer 连续失败前未
形成完整审查三件套；r17 则因同一 thread 使用了两个 workspace 根，在错误根得到空输入而
于 Data 阶段阻断。r18 使用正式 launcher 和单一 workspace 根重新启动，最终在
`experiment_design` 阶段以 `research_blocked` 收尾：设计文件已经生成并通过本地设计校验，
但实验设计生产子 Agent 的 `qwen3.8-max` 调用连续两次返回 `403 AccessDenied.Unpurchased`，
按重试策略停止；没有形成实验设计 Evidence，也没有实验结果、整合或最终发布。这些运行
用于定位运行时问题，均不改变 `do_not_launch`。

r15 的源表包含周期 15–24 共 10 行；相邻周期分析只能形成 15→16 至 23→24 共 9 对。
历史审查材料中出现的“10 对”是计数语义错误，后续派生表、实验输入合同和本说明均按 9
对记录。Data Agent 配置文件未改，本轮变化集中在编排、运行时 workspace 绑定、派生表和
实验衔接。

截至 2026-08-14 的历史本地验证，全量 pytest 为
`3401 passed, 13 skipped, 6 warnings, 8 subtests passed`；WebUI Node 测试 25 项通过，
production build 成功；Qwen Max、Qwen Plus、Kimi K3 for Coding 与 DeepSeek V4 Pro 的
四类真实兼容探针均通过。这些证据不替代 10 例 fresh 质量回归、仓库外至少 12 个 hidden
任务、真实复现与三视角专家多数票，当前科学发布状态仍为 `do_not_launch`。

FR-H10 的新生产路径质量探针没有被包装成成功：一次 pre-reload 运行在 Hypothesis 质量
字段和 Evidence verdict 持久化处失败；修复提示、路由错误归因和高质量清单选择语义后，
一次全新运行在 Data producer 未形成完整 canonical artifact 后以 `blocked` 结束。后者的
两类 assessment 与 verdict 均为 0，因而没有 Evidence round 可验收，运行保持
`blocked`。这两次都不是改造前 baseline，也不计入冻结后的 3/3 内容质量
回归；Data Agent 源码未修改。

随后补做了一次范围受控的真实 Kimi Evidence round。为隔离连续发生在 Evidence 之前的
Qwen Supervisor 流中断，本次从项目的 `solar-evidence` prompt、Kimi for Coding 模型和
Evidence typed tools 直接进入已持久化的 FR-H10 Data artifact；输入仍是 visible fixture，
不是新的真实观测。Kimi 逐一读取两份已注册来源，并通过单次
`evidence_review_submit_round` 成功持久化 round 1：恰好一个 `ReviewAssessmentV1`、一个
`ScientificQualityAssessmentV1` 和一个 `ReviewVerdictV2`，三者 artifact binding 一致。
结论为 `contradicted` / `block`：独立样本单位是 solar cycle，实际只有 3 个完整周期，
月度重复记录不能把 n 扩大到 600；附件还明确是 synthetic placeholder，且目标文件没有
注册，因此原有显著性、区间和 cycle-26 release claim 均不可接受。运行回执位于
`evals/runs/sci.evidence_acceptance.FR-H10.direct.r1/metadata.json`。这补齐的是“真实模型 +
真实 Evidence 工具 + 真实持久化”证据层，不是 WebUI 全链路通过，也不进入 10 例 3/3、
hidden、真实复现或专家验收计数；状态继续为 `do_not_launch`。

仓库外周期长度交互问题随后通过 production WebUI 的 headed browser 完成 Data → Evidence
→ Hypothesis → Evidence 范围。第五次全新会话耗时 2248.582 秒，Data 与 Hypothesis 两轮
均各自持久化一份 assessment、科学质量 sidecar 和 verdict，页面状态为
`released/hypothesis`。系统给出低置信、可证伪的负交互假设和无交互零假设，没有计算或
捏造交互系数，也没有声称原创性；现有证据仅能限定测量口径、小依赖样本和代理制度，不能
支持交互存在。运行后修复了“supporting evidence 为空却标为 limited_support”的 sidecar
语义，当前规范化结果为 `undecided`；该点已通过确定性测试，尚未由新的付费 WebUI 会话
复验。完整失败链、成功结果和时延边界见 `REAL_CLOSED_LOOP_RUN_LOG.md`。

## 2026-08-12 可见集成回归记录

- Planner 的 shadow revision candidate 在提交前统一规范化；候选完整计划错误数未严格
  下降时，保留 active draft、返回局部错误与相关区段，并继续逐段 staging，不累计
  active revision failure。只有错误数严格下降或归零时才允许原子提交；工具不会自动
  编造缺失 section 内容。
- 生产构建中的全新 WebUI 会话连续三次得到 `planning_frozen`，时延分别为
  2714.122、3130.002、1576.265 秒。第二、三次都实际经过“候选错误未减少”的安全
  回路，active revision failure 保持为 0；因此本次 Planner gate 为 3/3。
- 四类真实模型最小探针覆盖普通回答、单工具、结构化输出和多轮工具。最终兼容配置下，
  `qwen3.8-max`、`qwen3.7-plus`、`kimi-k3` 和 `deepseek-v4-pro` 均通过；首次失败
  记录仍保留，不用重试结果覆盖兼容问题。
- FR-H09 和 FR-H10 的 Qwen 生产者 + Kimi Evidence 定向运行分别形成 3/3 和 1/1
  assessment/round。FR-H09 完成回答但科研状态仍为 active；FR-H10 完成回答但科研
  状态为 blocked。这只验证真实调用、verdict 和持久化结构，不提升科学结论等级。
- 可见 36 次运行已完整落盘（closed 6、two_pass 30）。汇总记录位于
  `evals/runs/webui_eval_summary.20260812.json`：21 次完成且有回答、2 次完成但无回答、
  13 次 runtime error；15 次属于非完成结果，8 次不满足每轮恰好一个 assessment。
  只有 SC26-B01、B02 的三次签名稳定，B03 至 B06 均不稳定。two_pass/closed 的串行
  核心 P95 时延比为 2.166，超过工程门 2；运行计数、无 400、无非法路由和平均 token
  比例通过，但 assessment coverage、核心稳定性和 P95 时延门失败。
- 历史 DeepSeek meta-review 结果只作为旧运行证据保留，不再参与当前状态机路由。
  当前流程以 Kimi Evidence 的原子 assessment、科学质量 sidecar 和 verdict 为准。

本轮结论为 `do_not_launch`。这里的 36 次都是仓库内可见集成回归，不能证明科学召回、
低误阻断或顶刊候选质量；汇总中的科学裁决仍为
`pending_independent_labels`。

## 评测边界

`evals/full_research_heldout_v1.json` 已提交进仓库，因此只能作为冻结集成挑战集，
不能作为 hidden 证据。它可用于发现路由、返修、阴性结果、证据不足和跨阶段矛盾
等工程缺陷，但不得用于宣布 adaptive 默认策略或“顶刊候选”达标。

正式发布评测必须在实现和政策冻结后，从仓库外注入至少 12 个未参与开发的任务，
为 A/B/C/D 使用相同模型、endpoint、输入快照和全新任务工作区，并把记录标为
`suite_visibility=external_hidden`。`score_review_records.py` 会拒绝用可见挑战集
放行 adaptive 默认策略；它也会在缺少硬门、零 critical、真实实验复现、逐条追溯
或领域/方法统计/复现三视角多数票时明确拒绝“顶刊候选”发布门。

静态检查、真实程序运行、自动化测试、真实模型调用、外部 hidden 评测和领域/
统计/复现专家验证分别报告，互不替代。当前基础设施通过测试不等于科学有效性或
期刊等级已经得到确认。

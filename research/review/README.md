# Research Review 2.0

本目录保存整体科研闭环的评测政策与冻结集成挑战集。运行时实现位于
`src/research_review/`、`jw/research_review.py` 和
`jw/middleware/research_review_orchestration.py`。

## 执行边界

- Supervisor 只执行状态机、预算、依赖失效和 issue owner 路由。
- Planner、Data、Hypothesis、Experiment 只能修改自己的任务级产物。
- `solar-evidence` 仅能读取当前 artifact 明确引用的来源，并提交
  `ReviewVerdictV2`；它不能修改生产者产物、写知识库或自证异构二审。
- 产物一旦固定便不再更改：任何改动都会形成新版本，并使基于旧版本的审查结论失效。
  Canonical v1 文件若在 checkpoint 后改变，服务端强制 `block`。
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
- 当前评测采用显式多模型分工：Supervisor、Planner、Hypothesis、Experiment 和
  integration synthesis 使用 `qwen3.8-max/custom-openai`；Data、Knowledge、路由与
  辅助判断使用 `qwen3.7-plus/custom-openai`；Data 只接受运行时 model override，
  不为这次评测修改其源码。
- `solar-evidence` 使用 Kimi for Coding 路线的 `kimi-k3/kimi-coding`（运行时模型名
  `kimi-for-coding`，已知上下文窗口 1M）。two-pass 继续使用项目本地缓存及受限网页
  检索，不启用 Kimi 官方 web search。
- hypothesis、integration、final_release 的独立 meta-review 优先读取独立配置
  `JW_INDEPENDENT_REVIEW_MODEL/PROVIDER`，当前评测指定
  `deepseek-v4-pro/deepseek`。该角色不再复用 `JW_AUXILIARY_MODEL`。
- 生产运行不自动跨模型家族回退。真实探针失败时只能由 harness 显式换配置并重启，
  同时记录实际 `reviewer_family`、`heterogeneous` 和
  `human_review_required`；缺少异构回执时继续停在人工审查门。

模式由后端启动前设置的 `JW_EVIDENCE_REVIEW_MODE=closed|two_pass` 固定，运行中不
切换。模型或模式变化后应重启后端，并用真实 Evidence 路径重新验证。

## 真实 WebUI 评测

正式入口是生产构建，不接受直接 SDK 调用替代。先构建 `webui/dist`，然后分别启动
后端和前端：

```bash
bash research/review/evals/run_eval_backend.sh kimi two_pass
bash research/review/evals/run_eval_webui.sh
```

`all_visible_e2e_v2.json` 是 18 例可见清单；每例包含 prompt、上传文件、审查模式、
reviewer、预期结论类别和重复次数。运行器会为每次执行创建新的浏览器配置、任务、
线程与 run，通过真实上传控件提交附件，并保存 metadata、状态、assessment、回答、
截图和经过筛选的浏览器事件。两个并发任务使用不同调试端口。

正式 36 次回归按以下三个阶段执行；切换 closed/two_pass 前必须用对应模式重启后端：

```bash
node research/review/evals/run_eval_campaign.mjs closed-core
node research/review/evals/run_eval_campaign.mjs two-pass-core
node research/review/evals/run_eval_campaign.mjs two-pass-rest
```

最后由 `summarize_webui_runs.py` 汇总工程门、模式时延和用量。重大问题召回率、干净
案例误阻断率和科学评分只接受独立标注，不从自由文本或流程成功状态自动推断。

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
- DeepSeek 对当前唯一存在可审产物的 hypothesis 阶段完成异构 meta-review，结论为
  `fail`。integration 与 final_release 没有可审产物，不能生成合法回执，继续要求
  human review。没有阶段获得异构发布通过。

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

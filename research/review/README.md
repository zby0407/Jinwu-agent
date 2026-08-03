# Research Review 2.0

本目录保存整体科研闭环的评测政策与冻结集成挑战集。运行时实现位于
`src/research_review/`、`jw/research_review.py` 和
`jw/middleware/research_review_orchestration.py`。

## 执行边界

- Supervisor 只执行状态机、预算、依赖失效和 issue owner 路由。
- Planner、Data、Hypothesis、Experiment 只能修改自己的任务级产物。
- `solar-evidence` 仅能读取当前 hash-bound artifact 明确引用的来源，并提交
  `ReviewVerdictV2`；它不能修改生产者产物、写知识库或自证异构二审。
- 新 artifact 版本和上游 SHA 变化会使旧 verdict 失效。Canonical v1 文件若在
  checkpoint 后改变，服务端强制 `block`。
- `ResearchRunStateV2` 同时保存 Supervisor 的强制 stage DAG 与 Planner 冻结
  route DAG；生产者返修会把旧 verdict SHA 和 issue fingerprint 绑定进新 artifact，
  只表示重新提交，不代表验收已通过。
- 全图 action budget 和 Evidence review budget 都持久化在任务工作区，页面刷新、
  重新打开阶段或进程重启不会补回预算。
- 最终可见科研报告必须来自已接受 claims，包含所有 carried limits，并依次通过
  integration、final release 和独立 hash-bound 二审。模型返回的自由文本不能绕过
  必需节点。

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

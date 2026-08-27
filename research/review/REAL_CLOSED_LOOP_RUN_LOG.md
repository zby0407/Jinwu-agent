# 真实科研闭环运行记录

本文档记录生产构建 WebUI 和付费模型参与的真实科研运行。记录目标是回答四个问题：
系统实际执行了什么、形成了什么科学结果、暴露了什么问题、后续如何处置。

自动化单元测试、模型兼容探针、真实 WebUI 运行、聚焦 Evidence 调用和科学结论属于
不同证据层。只有经生产 WebUI 自然语言入口启动，并由系统自主完成预定研究范围的运行，
才记为 WebUI 闭环；直接进入单个 Agent 或预置工件的运行单列为聚焦验证。

## 当前案例定位

`MAIN-SC26-B06` 用于判断第 26 太阳活动周最终前兆预测的证据成熟度；它现在作为科学限制门，
不再替代第 26 周期初步概率预测本身。当前主科学交付同时包括 CL-20260825-48 发布的可更新
初步概率预测。`EXT-POLAR-LENGTH-FULL-01` 当前作为工程与
科研闭环压力基准，`EXT-RISE-AMPLITUDE-GENERALIZATION-01` 作为仓库内可见迁移基准。
下表中 r40–r52 的“主验收”类型是运行发生时的登记名称，按历史事实保留；这些记录不再
作为第 26 周期主任务或仓库外未知任务的替代证据。

## 记录规则

每次新运行追加一条记录，不覆盖失败结果。每条至少包含：

- 运行编号、时间、案例与用户原始问题；
- 入口、模型分工、审查模式和用户干预；
- 预定范围与实际终态；
- 用户可见结果、持久化产物和科学结论等级；
- 工程问题、科学问题、处置状态与证据目录。

结果状态统一使用：

- `completed`：预定范围完成，终态与用户可见结果一致；
- `completed_with_limits`：预定范围完成，结论携带明确限制；
- `blocked`：因输入、证据或运行条件不足而停止；
- `runtime_error`：因模型连接、流式传输或程序错误停止；
- `partial`：完成了局部步骤，但没有完成预定范围。

旧运行中出现的已废弃流程字段按历史事实保留，不作为当前运行规范。

## 运行索引

| 编号 | 时间（UTC） | 类型 | 案例 | 实际结果 | 科学结果 | 主要问题 | 处置 |
|---|---|---|---|---|---|---|---|
| CL-20260811-01 | 2026-08-11 20:00 | WebUI 质量探针 | FR-H10 | `partial` | 未形成审查结论 | 未进入科研状态机，assessment 为 0 | 已由后续运行覆盖验证 |
| CL-20260811-02 | 2026-08-11 20:05 | WebUI 质量探针 | FR-H10 | `blocked` | Data 未形成可审查工件 | 无 Evidence round | 保留为诚实负面结果 |
| CL-20260812-01 | 2026-08-12 03:51 | WebUI Evidence 探针 | FR-H10 | `runtime_error` | 无 verdict | 流式响应未完整结束 | 待统一处理长流中断 |
| CL-20260812-02 | 2026-08-12 03:55 | WebUI Evidence 探针 | FR-H10 | `runtime_error` | 无 verdict | 120 秒无新流式 chunk | 待统一处理长流中断 |
| CL-20260812-03 | 时间未写入原回执 | 聚焦 Evidence | FR-H10 | `completed` | `contradicted` / `block` | 只验证 Evidence，不是 WebUI 闭环 | 验证目的已完成 |
| CL-20260812-04 | 2026-08-12 15:46 | WebUI 主任务 | SC26-B06 | `blocked` | 缺少合格任务输入 | assessment 原子回合未形成 | 后续新运行复核 |
| CL-20260812-05 | 2026-08-12 15:52 | WebUI 主任务 | SC26-B06 | `partial` | Data `accept_with_limits` | 页面回答结束而科研状态仍为 `active/data` | 待修终态一致性 |
| CL-20260813-01 | 2026-08-13 04:51 | WebUI 自主主任务 | SC26-B06 | `runtime_error` | 未进入科研阶段 | Qwen endpoint 连接失败 | 更换业务空间后已重跑模型探针 |
| CL-20260813-02 | 2026-08-13 12:26 | WebUI 假设 + Evidence | MAIN-HYP-01 | `partial` | 假设审查完成 | 使用了现已废弃的流程终态 | 仅作历史对照，不再复用 |
| CL-20260813-03 | 2026-08-13 15:02 | WebUI 假设 + Evidence | MAIN-HYP-01 | `partial` | `accept_with_limits`，`exploratory` | Evidence 未进入最终回答；科研状态未终止；耗时过长 | 必须修复，详见下文 |
| CL-20260814-01 | 2026-08-13 16:24 | WebUI Data + 假设 + Evidence | MAIN-HYP-01 | `runtime_error` | Data `accept_with_limits`；Hypothesis 未完成审查 | Kimi tool calling 流式响应中断；旧 harness 误判为有回答 | 已修并由下一次运行复验 |
| CL-20260814-02 | 2026-08-13 17:24 | WebUI Data + 假设 + Evidence | MAIN-HYP-01 | `completed_with_limits` | 两阶段均为 `accept_with_limits`；假设为 `exploratory` | 合同纠错增加时延；内部状态和上游验收措辞不准 | 已修代码，真实结果保留 |
| CL-20260814-03 | 2026-08-13 18:13 | WebUI Data + 假设 + Evidence | MAIN-HYP-01 | `blocked` | Data 工件已形成，审查未落盘 | 父级 Qwen override 被错误用于 Kimi 子运行，reviewer 未发起远程调用 | 已修并由下一次运行复验 |
| CL-20260814-04 | 2026-08-13 18:47 | WebUI Data + 假设 + Evidence | MAIN-HYP-01 | `completed_with_limits` | Data `accept`；Hypothesis `accept_with_limits`、`exploratory` | Hypothesis 将已验收 Data 工件降为未核验缺口；原子提交两次格式纠错 | 已修代码，真实结果保留 |
| CL-20260814-05 | 2026-08-14 01:24 | 仓库外 WebUI 闭环 | EXT-POLAR-BASELINE-01 | `blocked` | 形成低置信可证伪假设，Hypothesis 审查未落盘 | Kimi 两次返回但未提交原子审查；总耗时过长 | PR 前阻塞，待修 |
| CL-20260814-06 | 2026-08-14 02:29 | 聚焦 Evidence 兼容验证 | EXT-POLAR-BASELINE-01 工件 | `runtime_error` | 无 verdict | forced tool choice 路径连接结束后不返回 | 不兼容方案已撤回 |
| CL-20260814-07 | 2026-08-14 06:32 | 仓库外 WebUI 闭环 | EXT-POLAR-GROWTH-01 | `blocked` | 形成低置信可证伪假设，Data 与 Hypothesis 审查完整 | Data 审查遗漏核心变量；Hypothesis 审查误报已存在字段 | 已修代码，真实结果保留 |
| CL-20260814-08 | 2026-08-14 08:01 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-01 | `completed_with_limits` | 形成低置信交互假设，Data 与 Hypothesis 审查完整 | 前四次暴露 stage 识别与原子提交兼容问题；成功轮仍把无支持主张写成 `limited_support` | 已修代码，待下次真实运行复验 |
| CL-20260816-09 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r10 | `blocked` | Planning、Data、Hypothesis 各形成完整三件套；实验设计阶段阻断 | Evidence specialist 两次未持久化 verdict | 保留失败现场，继续修复后重跑 |
| CL-20260816-10 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r11 | `blocked` | 未形成有效阶段审查 | REQUIRED_SPECIALIST_FAILED_TWICE；无 assessment 三件套 | 保留失败现场 |
| CL-20260816-11 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r12 | `runtime_error` | Planning、Data 各形成完整三件套，Hypothesis 未完成 | Qwen 流式连接被远端提前关闭，主图随后返回 APIConnectionError | 已增加一次有界传输重试并将 SDK 默认重试设为 0，待修复后 fresh 重跑 |
| CL-20260816-12 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r13 | `blocked` | Planning 形成三件套但被阻断 | reviewer 未显式传入，实际配置退化为 Qwen，同家族审查未满足规划门 | 保留失败现场；后续运行显式使用 Kimi reviewer |
| CL-20260816-13 | 2026-08-16 | 仓库外 WebUI 闭环（进行中） | EXT-POLAR-LENGTH-FULL-01 / r14 | `partial` | Planning 与 Data 各完成一轮 `accept_with_limits`；当前已进入 Hypothesis 任务 | Hypothesis、实验、整合和最终发布尚未收尾，不能判定终态 | 仅作进行中过程证据，不计入闭环通过 |
| CL-20260816-19 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r19 | `blocked` | Planning artifact 已形成，Evidence 三件套为 0；Data 未启动 | Planning Evidence 原子提交连续不合格 | 保留失败现场，随后加固提交边界 |
| CL-20260816-20 | 2026-08-16 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r20 | `blocked` | Planning `accept_with_limits`，Data `block`；未进入实验 | Data 结构字段投影不足，Reviewer 又把题外周期输入误作必需项 | 保留失败现场，随后修复为可返修审查 |
| CL-20260817-21 | 2026-08-17 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r21 | `runtime_error` | 未形成可审查的 Planning artifact 或科学结论 | headed browser、后端和 WebUI 进程在 Planning 期间消失 | 保留进程丢失记录，改用独立 tmux 会话重跑 |
| CL-20260817-22 | 2026-08-17 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r22 | `runtime_error` | Planning、Data 各完成一轮 `accept_with_limits`；无 Hypothesis artifact | Hypothesis 长调用发生 `APIConnectionError`；Harness 同时暴露空分析输出状态缺口 | 保留完整失败现场；修复、重启后以全新 r23 复验 |
| CL-20260817-24 | 2026-08-17 19:10–20:27 | 仓库外 WebUI 闭环 | EXT-POLAR-LENGTH-FULL-01 / r24 | `blocked` | 未形成 assessment、quality assessment 或科学结论 | Qwen 同家族审查在 Data 路径重复触发工具选择兼容失败，最终 `REQUIRED_SPECIALIST_FAILED_TWICE` | 保留失败现场；修复兼容路径后重新 fresh 复验 |
| CL-20260818-25 | 2026-08-17 19:28–20:37 | WebUI 自主主任务 | MAIN-SC26-B06 | `blocked` | Planning `accept_with_limits`；仅形成 Data 回执与 11 行历史表，未形成新的科学结论 | Data canonical artifact 不完整；千问 thinking 模式拒绝 `tool_choice=required/object` | 保留失败现场；修复后必须以全新 headed production WebUI 会话复验 |
| CL-20260821-27 | 2026-08-20 16:12–16:30 UTC | 全新克隆 WebUI 主验收 / r40 | EXT-POLAR-LENGTH-FULL-01 | `blocked` | Planning 接受；Data 证明项目无合格输入并阻断 | 初次部署不能自主取得已定义的公开观测数据 | 保留诚实阻断；增加受控数据获取后 fresh 复验 |
| CL-20260821-28 | 2026-08-20 16:45 UTC | WebUI 主验收 / r41 | EXT-POLAR-LENGTH-FULL-01 | `runtime_error` | 尚未进入 Data，科学状态未评估 | 预登记来源记录包含临时下载定位信息 | 立即停止；修复长期来源定位后 fresh 复验 |
| CL-20260821-29 | 2026-08-20 之后 | 隔离 WebUI 主验收 / r42 | EXT-POLAR-LENGTH-FULL-01 | `partial` | 自主取得两项输入并形成已核验周期表；无科学结论 | Data 审查误选获取前的旧上下文并连续返修 | 保留失败现场；修复当前上下文选择后 fresh 复验 |
| CL-20260821-30 | 2026-08-20 17:21 UTC | WebUI 启动复验 / r43 | EXT-POLAR-LENGTH-FULL-01 | `runtime_error` | 未进入模型或科研阶段 | 启动前未创建基础 workspace 目录，前端绑定轮询返回 400 | 修正启动环境后 fresh 复验 |
| CL-20260821-31 | 2026-08-20 17:22–20:41 UTC | 隔离 WebUI 主验收 / r44 | EXT-POLAR-LENGTH-FULL-01 | `blocked` | Data 与 Hypothesis 接受；没有实验结果 | 实验设计三次返修后只剩一项问题，但校验次数已经用完 | 将正式科研设计校验次数调为 4 后 fresh 复验 |
| CL-20260822-32 | 2026-08-21 | WebUI 主验收 / r45 | EXT-POLAR-LENGTH-FULL-01 | `partial` | 设计已进入自动实验交接 | 实验服务与上层校验次数不一致，同时暴露统计量语义漂移；无完整终态回执 | 保留 observer 和后端日志，统一设计合同后 fresh 复验 |
| CL-20260822-33 | 2026-08-21 | WebUI 主验收 / r46 | EXT-POLAR-LENGTH-FULL-01 | `runtime_interrupted` | 实验设计第 4 次校验后只剩 1 项问题 | 质量主张调用重复 35 次，未执行实验 | 修复成对比较记录和预算用尽后停止路由 |
| CL-20260822-34 | 2026-08-21 | WebUI 主验收 / r47 | EXT-POLAR-LENGTH-FULL-01 | `runtime_interrupted` | AnalysisClaim 第 15 次尝试后最终写入 | Planning 正式工件未提交，无实验和科学结果 | 在模型可见提示中给出完整 AnalysisClaim 字段与预测验证规则后 fresh 复验 |
| CL-20260822-35 | 2026-08-21 19:30–22:44 UTC | WebUI 主验收 / r48 | EXT-POLAR-LENGTH-FULL-01 | `blocked` | 至 Experiment Result 共完成 7 个审查轮次，每轮三件套完整 | 实验输出路径使用错误导致执行失败；Integration 又因固定来源中包含可变状态而阻断；无科学测量 | 修复输出目录指引与设计来源范围后 fresh 复验 |
| CL-20260822-36 | 2026-08-22 | WebUI 诊断 / r49 | EXT-POLAR-LENGTH-FULL-01 | `incomplete` | 保留 headed 会话和后端过程记录 | 无完整 harness metadata 或科研终态 | 不纳入闭环成功证据 |
| CL-20260822-37 | 2026-08-22 | WebUI 主验收 / r50 | EXT-POLAR-LENGTH-FULL-01 | `budget_stopped` | 实验审查为 `revise`，阻止不符合测量计划的数值进入下游 | 未形成已验证测量；工作进程指标不是科学结果 | 修正失败投影与假设证据门后 fresh 复验 |
| CL-20260822-38 | 2026-08-22 06:03 UTC | WebUI 启动复验 / r51 | EXT-POLAR-LENGTH-FULL-01 | `runtime_error` | 用户问题未提交 | 基础 workspace 目录未创建，绑定返回 HTTP 400 | 创建隔离目录后重新启动 |
| CL-20260822-39 | 2026-08-22 06:05–07:33 UTC | 隔离 WebUI 主验收 / r52 | EXT-POLAR-LENGTH-FULL-01 | `provider_error` | Planning、Data、Hypothesis 各一轮 `accept_with_limits`，每轮三件套完整 | 实验设计三次提交后仍剩一项测量引用问题；Qwen 周配额耗尽；无实验结果 | 修正周期对映射与局部设计返修指引，配额恢复后 fresh 复验 |
| CL-20260822-40 | 2026-08-22 10:42 UTC | 模型兼容探针 | 临时业务空间 | `completed` | 不评价科学问题 | Qwen 生产者与辅助模型的 8 项有界兼容检查通过 | 可用于启动新的主任务 WebUI 验收 |
| CL-20260825-47 | 2026-08-25 03:39–05:09 UTC | WebUI 主验收及同线程恢复 / r41d | MAIN-SC26-B06 | `completed_with_limits` | 最终发布为“暂不启动”，`accept_with_limits` | 首次运行在最终发布门暴露过期工具调用与终态误分类；修复后无新增用户输入恢复到 `released` | 端到端恢复已验证；仍保留全新不间断复验边界 |
| CL-20260825-48 | 2026-08-25 | 聚焦概率预测 | SC26 初步正式预测 | `completed_with_limits` | 平滑峰值 140，80% 区间 90–190 | 极小期前兆尚未形成，模型间分歧较大 | 已发布可更新先验；待极小期前兆更新 |

此外，2026-08-12 的 36 次可见集成回归作为批次记录保留：21 次完成且有回答、
2 次完成但无回答、13 次 runtime error；assessment coverage、核心签名稳定性和
two-pass 时延门均未通过。该批次用于定位工程问题，不构成科学质量结论。批次汇总见
`evals/runs/webui_eval_summary.20260812.json`。

## 重点运行：CL-20260813-03

### 输入与执行范围

用户问题：

> 当前第 25 太阳活动周的观测信号，能否为第 26 太阳活动周的强度趋势和物理机制提供证据？请提出一个最值得检验的科学假设，并说明现有证据。

- 入口：production build WebUI 全新会话；
- 用户干预：0 次批准、0 次运行指导；
- 生产者：`qwen3.8-max/custom-openai`；
- Evidence：Kimi for Coding 路线的 `kimi-k3/kimi-coding`；
- 审查模式：`two_pass`；
- 总耗时：1960.473 秒；
- WebUI 结果：`completed_with_answer`；
- 科研状态：`active/hypothesis`；
- 审查完整性：一个 `ReviewAssessmentV1`、一个
  `ScientificQualityAssessmentV1` 和一个 `ReviewVerdictV2`，工件绑定一致。

### 科学结果

系统保留的首选假设是：第 25 太阳活动周极小期附近形成的极区磁场或轴向偶极分量，
可在 Babcock–Leighton 表面磁通量输运框架下，为第 26 太阳活动周振幅提供条件性前兆。

Kimi Evidence 的 verdict 为 `accept_with_limits`，claim disposition 为
`limited_support`，confidence 为 `low`，质量等级为 `exploratory`。原创性判定为
`known_baseline`：极区场前兆不是新机制，当前可能的增量只在于把问题组织成预注册、
可证伪的条件趋势检验，并显式纳入深层发电机记忆和测量零假设。

本轮没有产生第 26 太阳活动周的数值预测。主要限制为：最终极小期极区场尚未观测；
项目已注册的 MWO/WSO 与 SILSO 文件在受限 Hypothesis 阶段没有被读取和计算；现有
load-bearing 文献主要是摘要层面来源；有效独立样本应按太阳活动周配对计算，不能用
月度记录扩大样本量。

建议的下一项检验是：从已注册原始数据重算极小期极区场前兆与后一活动周振幅，执行
预注册的 leave-one-cycle-out 回测，并报告 bootstrap 区间、定标与去趋势敏感性以及
influential cycles。

### 暴露的问题与修复决定

#### P0：必须先修

1. **Evidence 审查没有进入最终用户回答。** 终端记录中存在完整 Evidence 结论，页面
   最终只重复了生产者假设。用户看不到 nearest-prior-art、Evidence Matrix、证据等级
   和 carried limits。最终响应必须由已接受 claim 与 Evidence 结果确定性合成。
2. **问题要求“当前观测信号”，实际路线却禁止读取已有项目数据。** 项目输入清单已
   注册 MWO/WSO 与 SILSO 数据，但运行选择受限 Hypothesis 路线，最终只能讨论未来极小期
   前兆。对这类问题应先读取当前数据，或明确将结果命名为“文献约束的预备假设”，不能
   把未执行的数据分析当作对当前观测问题的完整回答。
3. **用户可见终态和科研状态不一致。** WebUI 显示回答完成，状态机仍为
   `active/hypothesis`。受限研究包应进入明确的完成终态；完整研究任务则应继续进入 Data、
   Experiment 和 Integration，不能在中途回答后静默停止。
4. **科学质量合同无法自然表达同一 claim 的多个 component。** Kimi 首次用
   `#prediction`、`#scope` 表达 component 时被判为未知 claim，改成同一 claim ID 后又因
   重复被拒；第三次将所有内容压成一个 mechanism component 才成功。这会损失
   statement、prediction 与 scope 的逐项审查。合同应允许一个合法 claim 下有多个唯一
   component，或把 component 设计为 claim 内部列表。

#### P1：随后修复

5. **内部路由文字进入对话。** `Memory preflight`、specialist dispatch、内部阶段名等
   工作信息不应作为科研答案展示。页面只应呈现研究结论、证据和必要的进度状态。
6. **一次受限假设任务调用链过长。** 本轮约有 35 次 Qwen 主模型调用；Kimi 原子提交
   连续两次合同失败后第三次成功。先修复合同和阶段终止，再减少重复读取、重复验证及
   无效模型回合，并记录各阶段时延和调用数。
7. **最终答案的来源可追溯性不足。** 工件中已有 supporting、limiting evidence 和
   nearest-prior-art，用户答案却没有显示来源、精确适用范围和 abstract-only 限制。
8. **“没有直接实证支持”的措辞范围过宽。** 它准确描述的是“本轮未读取当前任务的
   直接观测数据”，并不等于领域中不存在极区场前兆观测研究。最终合成时必须区分
   task-local evidence gap 与领域证据缺失。

#### 保留为科研边界，不作为程序故障

- 第 25/26 活动周交界极小期尚未发生，不能产生最终前兆值；
- 极区场连续观测覆盖的独立活动周数量少；
- 相关性不能单独识别 Babcock–Leighton 因果机制；
- abstract-only 来源只能提供受限支持；
- `accept_with_limits`、低置信、阴性结果和证据不足都是有效结果。

### 验收条件

上述 P0 修复完成后，使用相同自然语言问题重新运行一次 production WebUI：

- 系统自主选择与问题范围一致的 Data/Hypothesis/Evidence 路径；
- 若读取项目数据，回答标明实际分析的时间截止点、样本单位和计算结果；若不读取，明确
  标为预备假设并说明未回答当前观测量；
- 页面最终答案包含接受的假设、支持/反对/限制证据、nearest-prior-art、Evidence verdict
  和 carried limits；
- 一个 claim 的 statement、mechanism、prediction、scope 可分别审查，原子提交首次通过；
- 页面终态与持久化状态一致；
- 不出现内部路由或调试文字；
- 保存 metadata、最终回答、三类审查产物、状态和截图。

### 2026-08-14 修复状态

CL-20260813-03 暴露的四项 P0 已完成代码修复和确定性验证：

- 最终假设答复由已接受的 Hypothesis 工件和同一审查轮次的 Evidence assessment、
  quality assessment、verdict 确定性合成，包含证据矩阵、原创性边界和 carried limits；
- 同一 claim 可用 `(claim_id, claim_component)` 唯一键分别审查 statement、mechanism、
  prediction、scope 等分量；
- “当前观测 + 假设 + 证据”类自然语言问题保持 Hypothesis 为最终专业意图，同时在其前面
  加入 bounded Data 阶段，顺序为 Data 生产与审查、Hypothesis 生产与审查；
- 被接受的 bounded 最终结果完成渲染后，持久化状态进入 `released`，页面终态可与科研
  状态一致；携带工具调用的主模型过程文字会被清空，不再进入最终对话；证据缺失措辞已
  改为本次分析范围，不再泛化为领域中不存在直接实证。

验证结果：相关 Python 定向测试 162 项通过；全量 pytest 为 3380 passed、13 skipped、
6 warnings、8 subtests passed；WebUI Node 测试 21 项通过；生产构建成功。以上属于代码与
自动化验证。其后的真实复验见 CL-20260814-01 和 CL-20260814-02。

## 真实复验：CL-20260814-01 与 CL-20260814-02

两次运行均使用与 CL-20260813-03 完全相同的自然语言问题，从 production build WebUI
全新会话进入；用户没有提供内部阶段提示、工具指令或运行指导。生产者为
`qwen3.8-max/custom-openai`，Evidence 为 Kimi for Coding 路线的
`kimi-k3/kimi-coding`，审查模式为 `two_pass`。

### CL-20260814-01：诚实失败及故障定位

- 线程：`019ffbf0-9e32-79f0-ac1d-598e8956425a`；
- 耗时：2409.994 秒；
- 持久化终态：`blocked/hypothesis`；
- 已完成部分：Data 生产及一次完整 Evidence round，verdict 为
  `accept_with_limits`；
- 失败位置：Hypothesis Evidence tool calling；Kimi 的流式响应三次出现
  `RemoteProtocolError: incomplete chunked read`，未形成 Hypothesis assessment 或
  verdict；
- 记录偏差：旧 terminal classifier 将流程阻塞通知误分为
  `completed_with_answer`。该输出不是科学答案。

处置包括：Kimi tool calling 改用非流式响应，普通回答仍保留流式；Evidence 委派不再
复制父 Agent 的长篇假设文本，改为由 reviewer 打开服务端绑定上下文；terminal classifier
将流程阻塞通知识别为 `research_blocked`。

证据目录：`evals/runs/main_hypothesis.postfix.20260814.r1/`；后端日志：
`evals/runs/backend.main_hypothesis.postfix.20260814.kimi.two_pass.log`。

### CL-20260814-02：首次完整成功的同题真实闭环

- 线程：`019ffc27-36b3-75f2-b548-b11bc5b687e4`；
- 耗时：2430.644 秒；
- WebUI 终态：`completed_with_answer`；持久化终态：`released/hypothesis`；
- 路径：Data 生产与 Kimi Evidence → Hypothesis 生产与 Kimi Evidence；
- 完整性：两个 review round 各有且仅有一个 `ReviewAssessmentV1`、一个
  `ScientificQualityAssessmentV1` 和一个 `ReviewVerdictV2`；两阶段 verdict 均为
  `accept_with_limits`；
- 用户干预：0 次批准、0 次自动批准、0 次运行指导；
- 模型用量：输入 66084 tokens，输出 3984 tokens。

用户可见结果提出一个条件性极区场前兆假设：第 25 太阳活动周期极小期附近的极区场或
轴向偶极分量，可作为第 26 太阳活动周期振幅的条件性前兆；Babcock–Leighton 表面磁通量
输运是候选机制路径，而不是已证实的因果解释。答案同时保留 persistence、Waldmeier
rise-rate、深层发电机路径和 measurement/statistical null，并提出预注册的
leave-one-cycle-out 回测、persistence baseline、逐周期误差、杠杆点与校准敏感性分析。

本轮没有生成第 26 太阳活动周期的数值预测。Evidence 将主张评为 `limited_support`、
低置信和 `exploratory`；原创性属于 `known_baseline` 或增量检验设计，不支持优先权或
“首次”措辞。Data 阶段接受了第 15–24 太阳活动周期历史特征表的数据与来源边界，但没有
完成第 25 太阳活动周期前兆量化，也没有建立样本外预测能力。最终科学结果因此是可检验的
预备假设和证据边界，不是第 26 太阳活动周期预报，也不是机制因果证明。

本次成功运行仍暴露三项效率与表达问题：Evidence 首轮提交曾混淆 per-claim assessment
与 component-level quality matrix，并漏填 accepting verdict 的 `accepted_claims`，造成
多次工具纠错；生产者把内部“草稿/未发布”状态写入读者正文，与已释放终态矛盾；下游把
已通过 Evidence 的 Data 工件笼统写成“未核验”，没有区分“数据与来源边界已验收”和
“预测能力尚未检验”。现已分别通过明确双合同的行粒度、在审查上下文携带上游 verdict
及限制、禁止生产者正文输出内部生命周期状态进行修复。该后续修复已通过确定性测试；
CL-20260814-02 的历史答案不回写，仍作为真实运行原始记录保存。

证据目录：`evals/runs/main_hypothesis.postfix2.20260814.r1/`；持久化工作区：
`projects/default/runs/run_019ffc27-36b3-75f2_def36639/`；后端日志：
`evals/runs/backend.main_hypothesis.postfix2.20260814.kimi.two_pass.log`。

## 真实复验：CL-20260814-03 与 CL-20260814-04

两次运行继续使用相同自然语言问题、production build WebUI 全新会话、Qwen 生产者和
Kimi for Coding Evidence。页面由 headed browser 实际创建，用户批准、自动批准和运行
指导均为 0。

### CL-20260814-03：运行时模型身份错误

- 线程：`019ffc59-b2a2-7f13-b950-49b9a8a54bcc`；
- 终态：`blocked/data`；Data 工件已形成，assessment、quality 和 verdict 均为 0；
- 失败原因：Qwen 兼容中间件优先读取父级环境中的 Qwen override，没有优先读取当前
  Kimi reviewer 的运行时模型，因此把 Kimi 误判为 Qwen；本地证据导航重复消耗调用预算，
  没有向 Kimi for Coding endpoint 发出有效审查请求；
- 修复：当前运行时模型优先于父级 override；非 Qwen reviewer 不再进入 Qwen schema、
  thinking 和本地导航分支。后续四类 Kimi 兼容探针均通过。

本轮没有完整 harness metadata，只保留 observer、任务工作区、失败回执和后端日志，
不能补写不存在的耗时或科学结论。证据目录：
`evals/runs/main_hypothesis.postfix3.20260814.r1/`；持久化工作区：
`projects/default/runs/run_019ffc59-b2a2-7f13_1e662183/`。

### CL-20260814-04：完整成功及新增语义缺陷

- 线程：`019ffc73-30a0-7783-955d-af31b946e932`；
- 耗时：2031.742 秒，约 33.9 分钟；
- WebUI 终态：`completed_with_answer`；持久化终态：`released/hypothesis`；
- 路径：Data 生产与 Kimi Evidence → Hypothesis 生产与 Kimi Evidence；
- 完整性：两个 review round 各有且仅有一个 `ReviewAssessmentV1`、一个
  `ScientificQualityAssessmentV1` 和一个 `ReviewVerdictV2`；
- 模型用量：输入 46314 tokens，输出 4850 tokens；
- Data verdict 为 `accept`；Hypothesis verdict 为 `accept_with_limits`；最终答案已包含
  独立证据审查、claim component 质量上限、Evidence Matrix、原创性边界和结论限制。

科学结论仍是低置信的条件性极区场前兆假设：第 25 周衰减相至 25/26 极小期的极区场或
轴向偶极矩可能为第 26 周期峰幅提供方向性约束；核心前兆属于已知基线，增量主要在于
拒绝在极小期确认前给出定量预报，并把发电机记忆、经向流共同驱动和测量零假设纳入
预注册回测。当前证据主要为摘要级模拟/综述，Babcock–Leighton 只可作为候选机制，
不能据此认定因果。系统没有给出第 26 周期数值峰幅。

本轮暴露两项后续问题。第一，Data 审查确认了历史周期表的数据和来源边界，但 Hypothesis
请求仍以自然语言绑定，未把该工件声明为工具合同内的 `data_feature`，导致生产者和 Evidence
把同一工件降为“未核验缺口”。现已由运行时生成标准 Hypothesis 请求文件，将已接受 Data
工件及其限制作为可定位上游材料；它可支持特征产品存在性，仍不能支持预测技能或因果。
第二，Kimi 原子提交先后因 `search_cutoff` 不是完整 ISO-8601 时间、以及接受型 verdict
携带 major issue 被拒；模型均能根据工具错误自行修正并在第三次提交成功。工具提示现已
直接说明日期格式及“科学限制写入 carry_forward_limits、需要生产者修订才写 issue”的边界。

上述后续修复通过定向测试和全量 pytest，但没有把历史真实答案回写为修复后结果，也没有
立即对同一可见题再做一次付费运行。证据目录：
`evals/runs/main_hypothesis.postfix4.20260814.r1/`；持久化工作区：
`projects/default/runs/run_019ffc73-30a0-7783_043efe8a/`；后端日志：
`evals/runs/backend.main_hypothesis.postfix4.20260814.kimi.two_pass.log`。

## 仓库外真实验证：CL-20260814-05

原始问题此前未出现在仓库评测集：

> 在历史太阳活动周配对中，极小期极区磁场是否比“沿用上一活动周峰值”的持续性基线
> 更适合作为下一太阳活动周强度的前兆？请基于现有可用观测提出一个最值得检验、
> 可证伪的科学假设，并说明支持、反对和限制证据。

- 入口：production build WebUI 全新会话，headed browser；
- 线程：`019ffdde-74d0-7230-bf60-42422639915b`；
- 模型：Qwen 生产者，Kimi for Coding Evidence，`two_pass`；
- 用户干预：0 次运行指导；
- 后端运行时间：2026-08-14 01:24:03 至 02:21:39 UTC，约 57.6 分钟；
- 持久化终态：`blocked/hypothesis`；harness 未识别该 blocked 终态并继续轮询，
  因而没有完整写出本轮 metadata；
- 路径：Data 生产与 Evidence 完成，Hypothesis 生产完成，Hypothesis Evidence 未完成。

Data 阶段生成 10 个已完成活动周期的配对表，并明确记录直接极区场覆盖有限、早期记录使用
代理量、极小期确认依赖回顾性平滑、第 24→25 周期配对未纳入以及本阶段未计算区分性统计。
Data verdict 为 `accept_with_limits`，六项限制被原样携带。已接受 Data 工件随后以
`data_feature` 进入 Hypothesis 请求，且没有被误写成预测技能或因果支持；这项跨阶段绑定
在真实运行中得到验证。

Hypothesis 形成一个低置信探索性主张：在直接磁强计覆盖的历史配对子集中，极小期极区场
模型应在 leave-one-cycle-out 的 MAE、RMSE 和逐折误差上优于恒等持续性基线
`A_(N+1)=A_N`，并在控制上一周期峰值后保留信息。持续性/继承性、代理测量零假设和
小样本偶然均作为竞争解释保留。系统没有声称该比较已经成立，也没有生成数值预报。
长尾审查将其判为 `known_baseline`，不是原创机制；文献覆盖仅有三组摘要级来源，其中最近
先例只能说明极区场曾被用作前兆，不能证明其优于持续性基线。

本轮没有形成 Hypothesis `ReviewAssessmentV1`、`ScientificQualityAssessmentV1` 或
`ReviewVerdictV2`。Kimi 两次完成远程响应，但均未调用 `evidence_review_submit_round`；
状态机因此按 reviewer 合同失败置为 blocked。运行中还出现一次 Qwen 长流无新 chunk 超时，
同家族恢复后继续；整个单候选 Hypothesis 路径及 Evidence 等待耗时明显超过可接受范围。

真实产物同时暴露出 Hypothesis adapter 的顶层投影不完整：claim 内证据角色已能按 register
区分 supports、opposes 和 limits，但 artifact 顶层仍只引用状态文件且 `limitations` 为空。
现已改为从证据 register 暴露虚拟证据引用，并把限制证据投影为顶层 limitations；实际工件
数据验证得到 9 个证据引用和 5 条限制，相关定向测试 130 项通过；当前全量 pytest 为
3387 passed、13 skipped、6 warnings、8 subtests passed。该修复尚未经过新的完整 WebUI
闭环复验。

结论：本轮科学假设本身保持了可证伪性、低置信和原创性边界，但系统未完成配置要求的
Evidence round，且总时延过长。因此不能据此创建新的可发布 PR 候选。

证据目录：`evals/runs/external_polar_baseline.20260814.r1/`；持久化工作区：
`projects/default/runs/run_019ffdde-74d0-7230_09a29abe/`；后端日志：
`evals/runs/backend.external_polar_baseline.20260814.kimi.two_pass.log`。

## 聚焦兼容验证：CL-20260814-06

为验证 Kimi 未调用工具的问题，使用 CL-20260814-05 的真实 Hypothesis 状态建立新任务，
仅运行 Kimi Evidence，并尝试由通用 allowlist 中间件强制首个 `open_context` 工具。新任务
正确生成了 1 条 claim、9 个证据引用和 5 条限制，但 Kimi 调用约 8 分钟后仍未返回首个
工具结果，进程连接处于已结束但调用链未收尾的状态。该聚焦验证被终止，没有 assessment、
quality sidecar 或 verdict。forced tool choice 改动已撤回，不进入候选代码。

持久化工作区：`projects/default/runs/run_evidence-probe-ext_a2c7ea6f/`。该结果说明 Kimi
Coding 的非流式工具路径仍需单独解决，普通回答、通用工具或结构化输出探针通过不能替代
完整 Evidence Agent 验收。

## 仓库外真实验证：CL-20260814-07

原始问题此前未出现在仓库评测集：

> 在有直接磁强计观测覆盖的太阳活动周中，太阳极小期前三年的极区场增长斜率，是否比
> 极小期单点极区场强更能预测下一活动周的振幅？请基于现有可用观测提出一个最值得检验
> 的科学假设，并说明现有证据。

- 入口：production build WebUI 全新会话，headed browser；
- 线程：`019ffef9-1998-73c3-b803-1d81041d3d19`；
- 模型：Qwen 生产者，Kimi for Coding Evidence，`two_pass`；
- 用户干预：0 次运行指导；
- 耗时：2288.268 秒，约 38.1 分钟；
- 终态：`research_blocked`，持久化状态为 `blocked/hypothesis`；
- 完整性：Data 与 Hypothesis 两个 review round 均各有一份 assessment、quality sidecar
  和 verdict。

Data 阶段生成了第 15 至 24 活动周的十行历史前兆表，包括极小期极区场代理量、上一周期
振幅和周期长度等字段，但没有极小前三年增长斜率，也没有足以计算该斜率的逐月极区场序列。
Kimi Data review 仍给出 `accept_with_limits`。这说明当时的 Evidence 只核对了工件声明的
“历史绑定表存在”，没有把数据字段逐项对照用户问题的核心 observable，是本轮最重要的
科学语义缺陷。

Hypothesis 随后形成一个低置信探索性主张：在直接磁强计覆盖的极小样本中，前三年增长
斜率可能含有超出极小单点值的新增前兆信息，并把多点平均降低噪声、共同原因和测量伪影
列为替代解释。可证伪检验要求在相同信息截止时点下比较仅单点值、仅斜率和联合模型，使用
rolling-origin、bootstrap、噪声匹配替代序列以及 MWO/WSO 跨仪器一致性分析。该主张明确
标为 `exploratory_hypothesis`、实证支持为空、置信度低，没有宣称斜率已经优于单点值。

Kimi Hypothesis review 正确识别出本地证据没有直接比较斜率与单点值，也没有预测技能或
独立证据；但同时误报候选缺少 alternatives、confounders、falsifiers 和原创性边界。实际
工件已包含三项替代解释、四项混杂、四项证伪条件，并明确写为
`novelty_not_assessed`、检索家族数为零和覆盖缺口。阻断 verdict 因而同时包含有效缺陷与
reviewer 误判，不能整体视为正确科学裁决。

后续修复保持这份历史结果不变：Data review 必须检查任务要求的核心变量及其可计算输入；
核心字段缺失时应返修或阻断，不能由相邻历史表代替。Hypothesis review 则须先读取候选字段
再报告结构缺失，并允许明确标为探索性、完整披露证据缺口且提供可证伪下一检验的假设以
限制条件通过。Kimi 原子提交失败后的侧写文件清理、两次失败即停止和 harness 的三件套
完整性统计也已加入回归。

确定性验证为全量 pytest `3395 passed, 13 skipped, 7 warnings, 8 subtests passed`，WebUI
Node 测试 25 项通过，production build 成功。上述结果证明工程合同与构建可用，不证明该
科学假设成立。

证据目录：`evals/runs/external_polar_growth.20260814.r1.retry6/`；持久化工作区：
`projects/default/runs/run_019ffef9-1998-73c3_7938d255/`；后端日志：
`evals/runs/backend.external_polar_growth.retry6.isolated.20260814.kimi.two_pass.log`。

## 仓库外真实验证：CL-20260814-08

原始问题此前未出现在仓库评测集：

> 在太阳活动周15至24的逐周期观测中，上一活动周较长是否会削弱极小期极区场强对下一
> 活动周振幅的预测关系？请提出一个最值得检验、可证伪的交互作用假设，并说明现有证据
> 与最强零假设。

第五次全新会话完成了预定的 Data → Evidence → Hypothesis → Evidence 范围：

- 入口：production build WebUI，headed browser；
- 线程：`019fff4a-6644-79b0-acff-f98c195c5a5d`；
- 模型：Qwen 生产者，Kimi for Coding Evidence，`two_pass`；
- 用户干预：0 次批准、0 次运行指导；
- 耗时：2248.582 秒，约 37.5 分钟；
- 页面结果：`completed_with_answer`；科研状态：`released/hypothesis`；
- 完整性：Data 与 Hypothesis 两轮均各有一份 assessment、quality sidecar 和 verdict，
  每轮三者绑定一致；两轮 verdict 均为 `accept_with_limits`。

最终答案提出一个低置信探索性假设：上一周期长度可能调节极小期极区场与下一周期振幅的
关系；在线性模型中，待检验的极区场×周期长度交互项为负，表现为周期较长时极区场预测
斜率变平。最强零假设是交互项为零，观测到的交互只是十个相互依赖周期上的过参数化噪声。
候选同时列出共线性、MWO/WSO 测量制度切换和相位定义伪影，并预先给出交互区间、周期级
置换、留一周期影响分析、滚动原点验证和仅磁强计制度复算等区分性检验。

该假设目前没有实证支持。运行没有计算交互系数，也没有核验逐周期长度；现有十行表只含
极小日期、极区场代理量和振幅等字段，周期长度需由相邻极小日期另行构造，端点与索引口径
仍需明确。Kimi 质量矩阵把现有来源全部列为限制或缺口，结论上限为 `exploratory`，原创性
为 `novelty_not_assessed`。因此这次结果只证明系统能自主提出结构完整且不编造结果的可证伪
假设，不证明负交互存在，也不证明该假设具有原创性。

成功前的四次全新会话均在 Data Evidence 阶段诚实阻断，分别耗时 497.607、533.524、
434.182 和 589.315 秒，且 assessment、quality sidecar、verdict 均为零。它们依次暴露并
定位了自然语言 stage 标记未识别、真实委派提示变体未识别、较早的通用 stage 文本覆盖较晚
委派，以及数字文本检查与路由/质量字段组合过度约束。修复后第五次真实运行证明自然语言
入口、异构 reviewer、原子回合和 headed WebUI 结果可以贯通。

第五次运行仍暴露出一项科学语义问题：Hypothesis assessment 的 supporting evidence 为空，
却把 disposition 写成 `limited_support`。运行后新增的边界规范化已将这类 disposition 改为
`undecided`，同时保留探索性假设的 `accept_with_limits` 流程路由。该修复已经确定性测试
覆盖，但发生在第五次后端启动之后，不能回写历史答案，也尚未由新的付费 WebUI 运行复验。

本次总耗时中约 23 分钟用于 Hypothesis 生产与分析合同构造。最终假设的机制、零假设和检验
设计比早期运行更完整，但后半程多次模型调用没有带来相称的新增科学证据。时延仍是产品问题；
后续应减少重复检索与草稿校验回路，而不能用本次成功掩盖前四次失败或约 37.5 分钟总耗时。

证据目录：`evals/runs/external_polar_length.20260814.r1.retry5/`；持久化工作区：
`projects/default/runs/run_019fff4a-6644-79b0_a41191c7/`；后端日志：
`evals/runs/backend.external_polar_length.interaction5.20260814.kimi.two_pass.log`。

## 2026-08-16 r15–r18 接管记录

后续只读复核补充了 r15–r17 的实际边界，未改写原始运行产物：

- r15 的 Data 阶段真实读取两份此前获取并登记的 SILSO 与 MWO/WSO 输入，形成周期 15–24 的
  10 行逐周期表；相邻周期派生表为 15→16 至 23→24 的 9 对。Data 与 Hypothesis 审查曾把
  10 行误解为 10 对，随后由确定性派生表和实验合同更正为 `independent_sample_count=9`。
  运行最终阻断在 Experiment Design，没有实验结果、Hypothesis Update、Integration 或
  Final Release。
- r16 的 Kimi Planning reviewer 连续失败，未形成可验收的完整审查三件套；不能把该运行的
  其他目录痕迹解释成 Data 缺失。
- r17 的正确 workspace 根包含两份登记输入，错误后端根为空；Data 在错误根形成
  `input_missing`，直接原因是 thread binding 分裂，不是真实项目数据缺失。

r18 使用正式 launcher，将 `JW_WORKSPACE_DIR` 绑定到同一运行根重新启动。已确认 Planning、
Data、Hypothesis 分别以 `accept_with_limits` 通过 Evidence；实验设计文件已生成并由自动实验
模块标记为 `design_validated`。随后实验设计生产子 Agent 的 `qwen3.8-max` 调用连续两次
返回 `403 AccessDenied.Unpurchased`，运行按错误策略在 `experiment_design` 阶段以
`research_blocked` 终止。没有实验设计 Evidence、实验结果、Hypothesis Update、Integration
或 Final Release。r15 的历史“10 对”表述保留在原始产物中，并在本记录中附上更正，避免把
后续复核误写成当时已知事实。

本次 r18 的结构化回执位于 `evals/runs/next_stage_polar_length.full.r18/`；其中
`metadata.json` 记录 `outcome=research_blocked`、`current_stage=experiment_design`，错误
摘要为必需 specialist 连续失败。自动实验目录保留 `design.json`、`response.json`、
`compact_design_attempts.jsonl` 与 `state.json`，但没有 `record.json` 或结果报告。

当前 r18 证据目录：`evals/runs/next_stage_polar_length.full.r18/`；观察链接：
<http://127.0.0.1:4717/?threadId=01a00953-d62a-7843-b7fd-ac61074ea273>；后端日志：
`evals/runs/backend.next_stage.full.r18.20260816.kimi.two_pass.log`。

## 新 Token Plan 接口复验：CL-20260816-19

r19 使用新的千问 Token Plan Base URL，以 production build WebUI 全新 headed 会话再次运行
周期长度交互问题。用户输入仍只有原始自然语言研究问题，没有阶段名、工具调用说明或科学
结论注入。

- 线程：`01a00aff-2ac8-7dc3-952d-b0488669f9a1`；
- 运行根：`projects/default/runs/run_01a00aff-2ac8-7dc3_efd2a077/`；
- 模型：Qwen 生产者、Kimi for Coding Evidence，`two_pass`；
- 耗时：1174.074 秒；
- 终态：`research_blocked`，持久化状态为 `blocked/planning`；
- 完整性：Planning artifact v0001 已生成，Evidence 三件套均未落盘，Data 未启动。

新千问接口在路由和 Planning 生产期间持续返回 HTTP 200，没有出现 401、403、
`AccessDenied`、`input_missing` 或 workspace 根分裂。直接阻断原因是 Kimi Evidence 的原子
提交连续不符合科学质量字段要求：缺口证据填写了虚构来源、`release_candidate` 状态与结论
上限不一致、模拟或综述材料被赋予过高结论上限，并在接受型结果中遗漏 `accepted_claims`。
严格检查拒绝了这些提交，因此没有把不完整审查写成成功结果。

后续修改保留 r19 原始产物不变：Evidence 提交增加了只降低主张强度的安全修正，并补充了
缺口来源、结论上限和接受主张字段的明确提示。Data Agent 同时增加学术检索、任务级网页
来源和小型数据代码分析能力；这些修改发生在 r19 之后，必须由全新会话验证。

证据目录：`evals/runs/next_stage_polar_length.full.r19/`；后端日志：
`evals/runs/backend.next_stage.full.r19.20260816.kimi.two_pass.log`；harness 日志：
`evals/runs/webui.next_stage_polar_length.full.r19.20260816.harness.log`。

## Qwen Harness 与 Data 增强复验：CL-20260816-20

r20 在新千问接口与 Data 增强代码加载后，以 production build WebUI 全新 headed 会话运行同一
周期长度交互问题。页面只提交原始自然语言问题，没有阶段说明、科学答案或人工批准。

- 线程：`01a00b3b-efb1-7de3-99e5-4d36137ef913`；
- 运行根：`projects/default/runs/run_01a00b3b-efb1-7de3_dc5a1bc0/`；
- 生产者：`qwen3.8-max`，Data 子 Agent 使用 `qwen3.7-plus`；Evidence 实际调用由后端日志
  记录为 Kimi for Coding，模式为 `two_pass`；
- 耗时：1289.036 秒；
- 终态：`research_blocked`，持久化状态为 `blocked/data`；
- 完整性：Planning 与 Data 各形成一套 assessment、scientific quality assessment 和
  verdict；Planning 为 `accept_with_limits`，Data 为 `block`；后续阶段均未启动。

Planning 生成并冻结了交互检验路线，明确把周对作为独立样本，预先规定交互项、加性与仅极区
场基线、周期级置换、留一周期分析、滚动原点误差和降级条件。规划同时保留了早期极区场覆盖、
MWO/WSO 制度差异、单位和符号约定等未决项，没有把这些内容写成已核验事实。

Data 随后读取两份登记输入，生成周期 15–24 的十行前兆表和语义回执。Evidence 没有把文件
存在直接视为合格结果：数据工件仍由自由文本适配而来，未把表头、单位、符号约定、逐行时间
关系、有效独立样本数和不确定性完整投影到可审查字段，因此被阻断。审查提出的“需要周期
25/26 当前周期输入”超出原问题限定的历史周期 15–24，不应构成必要输入缺失；现有状态机却
仅凭 Reviewer 使用 `REQUIRED_DATA_INPUT_UNAVAILABLE` 标签就把该意见升级为永久阻断，导致
本可返修的 Data 阶段提前终止。

Data 入口的两个强制工具选择请求返回 HTTP 400；兼容层仅为
`solar_data_open_context` 与 `prepare_solar_precursor_cycle_table` 两个确定性过渡动作生成本地
工具调用，后续数据读取、质量审计和统计请求继续返回 HTTP 200。本轮没有出现 401、403、
超时、工作区分裂或运行期 Schema 校验错误。Data 没有调用外部 Harness 检索或托管代码分析，
因此本轮不能作为 Harness 来源绑定的端到端验收。

r20 只证明 Data 生产与审查阻断路径被真实运行到；它没有形成 Harness 回执、实验设计或结果、
样本外指标、Hypothesis Update、Integration 或 Final Release，也没有产生可支持或反对交互作用
假设的科学结论。

该运行保留为修复前证据。后续工作包括：让必要输入缺失以任务绑定的数据清单为准；把周期对
表、时间边界、单位、符号、来源制度和缺口投影为结构化 Data 主张；加固 Harness 路径、来源
角色、响应不完整状态和凭据回显处理；修复后必须使用全新会话复验。

证据目录：`evals/runs/next_stage_polar_length.full.r20/`；后端日志：
`evals/runs/backend.next_stage.full.r20.20260816.kimi.two_pass.log`；harness 日志：
`evals/runs/webui.next_stage_polar_length.full.r20.20260816.harness.log`。

## 独立进程会话前的失败：CL-20260817-21

r21 使用 production build WebUI 全新 headed 会话，页面仍只提交原始自然语言研究问题。Planning
文稿已经写入任务目录，但尚未注册为 `ResearchArtifactV2`，assessment、scientific quality
assessment 和 verdict 均为 0。运行约十分钟后，headed browser monitor、后端和 WebUI 临时执行
进程同时消失；最后持久化状态仍为 `active/planning`。

后端最后保存的事件是 Qwen 请求返回 HTTP 200，日志没有 shutdown traceback。该结果归类为
`runtime_process_loss`：它不是 Evidence 对规划的阻断，也不支持任何太阳活动科学判断。失败记录
位于 `evals/runs/next_stage_polar_length.full.r21/harness_failure.json`，后端日志位于
`evals/runs/backend.next_stage.full.r21.20260817.kimi.two_pass.log`。后续 r22 将后端、WebUI 和 headed
browser harness 分别放入独立 tmux 会话，仍使用全新线程和同一原始问题。

## 独立进程会话复验：CL-20260817-22

r22 的后端、production build WebUI 和 headed browser harness 分别运行在独立 tmux 会话中。
页面只提交原始自然语言问题，用户批准、自动批准和运行指导均为 0。

- 线程：`01a01073-6dc6-7670-bc42-cd81f1074a70`；
- 观察链接：<http://127.0.0.1:4717/?threadId=01a01073-6dc6-7670-bc42-cd81f1074a70>；
- 耗时：3207.258 秒，约 53.5 分钟；
- WebUI harness 终态：`runtime_error`，`has_answer=false`；
- 持久化状态：`active/data`；
- 完整性：Planning 与 Data 各有一份 artifact、一份 assessment、一份 scientific quality
  assessment 和一份 verdict；两轮均为 `accept_with_limits`，两轮均显式列出接受的 claim ID。

Data context 为 `inputs_available`，必需数据集为 `silso-monthly-total-v2` 和
`mwo-wso-polar-field-v2`，缺失列表为空且 `must_stop=false`。前兆表回执为
`solar-precursor-cycle-table-v2 / verified`，包含 cycle 14 边界行和 cycles 15–24，共 11 行；
回执声明 14→15 至 23→24 共 10 个请求周期对均可构造。回执仍明确记录预测量窗口尚未按计划
实现、目标振幅不确定性未计算和极小期日期不确定性未计算。

Data Agent 共调用 8 次受控代码分析 Harness。3 份回执因 `ReadTimeout` 为 `error`；另外 5 份
provider 返回 `completed`，但均没有分析条目或分析工件。Data artifact 没有把这些空回执投影
为候选证据，而是依靠任务绑定的本地结构化前兆表完成审查。该真实结果同时说明旧状态判定仍会
把“provider 完成但没有必需分析输出”写成 completed，后续已增加 partial 降级回归。

Data 审查后，系统开始生成 Hypothesis 草稿并绑定本地资料，但在 Hypothesis artifact 注册前，
Qwen 长调用最终抛出 `APIConnectionError('Connection error.')`。因此 Hypothesis artifact、实验
设计、实验结果、Hypothesis Update、Integration 和 Final Release 均为 0。本轮没有交互估计、
样本外误差或可支持/反对交互作用的科学结论。

证据目录：`evals/runs/next_stage_polar_length.full.r22/`；持久化工作区：
`projects/default/runs/run_01a01073-6dc6-7670_57c7c500/`；后端日志：
`evals/runs/backend.next_stage.full.r22.20260817.kimi.two_pass.log`；harness 日志：
`evals/runs/webui.next_stage_polar_length.full.r22.20260817.harness.log`。

## Qwen Pro 同模型复验与 thinking/tool_choice 阻断：CL-20260818-23

r23 在修复 Task 1–4 的最终复审问题后，以独立运行数据目录启动 backend、production build
WebUI 和 headed browser harness。页面只提交原始自然语言问题，没有阶段提示、工具说明、科学
答案或人工批准。

- 线程：`01a0110f-d112-7241-87c7-e8325287c130`；
- 观察链接：<http://127.0.0.1:4717/?threadId=01a0110f-d112-7241-87c7-e8325287c130>；
- 时间：2026-08-18 02:50:37–03:03:21（本地时间）；
- 耗时：764.153 秒，约 12.7 分钟；
- 模型：`qwen3.8-max/custom-openai`；Planning、Data、Evidence、Hypothesis 和 Experiment
  均按本轮配置使用千问；Evidence 与生产者属于同一模型家族，`heterogeneous=false`；
- 用户干预：0 次批准、0 次自动批准、0 次运行指导；浏览器模式为 headed；
- WebUI harness 终态：`research_blocked`，`has_answer=false`；持久化状态为 `blocked/planning`；
- 完整性：Planning 形成 `v0001` artifact，但 assessment、scientific quality assessment、
  verdict 和 Data artifact 均为 0，后续 Hypothesis、Experiment Design、Experiment Result、
  Hypothesis Update、Integration 和 Final Release 均未启动。

Planning 之后的必需 specialist 调用两次收到 Qwen/Token Plan 的 400 拒绝：
`The tool_choice parameter does not support being set to required or object in thinking mode`。
该错误不是科学审查意见，也不是数据缺失；状态机按“必需 specialist 连续失败两次”停止，保留
`REQUIRED_SPECIALIST_FAILED_TWICE` 和原始错误摘要。浏览器另有 WebGL、SSL 和 D-Bus 环境提示，
未被用作研究阻断原因。

该轮暴露了 Qwen thinking 模式下 Evidence 原子提交仍使用 OpenAI object tool choice 的兼容缺口。
随后新增回归：Evidence 进入提交阶段时只暴露唯一的 `evidence_review_submit_round` 工具，
保持自动选择并通过短指令要求完整参数；Qwen 仍可保留思考模式，避免发送 provider 拒绝的
`required/object` 选择。原 r23 目录和日志保持不变，修复后需要以全新会话复验。

证据目录：`evals/runs/next_stage_polar_length.full.r23/`；持久化工作区：
`projects/default/runs/run_01a0110f-d112-7241_8cfb89aa/`；后端日志：
`evals/runs/backend.next_stage.full.r23.20260818.qwen.two_pass.log`；harness 日志：
`evals/runs/webui.next_stage_polar_length.full.r23.20260818.harness.log`。

## 全新复验终态：CL-20260817-24

r24 的 headed production WebUI 会话已收尾。页面只提交周期长度交互问题原始自然语言，没有阶段提示、
工具说明或人工批准。observer 线程为
`01a01122-23fc-7e42-8b2f-e56ccae51b10`，观察链接为
<http://127.0.0.1:4717/?threadId=01a01122-23fc-7e42-8b2f-e56ccae51b10>。

- 时间：2026-08-17 19:10:41–20:27:43 UTC；耗时 4621.233 秒，约 77.0 分钟；
- 模型：`qwen3.8-max/custom-openai`；生产者与 reviewer 同属 Qwen，`heterogeneous=false`，
  审查模式为 `two_pass`；用户干预 0 次；
- 终态：`research_blocked` / `blocked`，`has_answer=false`；assessment、scientific quality
  assessment、Evidence review invocation 和科学结论均为 0。

Data 路径的多次工具调用曾收到 Token Plan 兼容接口的 HTTP 400，兼容层合成了本地过渡调用；后续
运行仍反复进入 Data 工具路径，最终以 `REQUIRED_SPECIALIST_FAILED_TWICE` 停止。该终态只说明本轮
没有形成可审查的科研工件，不能解释为周期长度交互假设得到支持或反对。

权威工作区 `projects/default/runs/run_01a01122-23fc-7e42_74c48881/` 的持久化状态为
`blocked/data`：Planning 已接受，Data 被阻断，后续阶段均未启动。该工作区已经保存 Data context、
前兆表 CSV、v2 回执和失败文件；只读复现显示，首要工程根因是审查器拒绝任务清单中的合法
`/project/data/...` 虚拟路径，导致 full-research Data context 没有通过权威性检查。项目输入实际
存在且与清单摘要匹配，前兆表回执也与 CSV 内容摘要一致。修复已加入受限项目共享目录映射、
注册清单核对和回归测试，r24 原始阻断产物保持不变。

证据目录：`evals/runs/next_stage_polar_length.full.r24/`；后端日志：
`evals/runs/backend.next_stage.full.r24.20260818.qwen.two_pass.log`；harness 日志：
`evals/runs/webui.next_stage_polar_length.full.r24.20260818.harness.log`。

## 主问题全新复验：CL-20260818-25

本轮以 production build WebUI 的全新 headed 会话，只提交主问题原始自然语言；没有提供阶段提示、
工具说明、科学答案或人工批准。问题要求将资料冻结至 2026-06-30，判断第 26 太阳活动周正式强度
分类和可检验峰值区间是否已经具备证据条件，并核查 SILSO、F10.7 和 MWO/WSO。

- 线程：`01a01131-fcc4-7840-9419-330ee2e26e1c`；
- 观察链接：<http://127.0.0.1:4727/?threadId=01a01131-fcc4-7840-9419-330ee2e26e1c>；
- 时间：2026-08-17 19:28:03–20:36:07 UTC（2026-08-18 03:28:03–04:36:07 北京时间）；研究状态
  在 4083.4 秒后写入阻断。后端背景运行随后于 20:37:28 UTC 结束，总执行时间 4164.283 秒；
- 模型：`qwen3.8-max/custom-openai`；用户干预 0 次；
- 终态：`research_blocked` / 持久化 `blocked/data`；没有最终回答；Hypothesis、Experiment Design、
  Experiment Result、Hypothesis Update、Integration 和 Final Release 均未启动。

Planning 已形成并通过 `accept_with_limits` 审查的 planning artifact、assessment、quality assessment
和 verdict。Data 阶段生成了 `solar_precursor_cycle_table` 回执与 CSV：cycle 14 边界行、cycle 15–24
的 10 个历史周期对，共 11 行。回执仍明确记录三项缺口：计划的极小期前后 6 个月窗口尚未实现、
目标振幅不确定性未计算、极小日期不确定性未计算；F10.7 没有形成可核验的任务级序列和出处。
这些是数据回执，不等同于已注册的 Data canonical artifact；`run_state` 中没有 Data artifact、
Data assessment 或 Data verdict，因此不能把该表写成已完成的数据审查。

Data 受控 Harness 共留下 12 份回执：两次 `code_interpreter` 为 `completed` 且各有两个分析工件，
一次为空输出的 `partial`，八次因 `RemoteProtocolError: Server disconnected without sending a response`
为 `error`；另一次 `web_search` 为 `partial`，保存 72 条外部线索/抽取工件，但部分页面抽取失败，
没有升级为支持证据。Harness 记录只说明工具调用和失败边界，不构成太阳物理实验结果。

Data 阶段最终失败回执记录了两次原因：第一次为缺少完整的 task-local canonical v1 artifact；第二次
为千问思考模式拒绝 `tool_choice=required/object`（HTTP 400）。状态机因此停止在 Data 阶段。本轮没有
计算交互或样本外指标，也没有形成支持、反对或正式发布第 26 周强度分类的科学结论。应保留原始运行
目录和失败回执；修复 Data canonical artifact 完整性与 Qwen thinking/tool-choice 兼容后，必须用全新
headed production WebUI 会话复验。

证据目录：`projects/default/runs/run_01a01131-fcc4-7840_d304e57c/`；后端日志：
`evals/runs/backend.main_sc26.retry1.20260818.qwen.two_pass.log`；harness 日志：
`evals/runs/webui.main_sc26.current.retry1.20260818.harness.log`。

## Token Plan Chat 兼容层最小真实探针：CL-20260818-HARNESS-01

针对 r24 暴露的 `/responses` 404，先在不启动新的科学闭环、也不打印凭据的条件下做了一个
小型协议探针。输入是一份已登记格式的逐周期 CSV，研究请求只要求读取表格、计算行列数并写出
一个校验文本。

- 协议：Token Plan OpenAI-compatible `chat/completions`；模型为 `qwen3.8-max`；
- 结果：千问返回唯一 `run_python` function call，宿主在隔离 `python_workspace` 中真实执行，
  标准输出报告 11 行、13 列，并生成 `verified rows=11` 文件；
- 回执：`status=completed`，包含一条 `derived_calculation` 和一条 `derived_output`，工具轨迹
  标记 `protocol=chat_completions`，无 errors/warnings；
- 边界：执行代码、输入摘要、输出文件和回执均落在该调用目录；普通 prose 不会产生计算条目。

该探针只验证端点协议、隔离执行和证据落盘，不证明任何太阳物理命题，也不能替代新的 headed
production WebUI 全流程验收。r24 原始运行的 `research_blocked` 终态和失败现场保持不变，未被本探针改写。

## 近期质量修复复验：CL-20260819-r31–r33

### r31：Planning Evidence 阻断

- 线程：`01a01676-70f7-75b1-bf00-32b6e1f78574`；运行：`01a01676-7169-7a61-b058-9e23d26f9fcc`；
- 终态：`research_blocked`，持久化状态为 `blocked/planning`，无最终回答；
- Planning artifact 曾生成，但连续两次没有形成与当前工件对应的 `ReviewVerdictV2`；Data、Hypothesis、
  Experiment 和后续阶段均未启动；
- 该轮是运行时审查交接失败，不能用来评价 Data Agent 或太阳物理假设。

### r32：Planning/Data 接受，Hypothesis checkpoint 缺失

r32 的 Planning 和 Data 各形成一套完整三件套，均为 `accept_with_limits`。Data 的 v2 周期表为
11 行，覆盖 14→15 至 23→24 的 10 个请求周期对；MWO 代理、WSO 制度、周 15 回退值、回顾性平滑
标签和 `n_eff≤10` 等限制均保留。Hypothesis 已生成候选草稿，但 checkpoint 没有绑定成功；没有
实验设计、实验结果、Hypothesis Update、Integration 或 Final Release。该轮不构成完整闭环，也不
提供交互效应的实证结果。

### r33：修复后 fresh headed 会话（进行中快照）

截至 2026-08-19 07:07（北京时间），r33 仍在同一 headed 会话中运行：

- 线程：`01a016f8-df28-7ad2-affd-03fab73cd906`；运行：`01a016f8-df52-7bd0-ab47-9bceb3bc1946`；
- 观察链接：<http://127.0.0.1:4751/?threadId=01a016f8-df28-7ad2-affd-03fab73cd906>；
- Planning 与 Data 已各以 `accept_with_limits` 持久化；Hypothesis 已有低置信探索性草稿并在绑定
  文献证据，checkpoint、实验与最终发布尚未形成；
- 当前快照不是终态。终态、耗时、每阶段工件数量和任何科学结论必须在运行自然结束后从同一
  `projects/default/runs/` 工作区与 `evals/runs/` 日志补录，不能由本快照推断。

本轮对应的代码修复是结构化交接修复：`src/scientific_hypothesis/harness.py` 补齐模型可见的
`scientific_quality` 合同并限制 `evidence_confidence_caps` 枚举；`jw/subagents/solar/solar_hypothesis.yaml`
明确 checkpoint 不等于发布、`needs_revision` 只有一次有界返修机会。定向回归为 73 项通过、8 个
子测试通过；该工程证据与真实模型调用、科学有效性保持分层。

## 完整闭环收尾：CL-20260820-26

本条目记录 `EXT-POLAR-LENGTH-FULL-01` 从 r34 到 r39 的同一任务收尾。fresh headed WebUI
会话只提交原始自然语言问题；后续恢复只提交“继续完成上述完整科研闭环”。线程为
`01a017a8-cf80-7371-a437-2079b63d13ff`，观察地址为
<http://127.0.0.1:4717/?threadId=01a017a8-cf80-7371-a437-2079b63d13ff>，持久化工作区为
`projects/default/runs/run_01a017a8-cf80-7371_e5f36de7/`。

### 运行序列

- r33 属于上一条独立线程，其浏览器监控在获得终态前中断；该快照不作为本任务的完成证据。
- r34 在 Planning、Data 和第一版 Hypothesis 工件形成后因 `APIConnectionError` 结束，页面终态为
  `runtime_error`。该失败保留，后续在同一任务上恢复。
- r35 完成 Hypothesis 审查、Experiment Design、真实 Experiment Result、实验后 Hypothesis Update
  和 Integration。真实实验运行
  `question_0555d8c0e646-20260819T095630Z-beb9e677` 的 attempt-001 完成计算但区间依据未通过追溯；
  attempt-002 补齐依据后重新执行并通过结果核验，科学终态为 `high_uncertainty`。
- r36 暴露恢复路由误回到有界 Hypothesis、并把状态误写为 `released/hypothesis` 的问题；
  Final Release 仍为 pending。r37 已恢复完整研究路由，但发布工具的逐字限制、逐字摘录、数字正则和
  段落匹配造成多次无科学意义的返工，因此该轮被中止。
- r38 删除上述文本级硬门，只保留发布边界结构检查，并把科学表达、限制覆盖和内容污染交给 Final
  Evidence。运行 `01a01aee-537b-74e3-b35a-da074b5798a7` 于 577.834 秒内成功完成：
  `final_release-artifact@v1` 与 Final Release 的三份审查文件落盘，verdict 为
  `accept_with_limits`。
- r39 增加“已接受报告实际返回后提交 released 状态”的终态动作。WSL 曾在第一次启动期间由外部
  重启，服务恢复后从同一 headed WebUI 任务再次续跑；运行
  `01a01b0c-b502-7eb2-9b3b-a9b6ed684df3` 于 66.417 秒内成功，最终状态为
  `released/final_release`。

### 科学结论与限制

- `β3=12.0217`，周期对重抽样区间 `[-71.2719, 106.2204]` 覆盖零；
- 加性零模型置换尾部比例为 `0.5193`，五项预注册支持条件均未通过；
- 交互模型 `MAE/RMSE=81.27/117.63`，高于加性模型的 `34.06/43.44`；
- 剔除 23→24 对或采用极小日期最长情景会使方向翻转；
- 结论限于周期 15–24 的 10 个相邻周期对，不外推至周期 25，不形成因果机制、显著性、原创性或
  正式预测声明。

Final Release 的 ReviewAssessment 将正文主张评为有高置信支持，但记录两条引用映射可在后续重发布
时改进；ScientificQualityAssessment 将结论与数值结果限定为 `evidence_constrained`。这些信息性
引用问题没有改变数值、结论或 `accept_with_limits` verdict。

证据包括：

- `research/review/evals/runs/backend.next_stage.full.r38.release_semantic_review.20260820.qwen.two_pass.log`；
- `research/review/evals/runs/backend.next_stage.full.r39.release_delivery.20260820.qwen.two_pass.log`；
- `projects/default/runs/run_01a017a8-cf80-7371_e5f36de7/research_review/`；
- `projects/default/runs/run_01a017a8-cf80-7371_e5f36de7/experiment/runs/question_0555d8c0e646-20260819T095630Z-beb9e677/`。

交付前对当前工作树重新执行全量工程检查：Python 为
`3668 passed, 13 skipped, 6 warnings, 8 subtests passed`，WebUI Node 为 `25/25`，production build、
Python 编译及根仓库与 8.12.1 工作区的差异格式检查均通过。检查完成后停止本轮后端和 WebUI，
`6174`、`4717`、`9239` 均无监听；运行目录与失败记录未删除。

## 全新克隆与自主数据入口复验：CL-20260821-27 至 31

本组运行在新克隆目录 `8.20.4` 中使用同一 B3 主验收问题。headed production WebUI 只接收原始
自然语言问题，运行器不提供数据文件、阶段指令、统计数值或预设结论。各轮失败均保留，后续修复
不回写已有运行。

### r40：无项目数据时在 Data 阶段诚实阻断

- 线程：`01a01ff2-587e-7c81-b82b-ef2ddff0f6db`；
- 时间：2026-08-20 16:12:41–16:30:40 UTC；耗时 1078.437 秒；
- 入口：全新 headed production WebUI，会话开始时任务输入和项目共享输入均为 0；
- 终态：`research_blocked`，持久化状态为 `blocked/data`，无最终回答，用户干预为 0。

Planning 形成完整的 assessment、scientific quality assessment 和 verdict，最终为 `accept`。Data
上下文随后确认两项必需数据均缺失，Data 只形成 `block` verdict，没有形成完整审查三件套；Hypothesis
至 Final Release 全部未启动。该轮没有实验、样本外指标或太阳物理结论。它证明当时系统能诚实停止，
也证明初次部署尚不能从公开权威来源自主取得协议已经明确规定的数据。

观察回执位于 `research/review/evals/runs/next_stage_polar_length.full.r40/`。本轮的科研阻断状态与
浏览器停止后的任务空闲状态属于不同层次；后者只表示程序已结束，不能改写 `blocked/data`。

### r41：来源定位问题触发主动停止

r41 在运行前由操作者预登记公开数据，因此即使继续也只能检验“项目已有数据”条件，不能证明
Data Agent 自主获取。检查长期来源记录时发现，其中一项记录误用了下载过程中的临时重定向地址。
运行在 Planning 阶段停止，科学状态为 `not_assessed`，没有进入 Data，也没有产生科学结论。

随后将长期记录改为 Dataverse 持久标识、稳定元数据接口、稳定文件接口和 file ID，并增加回归测试；
临时重定向地址不进入文档、提交或后续运行回执。r41 的停止回执位于
`research/review/evals/runs/next_stage_polar_length.full.r41/interruption.json`。

### r42：自主获取成功并暴露上下文选择缺陷

r42 使用隔离且初始为空的项目根启动，项目数据目录不存在，也没有隐式加载旧任务。系统在 Data
入口自主取得并登记 `silso-monthly-total-v2` 与 `mwo-wso-polar-field-v2`，生成的前兆表状态为
`verified`，包含 1 行边界周期和周期 15–24 共 10 行分析周期；两项来源记录均只保留稳定定位信息。

该轮随后连续两次收到同一 Data 返修意见。复核确认，Data 入口先写入了获取前的 `input_missing`
上下文，获取后又写入 `inputs_available` 上下文；审查器在两份记录并存时误选旧记录，因而把已经与
当前输入一致的表判断为上下文不一致。运行在 Data 阶段停止，没有 Hypothesis、实验、整合、发布或
科学结论。中断回执位于
`research/review/evals/runs/next_stage_polar_length.full.r42/interruption.json`。

修复后，缺失检查发生在正式上下文写入之前，受支持协议完成按需获取和项目刷新后只写入一次当前
上下文；正式研究模式的专用数据审查只使用通过当前任务、计划和输入清单检查的上下文。相关定向
回归及既有有界上下文兼容测试均通过，仍需由下一次全新 headed 会话确认真实路径。

### r43：启动环境错误

r43 在模型调用前失败。启动参数指向的基础 workspace 目录尚未创建，前端 workspace 绑定轮询因而
返回 HTTP 400 并超时。该结果归类为启动环境 `runtime_error`，不评价 Planning、Data Agent 或主
科学问题。原始失败回执位于
`research/review/evals/runs/next_stage_polar_length.full.r43/harness_failure.json`。后续启动在运行器
执行前显式创建隔离 workspace、bindings 和运行数据目录。

### r44：自主数据与假设通过，实验设计在最后一项返修前停止

r44 从另一个初始为空的项目根和全新 headed production WebUI 会话启动。页面只提交原始研究问题，
没有阶段说明、数据路径、统计答案、人工批准或追加指导。线程为
`01a02032-37ce-7fc3-9cc8-11a1c7813e7e`；后端运行约 3 小时 19 分钟后结束，持久化状态为
`blocked/experiment_design`，没有最终科学回答。

系统自主取得并登记 SILSO 月度总黑子数与 MWO/WSO 极区场两项必需数据。本轮只写入一份当前
`inputs_available` 上下文，缺失列表为空；前兆表包含周期 14 边界行和周期 15–24 的 10 行分析记录，
周期对表包含 14→15 至 23→24 共 10 对。Data 审查第 1 轮为 `accept_with_limits`，保留了 MWO 代理与
WSO 磁强计测量制度差异、14→15 南半球窗口覆盖不足、时间序列相关性及小样本限制。该结果验证了
自主获取、来源登记、数据表生成与 Data 交接，不构成交互作用的实证支持。

Hypothesis 阶段形成两个候选及其证据关系，独立审查第 1 轮为 `accept_with_limits`。主候选把
周期 15→16 至 23→24 的 9 对作为主要分析样本，把 14→15 仅留作标注敏感性分析；同时预先保留
有效独立样本数不足、MWO/WSO 制度切换、文献缺口和机制解释仅属探索性等限制。一次模型连接错误
经同一次任务的有界重试恢复，没有改写候选方向或数据范围。

实验设计随后连续提交三版。需修正项从首版的 23 项缩减到第二版的 2 项，第三版只剩同一批评价
观测上的成对比较记录未补齐；此时正式科研设计的三次校验机会已经用完，设计文件没有通过并落盘。
后续调度再次进入设计 Agent，但既不能新建同一范围的运行，也不能继续修改已停止的运行，最终诚实
阻断。目录中只有请求、输入快照、响应和三次设计尝试记录，没有 `design.json`、实验执行记录、
整合或最终发布。因此响应中的交互模型、置换、重抽样和滚动验证均只是未获接受的预登记方案，
不能描述为已经执行。

根因修复只调整正式科研编排：把设计校验机会由 3 次改为 4 次，使真实运行中已经收敛到单项问题的
设计可以完成一次最终修正；底层默认路径和停止机制保持不变。该修改先由失败测试确认，再通过定向
回归。r44 原始阻断目录与日志保留，修复效果必须由全新 r45 会话验证。

## 设计交接、质量合同与实验输出复验：CL-20260822-32 至 35

四次运行均使用 `EXT-POLAR-LENGTH-FULL-01` 问题原文、全新 headed production
WebUI 会话与 `two_pass` 审查；批准和操作者指导均为 0。它们用于验证新克隆
中的自主数据获取、设计交接、真实执行与审查路径，不沿用 r34–r39 的科学
数值或任务状态。

### r45–r47：收敛前的运行边界

r45 从实验设计继续进入自动实验交接，但只保存了 observer 与后端日志，没有完整
harness metadata。本轮暴露两个工程问题：正式编排允许的设计校验次数已提高，实验
服务却仍按旧上限拒绝；模型生成的分析还把预登记统计量转换成了另一种计算。后续
修复统一了服务与编排合同，并在请求与回执中保留已预登记的算术平均值及其标准误定义。

r46 已用完 4 次设计校验，最后只剩 1 项设计记录问题；之后质量主张记录连续调用
35 次而未停止，运行被中止在 `experiment_design`。这不是科学阴性结果，也没有
执行实验。修复后，校验预算用尽会形成明确停止动作，不再进入无信息增益的质量循环。

r47 在 Planning 阶段反复修正 AnalysisClaim 结构，第 15 次尝试后虽已写入记录，但没有
提交 Planning 正式工件，也没有执行实验。修复将完整字段骨架、独立样本单位、缺失处理、
影响分析和时间顺序样本外验证规则直接提供给 Planning、Hypothesis 与 Experiment 生产者。

### r48：完整审查链下的技术失败

r48 线程为 `01a025ce-19ca-7c91-a637-8672829cd5d4`，观察地址为
<http://127.0.0.1:4717/?threadId=01a025ce-19ca-7c91-a637-8672829cd5d4>。运行从
2026-08-21 19:30:55 UTC 至 22:44:18 UTC，耗时 11602.713 秒，终态为
`research_blocked/integration`，没有最终回答。

Planning、Data、Hypothesis 第 1 轮、Hypothesis 第 2 轮、Experiment Design、Experiment
Result 和 Integration 共保存 7 个审查轮次，每轮均且仅有一份 ReviewAssessment、
ScientificQualityAssessment 和 ReviewVerdict。前六个阶段判定为 `accept_with_limits`，
Integration 因 1 项关键问题判定为 `block`。

该轮 Experiment Result 并未产生科学测量。实验工作进程已生成分析代码，但在打开首个
预期输出 `fold_errors.csv` 时错把上游工件索引当作本轮输出路径，立即产生 `KeyError`。
结果阶段如实保留了该技术失败，没有伪造统计数值。Integration 阶段又发现，设计的固定来源
清单中包含了运行中会合法更新的状态文件，因而把正常状态变化判为来源不一致。

修复后，生产者可见上下文显式列出本轮输出文件对应的运行输出目录，并明确禁止从上游工件
索引取得未来输出；Experiment Design 的固定来源范围仅保留设计请求、响应与已接受设计，
实验状态仍作为 Experiment Result 的运行证据。两项修复和 AnalysisClaim 提示修复已通过
定向回归，真实闭环效果由下一次全新会话评价。r48 的原始阻断、完整审查记录与无科学
测量边界均保留，不因修复改写。

## 失败证据投影与供应商中断：CL-20260822-36 至 39

r49 运行到 Hypothesis 过程后被中止，只保留 observer 与后端过程记录，没有完整
harness metadata、最终回答或可发布科研状态，因而只作诊断记录。

r50 使用全新 headed production WebUI 会话，线程为
`01a0279f-d157-73c0-a8f3-d4bbe68f6a77`。实验工作进程在资源预算终止前返回了一批指标，
但实验审查记录为 `revise`：35 项测量的名称、角色或单位与已接受设计不一致，工作进程未从
周期特征表重新推导活动周长度并与周期对表交叉检查，也没有形成经核验的测量。该轮不提供任何可引用
的数值结论。

复核还发现，旧的实验投影优先读取审查中的拟定结果，并可能把工作进程指标描述为已验证结果。
修复后，最终运行记录的结果类型优先；只有工作进程真实完成且结果属于 `completed_interpretable`、
`scientific_null`、`high_uncertainty` 或 `partial_result` 时才投影测量。`technical_failure`、
`budget_stopped` 及其他未完成状态统一保留为技术失败边界，测量列表为空。Hypothesis 合同同时拒绝将
指向这类运行的实验证据写入支持或反对证据。对 r50 真实 `record.json` 的新投影结果为
`execution_completed=false`、`outcome=technical_failure`、`metrics=[]`，并保留测量计划不一致原因。

r51 在提交用户问题前因基础 workspace 目录不存在而超时，前端绑定返回 HTTP 400。
这是启动环境失败，不评价系统功能或科学有效性。

r52 使用隔离的运行数据、workspace 与 bindings 目录，headed production WebUI 线程为
`01a02812-b492-7e20-b919-f6b21de69161`，观察地址为
<http://127.0.0.1:4719/?threadId=01a02812-b492-7e20-b919-f6b21de69161>。Harness 记录显示只有一条用户消息，
为 88 字的原始自然语言问题；人工批准与追加指导均为 0。

Planning、Data 和 Hypothesis 各完成一轮 `accept_with_limits`，对应的 ReviewAssessment、
ScientificQualityAssessment 与 ReviewVerdict 数量一致。Data 阶段生成 11 行周期特征表与 10 行
周期对表；权威范围为 14→15 至 23→24，左端点是预测周期 14–23，右端点是目标周期 15–24，
不包含周期 25。Data 审查保留了混合测量制度、有效独立样本上限、代理变量语义与回顾性平滑等限制。
Hypothesis 的交互作用候选没有支持或反对证据，四条 Data 证据均按限制材料绑定，方向只作待验探索。

实验设计连续提交三次，第三次只剩 `criteria[15].measurement_refs` 未同时引用两个条件估计及差值；
此时设计尚未通过，实验也未执行。第四次修订前，Qwen 返回周配额耗尽，Harness 终态为
`provider_error`，持久化科研状态仍为 active。它不是科学终态，也不是设计拒绝结论。

后续修复将 `pair_coverage` 映射作为 Planning、Data、Hypothesis 和实验交接的统一依据，并要求
对 `design.criteria[i].measurement_refs` 的条件比较局部返修同时引用条件 A、条件 B 与已声明差值。
这些修复已通过定向测试；完整真实闭环仍需在供应商配额恢复后，以另一个全新 headed production
WebUI 会话复验。

## 临时业务空间兼容验证：CL-20260822-40

本轮通过独立权限受控配置使用业务空间的 OpenAI-compatible 路线，没有读取或输出 API Key。
`qwen3.8-max` 与 `qwen3.7-plus` 分别完成普通回答、单工具调用、结构化输出和多轮工具调用，
8 项检查均通过。回执位于
`evals/runs/provider_probe.business_workspace.20260822/compat.json`，只记录模型、provider、
检查状态和时间，不保存模型正文或凭据。

该结果确认当前 endpoint、凭据和两种 Qwen 模型的兼容调用链可用。它不是 production WebUI
运行，没有科研阶段工件，不评价主任务结论，也不能替代 Evidence reviewer、完整闭环或科学验证。

## 主验收与可见迁移复验：CL-20260823-41 至 42

### CL-20260823-41：`MAIN-SC26-B06` 主科学验收

- 类型：headed production WebUI 主科学验收。
- 入口与干预：全新会话只提交原始问题；初始观察器超时后进行两次有界恢复诊断，因此不计作完全
  零追加输入通过。线程为 `01a02987-6d40-7bc1-b885-7ad0a2c5b8ba`。
- 实际结果：Planning、Data、Hypothesis 与 Experiment Design 形成正式工件；实验设计为
  `accept_with_limits`。自动实验在执行前达到运行时间预算，`attempt_count=0`、
  `outcome=budget_stopped`，没有测量、诊断或执行回执。Experiment Result 判定为 `block`，科研状态
  为 `blocked/experiment_result`，没有 Integration 或 Final Release。
- 科学边界：不得发布第 26 周期强度分类或峰值区间；本轮没有通过最终发布门，也没有形成可交付的
  “暂不启动”完整科研报告。
- 处置状态：主验收未通过；保留全部负面运行证据。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r3/`。

### CL-20260823-42：`EXT-RISE-AMPLITUDE-GENERALIZATION-01` 可见迁移

- 类型：headed production WebUI 仓库内可见迁移基准。
- 入口与干预：r2 为全新会话，批准、自动批准和操作者指导均为 0；线程为
  `01a02a8e-f731-76b0-b739-27f610be2643`。
- 实际结果：Planning 与 Data 为 `accept`，Hypothesis 为 `accept_with_limits`。实验设计生成期间，
  同一供应商流式请求连续两次断开，唯一有界重试用尽后 LangGraph 以 `APIConnectionError` 结束；
  没有实验设计正式工件、实验结果、Integration 或 Final Release。
- 科学边界：假设仅限第 21–24 周的四个独立周期，已有文献支持为 0，不主张因果、原创性或外推；
  没有产生可引用统计结果。
- 工程边界：r1 曾暴露 `scientific_payload` 数值与数组字段被写成解释性文本，提示合同修订已通过
  自动测试；r2 未到达 Experiment Result，故该修订尚无真实端到端通过证据。
- 处置状态：迁移基准未通过；仓库外未知任务未执行，整体状态保持 `do_not_launch`。
- 证据目录：`research/review/evals/runs/rise_amplitude.transfer.r1/`、
  `research/review/evals/runs/rise_amplitude.transfer.r2/`。

### CL-20260824-43：`MAIN-SC26-B06` r12 主科学验收

- 类型：headed production WebUI 主科学验收。
- 入口与干预：全新会话只提交原始问题；人工批准、自动批准和追加指导均为 0。线程为
  `01a02f29-bb64-7a73-a5c6-643b4ca2c664`，运行标签为 `main_sc26.primary.r12`。
- 模型与审查模式：Qwen 生产者、Kimi two-pass Evidence；运行使用隔离的业务空间配置，未在日志或工件中保存凭据。
- 实际结果：Planning、Data、Hypothesis 和 Experiment Design 均形成正式工件并以
  `accept_with_limits` 通过。Experiment Design 首次紧凑校验发现 6 个合同字段问题，模型依据校验反馈完成第二次修订，
  设计随后通过验证。Experiment Result 交接时模型流式连接连续失败：首次任务发生传输异常并在中间件内重试一次；随后系统分别从两个已持久化检查点启动有界恢复，均再次发生 `APIConnectionError`。
- 执行边界：自动实验运行保持 `attempt_count=0`，没有启动工作进程，也没有生成五个实验结果 JSON、测量、诊断或执行回执；Integration 和 Final Release 未启动。
- 用户可见结果：Harness 终态为 `runtime_error`，未形成科研回答；持久化阶段仍停在 Experiment Design，Experiment Result 为 `pending`。
- 科学结果及置信边界：本轮没有新增科学测量或科学结论，不能据此发布第 26 周期强度分类、峰值区间或机制判断。前序阶段保留的结论仍仅是“证据不足、暂不发布正式预测”的条件性边界。
- 工程问题：新增的瞬时恢复路径从两个不同检查点各启动过一次，但模型请求仍在实验交接阶段因供应商或网络运行错误中断；这属于运行技术阻断，不是实验阴性结果，也不能据此判断套餐能力。
- 处置状态：r12 证据已保留；按本轮运行约束停止后端、WebUI 和浏览器，不启动新的 Qwen 运行，整体状态继续为 `do_not_launch`，等待用户恢复原套餐后再决定是否复验。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r12/`、
  `research/review/evals/runs/harness.main_sc26.primary.r12.log`、
  `research/review/evals/runs/backend.main_sc26.primary.r12.log`。

### CL-20260824-44：`MAIN-SC26-B06` r30 主科学验收

- 类型：headed production WebUI 主科学验收。
- 入口与干预：全新会话只提交原始问题，人工批准、自动批准和追加指导均为 0。线程为
  `01a03413-f648-7cd3-a71e-49de715f91cb`，运行标签为 `main_sc26.primary.r30`。
- 模型与审查模式：Qwen 生产者与 Qwen two-pass Evidence，使用套餐专属
  OpenAI-compatible endpoint；未在运行记录中保存凭据。
- 实际结果：Planning、Data 和 Hypothesis 均形成正式工件并以
  `accept_with_limits` 通过。Hypothesis 首次子任务在形成最新尾部审查前达到模型调用上限，
  父流程随后从持久化草案自动续跑，成功形成当前候选池的 canonical checkpoint 和正式工件。
- 实验设计与恢复：自动实验 Agent 绑定 9 份上游输入，完成不可变快照；前两版设计
  均未通过合同校验，设计校验预算使用 2/4。第一次供应商连接异常后，父流程从同一
  experiment run 和输入快照启动唯一一次图级重试；重试中再次发生同指纹
  `APIConnectionError`，按有界重试策略终止。
- 执行边界：实验运行为
  `question_65c536d2124b-20260824T144350Z-66d21643`，终止时 `attempt_count=0`、
  Experiment Design 仍为 `pending`；没有执行工作进程、实验测量、Experiment Result、
  Integration 或 Final Release。
- 用户可见结果：Harness 终态为 `runtime_error`，耗时 2853.901 秒，没有形成最终科研回答。
- 科学结果及置信边界：本轮不提供可引用的实验数值或第 26 周期强度预测。前序工件仅支持
  “截至 2026-06-30 暂不启动正式分类”的待验就绪性判断，不能替代真实实验和最终审查。
- 处置状态：无效验收证据已保留，不进入“好答案”归档；启动全新无人干预会话继续复验。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r30/`、
  `research/review/evals/runs/harness.main_sc26.primary.r30.log`、
  `research/review/evals/runs/backend.main_sc26.primary.r30.log`。

### CL-20260825-45：`MAIN-SC26-B06` r38w 主科学验收

- 类型：headed production WebUI 主科学验收。
- 入口与干预：Windows 原生 Chrome 在全新会话中只提交原始问题，无上传文件；人工批准、自动批准和追加指导均为 0。线程为
  `01a036a5-5ba6-7181-a29b-290b9e0f1636`，运行标签为 `main_sc26.primary.r38w`。
- 模型与审查模式：Qwen 生产者与 Qwen two-pass Evidence。
- 实际结果：Planning 第 1 轮以 `accept_with_limits` 形成正式工件。Data 在正式派发前连续两次未通过权威太阳数据本地预检，系统以
  `REQUIRED_SPECIALIST_FAILED_TWICE` 停止，终态为 `research_blocked/data`，耗时 547.522 秒。
- 科学边界：没有 Data 正式工件、Hypothesis、实验、整合、最终发布或科学回答。运行后对 SILSO 月总量、平滑值、极值表、历史极区场、F10.7 和 WSO 当前序列的六项隔离获取诊断均通过，因此本轮证据支持“运行当时的获取路径失败”，不支持“数据合同持续不可用”。
- 用户可见结果：无最终回答。
- 处置状态：负面运行证据已保留；不把预检失败解释为科学阴性结果。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r38w/`。

### CL-20260825-46：`MAIN-SC26-B06` r39w 主科学验收

- 类型：headed production WebUI 主科学验收。
- 入口与干预：Windows 原生 Chrome 在另一个全新会话中只提交原始问题，无上传文件；人工批准、自动批准和追加指导均为 0。线程为
  `01a036b0-8a7f-7973-9c95-29b46724e7be`，运行标签为 `main_sc26.primary.r39w`。
- 模型与审查模式：Qwen 生产者与 Qwen two-pass Evidence。
- 实际结果：Planning 生产者的流式请求连续两次出现同指纹 `APIConnectionError`，唯一图级有界重试用尽。Harness 终态为 `runtime_error`，耗时 326.759 秒；持久化科研状态仍为 `active/planning`，因为没有形成科学终态。
- 科学边界：没有 Planning 正式工件、审查裁决、数据产物或科学回答。该运行只证明当前供应商连接路径在有界恢复后仍不稳定，不评价数据充分性、模型假设或第 26 周期预测。
- 用户可见结果：无最终回答。
- 处置状态：运行错误已保留；按有界重试策略停止，不启动无限重试。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r39w/`。

### CL-20260825-47：`MAIN-SC26-B06` r41d 端到端恢复验收

- 类型：headed production WebUI 主科学验收，以及修复后的同线程无输入恢复。
- 入口与干预：全新会话只提交一次原始自然语言问题，无上传文件；人工批准、自动批准和追加指导均为 0。线程为
  `01a03700-00fd-7722-944c-f9f48ee64324`，最终消息记录中用户消息数为 1。
- 模型与审查模式：Qwen 生产者与 Qwen two-pass Evidence，使用隔离 workspace、bindings 和运行数据目录。
- 实际结果：Planning、Data、Hypothesis、Experiment Design、Experiment Result、更新后的 Hypothesis、Integration 和
  Final Release 均形成正式工件。最终持久化状态为 `released/final_release`，Final Release verdict 为
  `accept_with_limits`，接受 `final-release-output-v1`，无 blocked claim。
- 实验执行：自动实验实际启动 1 次并正常形成可解释结果，`outcome=completed_interpretable`、
  `phase=report_finalized`，累计工作进程时间 0.305084 秒。六项数据产品中仅 SILSO 平滑序列的 104.2 锚点被本轮解析器
  独立复原；SILSO 极值表、WSO Polar.html 和 MWO/WSO 标定 CSV 的解析校验失败，不能把上游数据核验全部描述成本轮
  独立复算成功。
- 用户可见结果：最终报告明确回答“暂不启动”。截至 2026-06-30，SILSO 黑子数和 F10.7 仍描述第 25 周期状态；
  WSO 极区磁场是第 26 周期候选前兆，但第 25/26 周期极小尚未确立，且极小附近同口径连续观测不足。因此当前不能发布
  第 26 周期强度正式分类、可检验峰值区间、振幅预测或前兆值。
- 科学边界：结论等级为 `evidence_constrained`。对“暂不启动”的置信度为中等；解析器失败、Polar.html 缺失行计数口径
  差异、文献覆盖不足和跨 MWO/WSO 测量体制可比性未验证均被带入最终报告。重新评估至少需要 SILSO 正式确立下一次极小，
  WSO 恢复并补足极小附近连续观测，以及历史标定验证同口径可比性。
- 修复与恢复边界：首次 headed 运行已完成前七个审查阶段，但最终发布阶段把流程阻断文本误计为回答。修复发布阶段的
  过期工具重试和终态分类后，在同一线程、同一任务 workspace 上以空输入恢复，没有增加用户消息。最终发布草稿随后通过
  独立 Evidence 审查并被原样返回。该证据证明已持久化任务能够从原始问题走完全部阶段并正确恢复到发布终态；它不是一次
  在全部修复预先就绪条件下从零开始且不中断的重复运行，后续若代码继续变化，仍应保留全新不间断复验要求。
- 证据目录：`research/review/evals/runs/main_sc26.primary.r41d/`；任务级审查产物位于
  `.r41d-workspace/projects/default/runs/run_01a03700-00fd-7722_c4ce8074/research_review/`。

### CL-20260825-48：第 26 周期初步正式概率预测

本轮把研究目标从“是否允许启动预测”推进为“在现有信息集下发布可更新的正式概率预测”。预测对象为
13 个月平滑 SILSO v2 太阳黑子数峰值。六个替代模型情景覆盖 109–179.4，分别来自近期同行研究和
按历史周期长度—下一周期振幅关系得到的本地基线。由于这些模型共享部分历史数据，本轮不把它们视为
独立观测，而采用等权模型混合，并加入 30 个太阳黑子数单位的模型差异尺度。

固定随机种子完成 2,000,000 次抽样后，集合中位数为 136.8，正式发布值取 140；80% 预测区间为
90–190，95% 预测区间为 65–220。峰值时间中位数约为 2034 年 10 月，80% 区间约为 2033 年中至
2036 年初。第 26 周期弱于第 25 周期当前暂定平滑峰值 160.9 的概率约为 72%，超过历史平均峰值
183 的概率约为 13%。据此分类为中等偏弱。

该结果是极小期前可发布的操作性先验，不声称第 25/26 周期极小或最终极区场已经观测。SILSO 正式
确认下一次极小、同口径极区场和轴向偶极矩形成后，应把相应前兆作为新似然更新本分布并收窄区间。
正式报告见 `SC26_FORMAL_FORECAST_20260825.md`；可复算程序和结构化结果分别见
`evals/sc26_operational_forecast_20260825.py` 与
`evals/runs/sc26.operational_forecast.20260825/result.json`。验收清单已新增 SC26-B07，将该预测设为
主问题交付门。本轮是聚焦科学计算和报告，不是新的 production WebUI 全流程运行。

### 当前主问题的独立复算证据

对已冻结的 14→15 至 23→24 共 10 个周期对重新计算后，极区场前兆与下一周期振幅的全样本 Pearson 相关系数为
`0.6453897478`；前兆模型的留一交叉验证 MAE 为 `36.8878207930`，上一周期 SSN 振幅基线为
`48.2514038622`；两者的 RMSE 比为 `0.8075714180`。逐折留一相关系数的总体标准差为
`0.1157226187`，样本标准差为 `0.1219823507`，范围为 `0.3621418768–0.7912260549`。

这些数值是对冻结历史表的独立复算，不是经 Experiment Result 和 Final Release 通过的新科学产物。样本仅有 10 对，跨 MWO/WSO 测量制度，且尚未估计时序依赖调整后的有效独立样本量。因此它们只支持“前兆关系值得继续检验”，不支持交互作用已成立、因果机制已证实或第 26 周期窄区间预测。截至 2026-06-30，未成熟的是终版前兆分类和窄区间发布；这不阻止 CL-20260825-48 发布宽区间的初步概率预测。

## 新运行条目模板

### CL-YYYYMMDD-NN

- 时间：
- 类型：WebUI 闭环 / WebUI 探针 / 聚焦 Evidence / 模型兼容探针
- 案例与原始问题：
- 入口与用户干预：
- 模型与审查模式：
- 预定范围：
- 实际终态与耗时：
- 用户可见结果：
- 科学结果及置信边界：
- 持久化产物：
- 工程问题：
- 科学问题：
- 处置状态：待修 / 已修待复验 / 已验证 / 科研边界
- 证据目录：

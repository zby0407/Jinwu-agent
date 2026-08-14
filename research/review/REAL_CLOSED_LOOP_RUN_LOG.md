# 真实科研闭环运行记录

本文档记录生产构建 WebUI 和付费模型参与的真实科研运行。记录目标是回答四个问题：
系统实际执行了什么、形成了什么科学结果、暴露了什么问题、后续如何处置。

自动化单元测试、模型兼容探针、真实 WebUI 运行、聚焦 Evidence 调用和科学结论属于
不同证据层。只有经生产 WebUI 自然语言入口启动，并由系统自主完成预定研究范围的运行，
才记为 WebUI 闭环；直接进入单个 Agent 或预置工件的运行单列为聚焦验证。

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

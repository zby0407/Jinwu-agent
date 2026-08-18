# SCI 同行初审研究包：质量合同与验收状态

本文说明当前 AI Scientist 能生成什么、由哪些门禁约束，以及哪些证据仍不足以证明其具备稳定的 SCI 同行初审能力。目标产物是可交给太阳物理同行进行第一轮科学审查的研究包，不是自动生成的投稿论文，也不承诺新颖性、重要性或可发表性。

## 研究包的最低构成

一个可审研究包应当同时包含：边界明确且可证伪的候选假设；modal baseline、机制上不同的 rival、measurement/statistical null 与受控 long-tail candidate；逐条 claim–evidence 对应关系；nearest-prior-art 差分；预先固定的统计分析合同；可复现的实验记录；主动寻找的反对证据和范围限制；以及不超过证据的综合结论。

`blocked`、`clarification`、阴性结果、低功效、证据不足和 `do_not_launch` 都是合法终态。系统不得为了形成完整叙事而把这些状态改写成机制或正结论。

## 已实现的确定性质量合同

### 阶段语义与证据投影

- Hypothesis 产物区分 `scientific_content`、`clarification_status` 和 `blocked_status`。后两者只生成 workflow-status claim，不进入机制、原创性或 integration claim 集。
- Hypothesis adapter 从 task-local evidence register 恢复实际 evidence row，并以该 row 的 `supports`、`opposes` 或 `limits` 关系为权威，不再用状态文件路径替代全部来源。
- 科学内容、证据、上游绑定和阶段均未变化时，时间戳、渲染文字或工作状态计数变化不会形成新的实质 artifact version；科学内容变化仍生成新版本并使下游重新审查。
- Integration 只复制已接受 claim。Final release 对新增数值、强因果、原创性和预测/置信区间措辞执行确定性差分，不能借 synthesis 混入未经审查的高风险内容。

### `AnalysisClaimContractV1`

Planner、Hypothesis 和 Experiment 可在 task-local 工作区记录统一分析合同，其中包括 estimand、独立样本单位与数量、observation cutoff、允许信息集、primary analysis、baseline、validation design、固定 decision rule、缺失/删失/数据修订、测量口径、effect size、不确定性、敏感性、influence analysis 与至少两个结果分支。

活动周级预测不能用月度记录扩大独立样本数；训练相关性不能通过样本外预测门禁；预测设计必须明确 rolling-origin、时间留出、external holdout 或同等样本外路线。阴性或无法裁决的分支与正结果具有同等合同地位。

### `ScientificQualityAssessmentV1`

Kimi Evidence 在每个 review round 的 verdict 前，除 `ReviewAssessmentV1` 外还必须生成恰好一个 claim-level 科学质量 sidecar。每个 claim 的 Evidence Matrix 记录：

- claim component 与是否 load-bearing；
- evidence role、source class、full-text/abstract/dataset/experiment 等 evidence scope；
- directness、scope match、entailment 和精确 locator；
- independence group、单项 quality cap、方法审查、原创性审查和未解决缺口。

Abstract-only、Wiki、review、simulation 或 user premise 不能独立承载 release claim。重复使用同一数据集、同一 simulation family 或派生论文只计一个 evidence family。Load-bearing release claim 需要范围匹配、直接且可定位的 primary evidence，并需要至少两个独立支持家族；存在未解决 evidence gap、scope mismatch 或 entailment defect 时不能获得 `release_candidate`。

### 原创性与 long-tail 分离

Hypothesis 提供独立的三轴 nearest-prior-art 缓存检索工具，至少覆盖核心机制、机制–observable 组合以及最强 rival/null，并按 literature family 去重。候选必须声明 contribution type、`novelty_delta`、nearest prior art、重复风险、检索截止和覆盖缺口。

`mechanism_distance` 不能提高原创性结论。少于三条查询轴、少于八个去重文献家族或缺少 nearest prior art 时，不得标记 `potentially_novel`；自动结果不得主张优先权或“首次”。

### Full-text 与 Evidence 审查

Evidence 可对 artifact 已声明的 task-local PDF、Markdown、HTML 和长文本执行 section search 与定点读取。PDF locator 保留页码，长文本保留 section id；空结果、截断或正文不可得均记录为 gap。Kimi 官方 web search 继续禁用，公网发现仍受项目 two-pass 路径限制。

Kimi Evidence 对每个 load-bearing claim 输出证据矩阵、方法与原创性边界，并在同一回合给出 `accept`、`accept_with_limits`、`revise` 或 `block`。无法由现有证据建立的主张被删去、降级或阻断，不进入额外待审状态。

## 当前验证证据

截至 2026-08-14，当前工作树具备以下证据：

- 相关定向测试通过；最终全量 pytest 为 `3401 passed, 13 skipped, 6 warnings, 8 subtests passed`。
- WebUI 内置 Node 测试 `25 passed`；Next.js production build 和 standalone 组装成功，保留一个已知 Turbopack NFT tracing warning。
- Qwen Max、Qwen Plus、Kimi K3 for Coding 和 DeepSeek V4 Pro 的真实普通回答、单工具、结构化输出、多轮工具四类探针全部通过。Kimi thinking 在普通回答和工具轮中可观察；DeepSeek 多轮工具确认了 `reasoning_content` 回传。
- 可见质量集已经冻结为 10 例，明确要求每例使用全新 8.12.1 会话形成一次 baseline，冻结实现后再运行 3 次，并以 scientific conclusion signature 比较。
- 10 例清单的递归继承与 case filtering 已由确定性测试覆盖，避免选择器被忽略后沿源套件默认路径旁路科研审查。

真实生产路径的 FR-H10 复验保留了两类负面结果。第一例是在提示重新加载前运行，Hypothesis 生成了错误嵌套的 `scientific_quality`，随后三次 Kimi Evidence 委派均未持久化 verdict；运行由 harness 终止，不能计作 baseline 或 Evidence 通过。修复提示、错误归因和清单解析后，第二例在全新会话中进入真实 research review，但 Data producer 连续两次未形成完整 canonical artifact，状态在 data 阶段合法结束为 `blocked`，因此没有进入 Kimi Evidence。该运行的 assessment、ScientificQualityAssessment 和 verdict 均为 0，科学发布状态保持 `do_not_launch`。因项目边界禁止修改 Data Agent 源码，本轮没有越界修复该上游缺口。

在此基础上，另行完成了一次聚焦的真实 Kimi Evidence round。运行跳过发生流中断的 Qwen Supervisor 转发层，直接使用项目 `solar-evidence` prompt、`kimi-k3/kimi-coding` 和同一套 typed Evidence tools 审查已持久化的 FR-H10 Data artifact。Kimi 实际读取了完整 Markdown 报告和 CSV，随后在 round 1 持久化恰好一个 `ReviewAssessmentV1`、一个 `ScientificQualityAssessmentV1` 和一个 `ReviewVerdictV2`；三者绑定同一 artifact。评估将唯一 claim 判为 `contradicted`，方法质量判为 `blocked`，verdict 为 `block`：独立样本单位是 solar cycle，独立样本数为 3，月度重复观测不能扩充 n；CSV 是 synthetic placeholder，声明的 target file 未进入可审来源，原预测区间和 release claim 因而没有有效依据。运行回执为 `research/review/evals/runs/sci.evidence_acceptance.FR-H10.direct.r1/metadata.json`。

这次结果只补齐“真实 Kimi 模型、真实 Evidence 工具、完整三件套持久化”这一证据层。它使用预置 visible fixture，不是完整 WebUI 闭环，不是 Qwen 新生成的 Data 产物，不是 hidden task 或真实数据复现，因此不改变 `do_not_launch` 边界。

随后使用仓库外周期长度交互问题完成一次 production WebUI headed 运行。Qwen 自主生成一个
低置信、可证伪的负交互假设，Kimi 对 Data 与 Hypothesis 分别形成一轮完整三件套；页面
状态为 `released/hypothesis`，总耗时 2248.582 秒。现有来源只支持测量口径和小样本限制，
没有直接支持交互存在；系统没有生成交互估计，也没有声明原创性。运行后进一步把
supporting evidence 为空的 `supported/limited_support` 规范化为 `undecided`。该语义修复
已通过确定性测试，尚未由新的付费 WebUI 会话复验。

以上运行证明失败会被保存且不会伪装为科学结论；它们没有形成改造前 fresh baseline，也没有完成 10 例每例 3 次的冻结后质量回归。

这些结果分别证明合同实现、程序回归、production build 和真实 provider 兼容；它们不证明 claim–evidence 判断正确，也不证明产出的科学结论达到 SCI 同行认可水平。

## 尚未通过的科学验收

以下条件全部完成前，项目状态保持 `do_not_launch`：

1. 十个可见案例完成 fresh baseline 与冻结后的 3/3 质量回归，且程序状态误分类、unsupported critical claim、false novelty priority 均为 0。
2. 人工标注的 Evidence Matrix 覆盖率为 100%，claim–evidence role/entailment 准确率不低于 95%，预埋 counterevidence/scope conflict 检出率为 100%，major defect recall 不低于 90%。
3. 领域、方法统计、复现三个盲评分维度均达到 80/100、无 unresolved critical defect，且相对 baseline 总分至少提高 5 分。
4. 实现和 rubric 冻结后，运行至少 12 个仓库外 hidden 太阳物理任务；hidden prompt 不得参与开发或提示词调优。
5. 至少一个高价值候选完成真实数据复现或外部数据检验，并取得领域专家、方法统计专家、复现审查者三视角多数票。

只有上述外部 hidden、真实复现和专家多数票同时通过，才能称为“SCI 同行初审研究包能力已验证”。

## 关键入口

- 质量合同：`src/research_quality/contracts.py`
- 质量工具：`jw/tools/research_quality.py`
- Review store 与文档读取：`jw/research_review.py`
- Evidence 审查工具：`jw/tools/research_review.py`
- 可见质量集：`research/review/evals/high_quality_review_visible_v1.json`
- 统一评测政策：`research/review/evals/evaluation_policy_v2.json`
- 可见质量记录评分器：`research/review/evals/score_high_quality_records.py`
- 真实模型兼容记录：`research/review/evals/runs/model_compat.sci_quality.20260812.json`

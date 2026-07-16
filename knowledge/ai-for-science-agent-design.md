# AI for Science Agent 设计证据

## 文献证据矩阵

### 开放目标与多种科研操作

Google 的 AI Co-Scientist 从自然语言研究目标和既有证据出发，通过生成、反思、排序、演化、邻近性和元评审等操作反复改进假设。这支持“能力按问题路由”，但不意味着每个问题都要运行所有角色。

来源：Nature 2026, *Accelerating scientific discovery with Co-Scientist*, https://www.nature.com/articles/s41586-026-10644-y

### 减少模板依赖与探索替代路线

AI Scientist-v2 使用渐进式 agentic tree search 和实验管理器，目标之一是降低对人工模板的依赖。其作者同时明确提醒：开放探索成功率可能低于有强模板的任务，且生成代码需要沙箱。这支持在规划阶段保留替代路线和失败分支，但当前 Planner 不执行代码。

来源：arXiv:2504.08066, https://arxiv.org/abs/2504.08066；代码仓库 https://github.com/SakanaAI/AI-Scientist-v2

### 文献与数据迭代，而非单次问答

Robin 将文献检索、假设、实验建议和数据分析连成循环。Nature 论文也报告，实际工具调用常趋于相同顺序，说明“形式上 agentic”不保证真正动态路由。Planner 因而需要记录每一步必要性，而不是仅堆叠工具。

来源：Nature 2026, *A multi-agent system for automating scientific discovery*, https://www.nature.com/articles/s41586-026-10652-y

### 可追溯的主张

Kosmos 强调把报告主张追溯到代码或主要文献。独立审计则指出，在空信号基准上仍可能形成看似合理的错误假设。这支持 evidence basis、来源限制、替代解释和失败分支。

来源：Kosmos, https://arxiv.org/abs/2511.02824；独立审计, https://arxiv.org/abs/2511.13825

### 科学文献检索应把检索、证据汇集和回答分开

PaperQA2 把论文发现、相关证据汇集与带引用回答分开，并对真实文献任务做了人机评估。Planner 的在线工具只返回 OpenAlex 元数据用于发现来源，不把元数据冒充全文证据。

来源：arXiv:2409.13740, https://arxiv.org/abs/2409.13740；仓库 https://github.com/Future-House/paper-qa

### 评价应针对具体能力与中间产物

ScienceAgentBench 从 44 篇同行评议论文构建 102 个真实任务，强调先评价科学流程中的具体任务；当时最佳 Agent 三次尝试也只能独立解决约三分之一任务。2026 年 SciAgentArena 进一步报告 Agent 在明确分析流程上较强，但在开放问题、新颖洞见和持续自导探索上仍不稳定。

来源：ScienceAgentBench, https://arxiv.org/abs/2410.05080；SciAgentArena, https://arxiv.org/abs/2606.12736

### 最终报告要与内部规划结构分离

STORM 将研究和提纲作为成文前阶段，并明确自动报告不等于可直接发表的论文。Open Deep Research 的最终报告约束强调与用户同语种、清晰 Markdown、连贯章节、简明语言和无过程性旁白。DeepResearch Bench II 从信息、分析和呈现三个维度评价专家式报告，ResearcherBench 则分别检查洞见覆盖与主张是否得到引文支持。

这些结果支持为 Planner 保留严格机器合同，同时由通过检查的内容生成独立用户版 Markdown。用户版只呈现研究问题、依据、数据需求、步骤、判断和停止条件；内部字段、枚举、修复记录与工具参数不进入报告。

来源：STORM, https://github.com/stanford-oval/storm；Open Deep Research, https://github.com/langchain-ai/open_deep_research；DeepResearch Bench II, https://github.com/imlrz/DeepResearch-Bench-II；ResearcherBench, https://github.com/GAIR-NLP/ResearcherBench

## 对本 Agent 的直接设计结论

- 大模型负责科研判断；代码不枚举问题类型或方法路线。
- 工具应提供证据、状态与约束，不替模型生成固定科学结论。
- 子问题、证据、数据、路线和报告之间要有可验证引用。
- 候选假设、数据集和评价指标都允许为空；是否需要由问题决定。
- 反思不是泛泛“再想一次”，而是检查替代解释、矛盾、混杂、空信号和证据缺口。
- 人类保留研究目标、风险接受和最终科学判断；保存规划书不等于验证科学真理。
- 用户版报告与内部机器结构分离，并从问题覆盖、分析闭环、表达清晰和证据支持四方面检查。

---
id: "kb_concept_open-source-curation_001"
type: "concept"
title: "高星开源项目设计取舍"
source_type: "expert"
source_ref: "knowledge/open-source-curation.md"
confidence: "medium"
status: "canonical"
valid_range: ""
related_ids: []
provenance: {"imported_by": "import_initial", "imported_at": "2026-07-21T12:53:17+00:00"}
version: 1
created_at: "2026-07-21T12:53:17+00:00"
updated_at: "2026-07-21T12:53:17+00:00"
created_by: "human"
---

## definition

# 高星开源项目设计取舍

以下为 2026-07-16 调研快照。星标用于观察项目采用度，架构取舍同时考虑许可证、维护、测试、依赖和研究规划职责边界。

| 仓库 / 维护者 | 采用度快照 | 许可证 | 活动与测试信号 | 结论 |
|---|---:|---|---|---|
| `karpathy/autoresearch` / Andrej Karpathy | 约 83.7k stars | MIT | 2026 年发布并活跃；仓库很小，未观察到独立 tests 目录 | adapt |
| `Future-House/paper-qa` / FutureHouse | 约 8.6k | Apache-2.0 | 约 933 commits，含 `tests/`、复现 split 与持续维护 | adapt |
| `SakanaAI/AI-Scientist` / Sakana AI | 约 13.8k | Responsible AI 衍生许可 | 2025-12 仍更新；以模板、示例运行和论文工件为主 | adapt, no import |
| `SakanaAI/AI-Scientist-v2` / Sakana AI | 约 6.4k | Other / 非宽松许可 | 2025-12 仍更新；仓库明确警告 LLM 代码执行风险 | adapt, no import |
| `langchain-ai/langgraph` / LangChain | 约 34.6k | MIT | 2026-06 发布 1.2.5，长期维护和发布体系成熟 | adapt patterns |
| `microsoft/autogen` / Microsoft | 约 57.2k | 代码 MIT、文档 CC-BY | 2026-04 仍更新，98 次发布、社区规模大 | watch |
| `Future-House/robin` / FutureHouse | 约 443 | Apache-2.0 | 2026-04 更新；以 notebooks、examples 和论文流程为主，未观察到独立 tests 目录 | adapt narrowly |
| `assafelovic/gpt-researcher` | 约 27.3k | Apache-2.0 | 将问题规划、来源跟踪、汇总和报告分开 | adapt report flow |
| `langchain-ai/open_deep_research` | 约 11.5k | MIT | 含 `tests/`、报告模型与公开评测配置 | adapt reporting constraints |
| `stanford-oval/storm` | 高采用度学术项目 | MIT | NAACL 2024；强调研究、提纲和带引文长报告 | adapt pre-writing separation |

以上活动和目录信号来自对应 GitHub 仓库页面；没有把“高星”当作导入依据，也没有把未观察到测试写成“没有测试”的绝对结论。

## karpathy/autoresearch — adapt

- 约 83.7k stars，MIT；仓库：https://github.com/karpathy/autoresearch
- 可迁移模式：固定可比较评价器、有限可变面、预算、结果日志、保留/丢弃/崩溃状态、简洁性偏好。
- 不迁移内容：它的“先跑基线—改训练代码—按单指标保留”是单 GPU 模型训练实验循环，不是开放研究规划的通用路线。

## Future-House/paper-qa — adapt

- 约 8.6k stars，Apache-2.0，仓库包含持续维护、测试和科学任务复现实例；https://github.com/Future-House/paper-qa
- 可迁移模式：查询改写、论文发现、证据汇集、引用与限制分离。
- 系统采用受限元数据检索和证据合同，保持知识访问边界清晰。

## SakanaAI/AI-Scientist 与 AI-Scientist-v2 — adapt, no code import

- v1 约 13.8k stars，v2 约 6.4k stars；https://github.com/SakanaAI/AI-Scientist 与 https://github.com/SakanaAI/AI-Scientist-v2
- 可迁移模式：新颖性检查、评审、替代路线搜索、管理器约束。
- 不直接导入：许可证不是宽松 MIT/Apache；系统主要面向自动执行机器学习研究，且明确提示 LLM 代码执行风险。

## langchain-ai/langgraph — adapt architecture patterns

- 约 34.6k stars，MIT，2026 年仍活跃；https://github.com/langchain-ai/langgraph
- 可迁移模式：显式状态、可恢复执行、人类介入和轨迹可观测性。
- 研究规划状态由 Pi Tools 与确定性校验层管理，保持依赖规模和运行边界清晰。

## microsoft/autogen — watch

- 约 57.2k stars，代码 MIT、文档 CC-BY；https://github.com/microsoft/autogen
- 多 Agent 编排生态成熟；研究规划 Agent 保持单一规划职责，通过清晰合同接入上层科研流程。

## Future-House/robin — adapt scientific loop, not domain template

- 约 443 stars，Apache-2.0，2026 年活跃；https://github.com/Future-House/robin
- 可迁移模式：文献与数据结果之间迭代、候选排序和证据更新。
- 不迁移疾病—实验—候选药物的固定领域流程。

## 成熟研究报告项目 — adapt report quality

- GPT Researcher 将问题规划、资料获取、来源跟踪和最终报告聚合分开。可迁移模式是“内部研究结构服务于最终报告”，而不是把内部对象原样展示给用户；https://github.com/assafelovic/gpt-researcher
- Open Deep Research 把资料压缩与最终报告写作分开，并要求最终报告使用用户语言、清晰 Markdown、连贯章节和非自指表达。可迁移模式是由确定性结果直接形成用户版内容，并限制无收益检索；https://github.com/langchain-ai/open_deep_research
- STORM 把前期研究、提纲和带引文文章分为不同阶段，同时明确自动生成内容仍需要编辑。可迁移模式是把研究规划书视为可执行的前期产物，不冒充论文或已验证结论；https://github.com/stanford-oval/storm
- DeepResearch Bench II 将专家报告质量拆为信息、分析和呈现维度；ResearcherBench 进一步分别检查关键洞见覆盖与主张—引文支持。可迁移模式是分别评价规划覆盖、科学判断闭环、表达质量和证据可追溯性；https://github.com/imlrz/DeepResearch-Bench-II 与 https://github.com/GAIR-NLP/ResearcherBench

## 本地采用原则

系统采用可审计的设计模式。Tools 与 Skills 在项目目录中实现；网络工具只读、输入输出有界、失败状态显式记录。研究规划工具的权限范围限定为资料发现、来源核验、数据检查、计划验证与保存。外部项目名称只保留在本参考材料中，不进入本系统的契约、工具或 Skill 命名。

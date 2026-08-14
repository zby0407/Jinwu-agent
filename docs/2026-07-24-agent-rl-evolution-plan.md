# JW 引入强化学习的演进方案

> 版本：v0.1 · 日期：2026-07-24 · 状态：设计提案，尚未实施
>
> 主体：科学假设子 Agent 2.0；扩展范围：统一科研主 Agent、研究规划、自动实验、证据审查与知识管理

## 0. 执行摘要

在现有 JW 多 Agent 科研闭环中引入强化学习是可行的，但不应从“对最终长回答做单一分数优化”开始。当前最适合、也最需要优先引入学习策略的是科学假设子 Agent：让它学习在证据不完整、候选机制竞争和实验预算有限的情况下，下一步应补什么证据、拆分或合并哪个候选、寻找什么反例、选择哪项最有区分力的检验，以及何时停止或降低置信度。

本方案的核心判断是：

1. **先建设科学质量验证器，再训练 RL。** 当前合同可以约束字段完整性，但尚不能可靠判断事实性机制陈述是否有证据、两个候选是真正竞争还是可以共存、所谓反例是否真实，以及下一项检验是否真的有区分力。直接把当前 validator 当奖励，会鼓励模型“填满 schema”，而不是提高科学可靠性。
2. **先训练操作策略，不先训练整段自然语言。** 第一版 RL policy 只决定下一项科研操作，基础大模型继续负责生成文本。这样动作空间短、奖励可归因、训练更稳定，也更容易嵌入现有 Pi/Qwen Agent。
3. **把假设组合升级为可检查的假设图。** 证据、观测、机制、前提、预测、反例、混杂因素和下一项检验都成为带类型节点；节点之间显式记录支持、反对、依赖、区分、等价、嵌套和可共存关系。
4. **采用硬门禁与多目标奖励，而非一个可被钻空子的总分。** 假引用、无依据事实、越界泛化和不可证伪假设先由确定性门禁拦截；通过后再评价证据覆盖、机制区分、反例覆盖、校准、新颖性和检验价值。
5. **演进顺序为：Verifier 2.0 → Best-of-N 基线 → SFT/DPO → 操作级 RL → 分层多轮 RL → 开放式质量多样性搜索。**

本方案预计先完成一个假设 Agent 专项 POC。只有当 RL 在隐藏任务上稳定优于“当前 Agent + Best-of-N + 强验证器”和 DPO 基线，且没有增加无依据主张或造成候选多样性塌缩，才进入全系统 RL 化。

---

## 1. 背景与目标

### 1.1 当前系统定位

JW 已经形成“研究规划 → 数据与实验 → 假设生成 → 证据审查与更新 → 报告与知识沉淀”的科研闭环。它的优势不是单次问答，而是：

- 有明确的专业子 Agent 边界；
- 有结构化请求、响应、排序和冻结合同；
- 有真实实验产物、哈希和运行状态门禁；
- 有证据登记簿、知识库和跨运行产物；
- 能把下一步检验重新送回研究与实验环节。

这些条件比从零训练一个通用 RL Agent 更有利，因为现有工具调用、状态变化和产物已经可以形成轨迹。

### 1.2 引入 RL 的目标

RL 的目标不是让回答更长、更像论文或更有说服力，而是优化以下可观察行为：

- 在有限预算内提高真实、可验证假设的发现率；
- 降低无依据事实、假引用和越界泛化；
- 保持多个机制路线，避免同义改写和单一模式塌缩；
- 主动寻找能削弱自己的反例；
- 选择更能区分竞争机制的下一项检验；
- 在加入或删除证据后合理更新置信度；
- 减少无效检索、重复反思和不产生信息增益的工具调用；
- 将成功与失败轨迹转化为后续可复用的训练数据。

### 1.3 非目标

本阶段不追求：

- 用 RL 取代确定性合同、哈希、沙箱和数据完整性检查；
- 让 Agent 自己给自己的最终答案打分并作为唯一奖励；
- 直接宣称生成了新科学发现；
- 用单次主观 Elo 或 LLM judge 分数证明科学能力；
- 一次性微调所有子 Agent；
- 在缺少冻结隐藏评测和可追溯结果检查时进行在线自我修改。

---

## 2. 可行性与文献依据

### 2.1 Agent RL 在什么条件下有效

Search-R1、ReTool、WebRL 等工作表明，当任务具有可执行工具、可核验结果和相对明确的终局反馈时，RL 可以改善检索、工具选择和多步交互：

- Search-R1：[Training LLMs to Reason and Leverage Search Engines with Reinforcement Learning](https://arxiv.org/abs/2503.09516)
- ReTool：[ReTool: Reinforcement Learning for Strategic Tool Use in LLMs](https://arxiv.org/abs/2504.11536)
- WebRL：[Training LLM Web Agents via Self-Evolving Online Curriculum Reinforcement Learning](https://arxiv.org/abs/2411.02337)

Agent Lightning 进一步把既有 Agent 执行抽象成 MDP，并通过训练—执行解耦和分层 credit assignment 处理复杂 Agent 轨迹。这一架构与 JW 的现有 Pi 工具调用和运行记录较为契合：

- [Agent Lightning: Train ANY AI Agents with Reinforcement Learning](https://arxiv.org/abs/2508.03680)

### 2.2 科学任务仍远未解决

科学任务比 SQL、数学和网页操作更开放，多个答案可能同时合理，终局奖励也更延迟：

- ScienceAgentBench 包含来自 44 篇论文的 102 个真实科学任务，最佳系统的独立解决率仍只有约三分之一：[ScienceAgentBench](https://arxiv.org/abs/2410.05080)
- AstaBench 覆盖 2400 多个科研任务和大量 Agent，显示通用科研助手仍有明显能力缺口：[AstaBench](https://arxiv.org/abs/2510.21652)
- BLADE 显示 Agent 对开放数据分析中的概念变量操作和统计模型覆盖仍很弱：[BLADE](https://arxiv.org/abs/2408.09667)
- SciAgentArena 显示 Agent 更擅长明确流程，对开放探索、持续自导和新颖洞见仍不稳定：[SciAgentArena](https://arxiv.org/abs/2606.12736)

因此，JW 应先把科学流程拆成有中间证据的子任务，而不是期待端到端 RL 自动涌现“科学家能力”。

### 2.3 假设生成已有正面信号，但评价是瓶颈

HypoBench 提供了跨真实与合成数据的系统评测；随着任务复杂度提高，最佳方法在合成任务中只恢复了 38.8% 的真实假设，说明假设覆盖、多样性和评价仍有很大空间：

- [HypoBench: Towards Systematic and Principled Benchmarking for Hypothesis Generation](https://arxiv.org/abs/2504.11524)

Google AI Co-Scientist 使用生成、反思、排序、演化和 tournament search 扩展 test-time compute，并在部分生物医学任务上进行了专家和实验验证。它证明了“候选池 + 比较 + 演化”的可行性，但论文也明确说明内部 Elo 属于自动评价而非独立真值：

- [Towards an AI co-scientist](https://arxiv.org/abs/2502.18864)

Graph-PRefLexOR 用图结构组织机制探索、关系构造、模式提取和假设综合，并使用 GRPO 训练。它报告了更好的可追溯性和语义多样性，为“假设图 + RL”提供了直接参考，但目前是 2026 年 7 月的新预印本，领域集中在材料科学，应作为探索证据而非成熟结论：

- [Graph-Native Reinforcement Learning Enables Traceable Scientific Hypothesis Generation through Conceptual Recombination](https://arxiv.org/abs/2607.00924)

### 2.4 多轮 RL 与自评奖励的风险

RAGEN 在多轮 Agent RL 中观察到 reward variance cliff、梯度尖峰和 Echo Trap，并指出如果缺少细粒度、推理相关的奖励，Agent 容易学到浅层策略或虚构思维：

- [RAGEN: Understanding Self-Evolution in LLM Agents via Multi-Turn Reinforcement Learning](https://arxiv.org/abs/2504.20073)

“同一个模型生成、评审并给自己奖励”尤其危险。近期研究中，自博弈把 judge 通过率从 0.72 提升到 0.94，但隐藏真实准确率仍约为 0.20；让 judge 在看到候选前独立作答，才显著降低了误判：

- [More Convincing, Not More Correct: Self-Play Reward Hacking of Reference-Free LLM Judges](https://arxiv.org/abs/2607.05904)

因此，本方案要求：

- 训练奖励与最终隐藏评测分离；
- judge 尽量先独立判断，再查看候选；
- 关键事实由检索、执行或确定性程序核验；
- 专家偏好和真实实验结果保留为高价值锚点；
- RL policy 不得访问隐藏证据和隐藏验收规则。

---

## 3. 现有科学假设 Agent 审计

### 3.1 已有优势

科学假设 Agent 1.0 已经建立了很好的工程基础：

- 请求、响应、排序、组合和终态合同；
- 支持、反对、限制和证据缺口的角色区分；
- 上游实验终态和哈希核验；
- 假设陈述、机制、预测、替代解释、混杂因素、证伪条件和下一项检验；
- 无支持证据或存在反对证据时不能标记高置信度；
- 数值门槛、新颖性表述和数据覆盖范围的部分门禁；
- 机器事实与读者版 Markdown 分离；
- 失败回滚和运行产物留存。

这些能力应保留为 RL 的环境和安全边界，而不是被训练代码替代。

### 3.2 当前样例暴露的主要问题

截至 2026-07-24 的仓库快照中，`hypothesis/runs/` 只有 3 份正式组合，且排序均为空。样本量不足以直接训练 RL，但已经足以发现验证边界问题。

典型样例中：

- 证据登记簿只有一条未核验的用户观察；
- 候选的 `physical_basis` 却引用具体论文并写出“观测已证实”；
- 同一候选的 `supporting_evidence` 为空；
- 预测中出现相关系数阈值、跨周期样本范围等未绑定依据；
- 物理机制与测量定义偏差被当成互斥候选，但它们可能同时成立；
- 部分机制主张、必要前提和假设之间存在语义张力；
- 反例表主要来自已有证据缺口，没有执行真正的反例发现。

这说明当前的主要矛盾是：

> **合同完整性已经较强，但 claim 级科学真实性、机制关系与证据敏感性仍较弱。**

### 3.3 代码层缺口

当前实现中的关键缺口包括：

1. `mechanism.physical_basis`、`required_premises`、`assumptions`、`observable` 等仍是自由文本，没有逐项证据锚点。
2. 证据角色检查主要覆盖 `supporting_evidence` 和 `opposing_evidence`，不能阻止无依据事实被写入其他字段。
3. 去重主要依赖逐字相等，不能识别语义改写、上下位关系和机制可组合性。
4. 数值门槛扫描只覆盖部分字段。
5. 知识库 grounding 为 warning，知识库异常时静默降级。
6. `pairwise_distinctions` 只要求文本存在，不能验证所述差异是否真实。
7. 反例表由反对证据和证据缺口自动汇总，不等于主动发现反例。
8. 排序可以为空；一旦排序，又要求全序，可能在证据不足时制造虚假精度。
9. 当前没有对“删除支持证据后置信度是否下降”进行行为检查。
10. 当前没有独立隐藏验证集来识别 reward hacking。

---

## 4. 总体设计原则

### 4.1 科学门禁与学习策略分离

系统分为两个相互独立的平面：

**确定性控制平面**

- 合同和引用闭合；
- 数据、运行状态与哈希完整性；
- 证据来源存在性；
- 数值与范围门禁；
- 安全、预算和文件边界；
- 保存、回滚与审计。

**可学习决策平面**

- 下一步执行哪类科研操作；
- 检索哪类证据；
- 是否生成、拆分、合并或保留候选；
- 哪个反例最值得调查；
- 哪项检验信息增益最高；
- 是否继续、停止或请求人工判断。

RL 只能优化第二个平面，不能覆盖第一个平面。

### 4.2 局部可验证优先于终局自评

优先奖励有客观反馈的局部动作：

- 引用是否存在；
- 摘录是否蕴含对应主张；
- 预测是否明确绑定可观测量；
- 下一项检验是否为多个候选给出不同预期；
- 加入反对证据后置信度是否下降；
- 工具执行是否成功；
- 实验结果是否可复现。

“是否像一个重大发现”“是否非常有创意”等开放判断不能成为早期主要奖励。

### 4.3 保持候选集合，而非过早收敛

科学假设常常不是互斥单选题。系统应维护：

- 当前支持较强的候选；
- 尚缺证据但值得保留的候选；
- 可与其他机制组合的候选；
- 已被削弱但仍未证伪的候选；
- 测量、数据处理和技术失败解释；
- 明确被淘汰及其淘汰原因。

RL 的目标不是尽快选出一个“赢家”，而是在预算约束下提高组合的解释覆盖、可区分性和下一步实验价值。

### 4.4 训练与评价隔离

- 训练任务、公开验证任务、隐藏测试任务严格分开；
- 相同科学问题的改写不得跨 split；
- 同一机制家族、论文谱系或数据切片尽量分组切分；
- 新颖性评测采用时间切分；
- hidden verifier、hidden evidence 和专家锚点不得暴露给 policy；
- 训练使用的 LLM judge 与最终评测 judge 至少在提示、模型或证据视图上隔离。

---

## 5. 目标总体架构

```text
用户研究目标
    │
    ▼
统一科研主 Agent
    │  维护 Research State / 预算 / 停止条件
    ├───────────────┬────────────────┬────────────────┐
    ▼               ▼                ▼                ▼
规划 Agent      数据/实验 Agent    假设 Agent 2.0    知识与证据层
确定路线          产生真实结果       假设图 + 策略      检索/来源/历史
    │               │                │                │
    └───────────────┴───────┬────────┴────────────────┘
                            ▼
                   独立 Verifier 集合
           合同 / 引用 / 蕴含 / 反例 / 校准 / 新颖性
                            │
                            ▼
                  Trajectory & Reward Store
                            │
          ┌─────────────────┴──────────────────┐
          ▼                                    ▼
     离线 SFT / DPO                    操作级 / 分层 RL
          │                                    │
          └─────────────────┬──────────────────┘
                            ▼
                     版本化 Policy Registry
```

总体架构采用训练—执行解耦：

- 在线 Agent 继续通过现有工具和合同运行；
- 每一步记录标准化 observation、action、tool result、validator result 和预算；
- 训练系统异步读取脱敏轨迹；
- 新 policy 必须离线评测和 shadow run；
- 通过发布门后才进入实际 Agent；
- 能随时回退到 prompt-only 或前一稳定版本。

---

## 6. 科学假设 Agent 2.0：核心方案

### 6.1 假设图

假设组合从一组长文本候选升级为 typed graph。

#### 节点类型

| 节点 | 含义 |
| --- | --- |
| `research_question` | 当前需要解释或预测的问题 |
| `observation` | 已观测事实或实验结果 |
| `claim` | 可被证据支持或反对的事实性陈述 |
| `mechanism` | 因果或物理机制 |
| `premise` | 机制成立所需前提 |
| `assumption` | 当前未证实但显式采用的假设 |
| `prediction` | 机制导出的可观测预测 |
| `confounder` | 可能制造相同信号的混杂因素 |
| `counterexample` | 能削弱候选的观测、结果或边界情形 |
| `test` | 下一项检验或实验 |
| `evidence` | 已绑定文献、实验、数据或用户材料 |
| `gap` | 尚未获得的关键证据 |

#### 边类型

| 边 | 含义 |
| --- | --- |
| `supports` | 证据支持主张 |
| `opposes` | 证据反对主张 |
| `limits` | 证据限制适用范围或强度 |
| `requires` | 机制依赖某前提 |
| `predicts` | 机制产生可观测预测 |
| `weakens_if` | 某结果会削弱候选 |
| `distinguishes` | 预测或检验可以区分候选 |
| `confounded_by` | 结论可能受混杂因素影响 |
| `equivalent_to` | 两候选实质等价 |
| `competes_with` | 两候选对同一现象给出竞争解释 |
| `composable_with` | 两候选可以共同成立 |
| `nested_in` | 一个候选是另一个的特殊情形 |
| `updates` | 新版本更新旧候选 |

#### Claim 级溯源

每个事实性 `claim`、`mechanism`、`premise` 和定量 `prediction` 必须包含：

```json
{
  "claim_id": "claim_c1_001",
  "text": "……",
  "epistemic_status": "verified_fact | supported_inference | assumption | gap",
  "evidence_ids": ["ev_001"],
  "source_spans": [
    {
      "evidence_id": "ev_001",
      "quote_or_data_locator": "……"
    }
  ],
  "scope": "……"
}
```

若没有证据，`epistemic_status` 只能是 `assumption` 或 `gap`，不得用自然语言引用论文名称来绕过证据登记簿。

### 6.2 标准工作流

假设 Agent 2.0 的一次 episode 建议拆成以下阶段：

1. **问题定界**
   判断问题是解释、预测、机制比较还是证据更新；明确适用范围和不可替代的输入。
2. **证据建图**
   把已核验观测、实验和文献拆成 claim 与 evidence 节点，不把来源发现当作支持证据。
3. **机制空间展开**
   生成主机制、替代机制、测量解释、数据处理解释和技术失败解释。
4. **关系审查**
   判断候选是等价、竞争、可组合还是嵌套；执行合并或拆分。
5. **预测与证伪**
   为每个候选生成独立于机制陈述的可观测预测，并说明什么结果会削弱它。
6. **反例搜索**
   主动查找已知反例、适用边界、负结果和与预期方向相反的观测。
7. **下一项检验选择**
   比较候选在不同检验结果下的预期，选择信息增益高、成本可接受的检验。
8. **置信度更新**
   根据支持、反对、缺口、范围和证据可靠性进行定性校准。
9. **组合终止**
   当继续检索的预期收益低于成本、达到预算、证据不足需人工输入或形成可执行下一步时停止。

### 6.3 RL 状态

建议将状态表示为：

```text
s_t = {
  research_question,
  scope_and_constraints,
  verified_evidence_graph,
  current_hypothesis_graph,
  unresolved_conflicts,
  uncovered_mechanism_families,
  pending_evidence_gaps,
  previous_actions,
  validator_feedback,
  remaining_budget
}
```

为了控制上下文长度，policy 不直接接收全部原始文献，而是接收：

- 文献与实验的稳定 id；
- 经过核验的短摘录；
- claim—evidence 关系；
- 可按需展开的检索句柄；
- 最近若干步操作和状态摘要。

### 6.4 RL 动作

第一阶段使用有限操作集合：

```text
PROPOSE_MECHANISM
ADD_MEASUREMENT_ALTERNATIVE
BIND_EVIDENCE
VERIFY_CLAIM
SEARCH_COUNTEREVIDENCE
MARK_AS_ASSUMPTION
NARROW_SCOPE
SPLIT_CANDIDATE
MERGE_CANDIDATES
MARK_RELATION
ADD_PREDICTION
ADD_FALSIFIER
DESIGN_DISCRIMINATING_TEST
REVISE_CONFIDENCE
REQUEST_EXPERT_REVIEW
STOP
```

动作参数继续由模型生成，但先通过 action schema 和确定性前置条件检查。例如：

- 没有两个候选时不能执行 `MERGE_CANDIDATES`；
- 没有证据 id 时不能执行 `BIND_EVIDENCE`；
- 不得把技术失败登记为科学反对证据；
- `REVISE_CONFIDENCE` 必须引用本轮发生变化的证据或关系；
- `STOP` 必须满足完成条件或给出明确 blocker。

### 6.5 为什么先训练 controller

直接更新整段假设文本存在四个问题：

- episode 很长，难以把终局奖励归因到某一步；
- 文本动作空间巨大，容易奖励投机；
- 科学问题往往没有唯一参考答案；
- 现有数据量不足以稳定训练完整 LLM policy。

操作级 controller 可以是：

- 小模型或 LoRA policy；
- 基础模型上的 action head；
- 工具调用 token 的轻量微调；
- 早期甚至可以是 contextual bandit。

基础模型仍负责在指定动作下生成候选文本，生成结果经过 Verifier 2.0 后才改变环境状态。

---

## 7. Verifier 2.0

### 7.1 必须新增的验证器

#### 1. Claim provenance verifier

- 检查所有事实性字段是否有 evidence id 或明确标记为 assumption/gap；
- 检查来源是否真实存在；
- 检查引用 span 是否可定位；
- 检查证据角色是否与支持、反对或限制一致。

#### 2. Evidence entailment verifier

- 在不看候选结论的情况下读取证据；
- 先独立抽取证据支持的主张；
- 再比较候选 claim 是否被蕴含、反对或仅相关；
- 输出 `entailed / contradicted / insufficient / scope_mismatch`；
- 高风险结果作为最终输出进入抽样检查，不形成内部等待节点。

#### 3. Semantic relation verifier

识别候选之间的：

- 同义改写；
- 同一机制不同参数化；
- 上下位或嵌套关系；
- 真正竞争关系；
- 可共同成立关系；
- 测量解释与物理解释的层级差异。

该验证器不能只依赖 embedding 距离，应结合结构化机制、前提和预测进行 NLI/关系判断。

#### 4. Falsifiability verifier

检查：

- 预测是否可观测；
- 削弱条件是否与候选核心机制相关；
- 证伪条件是否只是“实验失败”或“不支持”之类循环表述；
- 数值门槛是否有来源；
- 是否存在无论结果如何都不会降低置信度的不可证伪写法。

#### 5. Discriminating-test verifier

一项检验至少应满足：

- 覆盖两个或更多候选；
- 每个候选给出可比较的预期信号；
- 至少有两个候选的预期不同；
- 预期差异落到可观测量；
- 结果能够触发置信度或候选关系变化；
- 数据和成本要求在可执行范围内。

#### 6. Counterexample verifier

- 反例必须是已观测结果、可构造情形或明确边界，而不是把 evidence gap 改写一遍；
- 反例与候选之间必须有可解释的削弱关系；
- 搜索不到反例只能写“未找到”，不能转化为支持；
- 正负结果和未发表负结果的缺失风险应显式记录。

#### 7. Novelty verifier

采用 retrieve → filter → facet rerank → compare：

- 按问题、机制、对象、方法和预期结果多个 facet 检索；
- 先找最相近工作，再判断新增部分；
- 新颖性输出为 `known / incremental / potentially_novel / unverifiable`；
- 没有完成检索时不得声称首次提出。

相关参考：

- [Scideator](https://arxiv.org/abs/2409.14634)
- [Literature-Grounded Novelty Assessment of Scientific Ideas](https://arxiv.org/abs/2506.22026)

#### 8. Calibration verifier

对同一任务执行证据扰动：

- 删除一条支持证据；
- 注入一条已核验反对证据；
- 把证据替换为仅相关但不支持的来源；
- 缩小或扩大数据覆盖范围；
- 把实验终态从科学空结果改为技术失败。

检查假设置信度和状态是否按正确方向变化。

### 7.2 隐藏对抗测试

隐藏测试至少覆盖：

1. **证据删除测试**：移除核心支持后，置信度应下降或证据缺口应增加。
2. **反对证据注入测试**：不能忽略冲突并保持原有高置信度。
3. **释义不变性测试**：问题和证据改写后，核心关系应稳定。
4. **假引用陷阱**：格式正确但不存在的论文必须被拒绝。
5. **来源替换陷阱**：主题相关但不支持该 claim 的文献不能获得支持分。
6. **范围扩张陷阱**：局部数据不能泛化到所有活动周。
7. **无依据数字陷阱**：无来源的 p 值、样本数和阈值必须被识别。
8. **可组合性测试**：测量偏差与物理机制可以共同成立。
9. **技术失败测试**：运行失败不能变成科学反证。
10. **停止策略测试**：预算不足或证据不可得时应停止并请求人工输入。
11. **judge anchoring 测试**：judge 必须先形成独立判断，再查看候选。
12. **跨运行稳定性测试**：相同证据下的核心机制关系不应因表述随机变化。

---

## 8. 奖励与约束设计

### 8.1 不使用单一开放式总分

不推荐以下奖励：

- schema 是否通过；
- 候选数量；
- 引用数量；
- 回答长度；
- LLM judge 的“总体科学性”；
- 自评 Elo；
- 最终排名是否明确；
- 是否使用了更多工具。

这些指标都容易被策略利用。

### 8.2 硬约束

建议使用 constrained RL 或词典序优化。只有全部硬门禁通过，轨迹才进入质量评分：

```text
G_contract
G_source_exists
G_claim_grounded
G_no_fake_citation
G_scope_valid
G_numeric_grounded
G_falsifiable
G_safety
```

若任一关键门失败：

- 当前动作不写入权威假设图；
- 返回结构化错误；
- 轨迹获得局部负奖励；
- policy 可以修复，但不能靠后续长文本抵消严重错误。

### 8.3 多目标奖励向量

通过门禁后记录：

```text
r = [
  r_evidence_precision,
  r_claim_coverage,
  r_mechanism_coverage,
  r_semantic_distinctness,
  r_composability_accuracy,
  r_falsifiability,
  r_counterexample_quality,
  r_test_discrimination,
  r_confidence_calibration,
  r_novelty_grounding,
  r_expert_preference,
  r_efficiency
]
```

各分量含义：

- `r_evidence_precision`：被绑定证据确实支持对应 claim 的比例；
- `r_claim_coverage`：重要事实性主张完成溯源的程度；
- `r_mechanism_coverage`：是否覆盖已知主要机制家族和合理替代解释；
- `r_semantic_distinctness`：候选是否机制上独立，而非文字多样；
- `r_composability_accuracy`：是否正确识别可共存、竞争和嵌套关系；
- `r_falsifiability`：预测和削弱条件是否可观测、可执行；
- `r_counterexample_quality`：是否找到真实、相关且能改变判断的反例；
- `r_test_discrimination`：下一项检验对候选集合的预期信息增益；
- `r_confidence_calibration`：证据扰动后置信度变化是否合理；
- `r_novelty_grounding`：新颖性判断是否有文献检索依据；
- `r_expert_preference`：专家在盲评中的成对偏好；
- `r_efficiency`：达到同等质量所需的 token、工具调用和实验成本。

### 8.4 下一项检验的信息价值

可把候选集合视为当前不确定性分布。对于候选检验 `T`，理想分数是预期信息增益：

```text
EIG(T) = H(C | E) - E_y[H(C | E, y)]
```

其中：

- `C` 是候选机制；
- `E` 是当前证据；
- `y` 是检验可能产生的结果；
- `H` 是候选不确定性。

早期不必依赖精确概率，可用定性近似：

- 不同候选是否给出不同方向的预期；
- 结果是否会改变候选关系或置信度；
- 检验是否可执行；
- 成本和数据可得性；
- 是否同时消除多个证据缺口。

### 8.5 Reward aggregation

推荐顺序：

1. 硬门禁；
2. 证据可靠性和范围正确性；
3. 可证伪性、反例与区分力；
4. 机制覆盖和多样性；
5. 专家偏好；
6. 效率。

若必须输出标量训练信号：

- 权重必须在训练前冻结；
- 定期轮换非关键质量权重，降低单一指标投机；
- 隐藏评测不用相同权重；
- 保留每个分量，不只保存总分；
- 发布判断基于多指标门槛，不根据一个平均数。

---

## 9. 训练数据与轨迹体系

### 9.1 任务包

每个训练或评测任务包含：

```text
task_id
research_question
scope
verified_evidence
known_mechanism_families
candidate_relations
known_counterexamples
evidence_gaps
available_tests
expected_test_signatures
hidden_evidence
source_cutoff
expert_notes
```

不是每个任务都需要完整 gold hypothesis。可以只提供：

- 已知机制家族覆盖；
- claim—evidence 关系；
- 硬错误标签；
- 候选间关系；
- 下一项检验的成对偏好；
- 专家对两个组合的相对评价。

### 9.2 数据来源

1. 现有假设 Agent 的运行轨迹；
2. 已核验的研究规划和自动实验产物；
3. 太阳活动周领域文献与知识库；
4. 专家撰写或修订的候选组合；
5. Best-of-N 产生的多条候选轨迹；
6. 规则生成的对抗负例；
7. 证据删除、替换、矛盾注入和范围扰动；
8. 可控合成任务，用于提供完整 ground truth；
9. HypoBench 等公开任务的许可兼容子集；
10. 后续真实用户反馈与实验结果。

### 9.3 负例应覆盖

- 编造或错配论文；
- 有引用但证据不蕴含 claim；
- 只有相关性却写成因果；
- 无依据的精确阈值；
- 同义候选；
- 把可共存机制写成互斥；
- 不可观测预测；
- 循环证伪条件；
- 把技术失败写成反证；
- 把未找到反例写成支持；
- 删除证据后置信度不变；
- 为提高 judge 分数而增加术语和冗长说明；
- 重复调用工具但没有状态增益。

### 9.4 轨迹格式

建议统一记录：

```json
{
  "episode_id": "…",
  "task_id": "…",
  "policy_version": "…",
  "step": 3,
  "observation_ref": "…",
  "action": {
    "type": "SEARCH_COUNTEREVIDENCE",
    "arguments": {}
  },
  "tool_result_ref": "…",
  "state_delta_ref": "…",
  "validator_results": {},
  "reward_vector": {},
  "terminal_status": null,
  "token_cost": 0,
  "wall_time_ms": 0
}
```

原始文献和大型状态使用内容寻址引用，不在每一步重复复制。

### 9.5 数据切分

- 按研究问题族切分，而不是随机切分文本；
- 相同论文、同一机制改写和相邻数据窗口放在同一 split；
- 太阳活动周可按周期或时间段做留一评估；
- 新颖性采用论文发表时间 cutoff；
- 合成任务改变表面名称和数值，测试是否真正发现结构；
- 对外报告同时给出 in-domain、mechanism-held-out 和 time-held-out 结果。

---

## 10. 训练路线

### 阶段 0：只建设评测与 Verifier

目标：

- 完成 hypothesis graph v2 合同；
- 修复 claim 级证据、语义关系、反例、校准和隐藏测试；
- 建立稳定基线；
- 不做 RL。

退出条件：

- 现有真实样例中的无依据机制引用能被检测；
- 可组合机制不再被强制视为互斥；
- 证据删除和矛盾注入能触发合理更新；
- validator 在专家标注集上的主要错误类型可量化。

### 阶段 1：Best-of-N + 确定性筛选

目标：

- 同一任务采样多个候选组合；
- 使用硬门禁、独立 judge 和专家偏好选择；
- 验证“增加 test-time compute”在本领域是否有效；
- 生成后续偏好数据。

此阶段是 RL 必须超过的强基线。

### 阶段 2：SFT 与 DPO

目标：

- 用专家修订轨迹进行 SFT；
- 用候选组合、下一项检验和科研动作的成对偏好进行 DPO；
- 先学会基本科学纪律和偏好，再进入在线探索。

参考：

- [Direct Preference Optimization](https://arxiv.org/abs/2305.18290)

### 阶段 3：操作级 contextual bandit / RL

训练对象：

- 下一动作选择；
- 候选合并/拆分；
- 检索与反例搜索；
- 下一项检验选择；
- 停止决策。

基础 LLM 冻结或只训练小型 LoRA。episode 保持较短，频繁产生新 rollout，并使用多样初始状态。

### 阶段 4：分层多轮 RL

把 episode 拆成 option：

```text
evidence_option
hypothesis_space_option
counterexample_option
test_selection_option
calibration_option
```

每个 option 有局部奖励，最终组合有终局奖励。可参考 Agent Lightning 的训练—执行解耦和 credit assignment；多轮训练优先考虑带 critic 和稳定化措施的 PPO/actor-critic。GRPO 仅在短任务、奖励稳定的 sandbox 中对照，不直接作为默认方案。

### 阶段 5：开放式质量多样性搜索

在基础可靠性稳定后，引入：

- tournament selection；
- graph recombination；
- novelty archive；
- mechanism-family coverage；
- quality-diversity / MAP-Elites 风格候选库；
- 专家 seed hypothesis；
- 高成本实验的主动学习。

这一阶段不以“找到一个最高分候选”为目标，而是维护若干高质量、机制上不同的候选。

---

## 11. 首个 POC：太阳活动周假设 Agent

### 11.1 研究问题

验证操作级 RL 是否能在相同证据预算下，提高：

- claim 证据可靠性；
- 机制候选覆盖；
- 候选关系判断；
- 反例发现；
- 下一项检验的区分力；
- 证据变化后的置信度校准。

### 11.2 POC 数据

建议先构建 60–100 个太阳活动周任务，覆盖：

- Babcock–Leighton 与通量输运机制；
- 极区场前兆；
- Waldmeier 效应及跨周期稳定性；
- 周期长度、振幅和上升率关系；
- 南北半球不对称；
- F10.7 与太阳黑子数关系漂移；
- 数据版本与平滑窗口敏感性；
- 小样本相关、伪相关和范围外泛化；
- 科学空结果与技术失败；
- 组合机制和非互斥解释。

每题提供 5–15 条可定位的证据片段，并保留隐藏证据、反例或机制关系用于最终评测。首轮 task 数量是工程起点，完成 pilot 后再按方差和错误分布确定正式规模。

### 11.3 对照组

| 组别 | 系统 |
| --- | --- |
| B0 | 当前 prompt-only 假设 Agent |
| B1 | 当前 Agent + Verifier 2.0 |
| B2 | Best-of-N + Verifier 2.0 |
| B3 | SFT |
| B4 | SFT + DPO |
| R1 | 冻结 LLM + 操作级 RL controller |
| R2 | 分层多轮 RL，后续阶段才加入 |

### 11.4 主要指标

**硬错误指标**

- 假引用率；
- 无依据事实性主张率；
- 证据—主张错配率；
- 越界泛化率；
- 技术失败误作科学证据率；
- 无依据数值门槛率。

**科学质量指标**

- 已知机制家族覆盖率；
- ground-truth hypothesis discovery rate；
- 候选语义独立性；
- 候选关系准确率；
- 反例召回和相关性；
- 可观测预测比例；
- 下一项检验的专家成对偏好；
- 证据扰动后的置信度校准；
- 新颖性判断与文献检索的一致性。

**行为与效率指标**

- 达到合格组合所需工具调用数；
- 重复/无状态增益动作比例；
- token 与时间成本；
- stop decision 的过早和过晚比例；
- 不同随机种子下的稳定性；
- 候选多样性是否塌缩。

### 11.5 Go / No-Go

在正式训练前预注册主指标和发布门槛。原则上：

- RL 必须在隐藏任务上优于 B2 和 B4，而不只是优于当前 prompt；
- 硬错误不能因 RL 增加；
- 提升不能只出现在训练 judge 分数；
- 候选机制覆盖和语义多样性不能明显下降；
- 下一项检验必须获得独立专家或隐藏 verifier 的稳定偏好；
- 多随机种子结果一致；
- 若公开奖励上升而隐藏证据、专家评价或真实实验效用不变，则判定发生 reward hacking，停止扩展。

---

## 12. 从假设 Agent 扩展到其他 Agent

假设 Agent 是主体和首个训练对象。其他 Agent 只在其验证器成熟后逐步接入。

### 12.1 研究规划 Agent

适合学习：

- 下一步研究子问题；
- 数据、文献、实验或澄清的路线选择；
- 是否保留替代路线；
- 预算分配和停止。

奖励来源：

- 下游任务是否可执行；
- 所需数据是否真实存在；
- 是否减少死路和返工；
- 是否覆盖关键证据缺口；
- 最终假设与实验是否能回答原问题。

不应学习：

- 伪造数据源；
- 绕过输入兼容性检查；
- 只因路线短就牺牲科学覆盖。

### 12.2 自动实验 Agent

这是继假设 Agent 后第二适合 RL 的模块，因为实验有更多可执行反馈。

适合学习：

- 在批准的实验设计内选择下一项运行；
- 资源分配；
- 失败恢复策略；
- 超参数和消融顺序；
- 何时停止重复实验。

奖励来源：

- 执行成功；
- 可复现性；
- 统计功效或不确定性降低；
- 是否区分候选假设；
- 成本和运行时间。

数据完整性、代码沙箱、哈希、终态、预算和失败分类继续由确定性程序负责。

### 12.3 证据审查 Agent

适合学习：

- 优先审查哪条高影响 claim；
- 搜索支持还是反对证据；
- 哪些冲突需要专家；
- 是否触发候选降级、合并或重开。

它不能与假设生成 policy 共用唯一自评 reward；生成者和审查者应保留模型、提示或训练数据上的隔离。

### 12.4 知识管理 Agent

适合学习：

- 检索排序；
- 哪些历史 finding 值得呈现；
- candidate knowledge 的去重与关联；
- 何时建议人工晋升。

不应由 RL 自动完成：

- candidate → canonical 的最终晋升；
- 删除或覆盖可追溯知识；
- 修改来源和置信记录。

### 12.5 统一科研主 Agent

最后才训练主编排器，适合优化：

- 子 Agent 调用顺序；
- 预算分配；
- 何时回退上一阶段；
- 何时请求人工；
- 何时结束研究任务。

主 Agent 的奖励必须来自下游已验证产物，而不是最终报告的语言质量。

---

## 13. 工程落点

### 13.1 建议新增模块

```text
src/scientific_hypothesis/
  graph_contracts.py
  graph_state.py
  actions.py
  trajectory.py
  transition.py
  verifiers/
    provenance.py
    entailment.py
    semantic_relation.py
    falsifiability.py
    counterexample.py
    discriminating_test.py
    calibration.py
    novelty.py

src/rl/
  environment.py
  reward.py
  rollout.py
  dataset.py
  policy_registry.py
  evaluation.py

hypothesis/specs/
  hypothesis_graph_v2.schema.json
  hypothesis_action_v1.schema.json
  hypothesis_trajectory_v1.schema.json

evaluation/hypothesis_agent/
  tasks/
  hidden_tests/
  rubrics/
  baselines/
  reports/
```

### 13.2 现有模块改造

#### `src/scientific_hypothesis/contracts.py`

- 保留 v1 兼容读取；
- 新增 claim 节点和 evidence span；
- 增加候选关系枚举；
- 将事实、推断、假设和缺口显式分开；
- 所有定量字段都进入来源检查。

#### `src/scientific_hypothesis/harness.py`

- validator 从字段级升级为 graph invariant；
- KB grounding 从 fail-open warning 改为可配置 hard gate；
- 反例表改为已核验 counterexample 节点生成；
- 保存完整 validator report 和 state delta；
- 增加轨迹导出接口。

#### `src/scientific_hypothesis/ranking.py`

- 保留七维 rubric 作为分析视图；
- 默认输出 Pareto/偏序和“不足以排序”；
- 全序只在用户明确需要或证据充分时生成；
- 不把模型给出的总分直接作为 RL reward。

#### `jw/subagents/solar/solar_hypothesis.yaml`

- prompt 从“一次形成完整响应”改为“按动作更新假设图”；
- 加入 action schema；
- 加入停止条件；
- 不向模型暴露隐藏 verifier 和隐藏证据。

### 13.3 版本与迁移

- v1 正式产物保持只读；
- 提供 `portfolio_v1 → graph_v2` 的显式迁移器；
- 无法证明来源的历史 `physical_basis` 一律迁移为 `assumption` 或 `gap`，不能自动升级为已核验 claim；
- v2 产物保留 v1 的读者版 Markdown 渲染；
- policy、verifier、任务集和 reward 配置分别版本化；
- 每份正式产物记录生成 policy 和 verifier 版本。

---

## 14. 实施里程碑

### M0：基线冻结与审计

- 固化当前 prompt、模型、工具和三份历史产物；
- 汇总历史错误类型；
- 建立无 RL 基线报告。

### M1：Hypothesis Graph v2

- 完成图合同、迁移器和渲染器；
- claim 级来源闭合；
- 候选关系和偏序表示；
- 全部 v1 测试继续通过。

### M2：Verifier 2.0 与隐藏测试

- 完成八类验证器；
- 专家标注首批审计集；
- 建立证据扰动与 reward-hacking 测试；
- 输出 verifier 自身的 precision/recall 和错误分析。

### M3：Best-of-N、SFT、DPO

- 建立多样采样和筛选；
- 收集专家成对偏好；
- 形成第一个强非 RL 基线；
- 确定 RL 是否仍有明确增益空间。

### M4：操作级 RL POC

- 完成环境、动作、轨迹和局部奖励；
- 训练 controller；
- 在隐藏太阳任务上与 B2/B4 对照；
- 完成多随机种子和消融。

### M5：分层 RL 与真实闭环

- 把下一项检验交接到自动实验 Agent；
- 用真实实验结果更新假设；
- 建立跨 Agent credit assignment；
- shadow mode 运行，不直接替代稳定版本。

### M6：扩展到 Planner、实验与主编排器

- 逐个模块建立验证器和强基线；
- 不同时训练所有 policy；
- 主编排器最后接入。

---

## 15. 监控、回滚与治理

### 15.1 运行监控

持续记录：

- 每类动作频率；
- action entropy；
- validator 各类失败率；
- reward 各分量分布；
- 轨迹长度；
- 工具调用成功率；
- 候选机制覆盖；
- 硬错误；
- 隐藏评测与公开奖励差距；
- 不同 policy 版本间的行为漂移。

### 15.2 Reward-hacking 告警

出现以下情形自动阻止发布：

- judge 分数上升但证据精度不升；
- 引用数量增加但 claim entailment 下降；
- 候选数量增加但机制覆盖不变；
- 回答更长但下一项检验质量不变；
- 公开指标提升、隐藏指标下降；
- 多样性显著收缩；
- policy 频繁触发同一高分模板；
- 删除证据后置信度仍不变化。

### 15.3 发布策略

```text
离线训练
  → 固定隐藏集评测
  → 专家盲评
  → shadow run
  → 小流量 canary
  → 正式发布
```

任一步失败都回退到前一稳定 policy。合同、数据和历史产物不随 policy 回滚。

### 15.4 人类保留的决策

- 研究目标和安全边界；
- 高成本实验是否执行；
- 新颖性和科学影响的最终判断；
- candidate knowledge 是否晋升为 canonical；
- 有冲突证据时的重大结论；
- 对外发布或投稿；
- policy 正式上线。

---

## 16. 主要风险与缓解

| 风险 | 表现 | 缓解 |
| --- | --- | --- |
| 奖励作弊 | 输出更可信但不更正确 | 隐藏锚点、独立 judge、确定性核验 |
| 验证器偏差 | policy 只适应验证器风格 | 多 verifier、专家抽查、模型和提示隔离 |
| 多样性塌缩 | 所有问题都生成同类机制 | mechanism archive、quality-diversity、覆盖奖励 |
| 长程 credit 困难 | 不知道哪一步导致好结果 | 操作级动作、阶段奖励、分层 option |
| 训练不稳定 | reward cliff、Echo Trap | 多样初态、短 episode、频繁 rollout、critic 和轨迹过滤 |
| 数据泄漏 | 记住问题或论文答案 | 问题族切分、时间切分、合成改名、隐藏证据 |
| 专家成本高 | 难以大规模标注 | 成对偏好、主动抽样、只审查高影响 claim |
| 领域过拟合 | 只会太阳物理表述 | 先领域内可靠，再做机制 held-out 和跨域任务 |
| 自我确认 | 生成与审查互相强化 | 生成/审查隔离、反证优先、judge 先独立作答 |
| 假设全序失真 | 证据不足仍强排名 | Pareto/偏序、允许暂不可排序 |
| 工程复杂度过高 | 训练栈侵入现有 Agent | 训练—执行解耦、v1 兼容、shadow mode |

---

## 17. 决策建议

### 17.1 现在应该做

1. 将科学假设 Agent 1.0 升级为 graph-based v2；
2. 修复 claim 级证据和 fail-open grounding；
3. 建立太阳活动周假设 benchmark 与隐藏扰动；
4. 先跑 Best-of-N、SFT 和 DPO；
5. 训练只选择科研操作的轻量 controller；
6. 把真实下一项实验结果作为后续最重要的外部奖励；
7. 在假设 Agent 验证成功后，再扩展到自动实验和主编排器。

### 17.2 现在不应该做

1. 不直接对当前完整 JSON/Markdown 做端到端 GRPO；
2. 不把当前 schema validator pass 当主要奖励；
3. 不让同一个模型生成、评审并独占奖励；
4. 不用候选数量、引用数量、长度或自评 Elo 代表科学进步；
5. 不一次训练所有子 Agent；
6. 不在没有 hidden benchmark 和回滚机制时上线在线自学习；
7. 不把高语言质量等同于假设真实性。

### 17.3 最终推荐路线

```text
科学假设 Agent 1.0
  → Verifier 2.0
  → Hypothesis Graph 2.0
  → Best-of-N + 专家偏好
  → SFT / DPO
  → 操作级 RL Controller
  → 分层多轮 RL
  → 假设—实验真实闭环
  → Planner / Experiment / Orchestrator 扩展
  → 开放式质量多样性科学搜索
```

这条路线保留了 JW 现有合同、证据和实验系统的优势，把 RL 放在它最擅长的“序贯决策与资源分配”位置，同时避免让不可验证的自然语言自评成为科学质量代理。

---

## 18. 参考文献

### Agent RL 与工具使用

1. Luo et al. [Agent Lightning: Train ANY AI Agents with Reinforcement Learning](https://arxiv.org/abs/2508.03680), 2025.
2. Wang et al. [RAGEN: Understanding Self-Evolution in LLM Agents via Multi-Turn Reinforcement Learning](https://arxiv.org/abs/2504.20073), 2025.
3. Jin et al. [Search-R1: Training LLMs to Reason and Leverage Search Engines with Reinforcement Learning](https://arxiv.org/abs/2503.09516), 2025.
4. Feng et al. [ReTool: Reinforcement Learning for Strategic Tool Use in LLMs](https://arxiv.org/abs/2504.11536), 2025.
5. Qi et al. [WebRL: Training LLM Web Agents via Self-Evolving Online Curriculum Reinforcement Learning](https://arxiv.org/abs/2411.02337), 2024.
6. Rafailov et al. [Direct Preference Optimization](https://arxiv.org/abs/2305.18290), 2023.

### 科学 Agent 与假设生成

7. Gottweis et al. [Towards an AI co-scientist](https://arxiv.org/abs/2502.18864), 2025.
8. Liu et al. [HypoBench: Towards Systematic and Principled Benchmarking for Hypothesis Generation](https://arxiv.org/abs/2504.11524), 2025.
9. Zhou et al. [Hypothesis Generation with Large Language Models](https://arxiv.org/abs/2404.04326), 2024.
10. Baek et al. [ResearchAgent: Iterative Research Idea Generation over Scientific Literature with Large Language Models](https://arxiv.org/abs/2404.07738), 2024.
11. Gu and Krenn. [Interesting Scientific Idea Generation using Knowledge Graphs and LLMs](https://arxiv.org/abs/2405.17044), 2024.
12. Radensky et al. [Scideator: Human-LLM Scientific Idea Generation Grounded in Research-Paper Facet Recombination](https://arxiv.org/abs/2409.14634), 2024.
13. Shahid et al. [Literature-Grounded Novelty Assessment of Scientific Ideas](https://arxiv.org/abs/2506.22026), 2025.
14. Pal et al. [Graph-Native Reinforcement Learning Enables Traceable Scientific Hypothesis Generation through Conceptual Recombination](https://arxiv.org/abs/2607.00924), 2026.

### 评测与奖励风险

15. [ScienceAgentBench: Toward Rigorous Assessment of Language Agents for Data-Driven Scientific Discovery](https://arxiv.org/abs/2410.05080), 2024.
16. [AstaBench: Rigorous Benchmarking of AI Agents with a Scientific Research Suite](https://arxiv.org/abs/2510.21652), 2025.
17. Gu et al. [BLADE: Benchmarking Language Model Agents for Data-Driven Science](https://arxiv.org/abs/2408.09667), 2024.
18. [SciAgentArena: Benchmarking Multi-step Scientific Reasoning of Language Agents](https://arxiv.org/abs/2606.12736), 2026.
19. Zhou. [More Convincing, Not More Correct: Self-Play Reward Hacking of Reference-Free LLM Judges](https://arxiv.org/abs/2607.05904), 2026.

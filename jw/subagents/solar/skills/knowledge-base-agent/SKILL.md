---
name: knowledge-base-agent
description: 当 solar-knowledge 需要检索结构化知识、读取冻结 literature bundle、报告知识冲突或记录维护缺口时使用；默认只读，配合 kb_* 与 lit_* 工具。
---

# 知识管理 Agent 编排

知识库是闭环的唯一可复用 grounding 入口（R1：跨任务知识引用只能使用已入库 kb id）。
假设 Agent 可以另外绑定本轮冻结文献包中的逐字引文，但这种证据只属于当前任务，不能冒充
Wiki grounding 或自动写回 Wiki。本技能编排
solar-knowledge 子 Agent 的四条流程；条目写作、蒸馏、冲突、晋升的细节规范见
`references/` 下对应文件。

## 硬规则速览

- R1 单一可复用入口：跨任务 grounding 只能给 kb id；任务文献包证据不得跨任务复用。
- R2 候选先行：一切新知识一律 `status=candidate` 入库，只有跨运行复现可晋升。
- R4 溯源完整：`kb_read` 自动写 provenance_log；每轮研究结束产出使用清单。
- R5 冲突显性化：与 canonical 矛盾时不覆盖，报冲突并回喂规划。
- 你没有 `kb_promote` / `kb_deprecate`；知识不足或冲突时保持 candidate 或 grounding blocked。

## 流程 1：检索应答

其他 agent/用户问知识时：

0. 科研阶段（规划/数据/实验/假设/审查）由宿主自动注入 `knowledge-context-v1`，无需你
   再为这些阶段代查；宿主按阶段、状态、冲突与 valid_range 选择条目并写使用回执。
   若需要主动构造同结构上下文，用 `kb_context(stage, query, valid_range?, limit?)`，
   不要自己拼 `kb_search` 结果。它返回 canonical 优先的冻结包，candidate 只作 provisional lead，
   contested canonical 必须按未决冲突处理。
1. `kb_search(query, type?, status?, confidence?)` 检索；默认只回 canonical+candidate。
2. 对要引用的条目 `kb_read(entry_id, agent="solar-knowledge", run_id, purpose)` 取全文——
   读取即溯源，purpose 写清用途（grounding / review / conflict-check）。
3. 回答时给出条目 id + 置信度 + valid_range；candidate 条目必须标注"未晋升"。
4. 检索无结果时如实说"知识缺口"，不要凭印象补写；缺口本身就是可回喂规划的研究问题。

## 流程 2：摄取

文献路径（防幻觉硬契约）：

0. 最新研究发现由维护流程显式调用 `lit_feed_catalog()` → `lit_feed_sync(feed_id)`。
   订阅只把命中写入 raw source 候选层并留下同步回执；它不证明相关性、不产生 Wiki 条目，
   更不能自动晋升。某篇命中要用于当前任务时，仍须从下面第 1 步重新绑定任务。
   `lit_delta_list()` 是增量收件箱：`baseline_source` 只是历史基线，真正变化包括
   `new_source / new_version / metadata_updated / source_retracted /
   feed_discovered / feed_removed`。
1. `lit_bind_task(research_question, distill_focus, run_id?)`——先绑定父任务给定的原始研究问题
   和蒸馏焦点；不得自行改题。跨语言时保留问题核心专名并附来源语言等价词。
2. `lit_search(query, source="all"|"ads"|"openalex"|"arxiv"|"crossref",
   from_year?, to_year?, sort="relevance"|"recent")`——
   默认用 `all` 交叉检索，命中会刷新缓存，
   并把预印本、期刊版和更新版归入同一文献族；只使用返回的首选 source_id。
   NASA ADS 缺少 token 或任一来源故障时会返回 partial/unavailable 诊断；继续使用可核验命中，
   同时保留证据缺口，禁止补造引用。refereed/retracted 是来源风险标记，不等于主张已获支持。
3. `lit_fetch(source_id)`——首选版本摘要落盘 `workspace/literature/`，返回文本路径。
4. 读文本后自己写出蒸馏 content JSON，再调 `lit_distill(source_id, entry_type, title,
   content, binding_id, confidence?)`：
   - 每个证据性字段必须是 `{"text", "quote", "location"}`；quote 为 ≤40 词原文逐字段落，
     会在缓存文本里做原样命中校验，编造的 quote 直接拒收（quote_not_grounded）。
   - 无原文支撑的字段写 `"evidence_gap"`；必填字段不允许是 gap（支撑不了就换 entry_type
     或放弃这篇）。
   - 工具同时校验研究问题—focus—来源—输出的相关性；背景性或无关文献拒收。
   - 幂等键为同一文献族的 `(source_id, normalized_focus)`；同 focus 重复调用返回已有条目，
     不同任务 focus 可各自产生候选。
   - source_ref 自动回填 DOI/arXiv id；条目永远是 candidate。
   - 单源摘要默认 confidence=low、硬上限 medium；请求 high 会被拒绝。
5. 蒸馏预算：每轮研究默认 ≤5 篇，只蒸与绑定问题直接相关的来源。

面向假设阶段的只读证据路径：

1. `lit_bundle_build(research_question, focus, limit=3)` 只从本地缓存选择直接相关来源，
   冻结最多 5 篇的版本、指纹和摘要快照；不联网、不写 Wiki。
2. `scientific_hypothesis_bind_literature_evidence` 只能绑定包内来源，quote 必须在冻结摘要
   中逐字命中，role 只能是 supports/opposes/limits。
3. 文献包只服务当前研究问题。它不是 kb id、不是完整论文核验，也不能触发晋升。

文献增量影响现有 Wiki 时：

1. 先读目标条目，再用 `lit_impact_record` 登记 supports/contradicts/qualifies/extends、
   affected_fields、scope 和 ≤40 词逐字引文；此步不改 Wiki。
2. 确需改字段时用 `lit_patch_propose` 生成绑定目标 `base_version` 的候选补丁。
3. 补丁仅保存为 `proposal_only`，不自动应用；如果目标版本已变化，重新生成提案，不做自动合并。
4. 撤稿事件保留原来源和影响链，并直接阻断受影响条目的 grounding；禁止静默删除。

运行产出路径：实验 finalize 会自动把可解释结果写回为 finding、零结果写回为
counterexample、技术失败写回为 lesson（均为 candidate，source_type=historical_run）。
你的职责是用 `kb_search(status="candidate")` 定期盘点这些候选；只有主 Agent 通过
`kb_record_reproduction` 登记至少两个带可定位证据的独立 run 后，确定性晋升门才会考虑晋升。
存在未解决冲突时晋升必须被阻断。

手工编辑回导：`kb_import(path)` 校验并回写 `knowledge_base/<type>/<id>.md`（版本 +1）。

## 流程 3：健康巡检

巡检现在由确定性成长任务承担，不再靠模型即兴盘点。

**自动运行**：宿主（主 Agent 的 knowledge-context 中间件）在每轮对话结束时会按节流策略
自动触发一次维护周期（默认每小时 1 次，`JW_KB_AUTO_GROWTH` 关闭 / `JW_KB_AUTO_GROWTH_INTERVAL`
调间隔）。因此每日 `seed_sync / health_check / distill_queue` 不需要你主动调用；子 Agent
无法启动维护扫描。

**手动补触发**：需要立刻执行、或想强制重跑时调用 `kb_growth(action="cycle")`。它按日期
桶幂等地排队并运行 `seed_sync`（补齐内置 seed，不覆盖既有条目）、`health_check`、
`distill_queue` 三个任务，每个任务都有租约、尝试预算和 `kb_growth_jobs` 审计行；同一天
重复调用只会跳过已完成任务。

**看指标**：`kb_growth(action="status")` 返回队列看板 + 四个成长指标——`reuse_rate`
（被真正读过的条目占比）、`injection_rate`（宿主阶段注入占全部读取的比例）、
`promotion_precision`（canonical 中带跨运行证据的比例）、`distillation_conversion`
（已获取文献中被蒸馏的比例）。这些指标用来判断 Wiki 是否真的在被用，而不是只涨条目数。

`health_check` 结果给出可执行清单（只报告，不改写）：

- 长期未引用的 canonical（provenance_log 无 read）→ 建议复核 valid_range 是否过期。
- 孤证 candidate（创建 >N 天、无复现证据、未晋升）→ 建议用 `kb_record_reproduction`
  补证据，或废弃，或保留为已记录缺口。
- 未解决的 counterexample 冲突 → 阻断相关晋升，需用新证据裁决。
- 尚未蒸馏的缓存来源数 → 交给 `distill_queue`。
- `proposal_only` 文献补丁只用于提示应重新蒸馏哪些字段，不直接改写 Wiki。
- 撤稿来源对应的影响记录与条目保持 grounding blocked，直到新证据通过正常摄取路径形成新版本。

`distill_queue` 只排序“仍缺 Wiki 条目”的首选缓存来源，绝不自行蒸馏：蒸馏必须走
任务绑定的 `lit_bind_task → lit_fetch → lit_distill`（逐字引文接地）流程。
`kb_growth(action="feed_sync", feed_id=...)` 才会联网增量刷新单个订阅。
`kb_growth(action="status")` 返回最近任务看板。

实验 finalize 会自动编译主张：`knowledge_base.compiler` 用稳定 claim 指纹（问题 +
primary estimand + claim scope，lesson 用 failure_mode）判断“是否同一主张”。同一主张被
另一个 run 复现时落到**同一条** candidate 并追加复现证据，两个独立 run 齐备即自动过
晋升门；冲突未解决则阻断。你通常不需要手工建条——只有缺少 design 实质字段、或需要
补充文献/概念类知识时才用 `kb_propose`。

历史 run 回填用 `kb_growth(action="compile", limit=N)`：按时间倒序重放已 finalize 的 run，
把旧写回留下的 run 级孤证候选合并成 claim-keyed 条目并补齐跨运行证据。这是按需一次性
动作，不在每日巡检里。

## 流程 4：知识使用报告与冲突裁决

每轮研究结束：`kb_log(run_id)` 输出该轮读了哪些条目、产出了哪些候选，附进主 agent
的总结；未决冲突用 `kb_conflicts()` 列出并提示规划侧把冲突当研究问题。维护队列的
总体进度用 `kb_growth(action="status")` 查看。

冲突必须显式裁决，否则会一直阻断相关条目的晋升。`kb_adjudicate_conflict` 只做三件事
之一，且必须有 `rationale`（WebUI 的"成长与冲突"标签页也能提交）：

- `uphold_canonical`：既有结论成立，把反例条目置为 deprecated。
- `overturn_canonical`：既有结论被推翻，把它置为 deprecated；反例**仍需自己**补齐
  两个独立 run 的复现证据才可能晋升。
- `defer`：只记录已复核，冲突保持未解决并继续阻断晋升。

裁决不删除任何条目、不直接晋升任何条目——它只决定哪一条主张继续有效。

## 参考文件

- `references/entry-writing.md` — 条目写作规范（八类条目的 content 字段与质量线）
- `references/distillation.md` — 蒸馏规范（quote 契约、字段取舍、预算）
- `references/conflict-handling.md` — 冲突处理（R5 落地）
- `references/promotion-criteria.md` — 晋升标准（跨运行复现；DOI 不是晋升证据）

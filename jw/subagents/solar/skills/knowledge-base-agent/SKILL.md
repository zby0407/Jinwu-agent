---
name: knowledge-base-agent
description: |
  知识管理子 Agent（solar-knowledge）的编排技能。负责 LLM Wiki 的四条流程：
  检索应答、摄取（文献蒸馏 + 运行产出写回）、健康巡检、知识使用报告。
  当任务涉及查询/维护结构化知识条目、把文献蒸馏为可引用知识、处理条目冲突、
  或汇总某轮研究的知识使用情况时使用。配合 kb_* 与 lit_* 工具工作。
---

# 知识管理 Agent 编排

知识库是闭环的唯一可复用 grounding 入口（R1：跨任务知识引用只能使用已入库 kb id）。
假设 Agent 可以另外绑定本轮冻结文献包中的逐字引文，但这种证据只属于当前任务，不能冒充
Wiki grounding 或自动写回 Wiki。本技能编排
solar-knowledge 子 Agent 的四条流程；条目写作、蒸馏、冲突、晋升的细节规范见
`references/` 下对应文件。

## 硬规则速览

- R1 单一可复用入口：跨任务 grounding 只能给 kb id；任务文献包证据不得跨任务复用。
- R2 候选先行：一切新知识一律 `status=candidate` 入库，晋升只走审核门。
- R4 溯源完整：`kb_read` 自动写 provenance_log；每轮研究结束产出使用清单。
- R5 冲突显性化：与 canonical 矛盾时不覆盖，报冲突并回喂规划。
- 你没有 `kb_promote` / `kb_deprecate` / `kb_review_decide`——晋升与废弃决定权在审核门（人）。

## 流程 1：检索应答

其他 agent/用户问知识时：

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
3. 补丁只有经 `kb_review_decide` 人审后才能应用；如果目标版本已变化，补丁标为 stale，
   必须重新评估，不做自动合并。
4. 撤稿事件保留原来源和影响链，自动生成 `literature_retraction` 复核项；禁止静默删除。

运行产出路径：实验 finalize 会自动把 findings/反例/失败经验写回为 candidate
（source_type=historical_run, confidence=low）。你的职责是用 `kb_search(status="candidate")`
定期盘点这些候选，达到晋升条件时整理依据交审核队列，而不是自己晋升。

手工编辑回导：`kb_import(path)` 校验并回写 `knowledge_base/<type>/<id>.md`（版本 +1）。

## 流程 3：健康巡检

可被 /schedule 定期触发。用 `kb_search` 结构化过滤盘点：

- 长期未引用的 canonical（provenance_log 无近期 read）→ 建议复核 valid_range 是否过期。
- 孤证 candidate（创建 >N 天、无新证据、未晋升）→ 建议补证据或交人决定是否废弃。
- valid_range 明显过期的条目 → 生成维护建议清单给人。
- `kb_review_queue(kind="revalidate")` 中的旧文献蒸馏 → 逐项核对问题/focus/来源/主张；
  审核前这些条目被 grounding 门禁隔离，不能作为假设或实验依据。复核通过只解除隔离；
  旧 DOI 自动晋升条目已降回 candidate，仍须另走晋升门。
- `kb_review_queue(kind="wiki_patch")` 中的文献候选补丁 → 核对逐字引文、影响关系、字段范围
  和 base_version；批准只应用该补丁，拒绝保留审计记录。
- `kb_review_queue(kind="literature_retraction")` → 查看受影响条目和影响 id，确认需要重写、
  降级或补充来源的范围；不得把“来源撤稿”机械等同于整条 Wiki 自动废弃。
- 巡检只出建议，不自动改状态；所有状态变更都走审核门。

## 流程 4：知识使用报告

每轮研究结束：`kb_log(run_id)` 输出该轮读了哪些条目、产出了哪些候选，附进主 agent
的总结；未决冲突用 `kb_conflicts()` 列出并提示规划侧把冲突当研究问题。

## 参考文件

- `references/entry-writing.md` — 条目写作规范（七类条目的 content 字段与质量线）
- `references/distillation.md` — 蒸馏规范（quote 契约、字段取舍、预算）
- `references/conflict-handling.md` — 冲突处理（R5 落地）
- `references/promotion-criteria.md` — 晋升标准（跨运行复现或具名专家人审；DOI 不是晋升证据）

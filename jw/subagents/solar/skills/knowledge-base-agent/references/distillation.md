# 蒸馏规范（lit_distill 防幻觉契约）

蒸馏是把一篇缓存文献转成一条 candidate 条目。LLM（你）负责读文本、写 content；
`lit_distill` 负责校验 quote 并入库。契约：

1. 每个证据性字段写成 `{"text": "...", "quote": "...", "location": "..."}`：
   - `quote` 是缓存文本中的逐字段落，≤40 词，不得改写、翻译、拼接。校验做大小写/空白
     归一化后的子串匹配，编造的 quote 一律拒收（error_code=quote_not_grounded）。
   - `location` 标明位置（如 `abstract`、`paragraph 2`）。
   - `text` 是你的蒸馏正文；列表字段（see_also、testable_predictions）的 text 给字符串数组。
2. 无原文支撑的字段写 `"evidence_gap"`（或 `{"evidence_gap": "原因"}`），不要硬填。
   必填字段不允许 gap——文献支撑不了所选 entry_type 就换类型或放弃这篇。
3. 蒸馏前必须先用 `lit_bind_task` 绑定父任务给定的 `research_question` 和
   `distill_focus`；focus 不可选，也不得由知识 Agent 自行扩题。工具会检查问题—focus—
   来源—输出四者相关性，不相关或仅背景性来源拒收。
4. source_ref 自动回填 DOI/arXiv id；status 永远是 candidate。
5. 预印本、期刊版、更新版先归入同一文献族。幂等键为文献族首选 source_id 与
   normalized_focus；同一 focus 重复调用返回已有条目，不同 focus 可形成不同候选。
6. 单源摘要蒸馏 confidence 默认 low、硬上限 medium；请求 high 返回
   confidence_cap_exceeded。来源声望或 DOI 不能抬高置信度。
7. 每轮研究默认蒸馏上限 5 篇；只蒸与绑定问题直接相关的来源。

操作建议：

- 蒸馏前一定先 `lit_fetch` 并通读文本，quote 从文本里复制，不要凭摘要记忆写。
- distill_focus 必须复用研究问题中的区分性术语；跨语言来源写成双语焦点，例如
  `极区磁场 / polar field 对 SC26 振幅的前兆关系`，以便确定性相关性门审计。
- 摘要只提供背景框架时放弃蒸馏，不把“提到磁场/预测”扩写成具体前兆机制。

# 冲突处理（R5 冲突显性化）

原则：新候选与 canonical 矛盾时，绝不覆盖、不调和、不沉默——报冲突，交给审核队列，
并把冲突变成研究问题回喂规划。

触发与表现：

- `kb_propose` / `lit_distill` 入库一条 counterexample 且其 related_ids 指向某 canonical
  条目时，系统自动在 review_queue 生成 pending 冲突项，并在返回里带 warning。
  写入本身不被阻断（候选先行，R2）。
- `kb_conflicts()` 列全部未决冲突；`kb_conflicts(entry_id)` 看单条目的冲突详情。

你的职责：

1. 发现矛盾证据时，走 counterexample 条目 + related_ids 指向被反驳的 canonical，
   让冲突进入队列；不要在 candidate 正文里悄悄改写 canonical 的结论。
2. 巡检或应答时发现未决冲突，如实上报，并把它表述为一个可检验的研究子问题
   （冲突即研究问题），供规划 Agent 纳入 brief。
3. 冲突的裁决（驳回候选 / 废弃 canonical / 收窄 valid_range）是人和审核门的事；
   你没有 kb_review_decide / kb_deprecate，不要尝试旁路。
